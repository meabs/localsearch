from __future__ import annotations

import json
from pathlib import Path

import pytest

from operation_lens_v2.ingestion.duck_store import init_db
from operation_lens_v2.ingestion.email_threads import ingest_email_thread_parquet


def _write_sample_parquet(path: Path) -> None:
    import duckdb

    messages = json.dumps(
        [
            {
                "sender": "Jeffrey Epstein <jeevacation@gmail.com>",
                "recipients": ["Michael Wolff"],
                "timestamp": "Fri, Jan 9, 2015 at 1:20 PM",
                "subject": "Re:",
                "body": "Who should break the story?",
            },
            {
                "sender": "Michael Wolff",
                "recipients": ["Jeffrey Epstein <jeevacation@gmail.com>"],
                "timestamp": "Fri, Jan 9, 2015 at 2:33 PM",
                "subject": "Re:",
                "body": "Is Clinton willing to say he was not there?",
            },
        ]
    )
    con = duckdb.connect()
    con.execute(
        """
        COPY (
          SELECT
            'thread-1' AS thread_id,
            'sample.txt' AS source_file,
            'Re:' AS subject,
            ? AS messages,
            2 AS message_count
        ) TO ? (FORMAT PARQUET)
        """,
        [messages, str(path)],
    )
    con.close()


@pytest.mark.asyncio
async def test_ingest_email_thread_parquet_creates_graph_entities(tmp_path) -> None:
    parquet_path = tmp_path / "threads.parquet"
    _write_sample_parquet(parquet_path)

    db_path = tmp_path / "evidence.duckdb"
    con = init_db(str(db_path))
    con.close()

    result = await ingest_email_thread_parquet(
        parquet_path,
        db_path=str(db_path),
        case_ref="OP_EMAIL",
        case_name="Email Case",
    )

    assert result["threads"] == 1
    assert result["relationships"] == 2

    verify = init_db(str(db_path))
    entity_rows = verify.execute(
        """
        SELECT canonical_name
        FROM entities
        ORDER BY canonical_name
        """
    ).fetchall()
    relationship_rows = verify.execute(
        """
        SELECT r.relation_type, s.canonical_name, t.canonical_name
        FROM relationships r
        JOIN entities s ON s.entity_id = r.source_entity
        JOIN entities t ON t.entity_id = r.target_entity
        ORDER BY s.canonical_name, t.canonical_name
        """
    ).fetchall()
    document_row = verify.execute(
        "SELECT filename, filepath, page_count FROM documents"
    ).fetchone()

    assert entity_rows == [("Jeffrey Epstein",), ("Michael Wolff",)]
    assert relationship_rows == [
        ("EMAILED", "Jeffrey Epstein", "Michael Wolff"),
        ("EMAILED", "Michael Wolff", "Jeffrey Epstein"),
    ]
    assert document_row is not None
    assert document_row[2] == 2
    assert "thread=thread-1" in document_row[1]
