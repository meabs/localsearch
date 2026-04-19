"""Regex-based NER driven entirely by the entity schema JSON.

Any entity type in `config/entity_schema.json` with a `regex` block
is automatically extracted here — no hardcoded type handling.
"""

from __future__ import annotations

import logging
import re

from operation_lens_v2.ingestion.entity_schema import RegexRule, get_schema
from operation_lens_v2.models import ExtractedEntity

logger = logging.getLogger(__name__)
_NON_DIGITS_RE = re.compile(r"\D+")


def _mask_last_four(surface: str) -> str:
    stripped = surface.strip()
    if len(stripped) <= 4:
        return "*" * len(stripped)
    return f"{stripped[:-4]}****"


def _resolve_span(match, rule: RegexRule) -> tuple[str, int, int] | None:
    group_idx = rule.group
    try:
        if group_idx > 0 and group_idx <= (match.lastindex or 0):
            text = match.group(group_idx)
            start = match.start(group_idx)
            end = match.end(group_idx)
        else:
            text = match.group(0)
            start = match.start(0)
            end = match.end(0)
    except (IndexError, ValueError):
        return None
    if not text:
        return None
    return text, start, end


def _luhn_valid(digits: str) -> bool:
    total = 0
    double_digit = False
    for char in reversed(digits):
        value = ord(char) - ord("0")
        if double_digit:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        double_digit = not double_digit
    return total % 10 == 0


def _is_valid_credit_card(surface: str) -> bool:
    digits = _NON_DIGITS_RE.sub("", surface)
    if not 13 <= len(digits) <= 19:
        return False
    return _luhn_valid(digits)


def _is_valid_national_id(surface: str) -> bool:
    compact = re.sub(r"[\s-]", "", surface).upper()
    if re.fullmatch(r"(?!BG|GB|KN|NK|NT|TN|ZZ)[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]?", compact):
        return True
    ssn = re.fullmatch(r"(\d{3})-(\d{2})-(\d{4})", surface.strip())
    if not ssn:
        return False
    area, group, serial = ssn.groups()
    if area in {"000", "666"} or area.startswith("9"):
        return False
    if group == "00" or serial == "0000":
        return False
    return True


def extract_rule_entities(text: str) -> list[ExtractedEntity]:
    """Extract entities using regex rules defined in the entity schema."""
    schema = get_schema()
    out: list[ExtractedEntity] = []
    for type_name, rule in schema.regex_rules():
        try:
            for match in rule.pattern.finditer(text):
                resolved = _resolve_span(match, rule)
                if not resolved:
                    continue
                surface, start, end = resolved
                if type_name == "CREDIT_CARD" and not _is_valid_credit_card(surface):
                    continue
                if type_name == "NATIONAL_ID" and not _is_valid_national_id(surface):
                    continue
                if type_name == "NATIONAL_ID":
                    logger.debug("Matched NATIONAL_ID %s", _mask_last_four(surface))
                display = surface.upper() if type_name == "VEHICLE" else surface
                out.append(
                    ExtractedEntity(
                        text=display,
                        entity_type=type_name,
                        start=start,
                        end=end,
                    )
                )
        except Exception as exc:
            logger.warning("Regex rule for %s failed: %s", type_name, exc)

    return _dedupe(out)


def _dedupe(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    """Remove exact duplicates and fully-nested lower-priority spans."""
    if not entities:
        return entities
    entities = sorted(entities, key=lambda e: (e.start, -(e.end - e.start)))
    kept: list[ExtractedEntity] = []
    for ent in entities:
        overlaps = False
        for existing in kept:
            if (
                ent.start >= existing.start
                and ent.end <= existing.end
                and ent.entity_type == existing.entity_type
            ):
                overlaps = True
                break
        if not overlaps:
            kept.append(ent)
    return kept
