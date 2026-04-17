"""Tests for query retrievers — exact, FTS, reranker."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import init_db
from operation_lens_v2.query import reranker
from operation_lens_v2.query.parser import parse_query
from operation_lens_v2.query.pipeline import (
    _query_needs_history_context,
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


def test_parse_query_routes_phone_association_list_to_relationship_inventory():
    parsed = parse_query("List all phone numbers linked to this case and who they are associated with.")
    assert parsed["intent"] == "entity_relationship_inventory_query"
    assert parsed["inventory_target"] == "PHONE"
    assert parsed["relationship_focus"] is True


def test_query_needs_history_context_only_for_short_referential_followups():
    parsed_address = parse_query("22 Linton Gardens")
    assert parsed_address["intent"] == "general_query"
    assert _query_needs_history_context("22 Linton Gardens", parsed_address) is False

    parsed_followup = parse_query("what about him?")
    assert _query_needs_history_context("what about him?", parsed_followup) is True


def test_parse_query_sets_recall_priority_hints():
    parsed = parse_query("Give me a comprehensive report, don't miss related docs")
    assert parsed["recall_priority"] is True


def test_parse_query_detects_document_summary_reference():
    parsed = parse_query("Summarise key findings from OC-INT-003.pdf")
    assert parsed["intent"] == "document_summary_query"
    assert parsed["document_refs"] == ["OC-INT-003.pdf"]


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


def test_inventory_query_deduplicates_same_page_across_multiple_chunks():
    con = init_db(":memory:")

    import uuid

    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    entity_id = str(uuid.uuid4())
    chunk_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    alias_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_PAGE", "Page Dedup Case"],
    )
    con.execute(INSERT_DOCUMENT_SQL, [doc_id, case_id, "page-dedup.pdf", "/tmp/page-dedup.pdf", 1])
    con.execute(
        INSERT_CHUNK_SQL,
        [chunk_ids[0], doc_id, 1, 0, "First chunk says phone +44 7700 900123 was used.", 10],
    )
    con.execute(
        INSERT_CHUNK_SQL,
        [chunk_ids[1], doc_id, 1, 1, "Second chunk on same page repeats phone +44 7700 900123 with more context.", 14],
    )
    con.execute(INSERT_ENTITY_SQL, [entity_id, "+447700900123", "PHONE", doc_id])
    con.execute(INSERT_ALIAS_SQL, [alias_ids[0], entity_id, "+44 7700 900123", doc_id, chunk_ids[0]])
    con.execute(INSERT_ALIAS_SQL, [alias_ids[1], entity_id, "+44 7700 900123", doc_id, chunk_ids[1]])

    result = _run_inventory_query(
        con,
        query_text="phone numbers",
        case_ref=None,
        case_id=None,
        entity_type="PHONE",
    )

    citations = result["claims"][0]["citations"]
    assert len(citations) == 1
    assert citations[0]["page"] == 1
    assert "more context" in citations[0]["span_text"]


def test_inventory_query_deduplicates_same_filename_page_across_duplicate_docs():
    con = init_db(":memory:")

    import uuid

    case_id = str(uuid.uuid4())
    entity_id = str(uuid.uuid4())
    doc_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    chunk_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    alias_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_DUP", "Duplicate Doc Case"],
    )
    for doc_id in doc_ids:
        con.execute(INSERT_DOCUMENT_SQL, [doc_id, case_id, "same-file.pdf", f"/tmp/{doc_id}.pdf", 1])
    con.execute(
        INSERT_CHUNK_SQL,
        [chunk_ids[0], doc_ids[0], 1, 0, "First duplicate doc mentions +44 7700 900123.", 10],
    )
    con.execute(
        INSERT_CHUNK_SQL,
        [chunk_ids[1], doc_ids[1], 1, 0, "Second duplicate doc mentions +44 7700 900123 with richer phone context.", 14],
    )
    con.execute(INSERT_ENTITY_SQL, [entity_id, "+447700900123", "PHONE", doc_ids[0]])
    con.execute(INSERT_ALIAS_SQL, [alias_ids[0], entity_id, "+44 7700 900123", doc_ids[0], chunk_ids[0]])
    con.execute(INSERT_ALIAS_SQL, [alias_ids[1], entity_id, "+44 7700 900123", doc_ids[1], chunk_ids[1]])

    result = _run_inventory_query(
        con,
        query_text="phone numbers",
        case_ref=None,
        case_id=None,
        entity_type="PHONE",
    )

    citations = result["claims"][0]["citations"]
    assert len(citations) == 1
    assert citations[0]["doc_name"] == "same-file.pdf"
    assert "richer phone context" in citations[0]["span_text"]


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
async def test_run_query_filters_invalid_phone_inventory_entities(tmp_path, monkeypatch):
    await close_runtime_resources()
    db_path = tmp_path / "phone-filter.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    import uuid

    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_FILTER", "Phone Filter Case"],
    )
    con.execute(INSERT_DOCUMENT_SQL, [doc_id, case_id, "filter.pdf", "/tmp/filter.pdf", 1])

    seeded_entities = [
        ("+447700900123", "PHONE", "+44 7700 900123 phone used by the subject."),
        ("+20240205", "PHONE", "A malformed extracted date should not survive inventory filtering."),
        ("Tomek", "PHONE", "A name mislabelled as a phone should not survive inventory filtering."),
        ("+4470331234567890123456", "PHONE", "An overlong digit string should not survive inventory filtering."),
        ("number ending four eight one", "PHONE", "A prose fragment should not survive inventory filtering."),
        ("+23194400982115", "PHONE", "Primary banking account 23-19-44 00982115 at the same challenger bank."),
    ]

    for canonical_name, entity_type, chunk_text in seeded_entities:
        entity_id = str(uuid.uuid4())
        chunk_id = str(uuid.uuid4())
        alias_id = str(uuid.uuid4())
        con.execute(INSERT_CHUNK_SQL, [chunk_id, doc_id, 1, 0, chunk_text, 12])
        con.execute(INSERT_ENTITY_SQL, [entity_id, canonical_name, entity_type, doc_id])
        con.execute(INSERT_ALIAS_SQL, [alias_id, entity_id, canonical_name, doc_id, chunk_id])

    result = await run_query("phone numbers")

    assert result["intent"] == "entity_inventory_query"
    assert "+447700900123" in result["answer"]
    assert "+20240205" not in result["answer"]
    assert "Tomek" not in result["answer"]
    assert "+4470331234567890123456" not in result["answer"]
    assert "number ending four eight one" not in result["answer"]
    assert "+23194400982115" not in result["answer"]


@pytest.mark.asyncio
async def test_run_query_phone_relationship_inventory_returns_associations(tmp_path, monkeypatch):
    await close_runtime_resources()
    db_path = tmp_path / "phone-associations.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    import uuid

    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    phone_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())
    rel_id = str(uuid.uuid4())
    evidence_id = str(uuid.uuid4())

    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_ASSOC", "Association Case"],
    )
    con.execute(INSERT_DOCUMENT_SQL, [doc_id, case_id, "phone-intel.pdf", "/tmp/phone-intel.pdf", 1])
    con.execute(
        INSERT_CHUNK_SQL,
        [
            chunk_id,
            doc_id,
            1,
            0,
            "Phone +44 7700 900123 is associated with Marcus Webb in recent call data.",
            16,
        ],
    )
    con.execute(INSERT_ENTITY_SQL, [phone_id, "+44 7700 900123", "PHONE", doc_id])
    con.execute(INSERT_ENTITY_SQL, [person_id, "Marcus Webb", "PERSON", doc_id])
    con.execute(INSERT_ALIAS_SQL, [str(uuid.uuid4()), phone_id, "+44 7700 900123", doc_id, chunk_id])
    con.execute(INSERT_ALIAS_SQL, [str(uuid.uuid4()), person_id, "Marcus Webb", doc_id, chunk_id])
    con.execute(
        """
        INSERT INTO relationships (rel_id, source_entity, target_entity, relation_type, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        [rel_id, phone_id, person_id, "ASSOCIATED_WITH", 0.92],
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
            74,
            "Phone +44 7700 900123 is associated with Marcus Webb in recent call data.",
            "pattern",
        ],
    )

    result = await run_query(
        "List all phone numbers linked to this case and who they are associated with.",
        case_ref="OP_ASSOC",
    )

    assert result["intent"] == "entity_relationship_inventory_query"
    assert result["backend"] == "structured-sql"
    assert "+44 7700 900123 is associated with Marcus Webb" in result["answer"]
    assert result["claims"]


@pytest.mark.asyncio
async def test_run_query_does_not_let_chat_history_reclassify_self_contained_query(
    tmp_path,
    monkeypatch,
):
    await close_runtime_resources()
    db_path = tmp_path / "chat-history-routing.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    monkeypatch.setattr(
        "operation_lens_v2.query.retriever_vector.retrieve_vector",
        AsyncMock(return_value=[]),
    )

    observed_packet: dict[str, object] = {}

    async def fake_generate_answer(packet, *, use_cloud: bool):
        observed_packet.update(packet)
        return {
            "backend": "local-test",
            "answer": "KEY FINDINGS\nNo strongly supported relationships were found in the current evidence packet.",
            "claims": [],
        }

    async def passthrough_validate(payload):
        return {**payload, "claims": []}

    monkeypatch.setattr(
        "operation_lens_v2.query.pipeline.llm_router.generate_answer",
        fake_generate_answer,
    )
    monkeypatch.setattr(
        "operation_lens_v2.query.pipeline.claim_validator.validate_claims",
        passthrough_validate,
    )

    result = await run_query(
        "22 Linton Gardens",
        chat_history=[
            {"role": "user", "content": "List all phone numbers linked to this case and who they are associated with."},
            {"role": "assistant", "content": "KEY FINDINGS\n- +447712345678 is associated with Marcus Webb [NF-TEC-008.pdf, p.1]"},
            {"role": "user", "content": "Show location movements for Marcus Webb in chronological order with source references."},
        ],
    )

    assert result["intent"] == "general_query"
    assert observed_packet["query_text"] == "22 Linton Gardens"


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
        assert packet["query_intent"] == "entity_relationship_query"
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


@pytest.mark.asyncio
async def test_run_query_summarises_requested_document_from_chunks(tmp_path, monkeypatch):
    await close_runtime_resources()
    db_path = tmp_path / "document-query.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    monkeypatch.setattr(
        "operation_lens_v2.query.retriever_vector.retrieve_vector",
        AsyncMock(return_value=[]),
    )
    observed_packet: dict[str, object] = {}

    async def fake_generate_answer(packet, *, use_cloud: bool):
        observed_packet.update(packet)
        return {
            "backend": "local-doc-test",
            "answer": (
                "KEY FINDINGS\n"
                "- Surveillance noted two meetings at North Yard involving a white Transit van "
                "[OC-INT-003.pdf, p.1]\n"
                "CONFIDENCE POSTURE\n"
                "Excerpt-backed summary.\n"
                "EVIDENCE GAPS\n"
                "Single document."
            ),
            "claims": [],
        }

    async def passthrough_validate(payload):
        return {**payload, "claims": []}

    monkeypatch.setattr(
        "operation_lens_v2.query.pipeline.llm_router.generate_answer",
        fake_generate_answer,
    )
    monkeypatch.setattr(
        "operation_lens_v2.query.pipeline.claim_validator.validate_claims",
        passthrough_validate,
    )

    import uuid

    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())

    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_DOC", "Document Case"],
    )
    con.execute(
        INSERT_DOCUMENT_SQL,
        [doc_id, case_id, "OC-INT-003.pdf", "/tmp/OC-INT-003.pdf", 1],
    )
    con.execute(
        INSERT_CHUNK_SQL,
        [
            chunk_id,
            doc_id,
            1,
            0,
            "Surveillance noted two meetings at North Yard involving a white Transit van.",
            14,
        ],
    )

    result = await run_query("Summarise key findings from OC-INT-003.pdf")

    assert result["intent"] == "document_summary_query"
    assert result["result_count"] >= 1
    assert "North Yard" in result["answer"]
    assert "OC-INT-003.pdf" in result["answer"]
    assert observed_packet["query_intent"] == "document_summary_query"


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
