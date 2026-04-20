from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image

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
from operation_lens_v2.models import Chunk


@dataclass(slots=True)
class ImageIngestConfig:
    ocr_lang: str = "eng"
    min_chars: int = 20
    thumbnail_size: tuple[int, int] = (320, 320)


async def ingest_image(
    image_path: Path,
    *,
    db_path: str | None = None,
    case_ref: str = "UNASSIGNED",
    case_name: str | None = None,
    config: ImageIngestConfig | None = None,
) -> dict[str, int | str | bool]:
    """Ingest one standalone image as a single-page document."""
    config = config or ImageIngestConfig()
    db_path = db_path or settings.duckdb_path
    con = duck_store.init_db(db_path)
    lance = vector_store.VectorStore(settings.lancedb_path)

    resolved_case_name = case_name or case_ref.replace("_", " ").title()
    case_id = duck_store.create_case(con, case_ref=case_ref, case_name=resolved_case_name)
    doc_id = str(uuid4())
    event_id = str(uuid4())
    started_at = datetime.now(timezone.utc)

    with Image.open(image_path) as img:
        image_copy = img.copy()
        thumb = image_copy.copy()
        thumb.thumbnail(config.thumbnail_size)
        thumb_bytes_io = BytesIO()
        thumb.save(thumb_bytes_io, format="PNG")
        thumbnail_blob = thumb_bytes_io.getvalue()
        ocr_text = extractor.ocr_image(image_copy, doc_id=doc_id, page_no=1, lang=config.ocr_lang)

    ocr_failed = len(ocr_text.strip()) < config.min_chars
    page_text = ocr_text if ocr_text.strip() else image_path.name
    chunk_token_count = chunker.count_tokens(page_text)
    chunk = Chunk(
        chunk_id=str(uuid4()),
        doc_id=doc_id,
        page=1,
        chunk_index=0,
        text=page_text,
        token_count=chunk_token_count,
    )

    duck_store.upsert_document(
        con,
        doc_id=doc_id,
        filename=image_path.name,
        filepath=str(image_path),
        page_count=1,
        ocr_used=True,
        case_id=case_id,
        doc_format="image",
        thumbnail_blob=thumbnail_blob,
        ocr_failed=ocr_failed,
    )
    duck_store.insert_chunks(con, [chunk])

    general_entities = ner_gliner.extract_general_entities(chunk.text)
    rule_entities = ner_rules.extract_rule_entities(chunk.text)
    llm_entities = await ner_llm.extract_llm_entities(
        chunk.text,
        existing_entities=rule_entities + general_entities,
    )
    merged_entities = [*rule_entities, *general_entities, *llm_entities]
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

    relationships = await relationship_extractor.extract_relationships(chunk.text, merged_entities)
    rel_count = 0
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
            page=1,
            rel=rel,
        )
        rel_count += 1

    vector = await embedder.embed_text(chunk.text)
    lance.upsert(
        [
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": doc_id,
                "page": 1,
                "text": chunk.text,
                "vector": vector,
            }
        ]
    )

    duck_store.record_ingestion_event(
        con,
        event_id=event_id,
        case_id=case_id,
        doc_id=doc_id,
        source_path=str(image_path),
        source_type="image",
        status="ocr_failed" if ocr_failed else "success",
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        duration_ms=0,
        pages=1,
        chunks=1,
        entities_new=duck_store.count_entities_first_seen_in(con, doc_id),
        relationships_new=duck_store.count_relationships_evidenced_in(con, doc_id),
        ocr_used=True,
    )

    return {
        "doc_id": doc_id,
        "case_ref": case_ref,
        "pages": 1,
        "chunks": 1,
        "relationships": rel_count,
        "ocr_failed": ocr_failed,
        "event_id": event_id,
        "format": "image",
    }
