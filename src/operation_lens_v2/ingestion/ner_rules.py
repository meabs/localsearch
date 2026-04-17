"""Regex-based NER driven entirely by the entity schema JSON.

Any entity type in `config/entity_schema.json` with a `regex` block
is automatically extracted here — no hardcoded type handling.
"""

from __future__ import annotations

import logging

from operation_lens_v2.ingestion.entity_schema import RegexRule, get_schema
from operation_lens_v2.models import ExtractedEntity

logger = logging.getLogger(__name__)


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
