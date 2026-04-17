from __future__ import annotations

from collections import defaultdict
from typing import Any

WEIGHTS = {
    "graph": 1.3,
    "vector": 1.0,
    "fts": 0.9,
    "exact": 1.1,
}


def rerank_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        key = (
            result.get("chunk_id")
            or result.get("rel_id")
            or result.get("entity_id")
            or f"item:{len(grouped)}"
        )
        grouped[str(key)].append(result)

    merged: list[dict[str, Any]] = []
    for _, items in grouped.items():
        item = dict(items[0])
        source = str(item.get("source", "vector"))
        base = float(item.get("score", item.get("confidence", 0.0)))
        item["rank_score"] = base * WEIGHTS.get(source, 1.0) * (1 + 0.1 * (len(items) - 1))
        item["evidence_count"] = len(items)
        merged.append(item)

    merged.sort(key=lambda x: x["rank_score"], reverse=True)
    return merged
