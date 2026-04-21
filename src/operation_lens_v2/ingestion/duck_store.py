from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

import duckdb

from operation_lens_v2.models import Chunk, RelationshipCandidate

logger = logging.getLogger(__name__)


def _try_ddl(con: duckdb.DuckDBPyConnection, ddl: str, *, context: str) -> None:
    """Execute idempotent DDL — ``ALTER TABLE ADD COLUMN`` / ``CREATE INDEX``.

    DuckDB has no ``IF NOT EXISTS`` for ``ADD COLUMN`` and older builds reject
    it on ``CREATE INDEX``. We rely on the error to detect "already applied"
    rather than querying the schema first. Callers should only pass DDL whose
    failure is expected and safe; the error is logged at DEBUG with enough
    context to diagnose a genuine problem without flooding startup logs.
    """
    try:
        con.execute(ddl)
    except (duckdb.CatalogException, duckdb.TransactionException) as exc:
        logger.debug("DDL skipped (%s): %s — %s", context, ddl.strip(), exc)
        try:
            con.execute("ROLLBACK;")
        except duckdb.Error as rb_exc:
            logger.debug("ROLLBACK after %s DDL also failed: %s", context, rb_exc)
    except duckdb.ParserException as exc:
        # Older DuckDB builds reject `IF NOT EXISTS` on indexes — retry once.
        if "IF NOT EXISTS" in ddl:
            fallback = ddl.replace("IF NOT EXISTS ", "")
            logger.debug(
                "DDL parser rejected IF NOT EXISTS (%s); retrying without: %s — %s",
                context,
                fallback.strip(),
                exc,
            )
            try:
                con.execute(fallback)
            except duckdb.CatalogException as retry_exc:
                logger.debug("DDL fallback still rejected (%s): %s", context, retry_exc)
        else:
            logger.warning("DDL parse error (%s): %s — %s", context, ddl.strip(), exc)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cases (
  case_id TEXT PRIMARY KEY,
  case_ref TEXT UNIQUE NOT NULL,
  case_name TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY,
  case_id TEXT REFERENCES cases(case_id),
  filename TEXT NOT NULL,
  filepath TEXT NOT NULL,
  classification TEXT DEFAULT 'OFFICIAL',
  format TEXT DEFAULT 'pdf',
  page_count INTEGER,
  ingested_at TIMESTAMP DEFAULT now(),
  ocr_used BOOLEAN DEFAULT false,
  ocr_failed BOOLEAN DEFAULT false,
  thumbnail_blob BLOB
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,
  doc_id TEXT REFERENCES documents(doc_id),
  page INTEGER NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
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
"""


def connect(path: str) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection and ensure the FTS extension is loaded.

    INSTALL is idempotent at the DuckDB level — no process-wide flag needed,
    which also avoids a race across threads/processes sharing the database file.
    """
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("INSTALL fts;")
    except duckdb.Error as exc:
        logger.debug("INSTALL fts no-op/failure (likely already installed): %s", exc)
    con.execute("LOAD fts;")
    return con


def init_db(path: str) -> duckdb.DuckDBPyConnection:
    con = connect(path)
    con.execute(SCHEMA_SQL)
    _ensure_case_columns(con)
    _ensure_geocode_columns(con)
    _ensure_attachments_table(con)
    _ensure_temporal_columns(con)
    _ensure_ingestion_events_table(con)
    _ensure_entity_review_columns(con)
    _ensure_alias_resolution_columns(con)
    _ensure_graph_indexes(con)
    _ensure_document_columns(con)
    _ensure_document_page_quality_table(con)
    _try_ddl(
        con,
        "PRAGMA create_fts_index('chunks', 'chunk_id', 'text');",
        context="fts_index",
    )
    logger.info("DuckDB initialized at %s", path)
    return con


def _ensure_case_columns(con: duckdb.DuckDBPyConnection) -> None:
    _try_ddl(
        con,
        "ALTER TABLE documents ADD COLUMN case_id TEXT;",
        context="documents.case_id",
    )


def _ensure_geocode_columns(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in (
        "ALTER TABLE entities ADD COLUMN latitude DOUBLE;",
        "ALTER TABLE entities ADD COLUMN longitude DOUBLE;",
        "ALTER TABLE entities ADD COLUMN geocode_provider TEXT;",
        "ALTER TABLE entities ADD COLUMN geocode_display_name TEXT;",
        "ALTER TABLE entities ADD COLUMN geocoded_at TIMESTAMP;",
    ):
        _try_ddl(con, ddl, context="entities.geocode")


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
        _try_ddl(con, ddl, context="relationships.temporal")


def _ensure_document_columns(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in (
        "ALTER TABLE documents ADD COLUMN format TEXT DEFAULT 'pdf';",
        "ALTER TABLE documents ADD COLUMN ocr_failed BOOLEAN DEFAULT false;",
        "ALTER TABLE documents ADD COLUMN thumbnail_blob BLOB;",
        "ALTER TABLE documents ADD COLUMN source_hash TEXT;",
        "ALTER TABLE documents ADD COLUMN source_size_bytes BIGINT;",
        "ALTER TABLE documents ADD COLUMN source_mtime_ns BIGINT;",
        "ALTER TABLE documents ADD COLUMN supersedes_doc_id TEXT;",
    ):
        _try_ddl(con, ddl, context="documents.format")


def _ensure_document_page_quality_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS document_page_quality (
          doc_id TEXT REFERENCES documents(doc_id),
          page INTEGER NOT NULL,
          extraction_method TEXT,
          ocr_confidence FLOAT,
          needs_review BOOLEAN DEFAULT false,
          redaction_count INTEGER DEFAULT 0,
          evidence_gap BOOLEAN DEFAULT false,
          notes TEXT,
          PRIMARY KEY (doc_id, page)
        );
        """
    )


def _ensure_graph_indexes(con: duckdb.DuckDBPyConnection) -> None:
    # Traversal hot paths hit source_entity/target_entity on every hop.
    for ddl in (
        "CREATE INDEX IF NOT EXISTS rel_source_idx ON relationships(source_entity);",
        "CREATE INDEX IF NOT EXISTS rel_target_idx ON relationships(target_entity);",
        "CREATE INDEX IF NOT EXISTS rel_evidence_rel_idx ON relationship_evidence(rel_id);",
        "CREATE INDEX IF NOT EXISTS entity_aliases_entity_idx ON entity_aliases(entity_id);",
    ):
        _try_ddl(con, ddl, context="graph_index")


def _ensure_ingestion_events_table(con: duckdb.DuckDBPyConnection) -> None:
    """Persist one row per ingestion run so the Audit view can show history.

    Captures both success and failure paths; ``status`` distinguishes them.
    ``notes`` is a free-form JSON string so callers can stash source-specific
    extras (for example the email thread count) without a schema change.
    """
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_events (
          event_id TEXT PRIMARY KEY,
          case_id TEXT,
          doc_id TEXT,
          source_path TEXT,
          source_type TEXT,
          status TEXT,
          error_message TEXT,
          started_at TIMESTAMP,
          completed_at TIMESTAMP,
          duration_ms BIGINT,
          pages INTEGER,
          chunks INTEGER,
          entities_new INTEGER,
          relationships_new INTEGER,
          ocr_used BOOLEAN,
          notes TEXT
        );
        """
    )


def _ensure_entity_review_columns(con: duckdb.DuckDBPyConnection) -> None:
    """Columns that let operators confirm low-confidence entities from the Audit view."""
    for ddl in (
        "ALTER TABLE entities ADD COLUMN reviewed_at TIMESTAMP;",
        "ALTER TABLE entities ADD COLUMN reviewed_by TEXT;",
    ):
        _try_ddl(con, ddl, context="entities.review")


def _ensure_alias_resolution_columns(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in (
        "ALTER TABLE entity_aliases ADD COLUMN resolution_confidence FLOAT;",
        "ALTER TABLE entity_aliases ADD COLUMN resolution_method TEXT;",
        "ALTER TABLE entity_aliases ADD COLUMN matched_entity_id TEXT;",
    ):
        _try_ddl(con, ddl, context="entity_aliases.resolution")


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
) -> str:
    existing = con.execute(
        "SELECT case_id FROM cases WHERE lower(case_ref)=lower(?)",
        [case_ref],
    ).fetchone()
    if existing:
        return existing[0]
    case_id = str(uuid4())
    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, case_ref, case_name],
    )
    return case_id


def list_cases(con: duckdb.DuckDBPyConnection) -> list[dict[str, str]]:
    rows = con.execute(
        """
        SELECT c.case_id, c.case_ref, c.case_name, count(d.doc_id) as doc_count
        FROM cases c
        LEFT JOIN documents d ON d.case_id = c.case_id
        GROUP BY c.case_id, c.case_ref, c.case_name
        ORDER BY c.case_ref
        """
    ).fetchall()
    return [
        {"case_id": row[0], "case_ref": row[1], "case_name": row[2], "doc_count": str(row[3])}
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
            "ingested_at": str(row[4]),
        }
        for row in rows
    ]


def get_case_id_by_ref(con: duckdb.DuckDBPyConnection, case_ref: str) -> str | None:
    row = con.execute(
        "SELECT case_id FROM cases WHERE lower(case_ref)=lower(?)",
        [case_ref],
    ).fetchone()
    return row[0] if row else None


def get_doc_ids_for_case(con: duckdb.DuckDBPyConnection, case_id: str) -> set[str]:
    rows = con.execute("SELECT doc_id FROM documents WHERE case_id = ?", [case_id]).fetchall()
    return {row[0] for row in rows}


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
    doc_format: str = "pdf",
    ocr_failed: bool = False,
    thumbnail_blob: bytes | None = None,
    source_hash: str | None = None,
    source_size_bytes: int | None = None,
    source_mtime_ns: int | None = None,
    supersedes_doc_id: str | None = None,
) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO documents (
          doc_id, case_id, filename, filepath, format, page_count,
          ocr_used, ocr_failed, thumbnail_blob,
          source_hash, source_size_bytes, source_mtime_ns, supersedes_doc_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            doc_id,
            case_id,
            filename,
            filepath,
            doc_format,
            page_count,
            ocr_used,
            ocr_failed,
            thumbnail_blob,
            source_hash,
            source_size_bytes,
            source_mtime_ns,
            supersedes_doc_id,
        ],
    )


def upsert_page_quality(
    con: duckdb.DuckDBPyConnection,
    *,
    doc_id: str,
    page: int,
    extraction_method: str,
    ocr_confidence: float | None,
    needs_review: bool,
    redaction_count: int = 0,
    evidence_gap: bool = False,
    notes: str = "",
) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO document_page_quality (
          doc_id, page, extraction_method, ocr_confidence, needs_review,
          redaction_count, evidence_gap, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            doc_id,
            page,
            extraction_method,
            ocr_confidence,
            needs_review,
            redaction_count,
            evidence_gap,
            notes,
        ],
    )


def list_page_quality(
    con: duckdb.DuckDBPyConnection,
    doc_id: str,
) -> list[dict[str, object]]:
    rows = con.execute(
        """
        SELECT page, extraction_method, ocr_confidence, needs_review,
               redaction_count, evidence_gap, notes
        FROM document_page_quality
        WHERE doc_id = ?
        ORDER BY page
        """,
        [doc_id],
    ).fetchall()
    return [
        {
            "page": int(row[0]),
            "extraction_method": row[1],
            "ocr_confidence": float(row[2]) if row[2] is not None else None,
            "needs_review": bool(row[3]),
            "redaction_count": int(row[4] or 0),
            "evidence_gap": bool(row[5]),
            "notes": row[6] or "",
        }
        for row in rows
    ]


def insert_chunks(con: duckdb.DuckDBPyConnection, chunks: list[Chunk]) -> None:
    for chunk in chunks:
        con.execute(
            """
            INSERT OR REPLACE INTO chunks (chunk_id, doc_id, page, chunk_index, text, token_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                chunk.chunk_id,
                chunk.doc_id,
                chunk.page,
                chunk.chunk_index,
                chunk.text,
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


def bump_entity_confidence(
    con: duckdb.DuckDBPyConnection,
    entity_id: str,
    confidence: float,
) -> None:
    """Raise an entity's stored confidence if this mention is stronger.

    Uses ``max`` rather than overwrite so a single high-confidence re-
    mention can lift an entity out of the low-confidence review queue,
    while a later weak mention never drags a confirmed entity back down.
    """
    con.execute(
        "UPDATE entities SET confidence = greatest(confidence, ?) WHERE entity_id = ?",
        [float(confidence), entity_id],
    )


def register_alias(
    con: duckdb.DuckDBPyConnection,
    *,
    entity_id: str,
    alias_text: str,
    source_doc: str,
    source_chunk: str,
    resolution_confidence: float | None = None,
    resolution_method: str | None = None,
    matched_entity_id: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO entity_aliases (
          alias_id, entity_id, alias_text, source_doc, source_chunk,
          resolution_confidence, resolution_method, matched_entity_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(uuid4()),
            entity_id,
            alias_text,
            source_doc,
            source_chunk,
            resolution_confidence,
            resolution_method,
            matched_entity_id,
        ],
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


def record_ingestion_event(
    con: duckdb.DuckDBPyConnection,
    *,
    event_id: str,
    case_id: str | None,
    doc_id: str | None,
    source_path: str,
    source_type: str,
    status: str,
    started_at,
    completed_at,
    duration_ms: int,
    pages: int | None = None,
    chunks: int | None = None,
    entities_new: int | None = None,
    relationships_new: int | None = None,
    ocr_used: bool | None = None,
    error_message: str | None = None,
    notes: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO ingestion_events (
          event_id, case_id, doc_id, source_path, source_type, status,
          error_message, started_at, completed_at, duration_ms,
          pages, chunks, entities_new, relationships_new, ocr_used, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            event_id,
            case_id,
            doc_id,
            source_path,
            source_type,
            status,
            error_message,
            started_at,
            completed_at,
            duration_ms,
            pages,
            chunks,
            entities_new,
            relationships_new,
            ocr_used,
            notes,
        ],
    )


_INGESTION_EVENT_COLUMNS = (
    "e.event_id, e.case_id, c.case_ref, c.case_name, e.doc_id, d.filename, "
    "e.source_path, e.source_type, e.status, e.error_message, "
    "e.started_at, e.completed_at, e.duration_ms, e.pages, e.chunks, "
    "e.entities_new, e.relationships_new, e.ocr_used, e.notes"
)


def _row_to_ingestion_event(row) -> dict[str, object]:
    return {
        "event_id": row[0],
        "case_id": row[1],
        "case_ref": row[2],
        "case_name": row[3],
        "doc_id": row[4],
        "filename": row[5],
        "source_path": row[6],
        "source_type": row[7],
        "status": row[8],
        "error_message": row[9],
        "started_at": str(row[10]) if row[10] is not None else None,
        "completed_at": str(row[11]) if row[11] is not None else None,
        "duration_ms": int(row[12]) if row[12] is not None else None,
        "pages": int(row[13]) if row[13] is not None else None,
        "chunks": int(row[14]) if row[14] is not None else None,
        "entities_new": int(row[15]) if row[15] is not None else None,
        "relationships_new": int(row[16]) if row[16] is not None else None,
        "ocr_used": bool(row[17]) if row[17] is not None else None,
        "notes": row[18],
    }


def list_ingestion_events(
    con: duckdb.DuckDBPyConnection,
    *,
    case_ref: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    clauses: list[str] = []
    params: list[object] = []
    if case_ref:
        clauses.append("lower(c.case_ref) = lower(?)")
        params.append(case_ref)
    if status:
        clauses.append("e.status = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, limit))
    rows = con.execute(
        f"""
        SELECT {_INGESTION_EVENT_COLUMNS}
        FROM ingestion_events e
        LEFT JOIN cases c ON c.case_id = e.case_id
        LEFT JOIN documents d ON d.doc_id = e.doc_id
        {where_sql}
        ORDER BY e.started_at DESC NULLS LAST
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_row_to_ingestion_event(row) for row in rows]


def get_ingestion_event(
    con: duckdb.DuckDBPyConnection,
    event_id: str,
) -> dict[str, object] | None:
    row = con.execute(
        f"""
        SELECT {_INGESTION_EVENT_COLUMNS}
        FROM ingestion_events e
        LEFT JOIN cases c ON c.case_id = e.case_id
        LEFT JOIN documents d ON d.doc_id = e.doc_id
        WHERE e.event_id = ?
        """,
        [event_id],
    ).fetchone()
    return _row_to_ingestion_event(row) if row else None


def get_latest_ingestion_for_doc(
    con: duckdb.DuckDBPyConnection,
    doc_id: str,
) -> dict[str, object] | None:
    row = con.execute(
        f"""
        SELECT {_INGESTION_EVENT_COLUMNS}
        FROM ingestion_events e
        LEFT JOIN cases c ON c.case_id = e.case_id
        LEFT JOIN documents d ON d.doc_id = e.doc_id
        WHERE e.doc_id = ?
        ORDER BY e.started_at DESC NULLS LAST
        LIMIT 1
        """,
        [doc_id],
    ).fetchone()
    return _row_to_ingestion_event(row) if row else None


def count_entities_first_seen_in(
    con: duckdb.DuckDBPyConnection,
    doc_id: str,
) -> int:
    """How many canonical entities were created while ingesting ``doc_id``.

    Other documents can reuse these entities later via alias resolution, so
    this is the correct "new entities" figure for an ingestion event — it
    excludes matches against entities established by earlier ingestions.
    """
    row = con.execute(
        "SELECT count(*) FROM entities WHERE first_seen_doc = ?",
        [doc_id],
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def count_relationships_evidenced_in(
    con: duckdb.DuckDBPyConnection,
    doc_id: str,
) -> int:
    """How many distinct relationships this document contributes evidence to."""
    row = con.execute(
        """
        SELECT count(DISTINCT rel_id)
        FROM relationship_evidence
        WHERE doc_id = ?
        """,
        [doc_id],
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def list_entities_for_doc(
    con: duckdb.DuckDBPyConnection,
    doc_id: str,
    *,
    low_confidence_threshold: float,
) -> list[dict[str, object]]:
    """Return every entity attested by aliases in ``doc_id``.

    ``mentions_in_doc`` counts how many aliases in this document resolved to
    the entity, which is a better "how strongly does this ingestion back it?"
    signal than the global ``mention_count`` (which accumulates across the
    whole corpus).
    """
    rows = con.execute(
        """
        SELECT
          e.entity_id,
          e.canonical_name,
          e.entity_type,
          e.confidence,
          e.mention_count,
          e.reviewed_at,
          e.reviewed_by,
          count(ea.alias_id) AS mentions_in_doc
        FROM entity_aliases ea
        JOIN entities e ON e.entity_id = ea.entity_id
        WHERE ea.source_doc = ?
        GROUP BY e.entity_id, e.canonical_name, e.entity_type, e.confidence,
                 e.mention_count, e.reviewed_at, e.reviewed_by
        ORDER BY e.confidence ASC, mentions_in_doc DESC, e.canonical_name
        """,
        [doc_id],
    ).fetchall()
    return [
        {
            "entity_id": row[0],
            "canonical_name": row[1],
            "entity_type": row[2],
            "confidence": float(row[3]) if row[3] is not None else 0.0,
            "mention_count": int(row[4] or 0),
            "reviewed_at": str(row[5]) if row[5] is not None else None,
            "reviewed_by": row[6],
            "mentions_in_doc": int(row[7] or 0),
            "low_confidence": (
                row[5] is None
                and row[3] is not None
                and float(row[3]) < low_confidence_threshold
            ),
        }
        for row in rows
    ]


def confirm_entity(
    con: duckdb.DuckDBPyConnection,
    entity_id: str,
    *,
    reviewed_by: str | None = None,
) -> dict[str, object] | None:
    """Mark an entity as human-confirmed and lift its confidence to 1.0.

    Returns the updated entity row (suitable for the API response) or
    ``None`` if no entity matched the id.
    """
    existing = con.execute(
        "SELECT entity_id FROM entities WHERE entity_id = ?",
        [entity_id],
    ).fetchone()
    if existing is None:
        return None
    con.execute(
        """
        UPDATE entities
        SET confidence = 1.0, reviewed_at = now(), reviewed_by = ?
        WHERE entity_id = ?
        """,
        [reviewed_by, entity_id],
    )
    row = con.execute(
        """
        SELECT entity_id, canonical_name, entity_type, confidence,
               mention_count, reviewed_at, reviewed_by
        FROM entities
        WHERE entity_id = ?
        """,
        [entity_id],
    ).fetchone()
    return {
        "entity_id": row[0],
        "canonical_name": row[1],
        "entity_type": row[2],
        "confidence": float(row[3]) if row[3] is not None else 0.0,
        "mention_count": int(row[4] or 0),
        "reviewed_at": str(row[5]) if row[5] is not None else None,
        "reviewed_by": row[6],
    }


def count_entities(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT count(*) FROM entities").fetchone()
    return int(row[0] or 0)


def count_relationships(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT count(*) FROM relationships").fetchone()
    return int(row[0] or 0)


def delete_entity_cascade(
    con: duckdb.DuckDBPyConnection,
    entity_id: str,
) -> dict[str, int] | None:
    """Remove an entity and every edge/evidence/alias that referenced it.

    Intended for pruning clearly-bad extractions from the Audit view. We
    resolve the dependent rows first so DuckDB never sees a dangling FK
    reference and so the caller can show a summary of what was removed.
    Returns ``None`` when the entity id does not exist.
    """
    existing = con.execute(
        "SELECT entity_id FROM entities WHERE entity_id = ?",
        [entity_id],
    ).fetchone()
    if existing is None:
        return None

    rel_rows = con.execute(
        """
        SELECT rel_id FROM relationships
        WHERE source_entity = ? OR target_entity = ?
        """,
        [entity_id, entity_id],
    ).fetchall()
    rel_ids = [row[0] for row in rel_rows]

    evidence_removed = 0
    if rel_ids:
        placeholders = ",".join(["?"] * len(rel_ids))
        evidence_row = con.execute(
            f"SELECT count(*) FROM relationship_evidence WHERE rel_id IN ({placeholders})",
            rel_ids,
        ).fetchone()
        evidence_removed = int(evidence_row[0] or 0)
        con.execute(
            f"DELETE FROM relationship_evidence WHERE rel_id IN ({placeholders})",
            rel_ids,
        )
        con.execute(
            f"DELETE FROM relationships WHERE rel_id IN ({placeholders})",
            rel_ids,
        )

    aliases_row = con.execute(
        "SELECT count(*) FROM entity_aliases WHERE entity_id = ?",
        [entity_id],
    ).fetchone()
    aliases_removed = int(aliases_row[0] or 0)
    con.execute("DELETE FROM entity_aliases WHERE entity_id = ?", [entity_id])

    attachments_row = con.execute(
        "SELECT count(*) FROM entity_attachments WHERE entity_id = ?",
        [entity_id],
    ).fetchone()
    attachments_removed = int(attachments_row[0] or 0)
    con.execute("DELETE FROM entity_attachments WHERE entity_id = ?", [entity_id])

    con.execute("DELETE FROM entities WHERE entity_id = ?", [entity_id])
    return {
        "relationships_removed": len(rel_ids),
        "evidence_removed": evidence_removed,
        "aliases_removed": aliases_removed,
        "attachments_removed": attachments_removed,
    }
