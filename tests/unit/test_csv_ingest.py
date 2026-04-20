from __future__ import annotations

import pytest

from operation_lens_v2.ingestion import csv_ingest, duck_store, relationship_extractor
from operation_lens_v2.models import ExtractedEntity, RelationshipCandidate


class _VectorStoreStub:
    def __init__(self) -> None:
        self.rows = []

    def upsert(self, rows):
        self.rows.extend(rows)


@pytest.mark.asyncio
async def test_ingest_csv_builds_chunks_and_entities(tmp_path, monkeypatch):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "subject,phone,plate,location\n"
        "Alice,+447700900111,RX71 KLD,14 Arkwright Road\n"
        "Bob,+447700900222,AB12 CDE,Depot\n",
        encoding="utf-8",
    )

    async def _embed(_text):
        return [0.1, 0.2, 0.3]

    async def _no_llm(*_args, **_kwargs):
        return []

    async def _rels(text, entities):
        del entities
        return [
            RelationshipCandidate(
                source="RX71 KLD",
                target="RX71 KLD",
                relation_type="MENTIONED_WITH",
                span_text=text,
                span_start=0,
                span_end=min(20, len(text)),
                confidence=0.35,
                extraction_method="co-occurrence",
            )
        ]

    monkeypatch.setattr(csv_ingest.embedder, "embed_text", _embed)
    monkeypatch.setattr(
        csv_ingest.ner_rules,
        "extract_rule_entities",
        lambda text: [ExtractedEntity(text="RX71 KLD", entity_type="VEHICLE", start=0, end=8)],
    )
    monkeypatch.setattr(csv_ingest.ner_gliner, "extract_general_entities", lambda _text: [])
    monkeypatch.setattr(csv_ingest.ner_llm, "extract_llm_entities", _no_llm)
    monkeypatch.setattr(relationship_extractor, "extract_relationships", _rels)

    con = duck_store.init_db(str(tmp_path / "evidence.duckdb"))
    vs = _VectorStoreStub()
    result = await csv_ingest.ingest_csv(
        csv_path=csv_path,
        case_ref="CASE_CSV",
        duck_con=con,
        lance_table=vs,
        gliner_model=None,
        ollama_client=None,
        config=csv_ingest.CsvIngestConfig(),
    )

    assert result["format"] == "csv"
    assert result["chunks"] == 2
    assert len(vs.rows) == 2

    doc_row = con.execute(
        "SELECT filename, page_count FROM documents WHERE doc_id = ?",
        [result["doc_id"]],
    ).fetchone()
    assert doc_row == ("sample.csv", 2)
