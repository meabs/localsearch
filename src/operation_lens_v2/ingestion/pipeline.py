from __future__ import annotations

import logging
from pathlib import Path
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
            return {"doc_id": existing[0], "case_ref": case_ref, "skipped": 1}

    doc_id = str(uuid4())
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
