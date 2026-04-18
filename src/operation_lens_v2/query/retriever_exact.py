from __future__ import annotations

import re
from typing import Any


def retrieve_exact(
    con,
    query: str,
    limit: int = 20,
    case_id: str | None = None,
) -> list[dict[str, Any]]:
    tokens = re.findall(
        r"\b[A-Z]{2}\d{2}\s?[A-Z]{3}\b|\+?\d[\d\s]{8,}\d|OP[_\-\s]?[A-Z0-9]+\b",
        query,
    )
    if not tokens:
        return []
    if case_id:
        rows = con.execute(
            """
            SELECT
              ea.entity_id,
              ea.alias_text,
              e.canonical_name,
              e.entity_type,
              e.mention_count,
              d.doc_id,
              d.filename,
              c.page,
              c.text,
              ea.source_chunk,
              c.source_label,
              c.provenance_type
            FROM entity_aliases ea
            JOIN entities e ON e.entity_id = ea.entity_id
            LEFT JOIN documents d ON d.doc_id = ea.source_doc
            LEFT JOIN chunks c ON c.chunk_id = ea.source_chunk
            WHERE lower(ea.alias_text) IN ({}) AND d.case_id = ?
            LIMIT ?
            """.format(",".join(["?"] * len(tokens))),
            [*map(str.lower, tokens), case_id, limit],
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT
              ea.entity_id,
              ea.alias_text,
              e.canonical_name,
              e.entity_type,
              e.mention_count,
              d.doc_id,
              d.filename,
              c.page,
              c.text,
              ea.source_chunk,
              c.source_label,
              c.provenance_type
            FROM entity_aliases ea
            JOIN entities e ON e.entity_id = ea.entity_id
            LEFT JOIN documents d ON d.doc_id = ea.source_doc
            LEFT JOIN chunks c ON c.chunk_id = ea.source_chunk
            WHERE lower(ea.alias_text) IN ({})
            LIMIT ?
            """.format(",".join(["?"] * len(tokens))),
            [*map(str.lower, tokens), limit],
        ).fetchall()
    return [
        {
            "source": "exact",
            "entity_id": r[0],
            "alias_text": r[1],
            "canonical_name": r[2],
            "entity_type": r[3],
            "mention_count": int(r[4] or 0),
            "doc_id": r[5],
            "doc_name": r[6],
            "page": r[7],
            "text": r[8] or "",
            "chunk_id": r[9],
            "source_label": r[10],
            "provenance_type": r[11],
            "score": 1.0,
        }
        for r in rows
    ]
