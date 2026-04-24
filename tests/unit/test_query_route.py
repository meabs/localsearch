from __future__ import annotations

import pytest

from operation_lens_v2.api.routes.query import case_report_endpoint
from operation_lens_v2.api.schemas import CaseReportRequest


@pytest.mark.asyncio
async def test_case_report_endpoint_dispatches_pipeline(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_case_intelligence_report(case_ref: str, *, prompt: str | None = None):
        captured["case_ref"] = case_ref
        captured["prompt"] = prompt
        return {
            "intent": "case_intelligence_report",
            "case_scope": case_ref,
            "answer": "Generated",
            "claims": [],
            "report_pack": {"case_ref": case_ref},
        }

    monkeypatch.setattr(
        "operation_lens_v2.api.routes.query.run_case_intelligence_report",
        fake_run_case_intelligence_report,
    )

    result = await case_report_endpoint(
        CaseReportRequest(case_ref="OP_REPORT", prompt="Focus on timeline and network"),
    )

    assert captured["case_ref"] == "OP_REPORT"
    assert captured["prompt"] == "Focus on timeline and network"
    assert result["intent"] == "case_intelligence_report"
    assert result["report_pack"]["case_ref"] == "OP_REPORT"
