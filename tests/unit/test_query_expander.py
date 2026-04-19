from __future__ import annotations

import pytest

from operation_lens_v2.query.query_expander import expand_query


class _FakeResponse:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"response": self._response_text}


class _FakeClient:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.requests: list[dict[str, object]] = []

    async def post(self, path: str, json: dict[str, object]) -> _FakeResponse:
        self.requests.append({"path": path, "json": json})
        return _FakeResponse(self.response_text)


@pytest.mark.asyncio
async def test_expand_query_returns_original_first_and_strips_fences() -> None:
    client = _FakeClient('```json\n{"queries":["Webb Khalil link","shared meeting"]}\n```')

    queries = await expand_query(
        "What connects Webb to Khalil?",
        {"intent": "connection_query", "entities": ["Webb", "Khalil"]},
        client,
    )

    assert queries == [
        "What connects Webb to Khalil?",
        "Webb Khalil link",
        "shared meeting",
    ]
    assert client.requests[0]["path"] == "/api/generate"


@pytest.mark.asyncio
async def test_expand_query_gracefully_falls_back_on_bad_json() -> None:
    client = _FakeClient("not json")

    queries = await expand_query(
        "What connects Webb to Khalil?",
        {"intent": "connection_query", "entities": ["Webb", "Khalil"]},
        client,
    )

    assert queries == ["What connects Webb to Khalil?"]
