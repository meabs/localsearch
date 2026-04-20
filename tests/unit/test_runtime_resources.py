from __future__ import annotations

import pytest

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store
from operation_lens_v2.ingestion.embedder import embed_text
from operation_lens_v2.query.claim_validator import _extract_claims_from_answer
from operation_lens_v2.query.llm_router import _local_ollama_analysis
from operation_lens_v2.query.retriever_vector import retrieve_vector
from operation_lens_v2.runtime import close_runtime_resources, get_duck_connection, reset_duck_connection


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_connect_always_loads_fts(monkeypatch) -> None:
    """Each connect issues INSTALL (idempotent in DuckDB) and LOAD.

    The old implementation used a process-wide `_fts_extension_ready` flag to
    skip INSTALL after the first connect; that global was removed because
    INSTALL is already idempotent at the DuckDB level and the flag introduced
    a race across threads/processes sharing the db file.
    """
    executed_sql: list[str] = []

    class FakeConnection:
        def execute(self, sql: str):
            executed_sql.append(sql)
            return self

        def close(self) -> None:
            return None

    monkeypatch.setattr(duck_store.duckdb, "connect", lambda _: FakeConnection())

    duck_store.connect("data/test-one.duckdb")
    duck_store.connect("data/test-two.duckdb")

    assert executed_sql.count("INSTALL fts;") == 2
    assert executed_sql.count("LOAD fts;") == 2


@pytest.mark.asyncio
async def test_retrieve_vector_reuses_vector_store(monkeypatch, tmp_path) -> None:
    await close_runtime_resources()
    monkeypatch.setattr(settings, "lancedb_path", str(tmp_path / "lancedb"))

    connect_calls = 0

    class FakeSearch:
        def __init__(self, rows):
            self.rows = rows

        def limit(self, top_k: int):
            return self

        def to_list(self):
            return self.rows

    class FakeTable:
        def head(self, _: int):
            return self

        def to_pylist(self):
            return [{"vector": [0.1, 0.2]}]

        def search(self, vector):
            return FakeSearch(
                [{"chunk_id": "c1", "doc_id": "d1", "page": 1, "text": "x", "_distance": 0.2}]
            )

    class FakeDb:
        def table_names(self):
            return ["chunks"]

        def open_table(self, name: str):
            assert name == "chunks"
            return FakeTable()

    def fake_connect(_: str):
        nonlocal connect_calls
        connect_calls += 1
        return FakeDb()

    async def fake_embed(_: str) -> list[float]:
        return [0.1, 0.2]

    monkeypatch.setattr("operation_lens_v2.ingestion.vector_store.lancedb.connect", fake_connect)
    monkeypatch.setattr("operation_lens_v2.query.retriever_vector.embed_text", fake_embed)

    first = await retrieve_vector("first query")
    second = await retrieve_vector("second query")

    assert first
    assert second
    assert connect_calls == 1


@pytest.mark.asyncio
async def test_embed_and_llm_calls_reuse_same_http_client(monkeypatch) -> None:
    await close_runtime_resources()

    created_clients: list[object] = []

    class FakeClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            self.base_url = base_url
            self.timeout = timeout
            self.is_closed = False
            created_clients.append(self)

        async def post(self, path: str, json: dict, headers: dict | None = None):
            if path == "/api/embeddings":
                return _FakeResponse({"embedding": [0.1, 0.2]})
            prompt = str(json.get("prompt", ""))
            if "JSON array of claims" in prompt:
                return _FakeResponse({"response": '["Claim one"]'})
            return _FakeResponse({"response": "analysis"})

        async def aclose(self) -> None:
            self.is_closed = True

    monkeypatch.setattr("operation_lens_v2.runtime.httpx.AsyncClient", FakeClient)

    await embed_text("hello")
    await _extract_claims_from_answer("Claim one.")
    await _local_ollama_analysis(
        {"query_text": "q", "case_scope": "ALL_CASES"},
        [
            {
                "source": "Alice",
                "target": "Warehouse",
                "relation_type": "OBSERVED_AT",
                "confidence": 0.9,
                "doc_count": 1,
                "evidence_count": 1,
                "citations": [{"doc_id": "DOC-1", "page": 1, "span_text": "Alice at Warehouse"}],
            }
        ],
    )

    assert len(created_clients) == 1

    await close_runtime_resources()


def test_reset_duck_connection_drops_cached_connection(monkeypatch, tmp_path) -> None:
    closed: list[str] = []

    class FakeConnection:
        def execute(self, sql: str):
            return self

        def close(self) -> None:
            closed.append("closed")

    monkeypatch.setattr(duck_store.duckdb, "connect", lambda _: FakeConnection())

    path = str(tmp_path / "runtime-reset.duckdb")
    first = get_duck_connection(path)
    second = get_duck_connection(path)

    assert first is second

    reset_duck_connection(path)
    third = get_duck_connection(path)

    assert closed == ["closed"]
    assert third is not first
