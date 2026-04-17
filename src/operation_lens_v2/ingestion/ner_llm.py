from __future__ import annotations

import json
import logging
import re

import httpx

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.entity_schema import get_schema
from operation_lens_v2.models import ExtractedEntity

logger = logging.getLogger(__name__)


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _canonical_entity_type(value: str) -> str | None:
    """Resolve LLM-supplied label to a canonical type via the schema.

    Unknown-but-well-formed labels are kept as-is (upper_snake), so a model
    proposing a brand-new type (e.g. CRYPTO_WALLET) is not silently dropped.
    """
    schema = get_schema()
    canonical = schema.canonical_type(value)
    if canonical:
        return canonical
    normalized = str(value).strip().upper().replace(" ", "_").replace("-", "_")
    if re.fullmatch(r"[A-Z][A-Z0-9_]{1,39}", normalized):
        return normalized
    return None


def _find_span(text: str, surface: str) -> tuple[int, int] | None:
    surface = surface.strip()
    if not surface:
        return None
    pattern = re.compile(re.escape(surface), re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return None
    return match.start(), match.end()


async def extract_llm_entities(
    text: str, existing_entities: list[ExtractedEntity] | None = None
) -> list[ExtractedEntity]:
    """Use the local extraction model to propose entities beyond those already captured."""
    existing_entities = existing_entities or []
    known = [f"{entity.text} ({entity.entity_type})" for entity in existing_entities]
    known_types = sorted(get_schema().known_types())
    types_hint = ", ".join(known_types) if known_types else "any UPPER_SNAKE_CASE label"
    prompt = (
        "You are an information extraction system.\n"
        "Extract named entities from the text that are not already covered.\n"
        "Return a JSON array only, with objects containing: text, entity_type, confidence.\n"
        f"Prefer these entity types when applicable: {types_hint}.\n"
        "If an entity genuinely does not fit any of those, you may introduce a new "
        "UPPER_SNAKE_CASE type (max 40 characters). Do NOT invent types for convenience.\n"
        "Only include entities whose exact surface text appears in the input text.\n"
        "Do not include duplicates or substrings of the supplied known entities.\n"
        "confidence must be a float between 0.1 and 0.95.\n\n"
        f"Known entities: {', '.join(known) if known else 'none'}\n\n"
        f"Text:\n{text[:2500]}\n\n"
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
        logger.warning("LLM entity extraction failed: %s", exc)
        return []

    try:
        items = json.loads(_strip_markdown_fences(raw_text))
        if not isinstance(items, list):
            raise ValueError("Expected JSON array")
    except Exception as exc:
        logger.warning("LLM entity JSON parse failure: %s | raw: %.200s", exc, raw_text)
        return []

    out: list[ExtractedEntity] = []
    seen: set[tuple[str, str, int, int]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        surface = str(item.get("text", "")).strip()
        entity_type = _canonical_entity_type(str(item.get("entity_type", "")))
        if not surface or not entity_type:
            continue
        span = _find_span(text, surface)
        if not span:
            continue
        raw_conf = float(item.get("confidence", 0.5))
        confidence = max(settings.llm_confidence_min, min(settings.llm_confidence_max, raw_conf))
        start, end = span
        dedupe_key = (surface.lower(), entity_type, start, end)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(
            ExtractedEntity(
                text=text[start:end],
                entity_type=entity_type,
                start=start,
                end=end,
                confidence=confidence,
            )
        )
    return out
