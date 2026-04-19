from __future__ import annotations

import csv
from pathlib import Path

import pytest

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import init_db
from operation_lens_v2.ingestion.pipeline import ingest_pdf


class _FakeVectorStore:
    def __init__(self, *_args, **_kwargs) -> None:
        self.rows: list[dict[str, object]] = []

    def upsert(self, rows: list[dict[str, object]]) -> None:
        self.rows.extend(rows)


@pytest.mark.asyncio
async def test_csv_ingestion_streams_rows_into_chunks(tmp_path: Path, monkeypatch) -> None:
    csv_path = tmp_path / "rows.csv"
    db_path = tmp_path / "evidence.duckdb"
    lancedb_path = tmp_path / "lancedb"

    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    monkeypatch.setattr(settings, "lancedb_path", str(lancedb_path))
    monkeypatch.setattr(
        "operation_lens_v2.ingestion.pipeline.vector_store.VectorStore",
        _FakeVectorStore,
    )

    async def _embed_text(_text: str) -> list[float]:
        return [0.0, 0.0]

    monkeypatch.setattr("operation_lens_v2.ingestion.pipeline.embedder.embed_text", _embed_text)
    monkeypatch.setattr(
        "operation_lens_v2.ingestion.pipeline.ner_gliner.extract_general_entities",
        lambda _text: [],
    )
    monkeypatch.setattr(
        "operation_lens_v2.ingestion.pipeline.ner_rules.extract_rule_entities",
        lambda _text: [],
    )

    async def _no_relationships(_text, *_args, **_kwargs):
        return []

    monkeypatch.setattr(
        "operation_lens_v2.ingestion.pipeline.ner_llm.extract_llm_entities",
        _no_relationships,
    )
    monkeypatch.setattr(
        "operation_lens_v2.ingestion.pipeline.relationship_extractor.extract_relationships",
        _no_relationships,
    )

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["row_id", "name", "score"])
        writer.writeheader()
        for row_id in range(1, 1001):
            writer.writerow({"row_id": row_id, "name": f"Person {row_id}", "score": row_id % 10})

    result = await ingest_pdf(csv_path, db_path=str(db_path), case_ref="CSV_CASE")

    con = init_db(str(db_path))
    chunk_count = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    csv_chunk_count = con.execute(
        "SELECT count(*) FROM chunks WHERE source_kind = 'csv'"
    ).fetchone()[0]
    first_chunk = con.execute("SELECT text FROM chunks ORDER BY chunk_index LIMIT 1").fetchone()[0]
    schema_columns = {row[1] for row in con.execute("PRAGMA table_info('chunks')").fetchall()}

    assert result["pages"] == 1000
    assert result["chunks"] == 1000
    assert chunk_count == 1000
    assert csv_chunk_count == 1000
    assert first_chunk == "row_id: 1 | name: Person 1 | score: 1"
    assert "source_kind" in schema_columns
