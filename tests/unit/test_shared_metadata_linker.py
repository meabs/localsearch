from __future__ import annotations

import csv
from pathlib import Path

import pytest

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import init_db
from operation_lens_v2.ingestion.pipeline import ingest_corpus


class _FakeVectorStore:
    def __init__(self, *_args, **_kwargs) -> None:
        self.rows: list[dict[str, object]] = []

    def upsert(self, rows: list[dict[str, object]]) -> None:
        self.rows.extend(rows)


async def _empty_relationships(_text, *_args, **_kwargs):
    return []


async def _empty_llm_entities(_text, *_args, **_kwargs):
    return []


@pytest.mark.asyncio
async def test_shared_metadata_linker_creates_one_edge_for_shared_phone(
    tmp_path: Path, monkeypatch
) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    db_path = tmp_path / "shared_metadata.duckdb"
    lancedb_path = tmp_path / "lancedb"

    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    monkeypatch.setattr(settings, "lancedb_path", str(lancedb_path))
    monkeypatch.setattr(
        "operation_lens_v2.ingestion.pipeline.vector_store.VectorStore",
        _FakeVectorStore,
    )
    monkeypatch.setattr("operation_lens_v2.ingestion.pipeline.embedder.embed_text", _embed_text)
    monkeypatch.setattr(
        "operation_lens_v2.ingestion.pipeline.ner_gliner.extract_general_entities",
        lambda _text: [],
    )
    monkeypatch.setattr(
        "operation_lens_v2.ingestion.pipeline.relationship_extractor.extract_relationships",
        _empty_relationships,
    )
    monkeypatch.setattr(
        "operation_lens_v2.ingestion.pipeline.ner_llm.extract_llm_entities",
        _empty_llm_entities,
    )

    phone_number = "+44 7700 900123"
    for idx in range(2):
        csv_path = corpus_dir / f"doc-{idx + 1}.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["row_id", "phone", "note"])
            writer.writeheader()
            writer.writerow(
                {
                    "row_id": idx + 1,
                    "phone": phone_number,
                    "note": f"doc {idx + 1}",
                }
            )

    results = await ingest_corpus(corpus_dir, db_path=str(db_path), case_ref="SHARED_META")

    con = init_db(str(db_path))
    rel_count = con.execute(
        "SELECT count(*) FROM relationships WHERE relation_type = 'SHARED_METADATA'"
    ).fetchone()[0]
    evidence_docs = con.execute(
        """
        SELECT count(DISTINCT doc_id)
        FROM relationship_evidence
        WHERE rel_id IN (
          SELECT rel_id FROM relationships WHERE relation_type = 'SHARED_METADATA'
        )
        """
    ).fetchone()[0]

    assert len(results) == 2
    assert rel_count == 1
    assert evidence_docs == 2


async def _embed_text(_text: str) -> list[float]:
    return [0.0, 0.0]
