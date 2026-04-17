"""Tests for DuckGraphBackend and schema wiring."""

from __future__ import annotations

from pathlib import Path

from operation_lens_v2.ingestion.duck_store import (
    create_case,
    create_entity,
    create_relationship,
    init_db,
    insert_chunks,
    insert_relationship_evidence,
    upsert_document,
)
from operation_lens_v2.models import Chunk, RelationshipCandidate
from operation_lens_v2.store import get_graph_backend


def test_graph_backend_retrieve_expanded_graph(tmp_path: Path) -> None:
    db_path = tmp_path / "t.duckdb"
    con = init_db(str(db_path))
    case_id = create_case(con, case_ref="T1", case_name="Test")
    upsert_document(
        con,
        doc_id="d1",
        filename="a.pdf",
        filepath="/a.pdf",
        page_count=1,
        ocr_used=False,
        case_id=case_id,
    )
    e1 = create_entity(con, "Alice", "PERSON", "d1")
    e2 = create_entity(con, "Bob", "PERSON", "d1")
    insert_chunks(
        con,
        [
            Chunk(
                chunk_id="c1",
                doc_id="d1",
                page=1,
                chunk_index=0,
                text="Alice and Bob.",
                token_count=3,
            )
        ],
    )
    rel = RelationshipCandidate(
        source="Alice",
        target="Bob",
        relation_type="ASSOCIATED_WITH",
        span_text="x",
        span_start=0,
        span_end=1,
        confidence=0.9,
        extraction_method="pattern",
    )
    rid = create_relationship(
        con,
        source_entity=e1,
        target_entity=e2,
        relation_type="ASSOCIATED_WITH",
        confidence=0.9,
    )
    insert_relationship_evidence(
        con,
        rel_id=rid,
        chunk_id="c1",
        doc_id="d1",
        page=1,
        rel=rel,
    )
    rows = get_graph_backend(con).retrieve_expanded_graph([e1], limit=10)
    assert len(rows) == 1
    assert rows[0]["rel_id"] == rid
    assert rows[0]["event_time"] is None


def test_relationships_table_has_temporal_columns(tmp_path: Path) -> None:
    con = init_db(str(tmp_path / "s.duckdb"))
    cols = {r[1] for r in con.execute("PRAGMA table_info('relationships')").fetchall()}
    assert "event_time" in cols
    assert "valid_from" in cols
    assert "valid_to" in cols
