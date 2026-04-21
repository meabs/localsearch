from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    sha256: str
    size_bytes: int
    mtime_ns: int


def fingerprint_path(path: Path, *, chunk_size: int = 1024 * 1024) -> SourceFingerprint:
    """Return a stable content hash plus cheap file metadata for ingestion skips."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    stat = path.stat()
    return SourceFingerprint(
        sha256=digest.hexdigest(),
        size_bytes=int(stat.st_size),
        mtime_ns=int(stat.st_mtime_ns),
    )


def latest_document_for_path(con, *, filepath: str, case_id: str) -> dict[str, object] | None:
    row = con.execute(
        """
        SELECT doc_id, source_hash, source_size_bytes, source_mtime_ns
        FROM documents
        WHERE filepath = ? AND case_id = ?
        ORDER BY ingested_at DESC
        LIMIT 1
        """,
        [filepath, case_id],
    ).fetchone()
    if not row:
        return None
    return {
        "doc_id": row[0],
        "source_hash": row[1],
        "source_size_bytes": int(row[2]) if row[2] is not None else None,
        "source_mtime_ns": int(row[3]) if row[3] is not None else None,
    }


def is_unchanged(existing: dict[str, object] | None, fingerprint: SourceFingerprint) -> bool:
    if not existing:
        return False
    return existing.get("source_hash") == fingerprint.sha256
