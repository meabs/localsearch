from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import (
    chunker,
    duck_store,
    embedder,
    ner_gliner,
    ner_llm,
    ner_rules,
    normaliser,
    relationship_extractor,
    vector_store,
)
from operation_lens_v2.ingestion.change_detection import fingerprint_path
from operation_lens_v2.ingestion.media_objects import ensure_ffmpeg_on_path, extract_media_objects
from operation_lens_v2.models import Chunk

logger = logging.getLogger(__name__)

AUDIO_VIDEO_SUFFIXES = {
    ".aac",
    ".aiff",
    ".avi",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogg",
    ".wav",
    ".webm",
    ".wma",
}


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    method: str
    available: bool
    error: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def transcribe_media(media_path: Path, *, model_name: str | None = None) -> TranscriptionResult:
    """Transcribe audio/video with local Whisper when the optional package is installed."""
    if ensure_ffmpeg_on_path() is None:
        return TranscriptionResult(
            text="",
            method="whisper",
            available=True,
            error="ffmpeg executable not found; set FFMPEG_PATH or install ffmpeg",
        )
    try:
        import whisper  # type: ignore[import]
    except ImportError:
        return TranscriptionResult(
            text="",
            method="whisper",
            available=False,
            error="local Whisper package is not installed",
        )

    try:
        model = whisper.load_model(model_name or settings.whisper_model)
        result = model.transcribe(str(media_path))
    except Exception as exc:
        logger.warning("Whisper transcription failed for %s: %s", media_path, exc)
        return TranscriptionResult(text="", method="whisper", available=True, error=str(exc))

    text = str(result.get("text") or "").strip()
    return TranscriptionResult(text=text, method="whisper", available=True)


async def ingest_media(
    media_path: Path,
    *,
    db_path: str | None = None,
    case_ref: str = "UNASSIGNED",
    case_name: str | None = None,
) -> dict[str, int | str | bool]:
    db_path = db_path or settings.duckdb_path
    con = duck_store.init_db(db_path)
    lance = vector_store.VectorStore(settings.lancedb_path)
    resolved_case_name = case_name or case_ref.replace("_", " ").title()
    case_id = duck_store.create_case(con, case_ref=case_ref, case_name=resolved_case_name)
    doc_id = str(uuid4())
    event_id = str(uuid4())
    started_at = _utc_now()
    started_perf = perf_counter()
    fingerprint = fingerprint_path(media_path)

    try:
        transcription = transcribe_media(media_path)
    except Exception as exc:
        duration_ms = int((perf_counter() - started_perf) * 1000)
        duck_store.record_ingestion_event(
            con,
            event_id=event_id,
            case_id=case_id,
            doc_id=doc_id,
            source_path=str(media_path),
            source_type="media",
            status="failed",
            started_at=started_at,
            completed_at=_utc_now(),
            duration_ms=duration_ms,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise

    if not transcription.text:
        duck_store.upsert_document(
            con,
            doc_id=doc_id,
            filename=media_path.name,
            filepath=str(media_path),
            page_count=0,
            ocr_used=False,
            case_id=case_id,
            doc_format="media",
            source_hash=fingerprint.sha256,
            source_size_bytes=fingerprint.size_bytes,
            source_mtime_ns=fingerprint.mtime_ns,
        )
        media_graph = _extract_media_objects_safe(
            con,
            media_path=media_path,
            doc_id=doc_id,
            case_id=case_id,
        )
        result = {
            "doc_id": doc_id,
            "case_ref": case_ref,
            "pages": 0,
            "chunks": 0,
            "relationships": 0,
            "format": "media",
            "transcribed": False,
            "transcription_error": transcription.error or "empty transcription",
            "media_frames": int(media_graph.get("frames", 0)),
            "media_detections": int(media_graph.get("detections", 0)),
            "media_object_relationships": int(media_graph.get("object_relationships", 0)),
        }
        duration_ms = int((perf_counter() - started_perf) * 1000)
        duck_store.record_ingestion_event(
            con,
            event_id=event_id,
            case_id=case_id,
            doc_id=doc_id,
            source_path=str(media_path),
            source_type="media",
            status="transcription_empty",
            started_at=started_at,
            completed_at=_utc_now(),
            duration_ms=duration_ms,
            pages=0,
            chunks=0,
            relationships_new=0,
            ocr_used=False,
            notes="; ".join(
                part
                for part in [
                    transcription.error or "empty transcription",
                    str(media_graph.get("notes") or ""),
                ]
                if part
            ),
        )
        return result

    duck_store.upsert_document(
        con,
        doc_id=doc_id,
        filename=media_path.name,
        filepath=str(media_path),
        page_count=1,
        ocr_used=False,
        case_id=case_id,
        doc_format="media",
        source_hash=fingerprint.sha256,
        source_size_bytes=fingerprint.size_bytes,
        source_mtime_ns=fingerprint.mtime_ns,
    )
    media_graph = _extract_media_objects_safe(
        con,
        media_path=media_path,
        doc_id=doc_id,
        case_id=case_id,
    )
    page_text = f"[Transcript via {transcription.method}]\n{transcription.text}"
    chunks = chunker.chunk_pages(
        doc_id,
        [(1, page_text)],
        target_tokens=settings.chunk_target_tokens,
        overlap_ratio=settings.chunk_overlap_tokens / settings.chunk_target_tokens,
    )
    duck_store.insert_chunks(con, chunks)

    vector_rows = []
    relationship_count = 0
    for chunk in chunks:
        relationship_count += await _extract_entities_relationships(con, chunk, doc_id)
        vector_rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "page": chunk.page,
                "text": chunk.text,
                "vector": await embedder.embed_text(chunk.text),
            }
        )

    if vector_rows:
        lance.upsert(vector_rows)
    result = {
        "doc_id": doc_id,
        "case_ref": case_ref,
        "pages": 1,
        "chunks": len(chunks),
        "relationships": relationship_count,
        "format": "media",
        "transcribed": True,
        "media_frames": int(media_graph.get("frames", 0)),
        "media_detections": int(media_graph.get("detections", 0)),
        "media_object_relationships": int(media_graph.get("object_relationships", 0)),
    }
    duration_ms = int((perf_counter() - started_perf) * 1000)
    duck_store.record_ingestion_event(
        con,
        event_id=event_id,
        case_id=case_id,
        doc_id=doc_id,
        source_path=str(media_path),
        source_type="media",
        status="success",
        started_at=started_at,
        completed_at=_utc_now(),
        duration_ms=duration_ms,
        pages=1,
        chunks=len(chunks),
        entities_new=duck_store.count_entities_first_seen_in(con, doc_id),
        relationships_new=relationship_count,
        ocr_used=False,
        notes="; ".join(
            part
            for part in [
                f"transcription_method={transcription.method}",
                f"source_hash={fingerprint.sha256}",
                str(media_graph.get("notes") or ""),
            ]
            if part
        ),
    )
    return result


def _extract_media_objects_safe(
    con,
    *,
    media_path: Path,
    doc_id: str,
    case_id: str,
) -> dict[str, object]:
    try:
        return extract_media_objects(con, media_path=media_path, doc_id=doc_id, case_id=case_id)
    except Exception as exc:
        logger.warning("Media object extraction failed for %s: %s", media_path, exc)
        return {
            "frames": 0,
            "detections": 0,
            "object_relationships": 0,
            "notes": f"media_object_error={type(exc).__name__}: {exc}",
        }


async def _extract_entities_relationships(con, chunk: Chunk, doc_id: str) -> int:
    general_entities = ner_gliner.extract_general_entities(chunk.text)
    rule_entities = ner_rules.extract_rule_entities(chunk.text)
    llm_entities = await ner_llm.extract_llm_entities(
        chunk.text,
        existing_entities=rule_entities + general_entities,
    )
    merged_entities = [*rule_entities, *general_entities, *llm_entities]

    canonical_id_by_surface: dict[str, str] = {}
    for entity in merged_entities:
        resolution = normaliser.resolve_entity_with_provenance(
            surface=entity.text,
            entity_type=entity.entity_type,
            con=con,
            source_doc=doc_id,
            source_chunk=chunk.chunk_id,
            threshold=settings.alias_threshold,
            confidence=float(getattr(entity, "confidence", 1.0) or 1.0),
        )
        canonical_id_by_surface[entity.text] = resolution.entity_id

    relationship_count = 0
    relationships = await relationship_extractor.extract_relationships(chunk.text, merged_entities)
    for rel in relationships:
        source_id = canonical_id_by_surface.get(rel.source)
        target_id = canonical_id_by_surface.get(rel.target)
        if not source_id or not target_id or source_id == target_id:
            continue
        rel_id = duck_store.create_relationship(
            con,
            source_entity=source_id,
            target_entity=target_id,
            relation_type=rel.relation_type,
            confidence=rel.confidence,
        )
        duck_store.insert_relationship_evidence(
            con,
            rel_id=rel_id,
            chunk_id=chunk.chunk_id,
            doc_id=doc_id,
            page=chunk.page,
            rel=rel,
        )
        relationship_count += 1
    return relationship_count
