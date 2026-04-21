from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pandas as pd

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import (
    duck_store,
    embedder,
    ner_gliner,
    ner_llm,
    ner_rules,
    normaliser,
    relationship_extractor,
)
from operation_lens_v2.models import Chunk


@dataclass(slots=True)
class CsvIngestConfig:
    text_columns: list[str] | None = None
    id_column: str | None = None
    max_rows: int | None = None
    delimiter: str | None = None


def _guess_delimiter(csv_path: Path) -> str:
    sample = csv_path.read_text(encoding="utf-8", errors="replace")[:4096]
    try:
        return csv.Sniffer().sniff(sample).delimiter
    except csv.Error:
        return ","


def _row_text(row: dict[str, str], columns: list[str] | None) -> str:
    selected = columns if columns else list(row.keys())
    parts = []
    for column in selected:
        value = str(row.get(column, "")).strip()
        if value:
            parts.append(f"{column}: {value}")
    return " | ".join(parts)


async def ingest_csv(
    csv_path: Path,
    case_ref: str,
    duck_con,
    lance_table,
    gliner_model,
    ollama_client,
    config: CsvIngestConfig,
) -> dict[str, int | str]:
    """Ingest one CSV/TSV file through the same entity/relationship pipeline."""
    delimiter = config.delimiter or _guess_delimiter(csv_path)
    frame = pd.read_csv(
        csv_path,
        sep=delimiter,
        dtype=str,
        keep_default_na=False,
        nrows=config.max_rows,
    )
    rows = frame.to_dict(orient="records")

    doc_id = str(uuid4())
    case_name = case_ref.replace("_", " ").title()
    case_id = duck_store.create_case(duck_con, case_ref=case_ref, case_name=case_name)
    duck_store.upsert_document(
        duck_con,
        doc_id=doc_id,
        filename=csv_path.name,
        filepath=str(csv_path),
        page_count=len(rows),
        ocr_used=False,
        case_id=case_id,
    )

    chunk_rows: list[dict[str, object]] = []
    relationship_count = 0
    for index, row in enumerate(rows, start=1):
        text = _row_text(row, config.text_columns)
        if not text:
            continue

        chunk_id = str(uuid5(NAMESPACE_URL, f"{doc_id}:{index}"))
        token_count = len(text.split())
        chunk = Chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            page=index,
            chunk_index=index - 1,
            text=text,
            token_count=token_count,
        )
        duck_store.insert_chunks(duck_con, [chunk])

        general_entities = ner_gliner.extract_general_entities(text)
        rule_entities = ner_rules.extract_rule_entities(text)
        llm_entities = await ner_llm.extract_llm_entities(
            text,
            existing_entities=rule_entities + general_entities,
        )
        merged_entities = [*rule_entities, *general_entities, *llm_entities]

        canonical_id_by_surface: dict[str, str] = {}
        for entity in merged_entities:
            resolution = normaliser.resolve_entity_with_provenance(
                surface=entity.text,
                entity_type=entity.entity_type,
                con=duck_con,
                source_doc=doc_id,
                source_chunk=chunk.chunk_id,
                threshold=settings.alias_threshold,
                confidence=float(getattr(entity, "confidence", 1.0) or 1.0),
            )
            canonical_id_by_surface[entity.text] = resolution.entity_id

        relationships = await relationship_extractor.extract_relationships(text, merged_entities)
        for rel in relationships:
            source_id = canonical_id_by_surface.get(rel.source)
            target_id = canonical_id_by_surface.get(rel.target)
            if not source_id or not target_id or source_id == target_id:
                continue
            rel_id = duck_store.create_relationship(
                duck_con,
                source_entity=source_id,
                target_entity=target_id,
                relation_type=rel.relation_type,
                confidence=rel.confidence,
            )
            duck_store.insert_relationship_evidence(
                duck_con,
                rel_id=rel_id,
                chunk_id=chunk.chunk_id,
                doc_id=doc_id,
                page=chunk.page,
                rel=rel,
            )
            relationship_count += 1

        vector = await embedder.embed_text(text)
        chunk_rows.append(
            {
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "page": index,
                "text": text,
                "vector": vector,
            }
        )

    if chunk_rows:
        lance_table.upsert(chunk_rows)

    return {
        "doc_id": doc_id,
        "case_ref": case_ref,
        "pages": len(rows),
        "chunks": len(chunk_rows),
        "relationships": relationship_count,
        "format": "csv",
    }
