"""Tests for the Ingest Audit API surface.

Covers the three ingestion-visibility routes and the confirm action that lets
a human promote a flagged low-confidence entity into the graph.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from operation_lens_v2.api.routes.audit import (
    confirm_entity,
    get_ingestion,
    list_ingestions,
)
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import (
    create_case,
    init_db,
    record_ingestion_event,
    upsert_document,
)

INSERT_ENTITY_SQL = """
INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc, confidence)
VALUES (?, ?, ?, ?, ?)
"""
INSERT_ALIAS_SQL = """
INSERT INTO entity_aliases (alias_id, entity_id, alias_text, source_doc, source_chunk)
VALUES (?, ?, ?, ?, ?)
"""
INSERT_CHUNK_SQL = """
INSERT INTO chunks (chunk_id, doc_id, page, chunk_index, text, token_count)
VALUES (?, ?, ?, ?, ?, ?)
"""


def _seed_single_ingestion(
    tmp_path,
    monkeypatch,
    *,
    high_confidence: float = 0.92,
    low_confidence: float = 0.35,
) -> dict[str, str]:
    db_path = tmp_path / "audit.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    case_id = create_case(con, case_ref="OP_AUDIT", case_name="Audit Case")
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    upsert_document(
        con,
        doc_id=doc_id,
        filename="note.pdf",
        filepath="/tmp/note.pdf",
        page_count=3,
        ocr_used=False,
        case_id=case_id,
    )
    con.execute(INSERT_CHUNK_SQL, [chunk_id, doc_id, 1, 0, "Marcus Webb.", 2])

    high_entity_id = str(uuid.uuid4())
    low_entity_id = str(uuid.uuid4())
    con.execute(INSERT_ENTITY_SQL, [high_entity_id, "Marcus Webb", "PERSON", doc_id, high_confidence])
    con.execute(INSERT_ENTITY_SQL, [low_entity_id, "Possible Khalid", "PERSON", doc_id, low_confidence])
    con.execute(INSERT_ALIAS_SQL, [str(uuid.uuid4()), high_entity_id, "Marcus Webb", doc_id, chunk_id])
    con.execute(INSERT_ALIAS_SQL, [str(uuid.uuid4()), low_entity_id, "Khalid?", doc_id, chunk_id])

    event_id = str(uuid.uuid4())
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    record_ingestion_event(
        con,
        event_id=event_id,
        case_id=case_id,
        doc_id=doc_id,
        source_path="/tmp/note.pdf",
        source_type="pdf",
        status="success",
        started_at=now,
        completed_at=now,
        duration_ms=1234,
        pages=3,
        chunks=5,
        entities_new=2,
        relationships_new=0,
        ocr_used=False,
    )
    return {
        "event_id": event_id,
        "doc_id": doc_id,
        "high_entity_id": high_entity_id,
        "low_entity_id": low_entity_id,
    }


def test_list_ingestions_returns_recent_event(tmp_path, monkeypatch) -> None:
    seeded = _seed_single_ingestion(tmp_path, monkeypatch)

    result = list_ingestions()

    assert len(result["ingestions"]) == 1
    assert result["ingestions"][0]["event_id"] == seeded["event_id"]


def test_get_ingestion_flags_low_confidence_entities(tmp_path, monkeypatch) -> None:
    seeded = _seed_single_ingestion(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "low_confidence_threshold", 0.5)

    result = get_ingestion(seeded["event_id"])

    low_entities = [item for item in result["entities"] if item["low_confidence"]]
    assert len(low_entities) == 1
    assert low_entities[0]["entity_id"] == seeded["low_entity_id"]


def test_get_ingestion_raises_404_for_unknown_event(tmp_path, monkeypatch) -> None:
    _seed_single_ingestion(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        get_ingestion("00000000-0000-0000-0000-000000000000")

    assert exc_info.value.status_code == 404


def test_confirm_entity_lifts_confidence_to_one(tmp_path, monkeypatch) -> None:
    seeded = _seed_single_ingestion(tmp_path, monkeypatch)

    response = confirm_entity(seeded["low_entity_id"], reviewed_by="analyst-1")

    assert response["entity"]["confidence"] == 1.0
