from __future__ import annotations

from pathlib import Path

import pytest

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store, media_ingest, normaliser, pipeline
from operation_lens_v2.ingestion.change_detection import fingerprint_path
from operation_lens_v2.ingestion.pipeline import ingest_pdf
from operation_lens_v2.models import Chunk


@pytest.mark.asyncio
async def test_ingest_pdf_skips_when_source_hash_unchanged(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "hash-skip.duckdb"
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    monkeypatch.setattr(settings, "lancedb_path", str(tmp_path / "lancedb"))

    pdf_path = tmp_path / "note.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stable")
    fingerprint = fingerprint_path(pdf_path)
    con = duck_store.init_db(str(db_path))
    case_id = duck_store.create_case(con, case_ref="OP_HASH", case_name="Hash Case")
    duck_store.upsert_document(
        con,
        doc_id="doc-existing",
        filename=pdf_path.name,
        filepath=str(pdf_path),
        page_count=1,
        ocr_used=False,
        case_id=case_id,
        source_hash=fingerprint.sha256,
        source_size_bytes=fingerprint.size_bytes,
        source_mtime_ns=fingerprint.mtime_ns,
    )

    result = await ingest_pdf(pdf_path, case_ref="OP_HASH")

    assert result["skipped"] == 1
    assert result["doc_id"] == "doc-existing"
    assert duck_store.list_ingestion_events(con, case_ref="OP_HASH")[0]["status"] == "skipped"


@pytest.mark.asyncio
async def test_ingest_pdf_reingests_changed_source_and_links_previous_doc(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "hash-changed.duckdb"
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    monkeypatch.setattr(settings, "lancedb_path", str(tmp_path / "lancedb"))
    monkeypatch.setattr(pipeline.vector_store, "VectorStore", lambda _path: _FakeVectorStore())

    pdf_path = tmp_path / "changed.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 new bytes")
    con = duck_store.init_db(str(db_path))
    case_id = duck_store.create_case(con, case_ref="OP_HASH", case_name="Hash Case")
    duck_store.upsert_document(
        con,
        doc_id="doc-old",
        filename=pdf_path.name,
        filepath=str(pdf_path),
        page_count=1,
        ocr_used=False,
        case_id=case_id,
        source_hash="old-hash",
    )
    seen: dict[str, object] = {}

    async def fake_run(**kwargs):
        seen.update(kwargs)
        duck_store.upsert_document(
            kwargs["con"],
            doc_id=kwargs["doc_id"],
            filename=pdf_path.name,
            filepath=str(pdf_path),
            page_count=1,
            ocr_used=False,
            case_id=case_id,
            source_hash=kwargs["source_hash"],
            supersedes_doc_id=kwargs["supersedes_doc_id"],
        )
        return {
            "doc_id": kwargs["doc_id"],
            "case_ref": "OP_HASH",
            "pages": 1,
            "chunks": 0,
            "relationships": 0,
            "ocr_used": False,
        }

    monkeypatch.setattr(pipeline, "_run_pdf_ingestion", fake_run)

    result = await pipeline.ingest_pdf(pdf_path, case_ref="OP_HASH")

    assert "skipped" not in result
    assert seen["supersedes_doc_id"] == "doc-old"
    row = con.execute(
        "SELECT supersedes_doc_id, source_hash FROM documents WHERE doc_id = ?",
        [result["doc_id"]],
    ).fetchone()
    assert row[0] == "doc-old"
    assert row[1] != "old-hash"


def test_entity_resolution_records_confidence_and_provenance(tmp_path: Path) -> None:
    con = duck_store.init_db(str(tmp_path / "entities.duckdb"))
    duck_store.upsert_document(
        con,
        doc_id="doc-1",
        filename="a.txt",
        filepath="/tmp/a.txt",
        page_count=1,
        ocr_used=False,
    )
    duck_store.insert_chunks(
        con,
        [Chunk("chunk-1", "doc-1", 1, 0, "DC Marcus Webb", 3)],
    )

    first = normaliser.resolve_entity_with_provenance(
        surface="DC Marcus Webb",
        entity_type="PERSON",
        con=con,
        source_doc="doc-1",
        source_chunk="chunk-1",
        threshold=0.8,
    )
    second = normaliser.resolve_entity_with_provenance(
        surface="Marcus Webb",
        entity_type="PERSON",
        con=con,
        source_doc="doc-1",
        source_chunk="chunk-1",
        threshold=0.8,
    )

    assert second.entity_id == first.entity_id
    row = con.execute(
        """
        SELECT resolution_method, resolution_confidence, matched_entity_id
        FROM entity_aliases
        WHERE alias_text = 'Marcus Webb'
        """,
    ).fetchone()
    assert row[0] == "jaro_winkler"
    assert row[1] >= 0.8
    assert row[2] == first.entity_id


@pytest.mark.asyncio
async def test_media_ingest_uses_stubbed_local_whisper_path(tmp_path, monkeypatch) -> None:
    media_path = tmp_path / "interview.mp3"
    media_path.write_bytes(b"not real audio; transcription is stubbed")
    monkeypatch.setattr(settings, "lancedb_path", str(tmp_path / "lancedb"))
    monkeypatch.setattr(
        media_ingest,
        "transcribe_media",
        lambda _path: media_ingest.TranscriptionResult(
            text="Marcus Webb met Rania Khalil.",
            method="whisper",
            available=True,
        ),
    )
    monkeypatch.setattr(media_ingest.ner_gliner, "extract_general_entities", lambda _text: [])
    monkeypatch.setattr(media_ingest.ner_rules, "extract_rule_entities", lambda _text: [])
    monkeypatch.setattr(media_ingest.ner_llm, "extract_llm_entities", _async_empty)
    monkeypatch.setattr(media_ingest.relationship_extractor, "extract_relationships", _async_empty)
    monkeypatch.setattr(media_ingest.embedder, "embed_text", _async_embed)
    monkeypatch.setattr(media_ingest.vector_store, "VectorStore", lambda _path: _FakeVectorStore())

    result = await media_ingest.ingest_media(
        media_path,
        db_path=str(tmp_path / "media.duckdb"),
        case_ref="OP_MEDIA",
    )

    assert result["transcribed"] is True
    assert result["format"] == "media"
    con = duck_store.init_db(str(tmp_path / "media.duckdb"))
    row = con.execute("SELECT format, source_hash IS NOT NULL FROM documents").fetchone()
    assert row == ("media", True)


class _FakeVectorStore:
    def upsert(self, _rows):
        return None


async def _async_empty(*_args, **_kwargs):
    return []


async def _async_embed(_text: str) -> list[float]:
    return [0.0] * 768
