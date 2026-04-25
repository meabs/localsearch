from __future__ import annotations

import json
import logging
import re
from typing import Any

from operation_lens_v2.config import settings
from operation_lens_v2.query import parser
from operation_lens_v2.runtime import get_http_client

logger = logging.getLogger(__name__)

_DATE_TOKEN_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{2,4})\b",
    re.IGNORECASE,
)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_ALLOWED_INTENTS = {
    "general_query",
    "entity_relationship_query",
    "exact_identifier_query",
    "document_query",
    "document_summary_query",
    "entity_inventory_query",
    "entity_relationship_inventory_query",
}


def _strip_fences(text: str) -> str:
    return _JSON_FENCE_RE.sub("", text.strip()).strip()


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(text)
    return out


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _default_search_strategy(parsed: dict[str, Any]) -> str:
    intent = str(parsed.get("intent") or "general_query")
    if intent in {"entity_relationship_query", "entity_relationship_inventory_query"}:
        return "graph_forward"
    if intent in {"exact_identifier_query", "entity_inventory_query"}:
        return "exact_then_fts"
    if intent == "document_summary_query":
        return "document_focus"
    return "hybrid_balanced"


def _heuristic_pivots(query_text: str, parsed: dict[str, Any]) -> list[str]:
    entities = list(parsed.get("entities") or [])
    inventory_target = parsed.get("inventory_target")
    pivots: list[str] = []
    if entities:
        subject = entities[0]
        pivots.extend(
            [
                f"What connects {subject} to the strongest linked locations?",
                f"List vehicles, phones, and locations associated with {subject}.",
                f"Build a chronology for {subject} with exact citations.",
            ]
        )
    elif inventory_target:
        pivots.extend(
            [
                f"Which {inventory_target.lower()} entities bridge multiple documents?",
                f"Show the strongest associations for these {inventory_target.lower()} entities.",
                f"What locations recur across these {inventory_target.lower()} findings?",
            ]
        )
    elif parsed.get("document_refs"):
        doc_ref = str(parsed["document_refs"][0])
        pivots.extend(
            [
                f"Summarise key entities and leads from {doc_ref}.",
                f"What new associations appear in {doc_ref}?",
                f"Build a graph neighborhood from the main actors in {doc_ref}.",
            ]
        )
    else:
        pivots.extend(
            [
                f"What are the strongest leads suggested by: {query_text}",
                "Show bridge entities and shared infrastructure across the top results.",
                "What should be investigated next based on the current evidence?",
            ]
        )
    return pivots[:3]


def _deterministic_plan(query_text: str, parsed: dict[str, Any]) -> dict[str, Any]:
    subjects = _dedupe_strings(list(parsed.get("entities") or []))
    date_tokens = _dedupe_strings(_DATE_TOKEN_RE.findall(query_text))
    document_refs = _dedupe_strings(list(parsed.get("document_refs") or []))
    filters = {
        "document_refs": document_refs,
        "date_hints": date_tokens,
        "entity_types": [parsed["inventory_target"]] if parsed.get("inventory_target") else [],
        "relation_types": [],
    }
    graph_targets = subjects[:3]
    rewritten_query = query_text.strip()
    if subjects and not parsed.get("document_refs") and parsed.get("intent") == "general_query":
        rewritten_query = f"{query_text.strip()} with graph links and exact citations"

    return {
        "intent": parsed.get("intent", "general_query"),
        "subjects": subjects,
        "filters": filters,
        "search_strategy": _default_search_strategy(parsed),
        "graph_expansion_targets": graph_targets,
        "follow_up_questions": _heuristic_pivots(query_text, parsed),
        "suggested_pivots": _heuristic_pivots(query_text, parsed),
        "rewrite_query": rewritten_query,
        "planner_backend": "deterministic",
        "planner_status": "fallback",
    }


def _normalise_plan(
    query_text: str,
    parsed: dict[str, Any],
    raw_plan: dict[str, Any],
) -> dict[str, Any]:
    base = _deterministic_plan(query_text, parsed)
    plan = dict(base)
    if not isinstance(raw_plan, dict):
        return plan

    intent = str(raw_plan.get("intent") or base["intent"]).strip()
    if intent not in _ALLOWED_INTENTS:
        intent = str(base["intent"])
    plan["intent"] = intent
    plan["subjects"] = _dedupe_strings(_as_list(raw_plan.get("subjects") or base["subjects"]))

    raw_filters = raw_plan.get("filters") if isinstance(raw_plan.get("filters"), dict) else {}
    plan["filters"] = {
        "document_refs": _dedupe_strings(
            _as_list(raw_filters.get("document_refs") or base["filters"]["document_refs"])
        ),
        "date_hints": _dedupe_strings(
            _as_list(raw_filters.get("date_hints") or base["filters"]["date_hints"])
        ),
        "entity_types": _dedupe_strings(
            _as_list(raw_filters.get("entity_types") or base["filters"]["entity_types"])
        ),
        "relation_types": _dedupe_strings(
            _as_list(raw_filters.get("relation_types") or base["filters"]["relation_types"])
        ),
    }
    search_strategy = str(raw_plan.get("search_strategy") or base["search_strategy"]).strip()
    plan["search_strategy"] = search_strategy or base["search_strategy"]
    graph_targets = raw_plan.get("graph_expansion_targets") or plan["subjects"]
    plan["graph_expansion_targets"] = _dedupe_strings(_as_list(graph_targets))[:5]
    plan["follow_up_questions"] = _dedupe_strings(
        _as_list(raw_plan.get("follow_up_questions") or base["follow_up_questions"])
    )[:5]
    plan["suggested_pivots"] = _dedupe_strings(
        _as_list(raw_plan.get("suggested_pivots") or plan["follow_up_questions"])
    )[:5]
    rewrite_query = str(raw_plan.get("rewrite_query") or "").strip()
    plan["rewrite_query"] = rewrite_query or base["rewrite_query"]
    plan["planner_backend"] = str(raw_plan.get("planner_backend") or settings.local_reasoning_model)
    plan["planner_status"] = "refined"
    return plan


async def plan_query(
    query_text: str,
    *,
    planner_mode: bool = True,
) -> dict[str, Any]:
    parsed = parser.parse_query(query_text)
    base = _deterministic_plan(query_text, parsed)
    if not planner_mode:
        return {**base, "raw_query": query_text, "parsed": parsed}

    prompt = {
        "task": "Refine an investigative search plan for a local-first evidence graph system.",
        "query": query_text,
        "deterministic_parse": parsed,
        "requirements": {
            "intent": "One of the supplied intents when possible.",
            "subjects": "Key people, places, assets, or identifiers to search for.",
            "filters": "document_refs, date_hints, entity_types, relation_types",
            "search_strategy": "Short search mode such as graph_forward, exact_then_fts, document_focus, hybrid_balanced",
            "graph_expansion_targets": "Names to use for graph expansion if helpful.",
            "follow_up_questions": "Up to 3 follow-up questions an investigator should ask next.",
            "suggested_pivots": "Up to 3 concrete pivot queries.",
            "rewrite_query": "A retrieval-friendly rewrite of the user query that preserves intent.",
        },
        "return_format": "JSON object only. No prose.",
    }
    try:
        client = get_http_client(base_url=settings.ollama_base_url, timeout=settings.ollama_timeout)
        response = await client.post(
            "/api/generate",
            json={
                "model": settings.local_reasoning_model,
                "prompt": (
                    "You are a senior intelligence analyst helping plan local evidence retrieval.\n"
                    "Return JSON only.\n"
                    f"{json.dumps(prompt)}"
                ),
                "stream": False,
            },
        )
        response.raise_for_status()
        raw_text = str(response.json().get("response", ""))
        candidate = json.loads(_strip_fences(raw_text))
        normalized = _normalise_plan(query_text, parsed, candidate)
        normalized["raw_query"] = query_text
        normalized["parsed"] = parsed
        return normalized
    except Exception as exc:
        logger.warning("Local query planner failed, falling back to deterministic parse: %s", exc)
        return {**base, "raw_query": query_text, "parsed": parsed}
