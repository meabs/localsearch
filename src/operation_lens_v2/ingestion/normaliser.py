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


# UK plate formats in priority order. The first match wins, so more specific
# patterns (fixed length) come before broader dateless fallbacks. Each entry is
# ``(compiled_pattern, "group-joining template")`` — the template references
# numbered capture groups and controls where the space lands.
#
# Refs: DVLA registration formats.
#   Current  (2001+):      AA00 AAA   e.g. RX71 KLD
#   Prefix   (1983–2001):  A000 AAA   e.g. A123 BCD   (also A/B/C 3-digit)
#   Suffix   (1963–1983):  AAA 000A   e.g. ABC 123D
#   Dateless (pre-1963):   AAA 000 or 000 AAA (variable length)
_UK_PLATE_FORMATS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Current: 2 letters, 2 digits, 3 letters.
    (re.compile(r"^([A-Z]{2}\d{2})([A-Z]{3})$"), r"\1 \2"),
    # Prefix: 1 letter, 1-3 digits, 3 letters (total 5-7 chars).
    (re.compile(r"^([A-Z]\d{1,3})([A-Z]{3})$"), r"\1 \2"),
    # Suffix: 3 letters, 1-3 digits, 1 letter (total 5-7 chars).
    (re.compile(r"^([A-Z]{3})(\d{1,3}[A-Z])$"), r"\1 \2"),
    # Dateless variants: up to 3 letters + up to 4 digits (or reverse).
    (re.compile(r"^([A-Z]{1,3})(\d{1,4})$"), r"\1 \2"),
    (re.compile(r"^(\d{1,4})([A-Z]{1,3})$"), r"\1 \2"),
)


def _strategy_plate(raw: str, rule: NormaliseRule) -> str:
    """Normalise a UK vehicle registration to its canonical spaced form.

    Strips punctuation, uppercases, then matches against each known DVLA
    format in priority order. Unrecognised shapes are returned compacted
    (uppercase, no spaces) so downstream alias fuzzy-match can still see
    them without us inventing a wrong split.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    if not cleaned:
        return cleaned
    for pattern, template in _UK_PLATE_FORMATS:
        match = pattern.match(cleaned)
        if match:
            return pattern.sub(template, cleaned)
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
    """Normalise a surface form for the given entity type.

    An unknown `entity_type` logs a warning and degrades to trim, rather than
    silently using the default strategy — typos should be visible.
    """
    raw = (surface or "").strip()
    if not raw:
        return raw
    schema = get_schema()
    type_def = schema.entity_types.get(entity_type)
    if type_def is None:
        known = sorted(schema.entity_types.keys())
        logger.warning(
            "Normalise: unknown entity_type %r — falling back to trim. Known types: %s",
            entity_type,
            known,
        )
        rule = NormaliseRule()
    else:
        rule = type_def.normalise
    handler = _STRATEGIES.get(rule.strategy)
    if handler is None:
        logger.warning(
            "Normalise: unknown strategy %r for type %s — using trim.",
            rule.strategy,
            entity_type,
        )
        handler = _strategy_trim
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
    confidence: float = 1.0,
) -> str:
    """Resolve `surface` to a canonical entity_id, creating one if no match exists.

    ``confidence`` is the extractor's self-reported score for this mention
    (see ``ExtractedEntity.confidence``). When we mint a new entity we seed
    its stored confidence with this value so the Audit view can flag
    low-confidence candidates for human review. When we merge into an
    existing entity, we take the max of the two so a single strong mention
    lifts a previously-weak entity out of the review queue.
    """
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
            duck_store.bump_entity_confidence(con, entity_id, confidence)
            return entity_id
    new_id = duck_store.create_entity(
        con,
        canonical,
        entity_type,
        first_seen_doc=source_doc,
        confidence=confidence,
    )
    duck_store.register_alias(
        con,
        entity_id=new_id,
        alias_text=surface,
        source_doc=source_doc,
        source_chunk=source_chunk,
    )
    return new_id
