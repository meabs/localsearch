"""Tests for scope-enforced investigator tools."""

from __future__ import annotations

import uuid

import pytest

from operation_lens_v2.ingestion.duck_store import init_db
from operation_lens_v2.query import tools
from operation_lens_v2.query.scope import (
    ScopeViolation,
    build_scope,
)

INSERT_CASE_SQL = "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)"
INSERT_DOC_SQL = (
    "INSERT INTO documents (doc_id, case_id, filename, filepath, page_count)"
    " VALUES (?, ?, ?, ?, ?)"
)
INSERT_CHUNK_SQL = (
    "INSERT INTO chunks (chunk_id, doc_id, page, chunk_index, text, token_count)"
    " VALUES (?, ?, ?, ?, ?, ?)"
)
INSERT_ENTITY_SQL = (
    "INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc, mention_count)"
    " VALUES (?, ?, ?, ?, ?)"
)
INSERT_ALIAS_SQL = (
    "INSERT INTO entity_aliases (alias_id, entity_id, alias_text, source_doc, source_chunk)"
    " VALUES (?, ?, ?, ?, ?)"
)
INSERT_REL_SQL = (
    "INSERT INTO relationships (rel_id, source_entity, target_entity, relation_type, confidence,"
    " event_time) VALUES (?, ?, ?, ?, ?, ?)"
)
INSERT_EVIDENCE_SQL = (
    "INSERT INTO relationship_evidence (evidence_id, rel_id, chunk_id, doc_id, page, span_start,"
    " span_end, span_text, extraction_method, event_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


@pytest.fixture
def seeded_db():
    """In-memory DuckDB with three documents across two cases.

    Layout:
      Case OP_A:
        doc_a1: Webb + RX71 KLD co-mentioned in chunk a1c1 (page 1)
        doc_a2: RX71 KLD + Khalil co-mentioned in chunk a2c1 (page 1)
                Relationship Webb → Khalil via ASSOCIATED_WITH with evidence on doc_a2
      Case OP_B:
        doc_b1: unrelated — only a "Marsh" PERSON
    """
    con = init_db(":memory:")

    case_a = str(uuid.uuid4())
    case_b = str(uuid.uuid4())
    con.execute(INSERT_CASE_SQL, [case_a, "OP_A", "Case A"])
    con.execute(INSERT_CASE_SQL, [case_b, "OP_B", "Case B"])

    doc_a1 = "doc-a1"
    doc_a2 = "doc-a2"
    doc_b1 = "doc-b1"
    con.execute(INSERT_DOC_SQL, [doc_a1, case_a, "NF-INT-001.pdf", "/tmp/a1.pdf", 1])
    con.execute(INSERT_DOC_SQL, [doc_a2, case_a, "NF-SURV-004.pdf", "/tmp/a2.pdf", 1])
    con.execute(INSERT_DOC_SQL, [doc_b1, case_b, "Other.pdf", "/tmp/b1.pdf", 1])

    a1c1 = "chunk-a1c1"
    a2c1 = "chunk-a2c1"
    b1c1 = "chunk-b1c1"
    con.execute(
        INSERT_CHUNK_SQL,
        [a1c1, doc_a1, 1, 0, "Marcus Webb admits owning RX71 KLD.", 10],
    )
    con.execute(
        INSERT_CHUNK_SQL,
        [a2c1, doc_a2, 1, 0, "RX71 KLD seen at Depot. Driver identified as Rania Khalil.", 12],
    )
    con.execute(INSERT_CHUNK_SQL, [b1c1, doc_b1, 1, 0, "Danny Marsh observed alone.", 5])

    webb_id = "e-webb"
    plate_id = "e-plate"
    khalil_id = "e-khalil"
    marsh_id = "e-marsh"
    con.execute(INSERT_ENTITY_SQL, [webb_id, "Marcus Webb", "PERSON", doc_a1, 2])
    con.execute(INSERT_ENTITY_SQL, [plate_id, "RX71 KLD", "VEHICLE", doc_a1, 2])
    con.execute(INSERT_ENTITY_SQL, [khalil_id, "Rania Khalil", "PERSON", doc_a2, 1])
    con.execute(INSERT_ENTITY_SQL, [marsh_id, "Danny Marsh", "PERSON", doc_b1, 1])

    con.execute(INSERT_ALIAS_SQL, [str(uuid.uuid4()), webb_id, "Marcus Webb", doc_a1, a1c1])
    con.execute(INSERT_ALIAS_SQL, [str(uuid.uuid4()), plate_id, "RX71 KLD", doc_a1, a1c1])
    con.execute(INSERT_ALIAS_SQL, [str(uuid.uuid4()), plate_id, "RX71 KLD", doc_a2, a2c1])
    con.execute(INSERT_ALIAS_SQL, [str(uuid.uuid4()), khalil_id, "Rania Khalil", doc_a2, a2c1])
    con.execute(INSERT_ALIAS_SQL, [str(uuid.uuid4()), marsh_id, "Danny Marsh", doc_b1, b1c1])

    rel_id = "r-webb-khalil"
    con.execute(
        INSERT_REL_SQL,
        [rel_id, webb_id, khalil_id, "ASSOCIATED_WITH", 0.7, "2024-04-01T12:00:00"],
    )
    con.execute(
        INSERT_EVIDENCE_SQL,
        [
            str(uuid.uuid4()),
            rel_id,
            a2c1,
            doc_a2,
            1,
            0,
            60,
            "RX71 KLD seen at Depot. Driver identified as Rania Khalil.",
            "pattern",
            "2024-04-01T12:00:00",
        ],
    )

    yield {
        "con": con,
        "doc_a1": doc_a1,
        "doc_a2": doc_a2,
        "doc_b1": doc_b1,
        "webb": webb_id,
        "plate": plate_id,
        "khalil": khalil_id,
        "marsh": marsh_id,
        "chunk_a1c1": a1c1,
        "chunk_a2c1": a2c1,
        "chunk_b1c1": b1c1,
    }


# ── search_entities ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_entities_corpus_finds_across_cases(seeded_db):
    scope = build_scope("corpus")
    hits = await tools.search_entities(scope, "marsh", seeded_db["con"])
    assert any(h.entity_id == seeded_db["marsh"] for h in hits)


@pytest.mark.asyncio
async def test_search_entities_case_scope_excludes_other_case(seeded_db):
    scope = build_scope("case", case_scope="OP_A")
    hits = await tools.search_entities(scope, "marsh", seeded_db["con"])
    assert all(h.entity_id != seeded_db["marsh"] for h in hits)


@pytest.mark.asyncio
async def test_search_entities_document_scope_limits_to_doc(seeded_db):
    scope = build_scope("document", doc_id=seeded_db["doc_a1"])
    hits = await tools.search_entities(scope, "khalil", seeded_db["con"])
    assert hits == []


@pytest.mark.asyncio
async def test_search_entities_filters_by_type(seeded_db):
    scope = build_scope("corpus")
    hits = await tools.search_entities(scope, "rx71", seeded_db["con"], entity_type="VEHICLE")
    assert hits
    assert all(h.entity_type == "VEHICLE" for h in hits)


# ── get_entity_profile ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entity_profile_corpus_has_doc_count(seeded_db):
    scope = build_scope("corpus")
    profile = await tools.get_entity_profile(scope, seeded_db["plate"], seeded_db["con"])
    assert profile is not None
    assert profile.doc_count == 2


@pytest.mark.asyncio
async def test_entity_profile_document_scope_reduces_doc_count(seeded_db):
    scope = build_scope("document", doc_id=seeded_db["doc_a1"])
    profile = await tools.get_entity_profile(scope, seeded_db["plate"], seeded_db["con"])
    assert profile is not None
    assert profile.doc_count == 1


@pytest.mark.asyncio
async def test_entity_profile_returns_none_when_entity_outside_scope(seeded_db):
    scope = build_scope("case", case_scope="OP_B")
    profile = await tools.get_entity_profile(scope, seeded_db["webb"], seeded_db["con"])
    assert profile is None


# ── get_relationships ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_relationships_returns_edge_with_span(seeded_db):
    scope = build_scope("corpus")
    edges = await tools.get_relationships(scope, seeded_db["webb"], seeded_db["con"])
    assert len(edges) == 1
    assert edges[0].relation_type == "ASSOCIATED_WITH"
    assert edges[0].span_text and "Khalil" in edges[0].span_text
    assert edges[0].citation() is not None


@pytest.mark.asyncio
async def test_get_relationships_scoped_to_doc_without_evidence_returns_empty(seeded_db):
    # Webb is in doc_a1, but the relationship evidence lives on doc_a2
    scope = build_scope("document", doc_id=seeded_db["doc_a1"])
    edges = await tools.get_relationships(scope, seeded_db["webb"], seeded_db["con"])
    assert edges == []


# ── get_cooccurrence ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cooccurrence_finds_shared_chunk(seeded_db):
    scope = build_scope("corpus")
    hits = await tools.get_cooccurrence(
        scope, seeded_db["plate"], seeded_db["khalil"], seeded_db["con"]
    )
    assert len(hits) == 1
    assert hits[0].chunk_id == seeded_db["chunk_a2c1"]


@pytest.mark.asyncio
async def test_cooccurrence_empty_when_entities_never_share_chunk(seeded_db):
    scope = build_scope("corpus")
    hits = await tools.get_cooccurrence(
        scope, seeded_db["webb"], seeded_db["khalil"], seeded_db["con"]
    )
    assert hits == []


@pytest.mark.asyncio
async def test_cooccurrence_respects_document_scope(seeded_db):
    scope = build_scope("document", doc_id=seeded_db["doc_a1"])
    hits = await tools.get_cooccurrence(
        scope, seeded_db["plate"], seeded_db["khalil"], seeded_db["con"]
    )
    assert hits == []


# ── get_timeline ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeline_returns_dated_event(seeded_db):
    scope = build_scope("corpus")
    events = await tools.get_timeline(scope, seeded_db["con"])
    assert len(events) == 1
    assert "2024-04-01" in events[0].event_time


@pytest.mark.asyncio
async def test_timeline_filters_by_entity(seeded_db):
    scope = build_scope("corpus")
    events = await tools.get_timeline(
        scope, seeded_db["con"], entity_ids=[seeded_db["marsh"]]
    )
    assert events == []


# ── fetch_chunk ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_chunk_returns_text(seeded_db):
    scope = build_scope("corpus")
    chunk = await tools.fetch_chunk(scope, seeded_db["chunk_a1c1"], seeded_db["con"])
    assert chunk is not None
    assert "RX71 KLD" in chunk.text


@pytest.mark.asyncio
async def test_fetch_chunk_raises_scope_violation_outside_document(seeded_db):
    scope = build_scope("document", doc_id=seeded_db["doc_a1"])
    with pytest.raises(ScopeViolation):
        await tools.fetch_chunk(scope, seeded_db["chunk_a2c1"], seeded_db["con"])


@pytest.mark.asyncio
async def test_fetch_chunk_raises_scope_violation_outside_case(seeded_db):
    scope = build_scope("case", case_scope="OP_A")
    with pytest.raises(ScopeViolation):
        await tools.fetch_chunk(scope, seeded_db["chunk_b1c1"], seeded_db["con"])


# ── list_documents ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_documents_corpus_shows_all(seeded_db):
    scope = build_scope("corpus")
    docs = await tools.list_documents(scope, seeded_db["con"])
    assert {d.doc_id for d in docs} == {
        seeded_db["doc_a1"],
        seeded_db["doc_a2"],
        seeded_db["doc_b1"],
    }


@pytest.mark.asyncio
async def test_list_documents_case_scope_filters(seeded_db):
    scope = build_scope("case", case_scope="OP_A")
    docs = await tools.list_documents(scope, seeded_db["con"])
    assert {d.doc_id for d in docs} == {seeded_db["doc_a1"], seeded_db["doc_a2"]}


# ── get_document_entities ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_document_entities_returns_in_scope(seeded_db):
    scope = build_scope("document", doc_id=seeded_db["doc_a2"])
    hits = await tools.get_document_entities(scope, seeded_db["doc_a2"], seeded_db["con"])
    ids = {h.entity_id for h in hits}
    assert seeded_db["plate"] in ids
    assert seeded_db["khalil"] in ids


@pytest.mark.asyncio
async def test_document_entities_raises_when_doc_out_of_scope(seeded_db):
    scope = build_scope("document", doc_id=seeded_db["doc_a1"])
    with pytest.raises(ScopeViolation):
        await tools.get_document_entities(scope, seeded_db["doc_b1"], seeded_db["con"])


# ── walk_graph ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_walk_graph_finds_direct_path(seeded_db):
    scope = build_scope("corpus")
    paths = await tools.walk_graph(
        scope, seeded_db["webb"], seeded_db["khalil"], seeded_db["con"]
    )
    assert len(paths) == 1
    assert paths[0].hops == 1
    assert paths[0].citations
