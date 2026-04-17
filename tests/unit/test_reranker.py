from operation_lens_v2.query.reranker import rerank_results


def test_reranker_orders_by_weighted_score() -> None:
    ranked = rerank_results(
        [
            {"source": "vector", "score": 0.9, "chunk_id": "a"},
            {"source": "graph", "score": 0.8, "chunk_id": "b"},
        ]
    )
    assert ranked[0]["source"] == "graph"
