from __future__ import annotations

import logging
from datetime import datetime, timezone
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

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    # datetime.UTC was added in 3.11 but the aliased constant is clearer.
    return datetime.now(timezone.utc)


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

    # Idempotency: check if this filepath was already ingested under this case.
    if not force:
        existing = con.execute(
            "SELECT doc_id FROM documents WHERE filepath = ? AND case_id = ?",
            [str(pdf_path), case_id],
        ).fetchone()
        if existing:
            logger.info("Skipping already-ingested doc: %s (use force=True to re-ingest)", pdf_path)
            duck_store.record_ingestion_event(
                con,
                event_id=str(uuid4()),
                case_id=case_id,
                doc_id=existing[0],
                source_path=str(pdf_path),
                source_type="pdf",
                status="skipped",
                started_at=_utc_now(),
                completed_at=_utc_now(),
                duration_ms=0,
            )
            return {"doc_id": existing[0], "case_ref": case_ref, "skipped": 1}

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
) -> dict[str, int | str | bool]:
    """Inner pipeline body — returns the legacy ingestion summary.

    Split out so ``ingest_pdf`` can wrap the call in an ingestion-event
    recorder without inflating the function with timing/metrics bookkeeping.
    """
    pages, ocr_used = extractor.extract_pdf_text(pdf_path)
    duck_store.upsert_document(
        con,
        doc_id=doc_id,
        filename=pdf_path.name,
        filepath=str(pdf_path),
        page_count=len(pages),
        ocr_used=ocr_used,
        case_id=case_id,
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
            canonical_id_by_surface[entity.text] = normaliser.resolve_entity(
                surface=entity.text,
                entity_type=entity.entity_type,
                con=con,
                source_doc=doc_id,
                source_chunk=chunk.chunk_id,
                threshold=settings.alias_threshold,
                confidence=float(getattr(entity, "confidence", 1.0) or 1.0),
            )

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
    """Ingest all PDFs in a directory sequentially (one at a time to manage memory)."""
    pdf_files = sorted(corpus_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning("No PDF files found in %s", corpus_dir)
        return []
    results = []
    for pdf_path in pdf_files:
        result = await ingest_pdf(
            pdf_path,
            db_path=db_path,
            case_ref=case_ref,
            case_name=case_name,
            force=force,
        )
        results.append(result)
    return results
