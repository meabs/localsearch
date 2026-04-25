from __future__ import annotations

import pytest

from operation_lens_v2.query import planner


@pytest.mark.asyncio
async def test_plan_query_returns_deterministic_plan_when_disabled() -> None:
    result = await planner.plan_query(
        "List all phone numbers linked to this case and who they are associated with.",
        planner_mode=False,
    )

    assert result["intent"] == "entity_relationship_inventory_query"
    assert result["planner_status"] == "fallback"
    assert result["filters"]["entity_types"] == ["PHONE"]
    assert result["suggested_pivots"]
    assert result["rewrite_query"]


@pytest.mark.asyncio
async def test_plan_query_falls_back_when_local_planner_fails(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("operation_lens_v2.query.planner.get_http_client", boom)

    result = await planner.plan_query("What connects Marcus Webb to the depot?")

    assert result["planner_status"] == "fallback"
    assert result["planner_backend"] == "deterministic"
    assert "Marcus Webb" in result["subjects"]
