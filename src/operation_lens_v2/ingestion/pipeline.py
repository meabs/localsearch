from __future__ import annotations

import hashlib
import logging
from pathlib import Path
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
from operation_lens_v2.ingestion.entity_schema import get_schema, use_schema
from operation_lens_v2.ingestion.parser_registry import registry

logger = logging.getLogger(__name__)


def _spans_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a


def _merge_entity_sources(*sources: list) -> list:
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def ingest_document(
    evidence_path: Path,
    *,
    db_path: str | None = None,
    case_ref: str = "UNASSIGNED",
    case_name: str | None = None,
    force: bool = False,
) -> dict[str, int | str]:
    db_path = db_path or settings.duckdb_path
    con = duck_store.init_db(db_path)
    vs = vector_store.VectorStore(settings.lancedb_path)
    existing_case = duck_store.get_case_by_ref(con, case_ref)
    resolved_case_name = case_name or case_ref.replace("_", " ").title()
    case_id = duck_store.create_case(
        con,
        case_ref=case_ref,
        case_name=resolved_case_name,
        domain_pack=str(existing_case["domain_pack"]) if existing_case else "base",
        schema_overrides_json=None,
    )
    case_info = duck_store.get_case_by_ref(con, case_ref) or {
        "case_id": case_id,
        "domain_pack": "base",
        "schema_overrides": None,
    }

    parser = registry.get_parser(evidence_path)
    if parser is None:
        raise ValueError(f"Unsupported evidence type: {evidence_path.suffix or evidence_path.name}")

    content_hash = _file_sha256(evidence_path)
    if not force:
        existing = con.execute(
            "SELECT doc_id FROM documents WHERE filepath = ? AND case_id = ?",
            [str(evidence_path), case_id],
        ).fetchone()
        if existing:
            return {"doc_id": existing[0], "case_ref": case_ref, "skipped": 1}

        duplicate = duck_store.find_document_by_fingerprint(
            con,
            case_id=case_id,
            content_hash=content_hash,
        )
        if duplicate:
            return {"doc_id": duplicate, "case_ref": case_ref, "skipped": 1}

    doc_id = str(uuid4())
    parsed = parser.parse(evidence_path, document_id=doc_id)
    if not force:
        duplicate = duck_store.find_document_by_fingerprint(
            con,
            case_id=case_id,
            content_hash=content_hash,
            perceptual_hash=parsed.perceptual_hash,
        )
        if duplicate:
            return {"doc_id": duplicate, "case_ref": case_ref, "skipped": 1}

    schema = get_schema(
        domain_pack=str(case_info.get("domain_pack", "base") or "base"),
        overrides=case_info.get("schema_overrides"),
    )
    with use_schema(schema):
        duck_store.upsert_document(
            con,
            doc_id=doc_id,
            filename=evidence_path.name,
            filepath=str(evidence_path),
            page_count=len(parsed.text_blocks),
            ocr_used=any(block.provenance_type == "ocr_text" for block in parsed.text_blocks),
            case_id=case_id,
            source_type=parsed.source_type,
            source_metadata=parsed.source_metadata,
            parser_name=parsed.parser_name,
            content_hash=content_hash,
            perceptual_hash=parsed.perceptual_hash,
        )
        duck_store.insert_evidence_attachments(
            con,
            doc_id=doc_id,
            attachments=[
                {
                    "filename": item.filename,
                    "mime_type": item.mime_type,
                    "file_size": item.file_size,
                    "metadata": item.metadata,
                }
                for item in parsed.attachments
            ],
        )
        duck_store.insert_document_facts(
            con,
            doc_id=doc_id,
            facts=[
                {
                    "fact_type": item.fact_type,
                    "fact_value": item.fact_value,
                    "provenance_type": item.provenance_type,
                    "source_label": item.source_label,
                    "confidence": item.confidence,
                    "metadata": item.metadata,
                }
                for item in parsed.derived_facts
            ],
        )
        duck_store.insert_parser_warnings(
            con,
            doc_id=doc_id,
            parser_name=parsed.parser_name,
            warnings=[
                {
                    "warning_code": item.warning_code,
                    "message": item.message,
                    "metadata": item.metadata,
                }
                for item in parsed.parser_warnings
            ],
        )

        chunks = chunker.chunk_text_blocks(
            doc_id,
            parsed.text_blocks,
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
                    "source_label": chunk.source_label,
                    "provenance_type": chunk.provenance_type,
                    "vector": vector,
                }
            )

        vs.upsert(vector_rows)

    return {
        "doc_id": doc_id,
        "case_ref": case_ref,
        "pages": len(parsed.text_blocks),
        "chunks": len(chunks),
        "relationships": relation_count,
        "source_type": parsed.source_type,
    }


async def ingest_pdf(
    pdf_path: Path,
    *,
    db_path: str | None = None,
    case_ref: str = "UNASSIGNED",
    case_name: str | None = None,
    force: bool = False,
) -> dict[str, int | str]:
    return await ingest_document(
        pdf_path,
        db_path=db_path,
        case_ref=case_ref,
        case_name=case_name,
        force=force,
    )


async def ingest_corpus(
    corpus_dir: Path,
    *,
    db_path: str | None = None,
    case_ref: str = "UNASSIGNED",
    case_name: str | None = None,
    force: bool = False,
) -> list[dict[str, int | str]]:
    files = sorted(path for path in corpus_dir.iterdir() if path.is_file() and registry.get_parser(path))
    results = []
    for evidence_path in files:
        result = await ingest_document(
            evidence_path,
            db_path=db_path,
            case_ref=case_ref,
            case_name=case_name,
            force=force,
        )
        results.append(result)
    return results
