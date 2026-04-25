from __future__ import annotations

import pytest

from operation_lens_v2.api.routes.query import suggested_pivots


@pytest.mark.asyncio
async def test_suggested_pivots_endpoint_returns_planner_payload(monkeypatch) -> None:
    async def fake_plan_query(query: str, *, planner_mode: bool = True):
        return {
            "intent": "entity_relationship_query",
            "subjects": ["Marcus Webb"],
            "filters": {"document_refs": [], "date_hints": [], "entity_types": [], "relation_types": []},
            "search_strategy": "graph_forward",
            "graph_expansion_targets": ["Marcus Webb"],
            "follow_up_questions": ["What locations recur around Marcus Webb?"],
            "suggested_pivots": ["Build a chronology for Marcus Webb with exact citations."],
            "rewrite_query": "Marcus Webb with graph links and exact citations",
            "planner_backend": "deterministic",
            "planner_status": "fallback",
            "raw_query": query,
            "parsed": {"intent": "entity_relationship_query"},
        }

    monkeypatch.setattr("operation_lens_v2.api.routes.query.planner.plan_query", fake_plan_query)

    result = await suggested_pivots("What connects Marcus Webb to the depot?")

    assert result["query"] == "What connects Marcus Webb to the depot?"
    assert result["suggested_pivots"] == ["Build a chronology for Marcus Webb with exact citations."]
    assert result["follow_up_questions"] == ["What locations recur around Marcus Webb?"]
    assert result["rewrite_query"] == "Marcus Webb with graph links and exact citations"
