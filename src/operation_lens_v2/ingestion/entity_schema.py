"""Dynamic entity and domain schema loading."""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import re
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_ROOT = Path(__file__).resolve().parents[3] / "config"
_DEFAULT_SCHEMA_PATH = _CONFIG_ROOT / "entity_schema.json"
_DEFAULT_DOMAIN_PACKS_PATH = _CONFIG_ROOT / "domain_packs"


@dataclass(slots=True)
class NormaliseRule:
    strategy: str = "trim"
    strip_titles: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()
    expansions: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RegexRule:
    pattern: re.Pattern[str]
    group: int = 0


@dataclass(slots=True)
class EntityTypeDef:
    name: str
    gliner_prompts: tuple[str, ...]
    llm_aliases: tuple[str, ...]
    color: str | None
    normalise: NormaliseRule
    regex: RegexRule | None


@dataclass(slots=True)
class RelationPatternDef:
    name: str
    pattern: re.Pattern[str]
    relation_type: str


@dataclass(slots=True)
class Schema:
    entity_types: dict[str, EntityTypeDef]
    relation_hints: tuple[str, ...]
    relation_patterns: tuple[RelationPatternDef, ...]
    raw_config: dict[str, Any] = field(default_factory=dict)
    domain_pack: str = "base"

    def canonical_type(self, value: str) -> str | None:
        if not value:
            return None
        normalized = value.strip().upper().replace(" ", "_").replace("-", "_")
        if normalized in self.entity_types:
            return normalized
        for type_name, type_def in self.entity_types.items():
            if normalized in (alias.upper() for alias in type_def.llm_aliases):
                return type_name
        return None

    def known_types(self) -> set[str]:
        return set(self.entity_types.keys())

    def gliner_label_specs(self) -> list[tuple[str, str]]:
        specs: list[tuple[str, str]] = []
        for type_def in self.entity_types.values():
            for prompt in type_def.gliner_prompts:
                specs.append((prompt, type_def.name))
        return specs

    def regex_rules(self) -> list[tuple[str, RegexRule]]:
        return [(name, td.regex) for name, td in self.entity_types.items() if td.regex]

    def color_for(self, type_name: str) -> str | None:
        td = self.entity_types.get(type_name)
        return td.color if td else None

    def color_map(self) -> dict[str, str]:
        return {name: td.color for name, td in self.entity_types.items() if td.color}

    def ui_config(self) -> dict[str, Any]:
        return dict(self.raw_config.get("ui", {}) or {})


def _compile_flags(raw: Any) -> int:
    if not raw:
        return 0
    flags = 0
    for ch in str(raw).lower():
        if ch == "i":
            flags |= re.IGNORECASE
        elif ch == "m":
            flags |= re.MULTILINE
        elif ch == "s":
            flags |= re.DOTALL
    return flags


def _load_normalise(raw: dict[str, Any] | None) -> NormaliseRule:
    raw = raw or {}
    return NormaliseRule(
        strategy=str(raw.get("strategy", "trim")),
        strip_titles=tuple(raw.get("strip_titles", []) or []),
        suffixes=tuple(raw.get("suffixes", []) or []),
        expansions=dict(raw.get("expansions", {}) or {}),
    )


def _load_regex(raw: dict[str, Any] | None) -> RegexRule | None:
    if not raw or not raw.get("pattern"):
        return None
    pattern = re.compile(str(raw["pattern"]), _compile_flags(raw.get("flags", "")))
    return RegexRule(pattern=pattern, group=int(raw.get("group", 0)))


def _load_entity_types(raw: dict[str, Any]) -> dict[str, EntityTypeDef]:
    result: dict[str, EntityTypeDef] = {}
    for name, cfg in raw.items():
        canonical = str(name).strip().upper()
        try:
            result[canonical] = EntityTypeDef(
                name=canonical,
                gliner_prompts=tuple(cfg.get("gliner_prompts", []) or []),
                llm_aliases=tuple(cfg.get("llm_aliases", []) or []),
                color=cfg.get("color"),
                normalise=_load_normalise(cfg.get("normalise")),
                regex=_load_regex(cfg.get("regex")),
            )
        except Exception as exc:
            logger.warning("Skipping malformed entity type %s: %s", name, exc)
    return result


def _load_relation_patterns(raw: Iterable[dict[str, Any]] | None) -> tuple[RelationPatternDef, ...]:
    out: list[RelationPatternDef] = []
    for item in raw or []:
        pattern_src = item.get("pattern")
        relation_type = item.get("relation_type")
        if not pattern_src or not relation_type:
            continue
        try:
            compiled = re.compile(str(pattern_src), _compile_flags(item.get("flags", "")))
        except re.error as exc:
            logger.warning("Invalid relation pattern %s: %s", item.get("name"), exc)
            continue
        out.append(
            RelationPatternDef(
                name=str(item.get("name", relation_type)),
                pattern=compiled,
                relation_type=str(relation_type).upper(),
            )
        )
    return tuple(out)


def _empty_schema() -> Schema:
    return Schema(entity_types={}, relation_hints=(), relation_patterns=(), raw_config={})


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("Schema config not found at %s", path)
    except json.JSONDecodeError as exc:
        logger.error("Schema config at %s is invalid JSON: %s", path, exc)
    return {}


def _dedupe_list(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    deduped: list[Any] = []
    for value in values:
        try:
            marker = json.dumps(value, sort_keys=True, default=str)
        except TypeError:
            marker = repr(value)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(value)
    return deduped


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else value
        return merged
    if isinstance(base, list) and isinstance(override, list):
        return _dedupe_list([*base, *override])
    return override


def _schema_path() -> Path:
    return Path(os.environ.get("ENTITY_SCHEMA_PATH") or _DEFAULT_SCHEMA_PATH)


def _domain_packs_path() -> Path:
    return Path(os.environ.get("DOMAIN_PACKS_PATH") or _DEFAULT_DOMAIN_PACKS_PATH)


def _load_domain_pack_config(domain_pack: str, *, _seen: set[str] | None = None) -> dict[str, Any]:
    normalized = str(domain_pack or "").strip()
    if not normalized or normalized.lower() == "base":
        return {"name": "base"}

    seen = set(_seen or set())
    if normalized in seen:
        logger.error("Circular domain pack inheritance detected for %s", normalized)
        return {"name": normalized}
    seen.add(normalized)

    path = _domain_packs_path() / f"{normalized}.json"
    data = _read_json(path)
    if not data:
        return {"name": normalized}
    parent = str(data.get("extends", "base") or "base").strip()
    parent_cfg = (
        _load_domain_pack_config(parent, _seen=seen) if parent.lower() != "base" else {"name": "base"}
    )
    merged = _deep_merge(parent_cfg, data)
    merged["name"] = normalized
    return merged


def resolve_schema_config(
    *,
    domain_pack: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = _read_json(_schema_path())
    if not base:
        return {}
    pack_name = str(domain_pack or "base").strip() or "base"
    resolved: dict[str, Any] = dict(base)
    if pack_name.lower() != "base":
        resolved = _deep_merge(resolved, _load_domain_pack_config(pack_name))
    if overrides:
        resolved = _deep_merge(resolved, overrides)
    resolved.setdefault("name", pack_name)
    return resolved


def _schema_from_config(config: dict[str, Any]) -> Schema:
    return Schema(
        entity_types=_load_entity_types(config.get("entity_types", {}) or {}),
        relation_hints=tuple(config.get("relation_hints", []) or []),
        relation_patterns=_load_relation_patterns(config.get("relation_patterns")),
        raw_config=config,
        domain_pack=str(config.get("name", "base") or "base"),
    )


_schema_cache: dict[str, Schema] = {}
_lock = threading.Lock()
_active_schema: contextvars.ContextVar[Schema | None] = contextvars.ContextVar(
    "operation_lens_active_schema",
    default=None,
)


def list_domain_packs() -> list[dict[str, Any]]:
    packs_dir = _domain_packs_path()
    if not packs_dir.exists():
        return []
    packs: list[dict[str, Any]] = []
    for pack_file in sorted(packs_dir.glob("*.json")):
        raw = _read_json(pack_file)
        if not raw:
            continue
        ui = raw.get("ui", {}) or {}
        packs.append(
            {
                "name": str(raw.get("name") or pack_file.stem),
                "extends": str(raw.get("extends", "base") or "base"),
                "description": str(raw.get("description", "") or ""),
                "default_query_templates": list(ui.get("default_query_templates", []) or []),
                "default_dashboard_widgets": list(ui.get("default_dashboard_widgets", []) or []),
            }
        )
    return packs


def get_schema(
    *,
    domain_pack: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> Schema:
    active = _active_schema.get()
    if active is not None and domain_pack is None and overrides is None:
        return active

    key = str(domain_pack or "base").strip().lower() or "base"
    if overrides:
        return _schema_from_config(resolve_schema_config(domain_pack=key, overrides=overrides))

    cached = _schema_cache.get(key)
    if cached is not None:
        return cached
    with _lock:
        cached = _schema_cache.get(key)
        if cached is None:
            config = resolve_schema_config(domain_pack=key)
            cached = _schema_from_config(config) if config else _empty_schema()
            _schema_cache[key] = cached
    return cached


def reload_schema(
    path: Path | str | None = None,
    *,
    domain_packs_path: Path | str | None = None,
) -> Schema:
    global _schema_cache
    if path is not None:
        os.environ["ENTITY_SCHEMA_PATH"] = str(path)
    if domain_packs_path is not None:
        os.environ["DOMAIN_PACKS_PATH"] = str(domain_packs_path)
    with _lock:
        _schema_cache = {}
    return get_schema()


@contextlib.contextmanager
def use_schema(schema: Schema) -> Iterator[Schema]:
    token = _active_schema.set(schema)
    try:
        yield schema
    finally:
        _active_schema.reset(token)
