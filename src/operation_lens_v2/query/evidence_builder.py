from __future__ import annotations

from typing import Any
from uuid import uuid4


def build_evidence_packet(
    *,
    query_text: str,
    query_intent: str,
    entities_resolved: list[dict[str, str]],
    ranked_results: list[dict[str, Any]],
    case_scope: str = "ALL_CASES",
) -> dict[str, Any]:
    relationships = [
        {
            "source_entity": row.get("source_entity"),
            "target_entity": row.get("target_entity"),
            "source_name": row.get("source_name"),
            "target_name": row.get("target_name"),
            "source_type": row.get("source_type"),
            "target_type": row.get("target_type"),
            "relation_type": row.get("relation_type"),
            "confidence": row.get("confidence", row.get("rank_score", 0.0)),
            "doc_id": row.get("doc_id"),
            "doc_name": row.get("doc_name"),
            "page": row.get("page"),
            "span_text": row.get("span_text"),
            "chunk_id": row.get("chunk_id"),
        }
        for row in ranked_results
        if row.get("source") == "graph"
    ]
    exact_matches = [
        {
            "entity_id": row.get("entity_id"),
            "alias_text": row.get("alias_text"),
            "canonical_name": row.get("canonical_name"),
            "entity_type": row.get("entity_type"),
            "mention_count": row.get("mention_count", 0),
            "doc_id": row.get("doc_id"),
            "doc_name": row.get("doc_name"),
            "page": row.get("page"),
            "span_text": row.get("text", ""),
            "chunk_id": row.get("chunk_id"),
            "score": row.get("rank_score", row.get("score", 0.0)),
        }
        for row in ranked_results
        if row.get("source") == "exact"
    ]
    link_candidates = [
        {
            "doc_id": row.get("doc_id"),
            "page": row.get("page"),
            "chunk_id": row.get("chunk_id"),
            "span_text": row.get("text", ""),
            "entity_pair": list(row.get("entity_pair", []))[:2],
        }
        for row in ranked_results
        if row.get("source") == "cooccurrence"
    ]
    chunks = [
        {
            "chunk_id": row.get("chunk_id"),
            "doc_id": row.get("doc_id"),
            "doc_name": row.get("doc_name"),
            "page": row.get("page"),
            "text": row.get("text"),
        }
        for row in ranked_results
        if row.get("chunk_id")
    ]
    return {
        "query_id": str(uuid4()),
        "query_text": query_text,
        "query_intent": query_intent,
        "case_scope": case_scope,
        "entities_resolved": entities_resolved,
        "relationships": relationships,
        "link_candidates": link_candidates,
        "exact_matches": exact_matches[:30],
        "chunks": chunks[:20],
    }
