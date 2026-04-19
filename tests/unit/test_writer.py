"""Tests for the briefing writer.

Covers the deterministic fallback renderer and compose_briefing's happy/error paths
with a stubbed Ollama /api/generate endpoint.
"""

from __future__ import annotations

import httpx
import pytest

from operation_lens_v2.query.tool_schemas import (
    AtomicFact,
    Citation,
    DatedEvent,
    GraphPath,
    InvestigationReport,
    RelationshipEdge,
)
from operation_lens_v2.query.writer import (
    _spans_multiple_docs,
    compose_briefing,
    deterministic_briefing,
)


def _fact(doc_id: str = "doc-1", page: int = 1) -> AtomicFact:
    return AtomicFact(
        subject="Marcus Webb",
        predicate="was interviewed in",
        object="NF-INT-001",
        confidence=0.9,
        citation=Citation(doc_id=doc_id, doc_name=f"{doc_id}.pdf", page=page),
    )


def _report(**overrides) -> InvestigationReport:
    base = {
        "hypothesis": "Marcus Webb is the subject of NF-INT-001.",
        "key_facts": [_fact()],
        "confidence": "HIGH",
        "gaps": [],
        "next_actions": ["Cross-reference Webb's associates."],
    }
    base.update(overrides)
    return InvestigationReport(**base)


def test_deterministic_briefing_includes_all_required_sections():
    text = deterministic_briefing(_report())
    assert "ASSESSMENT" in text
    assert "KEY FINDINGS" in text
    assert "CONFIDENCE POSTURE" in text
    assert "EVIDENCE GAPS" in text
    assert "SUGGESTED NEXT ACTIONS" in text


def test_deterministic_briefing_cites_every_fact():
    text = deterministic_briefing(_report())
    assert "[doc-1, p.1]" in text
    assert "Marcus Webb was interviewed in NF-INT-001" in text


def test_deterministic_briefing_empty_report_reports_insufficiency():
    report = InvestigationReport(
        hypothesis="",
        key_facts=[],
        confidence="LOW",
    )
    text = deterministic_briefing(report)
    assert "Insufficient evidence" in text
    assert "No supported findings." in text
    assert "None reported." in text


def test_deterministic_briefing_shows_cross_document_links_when_multi_doc():
    path = GraphPath(
        source_entity_id="e-webb",
        target_entity_id="e-khalil",
        hops=2,
        edges=[
            RelationshipEdge(
                rel_id="r1",
                source_entity_id="e-webb",
                source_name="Marcus Webb",
                source_type="PERSON",
                target_entity_id="e-rx71",
                target_name="RX71 KLD",
                target_type="VEHICLE",
                relation_type="OWNS",
                confidence=0.9,
                doc_id="doc-1",
                page=1,
            ),
            RelationshipEdge(
                rel_id="r2",
                source_entity_id="e-rx71",
                source_name="RX71 KLD",
                source_type="VEHICLE",
                target_entity_id="e-khalil",
                target_name="Rania Khalil",
                target_type="PERSON",
                relation_type="DRIVEN_BY",
                confidence=0.85,
                doc_id="doc-2",
                page=3,
            ),
        ],
        citations=[
            Citation(doc_id="doc-1", doc_name="a.pdf", page=1),
            Citation(doc_id="doc-2", doc_name="b.pdf", page=3),
        ],
    )
    report = _report(
        key_facts=[_fact(doc_id="doc-1"), _fact(doc_id="doc-2", page=3)],
        paths=[path],
    )
    text = deterministic_briefing(report)
    assert "CROSS-DOCUMENT LINKS" in text
    assert "Marcus Webb OWNS RX71 KLD" in text
    assert "RX71 KLD DRIVEN_BY Rania Khalil" in text


def test_deterministic_briefing_omits_cross_document_section_for_single_doc():
    text = deterministic_briefing(_report())
    assert "CROSS-DOCUMENT LINKS" not in text


def test_deterministic_briefing_renders_timeline():
    event = DatedEvent(
        event_time="2024-05-01",
        text="Webb observed at 14 Arkwright Road 21:40.",
        doc_id="doc-3",
        doc_name="surv.pdf",
        page=2,
    )
    report = _report(timeline=[event])
    text = deterministic_briefing(report)
    assert "TIMELINE" in text
    assert "2024-05-01" in text
    assert "[doc-3, p.2]" in text


def test_spans_multiple_docs_detects_mixed_docs():
    report = _report(key_facts=[_fact(doc_id="doc-1"), _fact(doc_id="doc-2")])
    assert _spans_multiple_docs(report) is True


def test_spans_multiple_docs_single_doc_returns_false():
    assert _spans_multiple_docs(_report()) is False


class _StubResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


class _StubClient:
    def __init__(self, response: _StubResponse | Exception):
        self._response = response
        self.calls: list[dict] = []
        self.closed = False

    async def post(self, path: str, json: dict) -> _StubResponse:
        self.calls.append({"path": path, "json": json})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_compose_briefing_returns_model_text_on_success():
    client = _StubClient(_StubResponse({"response": "  MODEL BRIEFING TEXT  "}))
    text = await compose_briefing(_report(), "Who is Webb?", client=client)  # type: ignore[arg-type]
    assert text == "MODEL BRIEFING TEXT"
    assert client.calls[0]["path"] == "/api/generate"
    assert client.calls[0]["json"]["stream"] is False
    assert "Who is Webb?" in client.calls[0]["json"]["prompt"]


@pytest.mark.asyncio
async def test_compose_briefing_falls_back_on_empty_response():
    client = _StubClient(_StubResponse({"response": "   "}))
    text = await compose_briefing(_report(), "Who is Webb?", client=client)  # type: ignore[arg-type]
    assert "ASSESSMENT" in text
    assert "Marcus Webb" in text


@pytest.mark.asyncio
async def test_compose_briefing_falls_back_on_network_error():
    client = _StubClient(httpx.ConnectError("ollama down"))
    text = await compose_briefing(_report(), "Who is Webb?", client=client)  # type: ignore[arg-type]
    assert "ASSESSMENT" in text
    assert "KEY FINDINGS" in text
