from operation_lens_v2.query.reranker import rerank_results


def test_reranker_orders_by_weighted_score() -> None:
    ranked = rerank_results(
        [
            {"source": "vector", "score": 0.9, "chunk_id": "a"},
            {"source": "graph", "score": 0.8, "chunk_id": "b"},
        ]
    )
    assert ranked[0]["source"] == "graph"


def test_reranker_uses_reciprocal_rank_fusion_consensus() -> None:
    ranked = rerank_results(
        [
            {"source": "fts", "score": 1.0, "chunk_id": "single"},
            {"source": "fts", "score": 0.9, "chunk_id": "shared"},
            {"source": "vector", "score": 0.9, "chunk_id": "shared"},
        ],
        source_weights={"fts": 1.0, "vector": 1.0},
        rrf_k=60,
    )

    assert ranked[0]["chunk_id"] == "shared"
    assert ranked[0]["supporting_sources"] == ["fts", "vector"]
    assert ranked[0]["rrf_score"] > ranked[1]["rrf_score"]


def test_reranker_accepts_source_weight_overrides() -> None:
    ranked = rerank_results(
        [
            {"source": "vector", "score": 1.0, "chunk_id": "vector"},
            {"source": "graph", "score": 1.0, "chunk_id": "graph"},
        ],
        source_weights={"graph": 0.1, "vector": 2.0},
    )

    assert ranked[0]["source"] == "vector"


def test_reranker_exposes_learning_to_rank_hook() -> None:
    def prefer_low_base_score(features, representative, items):
        return 1.0 - features["base_score"]

    ranked = rerank_results(
        [
            {"source": "fts", "score": 0.9, "chunk_id": "high"},
            {"source": "fts", "score": 0.2, "chunk_id": "low"},
        ],
        learning_to_rank=prefer_low_base_score,
    )

    assert ranked[0]["chunk_id"] == "low"
    assert "score_features" in ranked[0]


def test_reranker_keeps_graph_relationship_when_chunk_matches_other_sources() -> None:
    ranked = rerank_results(
        [
            {"source": "fts", "score": 0.95, "chunk_id": "shared", "doc_id": "d1"},
            {
                "source": "graph",
                "score": 0.75,
                "rel_id": "rel-1",
                "chunk_id": "shared",
                "doc_id": "d1",
                "relation_type": "OBSERVED_AT",
            },
        ]
    )

    assert {item["source"] for item in ranked} == {"fts", "graph"}
    assert any(item.get("rel_id") == "rel-1" for item in ranked)
