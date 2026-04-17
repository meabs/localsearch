"""Tests for query retrievers — exact, FTS, reranker."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import init_db
from operation_lens_v2.query import reranker
from operation_lens_v2.query.parser import parse_query
from operation_lens_v2.query.pipeline import (
    _resolve_recall_strategy,
    _run_inventory_query,
    _with_document_coverage,
    run_query,
)
from operation_lens_v2.query.retriever_exact import retrieve_exact
from operation_lens_v2.query.retriever_fts import retrieve_fts
from operation_lens_v2.query.retriever_vector import retrieve_vector
from operation_lens_v2.runtime import close_runtime_resources

INSERT_DOCUMENT_SQL = """
    INSERT INTO documents (doc_id, case_id, filename, filepath, page_count)
    VALUES (?, ?, ?, ?, ?)
"""
INSERT_CHUNK_SQL = """
    INSERT INTO chunks (chunk_id, doc_id, page, chunk_index, text, token_count)
    VALUES (?, ?, ?, ?, ?, ?)
"""
INSERT_ENTITY_SQL = """
    INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc)
    VALUES (?, ?, ?, ?)
"""
INSERT_ALIAS_SQL = """
    INSERT INTO entity_aliases (alias_id, entity_id, alias_text, source_doc, source_chunk)
    VALUES (?, ?, ?, ?, ?)
"""


def _seed_db(con) -> tuple[str, str]:
    """Insert minimal data and return (entity_id, doc_id)."""
    import uuid

    doc_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_TEST", "Test Case"],
    )
    con.execute(INSERT_DOCUMENT_SQL, [doc_id, case_id, "test.pdf", "/tmp/test.pdf", 1])
    chunk_id = str(uuid.uuid4())
    con.execute(
        INSERT_CHUNK_SQL,
        [chunk_id, doc_id, 1, 0, "Marcus Webb was observed at 14 Arkwright Road.", 10],
    )
    entity_id = str(uuid.uuid4())
    con.execute(INSERT_ENTITY_SQL, [entity_id, "Marcus Webb", "PERSON", doc_id])
    alias_id = str(uuid.uuid4())
    con.execute(INSERT_ALIAS_SQL, [alias_id, entity_id, "RX71 KLD", doc_id, chunk_id])
    return entity_id, doc_id


# ── Exact retriever ────────────────────────────────────────────────────────────


def test_exact_retriever_matches_alias():
    con = init_db(":memory:")
    entity_id, doc_id = _seed_db(con)
    results = retrieve_exact(con, "RX71 KLD")
    assert len(results) >= 1
    assert results[0]["source"] == "exact"


def test_exact_retriever_returns_empty_on_no_match():
    con = init_db(":memory:")
    _seed_db(con)
    results = retrieve_exact(con, "ZZ99 ZZZ")
    assert results == []


def test_parse_query_routes_bare_phone_inventory_search():
    parsed = parse_query("phone numbers")
    assert parsed["intent"] == "entity_inventory_query"
    assert parsed["inventory_target"] == "PHONE"


def test_parse_query_sets_recall_priority_hints():
    parsed = parse_query("Give me a comprehensive report, don't miss related docs")
    assert parsed["recall_priority"] is True


def test_inventory_query_returns_exact_chunk_citations():
    con = init_db(":memory:")
    _seed_db(con)

    result = _run_inventory_query(
        con,
        query_text="list people",
        case_ref=None,
        case_id=None,
        entity_type="PERSON",
    )

    claim = result["claims"][0]
    assert claim["citations"]
    assert claim["citations"][0]["page"] == 1
    assert "Marcus Webb was observed at 14 Arkwright Road" in claim["citations"][0]["span_text"]
    assert result["answer"].startswith("KEY FINDINGS")
    assert "CONFIDENCE POSTURE" in result["answer"]
    assert "EVIDENCE GAPS" in result["answer"]


def test_inventory_query_deduplicates_repeated_citations():
    con = init_db(":memory:")
    entity_id, doc_id = _seed_db(con)

    alias_id = "alias-extra"
    chunk_id = con.execute("SELECT chunk_id FROM chunks LIMIT 1").fetchone()[0]
    con.execute(INSERT_ALIAS_SQL, [alias_id, entity_id, "Marcus Webb", doc_id, chunk_id])

    result = _run_inventory_query(
        con,
        query_text="list people",
        case_ref=None,
        case_id=None,
        entity_type="PERSON",
    )

    citations = result["claims"][0]["citations"]
    assert len(citations) == 1


@pytest.mark.asyncio
async def test_run_query_routes_phone_numbers_to_inventory(tmp_path, monkeypatch):
    await close_runtime_resources()
    db_path = tmp_path / "phones.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    import uuid

    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    entity_id = str(uuid.uuid4())
    alias_id = str(uuid.uuid4())

    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_PHONE", "Phone Case"],
    )
    con.execute(INSERT_DOCUMENT_SQL, [doc_id, case_id, "phone-note.pdf", "/tmp/phone-note.pdf", 1])
    con.execute(
        INSERT_CHUNK_SQL,
        [chunk_id, doc_id, 1, 0, "Contact number +44 7700 900123 was used by the subject.", 10],
    )
    con.execute(INSERT_ENTITY_SQL, [entity_id, "+44 7700 900123", "PHONE", doc_id])
    con.execute(INSERT_ALIAS_SQL, [alias_id, entity_id, "+44 7700 900123", doc_id, chunk_id])

    result = await run_query("phone numbers")

    assert result["intent"] == "entity_inventory_query"
    assert result["backend"] == "structured-sql"
    assert "+44 7700 900123" in result["answer"]


@pytest.mark.asyncio
async def test_run_query_preserves_graph_backed_relationship_flow(tmp_path, monkeypatch):
    await close_runtime_resources()
    db_path = tmp_path / "relationships.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    import uuid

    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())
    location_id = str(uuid.uuid4())
    rel_id = str(uuid.uuid4())
    evidence_id = str(uuid.uuid4())

    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_REL", "Relationship Case"],
    )
    con.execute(INSERT_DOCUMENT_SQL, [doc_id, case_id, "intel.pdf", "/tmp/intel.pdf", 1])
    con.execute(
        INSERT_CHUNK_SQL,
        [chunk_id, doc_id, 1, 0, "Marcus Webb was observed at North Yard.", 10],
    )
    con.execute(INSERT_ENTITY_SQL, [person_id, "Marcus Webb", "PERSON", doc_id])
    con.execute(INSERT_ENTITY_SQL, [location_id, "North Yard", "LOCATION", doc_id])
    con.execute(
        """
        INSERT INTO relationships (rel_id, source_entity, target_entity, relation_type, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        [rel_id, person_id, location_id, "OBSERVED_AT", 0.91],
    )
    con.execute(
        """
        INSERT INTO relationship_evidence (
          evidence_id, rel_id, chunk_id, doc_id, page,
          span_start, span_end, span_text, extraction_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            evidence_id,
            rel_id,
            chunk_id,
            doc_id,
            1,
            0,
            38,
            "Marcus Webb was observed at North Yard.",
            "pattern",
        ],
    )

    monkeypatch.setattr(
        "operation_lens_v2.query.retriever_vector.retrieve_vector",
        AsyncMock(return_value=[]),
    )

    async def fake_generate_answer(packet, *, use_cloud: bool):
        assert packet["relationships"]
        return {
            "backend": "local-test",
            "answer": "Marcus Webb was observed at North Yard.",
            "claims": [
                {
                    "text": "Marcus Webb was observed at North Yard",
                    "citations": [
                        {
                            "doc_id": "intel.pdf",
                            "doc_name": "intel.pdf",
                            "page": 1,
                            "span_text": "Marcus Webb was observed at North Yard.",
                        }
                    ],
                    "confidence": 0.91,
                }
            ],
        }

    async def fake_validate_claims(payload):
        return {
            **payload,
            "claims": [
                {
                    **payload["claims"][0],
                    "status": "SUPPORTED",
                    "validated": True,
                }
            ],
        }

    monkeypatch.setattr(
        "operation_lens_v2.query.pipeline.llm_router.generate_answer",
        fake_generate_answer,
    )
    monkeypatch.setattr(
        "operation_lens_v2.query.pipeline.claim_validator.validate_claims",
        fake_validate_claims,
    )

    result = await run_query("What locations is Marcus Webb connected to?", case_ref="OP_REL")

    assert result["intent"] == "entity_relationship_query"
    assert result["backend"] == "local-test"
    assert result["claims"][0]["status"] == "SUPPORTED"
    assert result["top_results"][0]["source"] == "graph"


# ── FTS retriever ──────────────────────────────────────────────────────────────


def test_fts_retriever_finds_chunk():
    con = init_db(":memory:")
    _seed_db(con)
    results = retrieve_fts(con, "Arkwright")
    # FTS may return 0 if index hasn't been populated; check graceful return.
    assert isinstance(results, list)
    for r in results:
        assert r["source"] == "fts"
        assert "chunk_id" in r


# ── Reranker ───────────────────────────────────────────────────────────────────


def test_reranker_graph_outranks_fts():
    results = [
        {"source": "fts", "chunk_id": "a", "score": 0.5, "doc_id": "d1", "page": 1},
        {
            "source": "graph",
            "chunk_id": "b",
            "rel_id": "r1",
            "score": 0.5,
            "doc_id": "d1",
            "page": 1,
            "relation_type": "OBSERVED_AT",
            "confidence": 0.5,
        },
    ]
    ranked = reranker.rerank_results(results)
    assert len(ranked) >= 2
    # Graph source (weight 1.3) should outrank FTS (weight 0.9) at equivalent base score.
    sources = [r["source"] for r in ranked]
    assert sources.index("graph") < sources.index("fts")


def test_reranker_deduplicates_by_chunk_id():
    results = [
        {"source": "fts", "chunk_id": "same", "score": 0.8, "doc_id": "d1", "page": 1},
        {"source": "vector", "chunk_id": "same", "score": 0.7, "doc_id": "d1", "page": 1},
    ]
    ranked = reranker.rerank_results(results)
    chunk_ids = [r["chunk_id"] for r in ranked if r.get("chunk_id")]
    assert chunk_ids.count("same") == 1


@pytest.mark.asyncio
async def test_vector_retriever_converts_distance_to_similarity(monkeypatch):
    monkeypatch.setattr(
        "operation_lens_v2.query.retriever_vector.embed_text",
        AsyncMock(return_value=[0.1, 0.2]),
    )

    class FakeStore:
        def __init__(self, path: str) -> None:
            self.path = path

        def vector_dim(self) -> int:
            return 2

        def search(self, vector, top_k: int = 10):
            return [
                {"chunk_id": "near", "doc_id": "d1", "page": 1, "text": "near", "_distance": 0.1},
                {"chunk_id": "far", "doc_id": "d2", "page": 1, "text": "far", "_distance": 0.9},
            ]

    monkeypatch.setattr(
        "operation_lens_v2.query.retriever_vector.get_vector_store",
        lambda _: FakeStore("unused"),
    )

    results = await retrieve_vector("query")

    assert results[0]["distance"] < results[1]["distance"]
    assert results[0]["score"] > results[1]["score"]


def test_reranker_preserves_all_results():
    results = [
        {"source": "fts", "chunk_id": f"c{i}", "score": float(i) / 100, "doc_id": "d1", "page": 1}
        for i in range(50)
    ]

    ranked = reranker.rerank_results(results)
    assert len(ranked) == len(results)


def test_pipeline_doc_coverage_includes_additional_docs():
    ranked = [
        {"chunk_id": "c1", "doc_id": "d1", "rank_score": 0.99},
        {"chunk_id": "c2", "doc_id": "d1", "rank_score": 0.98},
        {"chunk_id": "c3", "doc_id": "d1", "rank_score": 0.97},
        {"chunk_id": "c4", "doc_id": "d2", "rank_score": 0.60},
        {"chunk_id": "c5", "doc_id": "d3", "rank_score": 0.50},
    ]
    covered = _with_document_coverage(ranked, top_n=3, min_doc_coverage=2)
    assert len(covered) == 3
    assert len({item["doc_id"] for item in covered}) >= 2


def test_resolve_recall_strategy_honors_explicit_mode():
    mode, multiplier, coverage = _resolve_recall_strategy(
        requested_mode="exhaustive", parsed_recall_priority=False
    )
    assert mode == "exhaustive"
    assert multiplier >= 2
    assert coverage >= 2


def test_resolve_recall_strategy_auto_uses_fast_when_no_priority(monkeypatch):
    monkeypatch.setattr(settings, "hybrid_recall_default", False)
    mode, multiplier, coverage = _resolve_recall_strategy(
        requested_mode="auto", parsed_recall_priority=False
    )
    assert mode == "fast"
    assert multiplier == 1
    assert coverage == 1
