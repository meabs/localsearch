from __future__ import annotations

import json
import logging
import re
from typing import Any

from operation_lens_v2.config import settings

logger = logging.getLogger(__name__)

FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)
MAX_EXPANDED_QUERIES = 3


def _strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    match = FENCE_RE.match(cleaned)
    if match:
        return match.group(1).strip()
    cleaned = cleaned.replace("```json", "").replace("```", "")
    return cleaned.strip()


def _dedupe_queries(queries: list[str], *, original_query: str) -> list[str]:
    ordered: list[str] = [original_query]
    seen = {original_query.strip().lower()}
    for query in queries:
        candidate = str(query).strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in seen:
            continue
        ordered.append(candidate)
        seen.add(lowered)
        if len(ordered) >= MAX_EXPANDED_QUERIES:
            break
    return ordered


async def expand_query(
    query_text: str,
    parsed: dict[str, Any],
    client,
) -> list[str]:
    prompt_obj = {
        "task": "Generate a small set of retrieval queries for evidence search.",
        "query": query_text,
        "intent": parsed.get("intent"),
        "entities": parsed.get("entities", []),
        "resolved_entity_ids": parsed.get("resolved_entity_ids", []),
        "document_refs": parsed.get("document_refs", []),
        "instructions": [
            "Return JSON only with shape {\"queries\": [\"...\", \"...\"]}.",
            "Always include the original query as the first entry.",
            "Return 2 to 3 total queries when possible.",
            "Keep the queries short, concrete, and distinct.",
        ],
    }
    try:
        response = await client.post(
            "/api/generate",
            json={
                "model": settings.local_extraction_model,
                "prompt": json.dumps(prompt_obj),
                "stream": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
        response_text = _strip_markdown_fences(str(payload.get("response", "")))
        parsed_json = json.loads(response_text)
        queries = parsed_json.get("queries", [])
        if not isinstance(queries, list):
            return [query_text]
        candidate_queries = [str(item) for item in queries]
        return _dedupe_queries(candidate_queries, original_query=query_text)
    except Exception as exc:
        logger.warning("Query expansion failed: %s", exc)
        return [query_text]
