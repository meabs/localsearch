"""Relationship extraction: schema-driven patterns + open-type LLM + co-occurrence.

Relation types are not gated by a closed enum. Any UPPER_SNAKE label from
the LLM is accepted so long as it looks like a relation identifier; the
schema's `relation_hints` just suggest preferred labels.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.entity_schema import get_schema
from operation_lens_v2.models import ExtractedEntity, RelationshipCandidate

logger = logging.getLogger(__name__)

_RELATION_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")


def _canonical_relation_type(value: str, *, default: str = "INFERRED_LINK") -> str:
    """Accept any UPPER_SNAKE identifier; reject free-form prose."""
    normalized = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    if _RELATION_TYPE_RE.fullmatch(normalized):
        return normalized
    return default


def _run_patterns(text: str) -> list[RelationshipCandidate]:
    """Stage 1: apply generic relation patterns from the schema."""
    out: list[RelationshipCandidate] = []
    schema = get_schema()
    for pattern_def in schema.relation_patterns:
        try:
            for match in pattern_def.pattern.finditer(text):
                groups = match.groupdict()
                source = (groups.get("source") or "").strip()
                target = (groups.get("target") or "").strip()
                if not source or not target or source == target:
                    continue
                out.append(
                    RelationshipCandidate(
                        source=source,
                        target=target,
                        relation_type=pattern_def.relation_type,
                        span_text=match.group(0),
                        span_start=match.start(),
                        span_end=match.end(),
                        confidence=settings.pattern_confidence,
                        extraction_method="pattern",
                    )
                )
        except Exception as exc:
            logger.warning("Relation pattern %s failed: %s", pattern_def.name, exc)
    return out


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


_LLM_INPUT_CHAR_LIMIT = 2000


async def _run_llm_stage(text: str, entities: list[ExtractedEntity]) -> list[RelationshipCandidate]:
    """Stage 2: use the extraction model to propose typed relationships as JSON."""
    if not entities:
        return []

    schema = get_schema()
    entity_list = [f"{e.text} ({e.entity_type})" for e in entities]
    hints = (
        ", ".join(schema.relation_hints)
        if schema.relation_hints
        else "any UPPER_SNAKE_CASE label"
    )
    truncated_text = text[:_LLM_INPUT_CHAR_LIMIT]
    prompt = (
        "You are an information extraction system. Extract typed relationships from the text.\n"
        "Return a JSON array only — no prose, no markdown fences.\n"
        "Each item must have: source, target, relation_type, span_text, confidence.\n"
        f"Prefer these relation_type labels when applicable: {hints}.\n"
        "If none fit, you may invent a new UPPER_SNAKE_CASE relation type "
        "(max 40 characters). Do NOT invent types for convenience.\n"
        f"confidence must be a float between 0.1 and 0.95.\n\n"
        f"Known entities: {', '.join(entity_list)}\n\n"
        f"Text:\n{truncated_text}\n\n"
        "JSON array:"
    )

    url = f"{settings.ollama_base_url}/api/generate"
    try:
        async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
            resp = await client.post(
                url,
                json={
                    "model": settings.local_extraction_model,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            raw_text = resp.json().get("response", "")
    except Exception as exc:
        logger.warning("LLM relationship extraction failed: %s", exc)
        return []

    try:
        cleaned = _strip_markdown_fences(raw_text)
        items = json.loads(cleaned)
        if not isinstance(items, list):
            raise ValueError("Expected JSON array")
    except Exception as exc:
        logger.warning("LLM JSON parse failure (falling back): %s | raw: %.200s", exc, raw_text)
        return []

    out: list[RelationshipCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rtype = _canonical_relation_type(str(item.get("relation_type", "")))
        raw_conf = float(item.get("confidence", 0.5))
        confidence = max(settings.llm_confidence_min, min(settings.llm_confidence_max, raw_conf))
        span = str(item.get("span_text", ""))
        # Locate the span in the original text. The LLM only ever saw `truncated_text`,
        # so fall back to that window before declaring the span absent. If neither
        # contains it, record the span text but leave offsets at 0 (sentinel for
        # "unlocatable") rather than coercing an invalid -1 to a real offset.
        span_start = text.find(span) if span else -1
        if span and span_start < 0:
            span_start = truncated_text.find(span)
        if span and span_start >= 0:
            span_end = span_start + len(span)
        else:
            span_start = 0
            span_end = 0
        fallback_text = text[:100] if text else ""
        out.append(
            RelationshipCandidate(
                source=str(item.get("source", "")),
                target=str(item.get("target", "")),
                relation_type=rtype,
                span_text=span or fallback_text,
                span_start=span_start,
                span_end=span_end,
                confidence=confidence,
                extraction_method="llm",
            )
        )
    return out


async def extract_relationships(
    text: str, entities: list[ExtractedEntity]
) -> list[RelationshipCandidate]:
    """Two-stage relationship extraction: schema patterns, then LLM, then co-occurrence."""
    out = _run_patterns(text)

    if not out:
        llm_rels = await _run_llm_stage(text, entities)
        out.extend(llm_rels)

    if not out and len(entities) >= 2:
        out.append(
            RelationshipCandidate(
                source=entities[0].text,
                target=entities[1].text,
                relation_type="MENTIONED_WITH",
                span_text=text[:180],
                span_start=0,
                span_end=min(180, len(text)),
                confidence=settings.cooccurrence_confidence,
                extraction_method="co-occurrence",
            )
        )

    return out
