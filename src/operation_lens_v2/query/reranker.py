from __future__ import annotations

from collections import defaultdict
from typing import Any

WEIGHTS = {
    "graph": 1.3,
    "vector": 1.0,
    "fts": 0.9,
    "exact": 1.1,
}
SOURCE_PRIORITY = {
    "graph": 4,
    "exact": 3,
    "vector": 2,
    "fts": 1,
}


def _weighted_score(result: dict[str, Any]) -> float:
    source = str(result.get("source", "vector"))
    base = float(result.get("score", result.get("confidence", 0.0)))
    return base * WEIGHTS.get(source, 1.0)


def _result_key(result: dict[str, Any], idx: int) -> str:
    source = str(result.get("source", "vector"))

    if source == "graph":
        rel_id = result.get("rel_id")
        if rel_id:
            doc_id = result.get("doc_id") or result.get("chunk_id") or idx
            return f"graph:{rel_id}:{doc_id}"

    if source == "exact":
        entity_id = result.get("entity_id")
        if entity_id:
            doc_id = result.get("doc_id") or result.get("chunk_id") or idx
            return f"exact:{entity_id}:{doc_id}"

    chunk_id = result.get("chunk_id")
    if chunk_id:
        return f"chunk:{chunk_id}"

    for field in ("rel_id", "entity_id"):
        value = result.get(field)
        if value:
            return f"{source}:{field}:{value}"

    return f"{source}:item:{idx}"


def rerank_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for idx, result in enumerate(results):
        key = _result_key(result, idx)
        grouped[str(key)].append(result)

    merged: list[dict[str, Any]] = []
    for _, items in grouped.items():
        representative = max(
            items,
            key=lambda item: (
                _weighted_score(item),
                SOURCE_PRIORITY.get(str(item.get("source", "")), 0),
                float(item.get("score", item.get("confidence", 0.0))),
            ),
        )
        item = dict(representative)
        item["rank_score"] = _weighted_score(representative) * (1 + 0.1 * (len(items) - 1))
        item["evidence_count"] = len(items)
        item["supporting_sources"] = sorted(
            {str(candidate.get("source", "vector")) for candidate in items}
        )
        merged.append(item)

    merged.sort(key=lambda x: x["rank_score"], reverse=True)
    return merged
