"""Dynamic entity/relation schema loaded from a JSON registry.

The pipeline reads every entity type, GLiNER prompt, normalisation rule,
relation pattern, and UI color from a single JSON file. Adding a new
entity type is an edit to the JSON — no code change required.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "config" / "entity_schema.json"


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

    # ── Lookups ────────────────────────────────────────────────────────────
    def canonical_type(self, value: str) -> str | None:
        """Resolve a raw type/alias to a canonical entity type, or None."""
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
        """Return (prompt, canonical_type) pairs for every GLiNER prompt."""
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
    return Schema(entity_types={}, relation_hints=(), relation_patterns=())


def _load_from_path(path: Path) -> Schema:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("Entity schema not found at %s — extraction will be empty.", path)
        return _empty_schema()
    except json.JSONDecodeError as exc:
        logger.error("Entity schema at %s is invalid JSON: %s", path, exc)
        return _empty_schema()

    return Schema(
        entity_types=_load_entity_types(data.get("entity_types", {}) or {}),
        relation_hints=tuple(data.get("relation_hints", []) or []),
        relation_patterns=_load_relation_patterns(data.get("relation_patterns")),
    )


_schema: Schema | None = None
_lock = threading.Lock()


def _resolve_path() -> Path:
    override = os.environ.get("ENTITY_SCHEMA_PATH")
    return Path(override) if override else _DEFAULT_SCHEMA_PATH


def get_schema() -> Schema:
    """Return the singleton schema, loading it on first access."""
    global _schema
    if _schema is None:
        with _lock:
            if _schema is None:
                _schema = _load_from_path(_resolve_path())
    return _schema


def reload_schema(path: Path | str | None = None) -> Schema:
    """Reload the schema from disk. Intended for tests or hot-reload paths."""
    global _schema
    with _lock:
        _schema = _load_from_path(Path(path) if path else _resolve_path())
    return _schema
