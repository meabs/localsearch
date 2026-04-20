"""Store + pipeline coverage for the ingestion audit trail."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store
from operation_lens_v2.ingestion.pipeline import ingest_pdf

INSERT_ENTITY_SQL = """
INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc)
VALUES (?, ?, ?, ?)
"""
INSERT_RELATIONSHIP_SQL = """
INSERT INTO relationships (rel_id, source_entity, target_entity, relation_type, confidence)
VALUES (?, ?, ?, ?, ?)
"""
INSERT_EVIDENCE_SQL = """
INSERT INTO relationship_evidence (
  evidence_id, rel_id, chunk_id, doc_id, page,
  span_start, span_end, span_text, extraction_method
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


INSERT_DOCUMENT_SQL = (
    "INSERT INTO documents (doc_id, filename, filepath, page_count) VALUES (?, ?, ?, ?)"
)
INSERT_CHUNK_SQL = (
    "INSERT INTO chunks (chunk_id, doc_id, page, chunk_index, text, token_count) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def test_count_entities_first_seen_in_only_counts_this_doc(tmp_path) -> None:
    con = duck_store.init_db(str(tmp_path / "counts.duckdb"))
    this_doc_id = str(uuid.uuid4())
    other_doc_id = str(uuid.uuid4())
    con.execute(INSERT_DOCUMENT_SQL, [this_doc_id, "a.pdf", "/tmp/a.pdf", 1])
    con.execute(INSERT_DOCUMENT_SQL, [other_doc_id, "b.pdf", "/tmp/b.pdf", 1])
    con.execute(INSERT_ENTITY_SQL, [str(uuid.uuid4()), "Marcus Webb", "PERSON", this_doc_id])
    con.execute(INSERT_ENTITY_SQL, [str(uuid.uuid4()), "Rania Khalil", "PERSON", other_doc_id])

    assert duck_store.count_entities_first_seen_in(con, this_doc_id) == 1


def test_count_relationships_evidenced_in_is_distinct(tmp_path) -> None:
    con = duck_store.init_db(str(tmp_path / "rel-counts.duckdb"))
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    rel_id = str(uuid.uuid4())

    con.execute(INSERT_DOCUMENT_SQL, [doc_id, "note.pdf", "/tmp/note.pdf", 1])
    con.execute(INSERT_CHUNK_SQL, [chunk_id, doc_id, 1, 0, "text", 1])
    con.execute(INSERT_ENTITY_SQL, [source_id, "Marcus Webb", "PERSON", doc_id])
    con.execute(INSERT_ENTITY_SQL, [target_id, "14 Arkwright Road", "LOCATION", doc_id])
    con.execute(INSERT_RELATIONSHIP_SQL, [rel_id, source_id, target_id, "OBSERVED_AT", 0.9])
    # Two evidence rows for the same relationship must still count once.
    con.execute(
        INSERT_EVIDENCE_SQL,
        [str(uuid.uuid4()), rel_id, chunk_id, doc_id, 1, 0, 3, "txt", "pattern"],
    )
    con.execute(
        INSERT_EVIDENCE_SQL,
        [str(uuid.uuid4()), rel_id, chunk_id, doc_id, 1, 4, 7, "txt", "pattern"],
    )

    assert duck_store.count_relationships_evidenced_in(con, doc_id) == 1


@pytest.mark.asyncio
async def test_ingest_pdf_records_skipped_event_for_existing_document(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "skipped.duckdb"
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    monkeypatch.setattr(settings, "lancedb_path", str(tmp_path / "lancedb"))

    con = duck_store.init_db(str(db_path))
    case_id = duck_store.create_case(con, case_ref="OP_TEST", case_name="Test Case")
    existing_doc_id = str(uuid.uuid4())
    pdf_path = tmp_path / "already-ingested.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 placeholder")
    duck_store.upsert_document(
        con,
        doc_id=existing_doc_id,
        filename=pdf_path.name,
        filepath=str(pdf_path),
        page_count=1,
        ocr_used=False,
        case_id=case_id,
    )

    result = await ingest_pdf(Path(pdf_path), case_ref="OP_TEST")

    assert result["skipped"] == 1
    events = duck_store.list_ingestion_events(con, case_ref="OP_TEST")
    assert len(events) == 1
    assert events[0]["status"] == "skipped"
    assert events[0]["doc_id"] == existing_doc_id
