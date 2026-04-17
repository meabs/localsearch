"""Entity normalisation driven by strategies declared in the entity schema.

Each entity type picks a strategy in `config/entity_schema.json`; this
module dispatches on that string. Unknown strategies degrade to trim.
"""

from __future__ import annotations

import logging
import re

from rapidfuzz.distance import JaroWinkler

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store
from operation_lens_v2.ingestion.entity_schema import NormaliseRule, get_schema

logger = logging.getLogger(__name__)


def _title_case_words(value: str) -> str:
    return " ".join(part.capitalize() for part in value.split() if part)


def _strategy_person(raw: str, rule: NormaliseRule) -> str:
    cleaned = raw
    for title in rule.strip_titles:
        cleaned = re.sub(rf"\b{re.escape(title)}\.?\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if "," in cleaned:
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
        if len(parts) == 2:
            cleaned = f"{parts[1]} {parts[0]}"
    return _title_case_words(cleaned.lower())


def _strategy_strip_suffix_title(raw: str, rule: NormaliseRule) -> str:
    cleaned = raw.strip()
    for suffix in rule.suffixes:
        cleaned = re.sub(rf"\b{re.escape(suffix)}\b\.?", "", cleaned, flags=re.IGNORECASE)
    return _title_case_words(cleaned.strip().lower())


def _strategy_title_expand(raw: str, rule: NormaliseRule) -> str:
    cleaned = raw.strip()
    for pattern, replacement in rule.expansions.items():
        cleaned = re.sub(pattern, replacement, cleaned)
    return _title_case_words(cleaned.lower())


def _strategy_phone_e164(raw: str, rule: NormaliseRule) -> str:
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw.strip()
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = "44" + digits[1:]
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits


def _strategy_plate(raw: str, rule: NormaliseRule) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    if 5 <= len(cleaned) <= 8:
        split_point = max(2, len(cleaned) - 3)
        return f"{cleaned[:split_point]} {cleaned[split_point:]}"
    return cleaned


def _strategy_upper_snake(raw: str, rule: NormaliseRule) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", raw.strip()).upper()
    return re.sub(r"[\s-]+", "_", cleaned).strip("_")


def _strategy_upper_compact(raw: str, rule: NormaliseRule) -> str:
    return re.sub(r"\s+", "", raw.strip()).upper()


def _strategy_lower(raw: str, rule: NormaliseRule) -> str:
    return raw.strip().lower()


def _strategy_trim(raw: str, rule: NormaliseRule) -> str:
    return raw.strip()


_STRATEGIES = {
    "person": _strategy_person,
    "strip_suffix_title": _strategy_strip_suffix_title,
    "title_expand": _strategy_title_expand,
    "phone_e164": _strategy_phone_e164,
    "plate": _strategy_plate,
    "upper_snake": _strategy_upper_snake,
    "upper_compact": _strategy_upper_compact,
    "lower": _strategy_lower,
    "trim": _strategy_trim,
}


def normalise(surface: str, entity_type: str) -> str:
    """Normalise a surface form for the given entity type."""
    raw = (surface or "").strip()
    if not raw:
        return raw
    schema = get_schema()
    type_def = schema.entity_types.get(entity_type)
    rule = type_def.normalise if type_def else NormaliseRule()
    handler = _STRATEGIES.get(rule.strategy, _strategy_trim)
    try:
        return handler(raw, rule)
    except Exception as exc:
        logger.warning("Normalise strategy %s failed for %s: %s", rule.strategy, entity_type, exc)
        return raw


def resolve_entity(
    *,
    surface: str,
    entity_type: str,
    con,
    source_doc: str,
    source_chunk: str,
    threshold: float | None = None,
) -> str:
    """Resolve `surface` to a canonical entity_id, creating one if no match exists."""
    canonical = normalise(surface, entity_type)
    match_threshold = threshold if threshold is not None else settings.alias_threshold
    candidates = duck_store.get_entities_by_type(con, entity_type)
    for entity_id, candidate_name in candidates:
        if JaroWinkler.similarity(canonical, candidate_name) >= match_threshold:
            duck_store.register_alias(
                con,
                entity_id=entity_id,
                alias_text=surface,
                source_doc=source_doc,
                source_chunk=source_chunk,
            )
            return entity_id
    new_id = duck_store.create_entity(con, canonical, entity_type, first_seen_doc=source_doc)
    duck_store.register_alias(
        con,
        entity_id=new_id,
        alias_text=surface,
        source_doc=source_doc,
        source_chunk=source_chunk,
    )
    return new_id
