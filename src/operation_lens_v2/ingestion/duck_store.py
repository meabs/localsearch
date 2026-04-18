from __future__ import annotations

import logging
import json
from pathlib import Path
from uuid import uuid4

import duckdb

from operation_lens_v2.models import Chunk, RelationshipCandidate

logger = logging.getLogger(__name__)
_fts_extension_ready = False


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cases (
  case_id TEXT PRIMARY KEY,
  case_ref TEXT UNIQUE NOT NULL,
  case_name TEXT NOT NULL,
  domain_pack TEXT DEFAULT 'base',
  schema_overrides_json TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY,
  case_id TEXT REFERENCES cases(case_id),
  filename TEXT NOT NULL,
  filepath TEXT NOT NULL,
  classification TEXT DEFAULT 'OFFICIAL',
  page_count INTEGER,
  source_type TEXT DEFAULT 'pdf',
  source_metadata TEXT,
  parser_name TEXT,
  content_hash TEXT,
  perceptual_hash TEXT,
  ingested_at TIMESTAMP DEFAULT now(),
  ocr_used BOOLEAN DEFAULT false
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,
  doc_id TEXT REFERENCES documents(doc_id),
  page INTEGER NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  source_label TEXT,
  provenance_type TEXT DEFAULT 'native_text',
  token_count INTEGER,
  ingested_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entities (
  entity_id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  first_seen_doc TEXT REFERENCES documents(doc_id),
  mention_count INTEGER DEFAULT 1,
  confidence FLOAT DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS entity_aliases (
  alias_id TEXT PRIMARY KEY,
  entity_id TEXT REFERENCES entities(entity_id),
  alias_text TEXT NOT NULL,
  source_doc TEXT REFERENCES documents(doc_id),
  source_chunk TEXT REFERENCES chunks(chunk_id)
);

CREATE TABLE IF NOT EXISTS relationships (
  rel_id TEXT PRIMARY KEY,
  source_entity TEXT REFERENCES entities(entity_id),
  target_entity TEXT REFERENCES entities(entity_id),
  relation_type TEXT NOT NULL,
  confidence FLOAT NOT NULL,
  first_evidenced TIMESTAMP DEFAULT now(),
  event_time TIMESTAMP,
  valid_from TIMESTAMP,
  valid_to TIMESTAMP
);

CREATE TABLE IF NOT EXISTS relationship_evidence (
  evidence_id TEXT PRIMARY KEY,
  rel_id TEXT REFERENCES relationships(rel_id),
  chunk_id TEXT REFERENCES chunks(chunk_id),
  doc_id TEXT REFERENCES documents(doc_id),
  page INTEGER NOT NULL,
  span_start INTEGER,
  span_end INTEGER,
  span_text TEXT NOT NULL,
  extraction_method TEXT,
  event_time TIMESTAMP
);

CREATE TABLE IF NOT EXISTS queries (
  query_id TEXT PRIMARY KEY,
  query_text TEXT NOT NULL,
  intent TEXT,
  llm_backend TEXT,
  submitted_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS answer_spans (
  span_id TEXT PRIMARY KEY,
  query_id TEXT REFERENCES queries(query_id),
  claim_text TEXT NOT NULL,
  supporting_evidence TEXT[],
  confidence FLOAT,
  validated BOOLEAN
);

CREATE TABLE IF NOT EXISTS evidence_attachments (
  attachment_id TEXT PRIMARY KEY,
  doc_id TEXT REFERENCES documents(doc_id),
  filename TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  file_size BIGINT NOT NULL,
  metadata_json TEXT,
  ingested_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_facts (
  fact_id TEXT PRIMARY KEY,
  doc_id TEXT REFERENCES documents(doc_id),
  chunk_id TEXT,
  fact_type TEXT NOT NULL,
  fact_value TEXT NOT NULL,
  provenance_type TEXT NOT NULL,
  source_label TEXT,
  confidence FLOAT DEFAULT 1.0,
  metadata_json TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_parser_warnings (
  warning_id TEXT PRIMARY KEY,
  doc_id TEXT REFERENCES documents(doc_id),
  parser_name TEXT NOT NULL,
  warning_code TEXT NOT NULL,
  warning_message TEXT NOT NULL,
  metadata_json TEXT,
  created_at TIMESTAMP DEFAULT now()
);
"""


def connect(path: str) -> duckdb.DuckDBPyConnection:
    global _fts_extension_ready
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    if not _fts_extension_ready:
        con.execute("INSTALL fts;")
        _fts_extension_ready = True
    con.execute("LOAD fts;")
    return con


def init_db(path: str) -> duckdb.DuckDBPyConnection:
    con = connect(path)
    con.execute(SCHEMA_SQL)
    _ensure_case_columns(con)
    _ensure_document_columns(con)
    _ensure_chunk_columns(con)
    _ensure_geocode_columns(con)
    _ensure_attachments_table(con)
    _ensure_evidence_tables(con)
    _ensure_temporal_columns(con)
    _ensure_graph_indexes(con)
    try:
        con.execute("PRAGMA create_fts_index('chunks', 'chunk_id', 'text');")
    except duckdb.CatalogException:
        # Keep init idempotent across repeated ingest calls.
        pass
    logger.info("DuckDB initialized at %s", path)
    return con


def _ensure_case_columns(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in (
        "ALTER TABLE documents ADD COLUMN case_id TEXT;",
        "ALTER TABLE cases ADD COLUMN domain_pack TEXT DEFAULT 'base';",
        "ALTER TABLE cases ADD COLUMN schema_overrides_json TEXT;",
    ):
        try:
            con.execute(ddl)
        except duckdb.CatalogException:
            pass


def _ensure_document_columns(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in (
        "ALTER TABLE documents ADD COLUMN source_type TEXT DEFAULT 'pdf';",
        "ALTER TABLE documents ADD COLUMN source_metadata TEXT;",
        "ALTER TABLE documents ADD COLUMN parser_name TEXT;",
        "ALTER TABLE documents ADD COLUMN content_hash TEXT;",
        "ALTER TABLE documents ADD COLUMN perceptual_hash TEXT;",
    ):
        try:
            con.execute(ddl)
        except duckdb.CatalogException:
            pass


def _ensure_chunk_columns(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in (
        "ALTER TABLE chunks ADD COLUMN source_label TEXT;",
        "ALTER TABLE chunks ADD COLUMN provenance_type TEXT DEFAULT 'native_text';",
    ):
        try:
            con.execute(ddl)
        except duckdb.CatalogException:
            pass


def _ensure_geocode_columns(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in (
        "ALTER TABLE entities ADD COLUMN latitude DOUBLE;",
        "ALTER TABLE entities ADD COLUMN longitude DOUBLE;",
        "ALTER TABLE entities ADD COLUMN geocode_provider TEXT;",
        "ALTER TABLE entities ADD COLUMN geocode_display_name TEXT;",
        "ALTER TABLE entities ADD COLUMN geocoded_at TIMESTAMP;",
    ):
        try:
            con.execute(ddl)
        except duckdb.CatalogException:
            pass


def _ensure_temporal_columns(con: duckdb.DuckDBPyConnection) -> None:
    # event_time carries a per-edge moment when the supporting evidence places
    # the interaction in time. Populated lazily — nullable so existing rows
    # keep working until the Phase 9 temporal extractor backfills.
    for ddl in (
        "ALTER TABLE relationships ADD COLUMN event_time TIMESTAMP;",
        "ALTER TABLE relationships ADD COLUMN valid_from TIMESTAMP;",
        "ALTER TABLE relationships ADD COLUMN valid_to TIMESTAMP;",
        "ALTER TABLE relationship_evidence ADD COLUMN event_time TIMESTAMP;",
    ):
        try:
            con.execute(ddl)
        except duckdb.CatalogException:
            pass


def _ensure_graph_indexes(con: duckdb.DuckDBPyConnection) -> None:
    # Traversal hot paths hit source_entity/target_entity on every hop. DuckDB
    # has no separate CREATE INDEX pragma for these in older versions, so wrap
    # each individually.
    for ddl in (
        "CREATE INDEX IF NOT EXISTS rel_source_idx ON relationships(source_entity);",
        "CREATE INDEX IF NOT EXISTS rel_target_idx ON relationships(target_entity);",
        "CREATE INDEX IF NOT EXISTS rel_evidence_rel_idx ON relationship_evidence(rel_id);",
        "CREATE INDEX IF NOT EXISTS entity_aliases_entity_idx ON entity_aliases(entity_id);",
    ):
        try:
            con.execute(ddl)
        except duckdb.CatalogException:
            pass
        except duckdb.ParserException:
            # Older DuckDB builds reject `IF NOT EXISTS` on indexes.
            base = ddl.replace("IF NOT EXISTS ", "")
            try:
                con.execute(base)
            except duckdb.CatalogException:
                pass


def _ensure_attachments_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_attachments (
          attachment_id TEXT PRIMARY KEY,
          entity_id TEXT REFERENCES entities(entity_id),
          filename TEXT NOT NULL,
          mime_type TEXT NOT NULL,
          file_size BIGINT NOT NULL,
          caption TEXT,
          storage_path TEXT NOT NULL,
          uploaded_at TIMESTAMP DEFAULT now()
        );
        """
    )


def _ensure_evidence_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_attachments (
          attachment_id TEXT PRIMARY KEY,
          doc_id TEXT REFERENCES documents(doc_id),
          filename TEXT NOT NULL,
          mime_type TEXT NOT NULL,
          file_size BIGINT NOT NULL,
          metadata_json TEXT,
          ingested_at TIMESTAMP DEFAULT now()
        );
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS document_facts (
          fact_id TEXT PRIMARY KEY,
          doc_id TEXT REFERENCES documents(doc_id),
          chunk_id TEXT,
          fact_type TEXT NOT NULL,
          fact_value TEXT NOT NULL,
          provenance_type TEXT NOT NULL,
          source_label TEXT,
          confidence FLOAT DEFAULT 1.0,
          metadata_json TEXT,
          created_at TIMESTAMP DEFAULT now()
        );
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS document_parser_warnings (
          warning_id TEXT PRIMARY KEY,
          doc_id TEXT REFERENCES documents(doc_id),
          parser_name TEXT NOT NULL,
          warning_code TEXT NOT NULL,
          warning_message TEXT NOT NULL,
          metadata_json TEXT,
          created_at TIMESTAMP DEFAULT now()
        );
        """
    )


def create_attachment(
    con: duckdb.DuckDBPyConnection,
    *,
    entity_id: str,
    filename: str,
    mime_type: str,
    file_size: int,
    storage_path: str,
    caption: str | None = None,
) -> str:
    attachment_id = str(uuid4())
    con.execute(
        """
        INSERT INTO entity_attachments
          (attachment_id, entity_id, filename, mime_type, file_size, caption, storage_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [attachment_id, entity_id, filename, mime_type, file_size, caption, storage_path],
    )
    return attachment_id


def list_attachments(
    con: duckdb.DuckDBPyConnection, entity_id: str
) -> list[dict[str, object]]:
    rows = con.execute(
        """
        SELECT attachment_id, filename, mime_type, file_size, caption, uploaded_at
        FROM entity_attachments
        WHERE entity_id = ?
        ORDER BY uploaded_at DESC
        """,
        [entity_id],
    ).fetchall()
    return [
        {
            "attachment_id": row[0],
            "filename": row[1],
            "mime_type": row[2],
            "file_size": int(row[3]),
            "caption": row[4],
            "uploaded_at": str(row[5]) if row[5] is not None else None,
        }
        for row in rows
    ]


def get_attachment(
    con: duckdb.DuckDBPyConnection, attachment_id: str
) -> dict[str, object] | None:
    row = con.execute(
        """
        SELECT attachment_id, entity_id, filename, mime_type, file_size,
               caption, storage_path, uploaded_at
        FROM entity_attachments
        WHERE attachment_id = ?
        """,
        [attachment_id],
    ).fetchone()
    if not row:
        return None
    return {
        "attachment_id": row[0],
        "entity_id": row[1],
        "filename": row[2],
        "mime_type": row[3],
        "file_size": int(row[4]),
        "caption": row[5],
        "storage_path": row[6],
        "uploaded_at": str(row[7]) if row[7] is not None else None,
    }


def delete_attachment(con: duckdb.DuckDBPyConnection, attachment_id: str) -> None:
    con.execute(
        "DELETE FROM entity_attachments WHERE attachment_id = ?",
        [attachment_id],
    )


def set_entity_geocode(
    con: duckdb.DuckDBPyConnection,
    *,
    entity_id: str,
    latitude: float,
    longitude: float,
    provider: str,
    display_name: str | None = None,
) -> None:
    con.execute(
        """
        UPDATE entities
        SET latitude = ?, longitude = ?, geocode_provider = ?,
            geocode_display_name = ?, geocoded_at = now()
        WHERE entity_id = ?
        """,
        [latitude, longitude, provider, display_name, entity_id],
    )


def get_entity_geocode(
    con: duckdb.DuckDBPyConnection, entity_id: str
) -> dict[str, object] | None:
    row = con.execute(
        """
        SELECT latitude, longitude, geocode_provider, geocode_display_name, geocoded_at
        FROM entities WHERE entity_id = ?
        """,
        [entity_id],
    ).fetchone()
    if not row or row[0] is None or row[1] is None:
        return None
    return {
        "latitude": float(row[0]),
        "longitude": float(row[1]),
        "provider": row[2],
        "display_name": row[3],
        "geocoded_at": str(row[4]) if row[4] is not None else None,
    }


def create_case(
    con: duckdb.DuckDBPyConnection,
    *,
    case_ref: str,
    case_name: str,
    domain_pack: str = "base",
    schema_overrides_json: str | None = None,
) -> str:
    existing = con.execute(
        "SELECT case_id FROM cases WHERE lower(case_ref)=lower(?)",
        [case_ref],
    ).fetchone()
    if existing:
        con.execute(
            """
            UPDATE cases
            SET case_name = coalesce(?, case_name),
                domain_pack = coalesce(?, domain_pack),
                schema_overrides_json = coalesce(?, schema_overrides_json)
            WHERE case_id = ?
            """,
            [case_name, domain_pack, schema_overrides_json, existing[0]],
        )
        return existing[0]
    case_id = str(uuid4())
    con.execute(
        """
        INSERT INTO cases (case_id, case_ref, case_name, domain_pack, schema_overrides_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        [case_id, case_ref, case_name, domain_pack, schema_overrides_json],
    )
    return case_id


def list_cases(con: duckdb.DuckDBPyConnection) -> list[dict[str, str]]:
    rows = con.execute(
        """
        SELECT c.case_id, c.case_ref, c.case_name, c.domain_pack, count(d.doc_id) as doc_count
        FROM cases c
        LEFT JOIN documents d ON d.case_id = c.case_id
        GROUP BY c.case_id, c.case_ref, c.case_name, c.domain_pack
        ORDER BY c.case_ref
        """
    ).fetchall()
    return [
        {
            "case_id": row[0],
            "case_ref": row[1],
            "case_name": row[2],
            "domain_pack": row[3] or "base",
            "doc_count": str(row[4]),
        }
        for row in rows
    ]


def list_case_documents(
    con: duckdb.DuckDBPyConnection,
    *,
    case_ref: str,
) -> list[dict[str, str]]:
    rows = con.execute(
        """
        SELECT
          d.doc_id,
          d.filename,
          d.filepath,
          coalesce(d.page_count, 0) AS page_count,
          coalesce(d.source_type, 'pdf') AS source_type,
          d.ingested_at
        FROM documents d
        JOIN cases c ON c.case_id = d.case_id
        WHERE lower(c.case_ref) = lower(?)
        ORDER BY d.ingested_at DESC, d.filename
        """,
        [case_ref],
    ).fetchall()
    return [
        {
            "doc_id": row[0],
            "filename": row[1],
            "filepath": row[2],
            "page_count": str(row[3]),
            "source_type": row[4],
            "ingested_at": str(row[5]),
        }
        for row in rows
    ]


def get_case_id_by_ref(con: duckdb.DuckDBPyConnection, case_ref: str) -> str | None:
    row = con.execute(
        "SELECT case_id FROM cases WHERE lower(case_ref)=lower(?)",
        [case_ref],
    ).fetchone()
    return row[0] if row else None


def get_case_by_ref(con: duckdb.DuckDBPyConnection, case_ref: str) -> dict[str, object] | None:
    row = con.execute(
        """
        SELECT case_id, case_ref, case_name, coalesce(domain_pack, 'base'), schema_overrides_json
        FROM cases
        WHERE lower(case_ref)=lower(?)
        """,
        [case_ref],
    ).fetchone()
    if not row:
        return None
    overrides = None
    if row[4]:
        try:
            overrides = json.loads(row[4])
        except json.JSONDecodeError:
            overrides = None
    return {
        "case_id": row[0],
        "case_ref": row[1],
        "case_name": row[2],
        "domain_pack": row[3] or "base",
        "schema_overrides": overrides,
    }


def set_case_domain_pack(
    con: duckdb.DuckDBPyConnection,
    *,
    case_ref: str,
    domain_pack: str,
    schema_overrides: dict[str, object] | None = None,
) -> dict[str, object] | None:
    schema_overrides_json = json.dumps(schema_overrides) if schema_overrides else None
    con.execute(
        """
        UPDATE cases
        SET domain_pack = ?, schema_overrides_json = ?
        WHERE lower(case_ref)=lower(?)
        """,
        [domain_pack, schema_overrides_json, case_ref],
    )
    return get_case_by_ref(con, case_ref)


def get_doc_ids_for_case(con: duckdb.DuckDBPyConnection, case_id: str) -> set[str]:
    rows = con.execute("SELECT doc_id FROM documents WHERE case_id = ?", [case_id]).fetchall()
    return {row[0] for row in rows}


def find_document_by_fingerprint(
    con: duckdb.DuckDBPyConnection,
    *,
    case_id: str,
    content_hash: str | None = None,
    perceptual_hash: str | None = None,
) -> str | None:
    if content_hash:
        row = con.execute(
            """
            SELECT doc_id
            FROM documents
            WHERE case_id = ? AND content_hash = ?
            """,
            [case_id, content_hash],
        ).fetchone()
        if row:
            return row[0]
    if perceptual_hash:
        row = con.execute(
            """
            SELECT doc_id
            FROM documents
            WHERE case_id = ? AND perceptual_hash = ?
            """,
            [case_id, perceptual_hash],
        ).fetchone()
        if row:
            return row[0]
    return None


def db_health(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    con.execute("SELECT 1;").fetchone()
    table_count = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='main';"
    ).fetchone()[0]
    return {"status": "ok", "tables": str(table_count)}


def upsert_document(
    con: duckdb.DuckDBPyConnection,
    *,
    doc_id: str,
    filename: str,
    filepath: str,
    page_count: int,
    ocr_used: bool,
    case_id: str | None = None,
    source_type: str = "pdf",
    source_metadata: dict[str, object] | None = None,
    parser_name: str | None = None,
    content_hash: str | None = None,
    perceptual_hash: str | None = None,
) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO documents (
          doc_id, case_id, filename, filepath, page_count, ocr_used,
          source_type, source_metadata, parser_name, content_hash, perceptual_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            doc_id,
            case_id,
            filename,
            filepath,
            page_count,
            ocr_used,
            source_type,
            json.dumps(source_metadata or {}),
            parser_name,
            content_hash,
            perceptual_hash,
        ],
    )


def insert_chunks(con: duckdb.DuckDBPyConnection, chunks: list[Chunk]) -> None:
    for chunk in chunks:
        con.execute(
            """
            INSERT OR REPLACE INTO chunks (
              chunk_id, doc_id, page, chunk_index, text, source_label, provenance_type, token_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                chunk.chunk_id,
                chunk.doc_id,
                chunk.page,
                chunk.chunk_index,
                chunk.text,
                chunk.source_label,
                chunk.provenance_type,
                chunk.token_count,
            ],
        )


def get_entities_by_type(con: duckdb.DuckDBPyConnection, entity_type: str) -> list[tuple[str, str]]:
    return con.execute(
        "SELECT entity_id, canonical_name FROM entities WHERE entity_type = ?",
        [entity_type],
    ).fetchall()


def create_entity(
    con: duckdb.DuckDBPyConnection,
    canonical_name: str,
    entity_type: str,
    first_seen_doc: str,
    confidence: float = 1.0,
) -> str:
    entity_id = str(uuid4())
    con.execute(
        """
        INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        [entity_id, canonical_name, entity_type, first_seen_doc, confidence],
    )
    return entity_id


def register_alias(
    con: duckdb.DuckDBPyConnection,
    *,
    entity_id: str,
    alias_text: str,
    source_doc: str,
    source_chunk: str,
) -> None:
    con.execute(
        """
        INSERT INTO entity_aliases (alias_id, entity_id, alias_text, source_doc, source_chunk)
        VALUES (?, ?, ?, ?, ?)
        """,
        [str(uuid4()), entity_id, alias_text, source_doc, source_chunk],
    )
    con.execute(
        "UPDATE entities SET mention_count = mention_count + 1 WHERE entity_id = ?",
        [entity_id],
    )


def create_relationship(
    con: duckdb.DuckDBPyConnection,
    *,
    source_entity: str,
    target_entity: str,
    relation_type: str,
    confidence: float,
    event_time: object | None = None,
) -> str:
    rel_id = str(uuid4())
    con.execute(
        """
        INSERT INTO relationships (
          rel_id, source_entity, target_entity, relation_type, confidence, event_time
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [rel_id, source_entity, target_entity, relation_type, confidence, event_time],
    )
    return rel_id


def insert_relationship_evidence(
    con: duckdb.DuckDBPyConnection,
    *,
    rel_id: str,
    chunk_id: str,
    doc_id: str,
    page: int,
    rel: RelationshipCandidate,
    event_time: object | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO relationship_evidence (
          evidence_id, rel_id, chunk_id, doc_id, page,
          span_start, span_end, span_text, extraction_method, event_time
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(uuid4()),
            rel_id,
            chunk_id,
            doc_id,
            page,
            rel.span_start,
            rel.span_end,
            rel.span_text,
            rel.extraction_method,
            event_time,
        ],
    )


def insert_evidence_attachments(
    con: duckdb.DuckDBPyConnection,
    *,
    doc_id: str,
    attachments: list[dict[str, object]],
) -> None:
    for attachment in attachments:
        con.execute(
            """
            INSERT INTO evidence_attachments (
              attachment_id, doc_id, filename, mime_type, file_size, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                str(uuid4()),
                doc_id,
                attachment.get("filename", "attachment"),
                attachment.get("mime_type", "application/octet-stream"),
                int(attachment.get("file_size", 0) or 0),
                json.dumps(attachment.get("metadata", {}) or {}),
            ],
        )


def insert_document_facts(
    con: duckdb.DuckDBPyConnection,
    *,
    doc_id: str,
    facts: list[dict[str, object]],
) -> None:
    for fact in facts:
        con.execute(
            """
            INSERT INTO document_facts (
              fact_id, doc_id, chunk_id, fact_type, fact_value, provenance_type,
              source_label, confidence, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(uuid4()),
                doc_id,
                fact.get("chunk_id"),
                fact.get("fact_type", ""),
                fact.get("fact_value", ""),
                fact.get("provenance_type", "native_text"),
                fact.get("source_label"),
                float(fact.get("confidence", 1.0) or 1.0),
                json.dumps(fact.get("metadata", {}) or {}),
            ],
        )


def insert_parser_warnings(
    con: duckdb.DuckDBPyConnection,
    *,
    doc_id: str,
    parser_name: str,
    warnings: list[dict[str, object]],
) -> None:
    for warning in warnings:
        con.execute(
            """
            INSERT INTO document_parser_warnings (
              warning_id, doc_id, parser_name, warning_code, warning_message, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                str(uuid4()),
                doc_id,
                parser_name,
                warning.get("warning_code", "warning"),
                warning.get("message", ""),
                json.dumps(warning.get("metadata", {}) or {}),
            ],
        )
