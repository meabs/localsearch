from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from operation_lens_v2.config import settings

DEFAULT_SOURCE_WEIGHTS = {
    "graph": 1.3,
    "exact": 1.1,
    "vector": 1.0,
    "fts": 0.9,
    "document": 1.0,
}
SOURCE_PRIORITY = {
    "graph": 4,
    "exact": 3,
    "vector": 2,
    "fts": 1,
    "document": 0,
}
LearningToRankHook = Callable[[dict[str, float], dict[str, Any], list[dict[str, Any]]], float]


def _source_weights(source_weights: dict[str, float] | None = None) -> dict[str, float]:
    weights = dict(DEFAULT_SOURCE_WEIGHTS)
    configured = source_weights if source_weights is not None else settings.rerank_source_weights
    weights.update({str(source): float(weight) for source, weight in configured.items()})
    return weights


def _base_score(result: dict[str, Any]) -> float:
    return float(result.get("score", result.get("confidence", 0.0)))


def _weighted_score(
    result: dict[str, Any],
    source_weights: dict[str, float] | None = None,
) -> float:
    source = str(result.get("source", "vector"))
    return _base_score(result) * _source_weights(source_weights).get(source, 1.0)


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


def _rrf_scores(
    results: list[dict[str, Any]],
    *,
    source_weights: dict[str, float],
    rrf_k: int,
) -> dict[str, float]:
    by_source: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for idx, result in enumerate(results):
        by_source[str(result.get("source", "vector"))].append((idx, result))

    scores: dict[str, float] = defaultdict(float)
    for source, source_results in by_source.items():
        weight = source_weights.get(source, 1.0)
        ranked = sorted(
            source_results,
            key=lambda pair: (
                _base_score(pair[1]),
                SOURCE_PRIORITY.get(str(pair[1].get("source", "")), 0),
            ),
            reverse=True,
        )
        for rank, (idx, result) in enumerate(ranked, start=1):
            scores[_result_key(result, idx)] += weight / (rrf_k + rank)
    return scores


def _score_features(
    *,
    representative: dict[str, Any],
    items: list[dict[str, Any]],
    rrf_score: float,
    source_weights: dict[str, float],
) -> dict[str, float]:
    source = str(representative.get("source", "vector"))
    base_scores = [_base_score(item) for item in items]
    return {
        "rrf_score": rrf_score,
        "base_score": _base_score(representative),
        "max_base_score": max(base_scores, default=0.0),
        "evidence_count": float(len(items)),
        "source_weight": source_weights.get(source, 1.0),
        "source_priority": float(SOURCE_PRIORITY.get(source, 0)),
    }


def rerank_results(
    results: list[dict[str, Any]],
    *,
    source_weights: dict[str, float] | None = None,
    rrf_k: int | None = None,
    learning_to_rank: LearningToRankHook | None = None,
) -> list[dict[str, Any]]:
    weights = _source_weights(source_weights)
    reciprocal_rank_k = max(1, int(rrf_k if rrf_k is not None else settings.rerank_rrf_k))
    rrf_scores = _rrf_scores(results, source_weights=weights, rrf_k=reciprocal_rank_k)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for idx, result in enumerate(results):
        key = _result_key(result, idx)
        grouped[str(key)].append(result)

    merged: list[dict[str, Any]] = []
    for key, items in grouped.items():
        representative = max(
            items,
            key=lambda item: (
                _weighted_score(item, weights),
                SOURCE_PRIORITY.get(str(item.get("source", "")), 0),
                _base_score(item),
            ),
        )
        rrf_score = rrf_scores.get(key, 0.0)
        features = _score_features(
            representative=representative,
            items=items,
            rrf_score=rrf_score,
            source_weights=weights,
        )
        item = dict(representative)
        item["rank_score"] = (
            float(learning_to_rank(features, representative, items))
            if learning_to_rank is not None
            else rrf_score
        )
        item["rrf_score"] = rrf_score
        item["evidence_count"] = len(items)
        item["supporting_sources"] = sorted(
            {str(candidate.get("source", "vector")) for candidate in items}
        )
        item["score_features"] = features
        merged.append(item)

    merged.sort(
        key=lambda x: (
            float(x["rank_score"]),
            SOURCE_PRIORITY.get(str(x.get("source", "")), 0),
            float(x.get("score", x.get("confidence", 0.0))),
        ),
        reverse=True,
    )
    return merged
