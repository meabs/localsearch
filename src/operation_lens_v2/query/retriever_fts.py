from __future__ import annotations

from typing import Any


def retrieve_fts(
    con,
    query: str,
    limit: int = 20,
    case_id: str | None = None,
) -> list[dict[str, Any]]:
    try:
        if case_id:
            rows = con.execute(
                """
                SELECT c.chunk_id, c.doc_id, c.page, c.text, c.source_label, c.provenance_type,
                       fts_main_chunks.match_bm25(c.chunk_id, ?) AS score
                FROM chunks c
                JOIN documents d ON d.doc_id = c.doc_id
                JOIN fts_main_chunks ON c.chunk_id = fts_main_chunks.chunk_id
                WHERE score IS NOT NULL AND d.case_id = ?
                ORDER BY score DESC
                LIMIT ?
                """,
                [query, case_id, limit],
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT c.chunk_id, c.doc_id, c.page, c.text, c.source_label, c.provenance_type,
                       fts_main_chunks.match_bm25(c.chunk_id, ?) AS score
                FROM chunks c
                JOIN fts_main_chunks ON c.chunk_id = fts_main_chunks.chunk_id
                WHERE score IS NOT NULL
                ORDER BY score DESC
                LIMIT ?
                """,
                [query, limit],
            ).fetchall()
    except Exception:
        rows = []

    return [
        {
            "source": "fts",
            "chunk_id": r[0],
            "doc_id": r[1],
            "page": r[2],
            "text": r[3],
            "source_label": r[4],
            "provenance_type": r[5],
            "score": float(r[6]) if r[6] is not None else 0.0,
        }
        for r in rows
    ]
