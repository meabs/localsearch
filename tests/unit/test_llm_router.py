from __future__ import annotations

import pytest

from operation_lens_v2.config import settings
from operation_lens_v2.query.llm_router import generate_answer


class _FakeResponse:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"response": self._response_text}


class _FakeClient:
    def __init__(self, response_text: str, captured_requests: list[dict[str, object]]) -> None:
        self._response_text = response_text
        self._captured_requests = captured_requests

    async def post(self, path: str, json: dict[str, object]) -> _FakeResponse:
        self._captured_requests.append({"path": path, "json": json})
        return _FakeResponse(self._response_text)


@pytest.mark.asyncio
async def test_generate_answer_document_summary_ignores_boilerplate(monkeypatch) -> None:
    async def fake_local_document_analysis(evidence_packet, chunks):
        return None

    monkeypatch.setattr(
        "operation_lens_v2.query.llm_router._local_document_analysis",
        fake_local_document_analysis,
    )

    payload = {
        "query_intent": "document_summary_query",
        "query_text": "Summarise key findings from OC-INT-003.pdf",
        "chunks": [
            {
                "doc_id": "doc-1",
                "doc_name": "OC-INT-003.pdf",
                "page": 1,
                "text": (
                    "OPERATION CHESTER | OFFICIAL-SENSITIVE | OC-INT-003 | DAILY INTELLIGENCE REPORT. "
                    "Analyst: PS 1102 Ainsworth. Grading (source): B - usually reliable. "
                    "Anonymous CrimeStoppers report dated 03/04/2024 references a property on the "
                    "Breckfield side of the estate being used for late-night meetings."
                ),
            }
        ],
        "relationships": [],
        "exact_matches": [],
    }

    out = await generate_answer(payload)

    assert "late-night meetings" in out["answer"]
    assert "OFFICIAL-SENSITIVE" not in out["answer"]
    assert out["claims"]


@pytest.mark.asyncio
async def test_generate_answer_document_summary_prefers_local_analysis(monkeypatch) -> None:
    async def fake_local_document_analysis(evidence_packet, chunks):
        return (
            "KEY FINDINGS\n"
            "- Two late-night meetings were reported at the Breckfield property [OC-INT-003.pdf, p.1]\n"
            "CONFIDENCE POSTURE\n"
            "Excerpt-backed analytic summary.\n"
            "EVIDENCE GAPS\n"
            "Single-source document summary."
        )

    monkeypatch.setattr(
        "operation_lens_v2.query.llm_router._local_document_analysis",
        fake_local_document_analysis,
    )

    payload = {
        "query_intent": "document_summary_query",
        "query_text": "Summarise key findings from OC-INT-003.pdf",
        "chunks": [
            {
                "doc_id": "doc-1",
                "doc_name": "OC-INT-003.pdf",
                "page": 1,
                "text": "Some source text.",
            }
        ],
        "relationships": [],
        "exact_matches": [],
    }

    out = await generate_answer(payload)

    assert "Two late-night meetings" in out["answer"]
    assert out["claims"] == []


@pytest.mark.asyncio
async def test_generate_answer_freeform_uses_local_llm_and_includes_exact_matches(
    monkeypatch,
) -> None:
    captured_requests: list[dict[str, object]] = []

    def fake_get_http_client(*args, **kwargs):
        return _FakeClient(
            "Assessment: the evidence supports a shared contact pattern.",
            captured_requests,
        )

    monkeypatch.setattr("operation_lens_v2.query.llm_router.get_http_client", fake_get_http_client)

    payload = {
        "query_intent": "freeform_query",
        "query_text": "What do we know about this set of documents?",
        "chunks": [
            {
                "doc_id": "doc-1",
                "doc_name": "report-1.pdf",
                "page": 1,
                "text": "Witness notes reference a shared phone number and a vehicle.",
            }
        ],
        "relationships": [],
        "exact_matches": [
            {
                "canonical_name": "John Doe",
                "entity_type": "PERSON",
                "citations": [{"doc_id": "report-1.pdf", "page": 1}],
            }
        ],
    }

    out = await generate_answer(payload)

    assert out["backend"] == settings.local_reasoning_model
    assert out["answer"] == "Assessment: the evidence supports a shared contact pattern."
    assert out["claims"] == []
    assert captured_requests[0]["path"] == "/api/generate"

    request_json = captured_requests[0]["json"]
    assert request_json["model"] == settings.local_reasoning_model
    prompt = request_json["prompt"]
    assert "known_entities" in prompt
    assert "John Doe" in prompt
    assert "PERSON" in prompt


@pytest.mark.asyncio
async def test_generate_answer_freeform_falls_back_to_chunk_bullets_when_llm_returns_none(
    monkeypatch,
) -> None:
    class _NoneClient:
        async def post(self, path: str, json: dict[str, object]) -> _FakeResponse:
            return _FakeResponse("")

    monkeypatch.setattr(
        "operation_lens_v2.query.llm_router.get_http_client",
        lambda *args, **kwargs: _NoneClient(),
    )

    payload = {
        "query_intent": "freeform_query",
        "query_text": "Summarise the freeform evidence.",
        "chunks": [
            {
                "doc_id": "doc-1",
                "doc_name": "notes.pdf",
                "page": 2,
                "text": "The suspect mentioned a meeting at the depot near the estate.",
            }
        ],
        "relationships": [],
        "exact_matches": [],
    }

    out = await generate_answer(payload)

    assert out["backend"] == "deterministic-fallback"
    assert "KEY FINDINGS" in out["answer"]
    assert "meeting at the depot" in out["answer"]
    assert out["claims"] == []


@pytest.mark.asyncio
async def test_generate_answer_freeform_falls_back_to_entity_template_when_llm_errors(
    monkeypatch,
) -> None:
    class _ErrorClient:
        async def post(self, path: str, json: dict[str, object]) -> _FakeResponse:
            raise RuntimeError("ollama unavailable")

    monkeypatch.setattr(
        "operation_lens_v2.query.llm_router.get_http_client",
        lambda *args, **kwargs: _ErrorClient(),
    )

    payload = {
        "query_intent": "freeform_query",
        "query_text": "Summarise the freeform evidence.",
        "chunks": [
            {
                "doc_id": "doc-1",
                "doc_name": "notes.pdf",
                "page": 2,
                "text": "The suspect mentioned a meeting at the depot near the estate.",
            }
        ],
        "relationships": [],
        "exact_matches": [
            {
                "entity_id": "person-1",
                "canonical_name": "Alice Example",
                "entity_type": "PERSON",
                "citations": [{"doc_id": "notes.pdf", "page": 2}],
            },
            {
                "entity_id": "location-1",
                "canonical_name": "Depot Yard",
                "entity_type": "LOCATION",
                "citations": [{"doc_id": "notes.pdf", "page": 2}],
            },
        ],
    }

    out = await generate_answer(payload)

    assert out["backend"] == "deterministic-fallback"
    assert "PEOPLE" in out["answer"]
    assert "Alice Example" in out["answer"]
    assert "PLACES" in out["answer"]
    assert "Depot Yard" in out["answer"]
    assert out["claims"]
