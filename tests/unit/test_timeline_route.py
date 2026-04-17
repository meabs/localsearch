from __future__ import annotations

import uuid

from operation_lens_v2.api.routes.timeline import timeline
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import init_db


def _seed_timeline_case(con) -> tuple[str, str, str]:
    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    entity_id = str(uuid.uuid4())

    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_TIMELINE", "Timeline Case"],
    )
    con.execute(
        """
        INSERT INTO documents (doc_id, case_id, filename, filepath, page_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        [doc_id, case_id, "timeline-note.pdf", "/tmp/timeline-note.pdf", 1],
    )
    con.execute(
        """
        INSERT INTO chunks (chunk_id, doc_id, page, chunk_index, text, token_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            chunk_id,
            doc_id,
            1,
            0,
            "12 March 2026 at 09:30 J Pike arrived at the yard and exchanged a package.",
            14,
        ],
    )
    con.execute(
        """
        INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc)
        VALUES (?, ?, ?, ?)
        """,
        [entity_id, "Jonas Pike", "PERSON", doc_id],
    )
    con.execute(
        """
        INSERT INTO entity_aliases (alias_id, entity_id, alias_text, source_doc, source_chunk)
        VALUES (?, ?, ?, ?, ?)
        """,
        [str(uuid.uuid4()), entity_id, "J Pike", doc_id, chunk_id],
    )
    return doc_id, chunk_id, entity_id


def _seed_timeline_event(
    con,
    *,
    case_id: str,
    doc_id: str,
    filename: str,
    text: str,
) -> None:
    con.execute(
        """
        INSERT INTO documents (doc_id, case_id, filename, filepath, page_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        [doc_id, case_id, filename, f"/tmp/{filename}", 1],
    )
    con.execute(
        """
        INSERT INTO chunks (chunk_id, doc_id, page, chunk_index, text, token_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [str(uuid.uuid4()), doc_id, 1, 0, text, 12],
    )


def test_timeline_matches_entity_via_alias_mapping(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "timeline.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    _seed_timeline_case(con)

    result = timeline(entity="Jonas Pike")

    assert result["count"] == 1
    assert result["events"][0]["filename"] == "timeline-note.pdf"
    assert "12 March 2026" in result["events"][0]["excerpt"]


def test_timeline_matches_partial_dynamic_name_search(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "timeline-partial.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    _seed_timeline_case(con)

    result = timeline(entity="Jonas")

    assert result["count"] == 1
    assert result["entity_filter"] == "Jonas"


def test_timeline_orders_events_chronologically_across_formats(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "timeline-ordering.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    case_id = str(uuid.uuid4())
    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_TIMELINE_ORDER", "Timeline Order Case"],
    )

    _seed_timeline_event(
        con,
        case_id=case_id,
        doc_id="aaa-doc",
        filename="future-iso.pdf",
        text="2026-04-01T09:00 briefing notes were shared with the team.",
    )
    _seed_timeline_event(
        con,
        case_id=case_id,
        doc_id="mmm-doc",
        filename="mid-numeric.pdf",
        text="12/03/2025 14:30 a handoff call confirmed the delivery window.",
    )
    _seed_timeline_event(
        con,
        case_id=case_id,
        doc_id="zzz-doc",
        filename="past-text.pdf",
        text="January 5, 2025 at 08:15 the shipment was logged into storage.",
    )

    result = timeline(limit=10)

    assert result["count"] == 3
    assert [event["filename"] for event in result["events"]] == [
        "past-text.pdf",
        "mid-numeric.pdf",
        "future-iso.pdf",
    ]
