from __future__ import annotations

import pytest

from operation_lens_v2.config import settings
from operation_lens_v2.query.atomic_facts import AtomicFact, extract_atomic_facts


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
async def test_extract_atomic_facts_returns_two_facts_with_correct_provenance() -> None:
    captured_requests: list[dict[str, object]] = []
    client = _FakeClient(
        """```json
        [
          {
            "subject": "John Smith",
            "predicate": "called",
            "object": "07700 900123",
            "when": "2024-04-10T14:30:00Z",
            "where": "Depot Yard",
            "confidence": 0.99
          },
          {
            "subject": "John Smith",
            "predicate": "met",
            "object": "Alice Example",
            "when": "2024-04-10T15:00:00Z",
            "where": "Depot Yard",
            "confidence": 0.05
          }
        ]
        ```""",
        captured_requests,
    )

    chunks = [
        {
            "doc_id": "doc-17",
            "page": 4,
            "source_chunk_id": "chunk-4",
            "text": "John Smith called 07700 900123 and later met Alice Example at Depot Yard.",
        }
    ]

    facts = await extract_atomic_facts(chunks, client)

    assert facts == [
        AtomicFact(
            subject="John Smith",
            predicate="called",
            object="07700 900123",
            when="2024-04-10T14:30:00Z",
            where="Depot Yard",
            doc_id="doc-17",
            page=4,
            source_chunk_id="chunk-4",
            confidence=0.95,
        ),
        AtomicFact(
            subject="John Smith",
            predicate="met",
            object="Alice Example",
            when="2024-04-10T15:00:00Z",
            where="Depot Yard",
            doc_id="doc-17",
            page=4,
            source_chunk_id="chunk-4",
            confidence=0.1,
        ),
    ]

    assert captured_requests[0]["path"] == "/api/generate"
    request_json = captured_requests[0]["json"]
    assert request_json["model"] == settings.local_extraction_model
    assert request_json["stream"] is False
    assert "Extract atomic facts as JSON list" in request_json["prompt"]
    assert "chunk-4" in request_json["prompt"]
