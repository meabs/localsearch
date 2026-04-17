from operation_lens_v2.query.reranker import rerank_results


def test_reranker_orders_by_weighted_score() -> None:
    ranked = rerank_results(
        [
            {"source": "vector", "score": 0.9, "chunk_id": "a"},
            {"source": "graph", "score": 0.8, "chunk_id": "b"},
        ]
    )
    assert ranked[0]["source"] == "graph"


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
