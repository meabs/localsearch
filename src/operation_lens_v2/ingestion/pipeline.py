from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import (
    chunker,
    duck_store,
    embedder,
    extractor,
    ner_gliner,
    ner_llm,
    ner_rules,
    normaliser,
    relationship_extractor,
    vector_store,
)
from operation_lens_v2.ingestion.change_detection import (
    fingerprint_path,
    is_unchanged,
    latest_document_for_path,
)
from operation_lens_v2.ingestion.media_ingest import AUDIO_VIDEO_SUFFIXES, ingest_media

logger = logging.getLogger(__name__)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _spans_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a


def _merge_entity_sources(*sources: list) -> list:
    """Merge entity lists by precedence order, skipping later overlaps."""
    merged = []
    accepted_spans: list[tuple[int, int]] = []
    for source in sources:
        for entity in source:
            if any(
                _spans_overlap(entity.start, entity.end, start, end)
                for start, end in accepted_spans
            ):
                continue
            merged.append(entity)
            accepted_spans.append((entity.start, entity.end))
    return merged


async def ingest_pdf(
    pdf_path: Path,
    *,
    db_path: str | None = None,
    case_ref: str = "UNASSIGNED",
    case_name: str | None = None,
    force: bool = False,
) -> dict[str, int | str]:
    """Full pipeline for one PDF. Async — touches Ollama for embedding and LLM extraction.

    Idempotent by default (skips already-ingested docs). Pass force=True to re-ingest.
    """
    db_path = db_path or settings.duckdb_path
    con = duck_store.init_db(db_path)
    vs = vector_store.VectorStore(settings.lancedb_path)
    resolved_case_name = case_name or case_ref.replace("_", " ").title()
    case_id = duck_store.create_case(con, case_ref=case_ref, case_name=resolved_case_name)

    fingerprint = fingerprint_path(pdf_path)
    existing_doc = latest_document_for_path(con, filepath=str(pdf_path), case_id=case_id)

    # Idempotency: hash-aware for new ingestions, with a legacy filepath fallback
    # for documents ingested before source hashes existed.
    if not force:
        legacy_existing = con.execute(
            (
                "SELECT doc_id FROM documents "
                "WHERE filepath = ? AND case_id = ? AND source_hash IS NULL"
            ),
            [str(pdf_path), case_id],
        ).fetchone()
        if is_unchanged(existing_doc, fingerprint) or legacy_existing:
            existing_doc_id = str(
                (existing_doc or {}).get("doc_id")
                or (legacy_existing[0] if legacy_existing else "")
            )
            logger.info("Skipping already-ingested doc: %s (use force=True to re-ingest)", pdf_path)
            duck_store.record_ingestion_event(
                con,
                event_id=str(uuid4()),
                case_id=case_id,
                doc_id=existing_doc_id,
                source_path=str(pdf_path),
                source_type="pdf",
                status="skipped",
                started_at=_utc_now(),
                completed_at=_utc_now(),
                duration_ms=0,
                notes=f"source_hash={fingerprint.sha256}",
            )
            return {"doc_id": existing_doc_id, "case_ref": case_ref, "skipped": 1}

    event_id = str(uuid4())
    started_at = _utc_now()
    started_perf = perf_counter()
    doc_id = str(uuid4())

    try:
        result = await _run_pdf_ingestion(
            pdf_path=pdf_path,
            doc_id=doc_id,
            case_ref=case_ref,
            case_id=case_id,
            con=con,
            vs=vs,
            source_hash=fingerprint.sha256,
            source_size_bytes=fingerprint.size_bytes,
            source_mtime_ns=fingerprint.mtime_ns,
            supersedes_doc_id=str(existing_doc["doc_id"]) if existing_doc else None,
        )
    except Exception as exc:
        duration_ms = int((perf_counter() - started_perf) * 1000)
        duck_store.record_ingestion_event(
            con,
            event_id=event_id,
            case_id=case_id,
            doc_id=doc_id,
            source_path=str(pdf_path),
            source_type="pdf",
            status="failed",
            started_at=started_at,
            completed_at=_utc_now(),
            duration_ms=duration_ms,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise

    duration_ms = int((perf_counter() - started_perf) * 1000)
    entities_new = duck_store.count_entities_first_seen_in(con, doc_id)
    relationships_new = duck_store.count_relationships_evidenced_in(con, doc_id)
    duck_store.record_ingestion_event(
        con,
        event_id=event_id,
        case_id=case_id,
        doc_id=doc_id,
        source_path=str(pdf_path),
        source_type="pdf",
        status="success",
        started_at=started_at,
        completed_at=_utc_now(),
        duration_ms=duration_ms,
        pages=result["pages"],
        chunks=result["chunks"],
        entities_new=entities_new,
        relationships_new=relationships_new,
        ocr_used=result["ocr_used"],
        notes=f"source_hash={fingerprint.sha256}",
    )
    result["entities_new"] = entities_new
    result["relationships_new"] = relationships_new
    result["event_id"] = event_id
    return result


async def _run_pdf_ingestion(
    *,
    pdf_path: Path,
    doc_id: str,
    case_ref: str,
    case_id: str,
    con,
    vs,
    source_hash: str | None = None,
    source_size_bytes: int | None = None,
    source_mtime_ns: int | None = None,
    supersedes_doc_id: str | None = None,
) -> dict[str, int | str | bool]:
    """Inner pipeline body — returns the legacy ingestion summary.

    Split out so ``ingest_pdf`` can wrap the call in an ingestion-event
    recorder without inflating the function with timing/metrics bookkeeping.
    """
    extraction = extractor.extract_pdf_text_detailed(pdf_path)
    pages = extraction.pages
    ocr_used = extraction.ocr_used
    duck_store.upsert_document(
        con,
        doc_id=doc_id,
        filename=pdf_path.name,
        filepath=str(pdf_path),
        page_count=len(pages),
        ocr_used=ocr_used,
        case_id=case_id,
        source_hash=source_hash,
        source_size_bytes=source_size_bytes,
        source_mtime_ns=source_mtime_ns,
        supersedes_doc_id=supersedes_doc_id,
    )
    for quality in extraction.page_quality:
        duck_store.upsert_page_quality(
            con,
            doc_id=doc_id,
            page=quality.page,
            extraction_method=quality.extraction_method,
            ocr_confidence=quality.ocr_confidence,
            needs_review=quality.needs_review,
            redaction_count=quality.redaction_count,
            evidence_gap=quality.evidence_gap,
            notes=quality.notes,
        )
    logger.info(
        "Ingesting doc=%s pages=%d ocr=%s case=%s", pdf_path.name, len(pages), ocr_used, case_ref
    )

    chunks = chunker.chunk_pages(
        doc_id,
        pages,
        target_tokens=settings.chunk_target_tokens,
        overlap_ratio=settings.chunk_overlap_tokens / settings.chunk_target_tokens,
    )
    duck_store.insert_chunks(con, chunks)

    vector_rows = []
    relation_count = 0

    for chunk in chunks:
        general_entities = ner_gliner.extract_general_entities(chunk.text)
        rule_entities = ner_rules.extract_rule_entities(chunk.text)
        llm_entities = await ner_llm.extract_llm_entities(
            chunk.text, existing_entities=rule_entities + general_entities
        )

        # Deterministic patterns win first, then GLiNER, then local LLM extras.
        merged_entities = _merge_entity_sources(rule_entities, general_entities, llm_entities)

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

        logger.debug("chunk=%s entities=%d", chunk.chunk_id[:8], len(merged_entities))

        rels = await relationship_extractor.extract_relationships(chunk.text, merged_entities)
        for rel in rels:
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
            relation_count += 1

        vector = await embedder.embed_text(chunk.text)
        vector_rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "page": chunk.page,
                "text": chunk.text,
                "vector": vector,
            }
        )

    vs.upsert(vector_rows)
    logger.info(
        "Ingested doc=%s chunks=%d relationships=%d", pdf_path.name, len(chunks), relation_count
    )
    return {
        "doc_id": doc_id,
        "case_ref": case_ref,
        "pages": len(pages),
        "chunks": len(chunks),
        "relationships": relation_count,
        "ocr_used": ocr_used,
    }


async def ingest_corpus(
    corpus_dir: Path,
    *,
    db_path: str | None = None,
    case_ref: str = "UNASSIGNED",
    case_name: str | None = None,
    force: bool = False,
) -> list[dict[str, int | str]]:
    """Ingest supported files in a directory sequentially."""
    files = sorted(
        [
            *corpus_dir.glob("*.pdf"),
            *corpus_dir.glob("*.csv"),
            *corpus_dir.glob("*.tsv"),
            *[path for suffix in AUDIO_VIDEO_SUFFIXES for path in corpus_dir.glob(f"*{suffix}")],
        ]
    )
    if not files:
        logger.warning("No supported files found in %s", corpus_dir)
        return []
    results = []
    for input_path in files:
        if input_path.suffix.lower() in {".csv", ".tsv"}:
            result = await ingest_tabular(
                input_path,
                db_path=db_path,
                case_ref=case_ref,
                case_name=case_name,
            )
        elif input_path.suffix.lower() in AUDIO_VIDEO_SUFFIXES:
            result = await ingest_media(
                input_path,
                db_path=db_path,
                case_ref=case_ref,
                case_name=case_name,
            )
        else:
            result = await ingest_pdf(
                input_path,
                db_path=db_path,
                case_ref=case_ref,
                case_name=case_name,
                force=force,
            )
        results.append(result)
    return results


async def ingest_tabular(
    csv_path: Path,
    *,
    db_path: str | None = None,
    case_ref: str = "UNASSIGNED",
    case_name: str | None = None,
) -> dict[str, int | str]:
    """Ingest CSV/TSV content into the standard NER/relationship/vector pipeline."""
    from operation_lens_v2.ingestion.csv_ingest import CsvIngestConfig, ingest_csv

    del case_name  # case naming is managed inside csv_ingest via case_ref.
    db_path = db_path or settings.duckdb_path
    con = duck_store.init_db(db_path)
    vs = vector_store.VectorStore(settings.lancedb_path)
    return await ingest_csv(
        csv_path=csv_path,
        case_ref=case_ref,
        duck_con=con,
        lance_table=vs,
        gliner_model=ner_gliner.load_gliner_model(),
        ollama_client=None,
        config=CsvIngestConfig(delimiter="\t" if csv_path.suffix.lower() == ".tsv" else None),
    )
