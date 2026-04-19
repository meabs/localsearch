from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from operation_lens_v2.config import settings

logger = logging.getLogger(__name__)

WHITESPACE_RE = re.compile(r"\s+")
MARKDOWN_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.DOTALL)
ATOMIC_FACT_SYSTEM_PROMPT = (
    "You are a precise extraction model. Return only a JSON list of atomic facts. "
    "Extract only concrete statements directly supported by the chunk. Skip speculation, "
    "inference, guesses, and commentary."
)


@dataclass(frozen=True)
class AtomicFact:
    subject: str
    predicate: str
    object: str
    when: str | None
    where: str | None
    doc_id: str
    page: int
    source_chunk_id: str
    confidence: float


def _normalise_text(value: Any) -> str:
    return WHITESPACE_RE.sub(" ", str(value or "")).strip()


def _strip_markdown_fences(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = MARKDOWN_FENCE_RE.sub("", cleaned).strip()
    return cleaned


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.5
    return max(0.1, min(0.95, confidence))


def _is_truthy_flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _coerce_page(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _chunk_doc_id(chunk: dict[str, Any]) -> str:
    return str(chunk.get("doc_id") or chunk.get("doc_name") or "unknown-doc")


def _chunk_source_id(chunk: dict[str, Any], doc_id: str, page: int) -> str:
    for key in ("source_chunk_id", "chunk_id", "id"):
        value = chunk.get(key)
        if value not in (None, ""):
            return str(value)
    return f"{doc_id}:{page}"


def _build_chunk_prompt(chunk: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    doc_id = _chunk_doc_id(chunk)
    page = _coerce_page(chunk.get("page"))
    source_chunk_id = _chunk_source_id(chunk, doc_id, page)
    cleaned_text = _normalise_text(chunk.get("text"))
    prompt_obj = {
        "task": "Extract atomic facts as JSON list. One fact per {subject, predicate, object}. Skip speculation.",
        "chunk": {
            "doc_id": doc_id,
            "page": page,
            "source_chunk_id": source_chunk_id,
            "text": cleaned_text,
        },
        "instructions": [
            "Return a JSON list only.",
            "Each item must include subject, predicate, object, when, where, confidence.",
            "Use when and where only when they are supported by the text.",
            "Mark only directly supported facts; skip speculation.",
            "If the chunk contains no extractable facts, return an empty list.",
        ],
    }
    return source_chunk_id, prompt_obj


def _extract_fact_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        facts = payload.get("facts")
        if isinstance(facts, list):
            return [item for item in facts if isinstance(item, dict)]
    return []


async def extract_atomic_facts(
    chunks: list[dict[str, Any]],
    client: Any,
) -> list[AtomicFact]:
    facts: list[AtomicFact] = []

    for chunk in chunks:
        if not _normalise_text(chunk.get("text")):
            continue

        source_chunk_id, prompt_obj = _build_chunk_prompt(chunk)
        try:
            response = await client.post(
                "/api/generate",
                json={
                    "model": settings.local_extraction_model,
                    "prompt": f"{ATOMIC_FACT_SYSTEM_PROMPT}\n\n{json.dumps(prompt_obj)}",
                    "stream": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
            response_text = _strip_markdown_fences(str(payload.get("response", "")))
            parsed = json.loads(response_text)
        except Exception as exc:
            logger.warning(
                "Atomic fact extraction failed for chunk %s: %s",
                source_chunk_id,
                exc,
            )
            continue

        for row in _extract_fact_rows(parsed):
            if _is_truthy_flag(row.get("speculative")) or _is_truthy_flag(row.get("is_speculative")):
                continue

            subject = _normalise_text(row.get("subject"))
            predicate = _normalise_text(row.get("predicate"))
            object_text = _normalise_text(row.get("object"))
            if not subject or not predicate or not object_text:
                continue

            facts.append(
                AtomicFact(
                    subject=subject,
                    predicate=predicate,
                    object=object_text,
                    when=_normalise_text(row.get("when")) or None,
                    where=_normalise_text(row.get("where")) or None,
                    doc_id=_chunk_doc_id(chunk),
                    page=_coerce_page(chunk.get("page")),
                    source_chunk_id=source_chunk_id,
                    confidence=_clamp_confidence(row.get("confidence")),
                )
            )

    return facts
