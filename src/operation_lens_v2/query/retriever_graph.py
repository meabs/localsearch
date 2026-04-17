from __future__ import annotations

from typing import Any

from operation_lens_v2.ingestion.entity_schema import get_schema
from operation_lens_v2.ingestion.normaliser import normalise


def _resolve_entity_ids(con, names: list[str]) -> list[str]:
    # Try every registered entity type's normaliser so "Marcus Webb" (PERSON)
    # and "marcus.webb@x" (EMAIL) both resolve from free-form mentions. Also
    # include the raw surface + a lowered fallback so direct DB inserts match.
    cleaned_names = [n.strip() for n in names if n and n.strip()]
    if not cleaned_names:
        return []
    known_types = sorted(get_schema().known_types())
    candidate_names: list[str] = []
    for name in cleaned_names:
        candidate_names.append(name)
        for entity_type in known_types:
            candidate_names.append(normalise(name, entity_type))
    candidate_names = list(dict.fromkeys(c for c in candidate_names if c))
    if not candidate_names:
        return []
    placeholders = ",".join(["?"] * len(candidate_names))
    rows = con.execute(
        f"""
        SELECT entity_id FROM entities
        WHERE canonical_name IN ({placeholders})
           OR lower(canonical_name) IN ({placeholders})
        """,
        [*candidate_names, *[c.lower() for c in candidate_names]],
    ).fetchall()
    entity_ids = [r[0] for r in rows]
    return list(dict.fromkeys(entity_ids))


def retrieve_graph(
    con,
    entity_names: list[str],
    limit: int = 50,
    case_id: str | None = None,
) -> list[dict[str, Any]]:
    ids = _resolve_entity_ids(con, entity_names)
    if not ids:
        return []
    placeholders = ",".join(["?"] * len(ids))
    if case_id:
        rows = con.execute(
            f"""
            SELECT
              r.rel_id,
              r.source_entity,
              r.target_entity,
              r.relation_type,
              r.confidence,
              re.doc_id,
              d.filename,
              re.page,
              re.span_text,
              re.chunk_id,
              es.canonical_name AS source_name,
              et.canonical_name AS target_name,
              es.entity_type AS source_type,
              et.entity_type AS target_type
            FROM relationships r
            JOIN relationship_evidence re ON r.rel_id = re.rel_id
            JOIN entities es ON es.entity_id = r.source_entity
            JOIN entities et ON et.entity_id = r.target_entity
            JOIN documents d ON d.doc_id = re.doc_id
            WHERE (r.source_entity IN ({placeholders}) OR r.target_entity IN ({placeholders}))
              AND d.case_id = ?
            ORDER BY r.confidence DESC
            LIMIT ?
            """,
            [*ids, *ids, case_id, limit],
        ).fetchall()
    else:
        rows = con.execute(
            f"""
            SELECT
              r.rel_id,
              r.source_entity,
              r.target_entity,
              r.relation_type,
              r.confidence,
              re.doc_id,
              d.filename,
              re.page,
              re.span_text,
              re.chunk_id,
              es.canonical_name AS source_name,
              et.canonical_name AS target_name,
              es.entity_type AS source_type,
              et.entity_type AS target_type
            FROM relationships r
            JOIN relationship_evidence re ON r.rel_id = re.rel_id
            JOIN entities es ON es.entity_id = r.source_entity
            JOIN entities et ON et.entity_id = r.target_entity
            JOIN documents d ON d.doc_id = re.doc_id
            WHERE r.source_entity IN ({placeholders}) OR r.target_entity IN ({placeholders})
            ORDER BY r.confidence DESC
            LIMIT ?
            """,
            [*ids, *ids, limit],
        ).fetchall()
    # Expand to second-hop neighborhood so person queries pull linked locations/assets.
    first_hop_ids: set[str] = set(ids)
    for row in rows:
        first_hop_ids.add(row[1])
        first_hop_ids.add(row[2])
    second_hop_ids = first_hop_ids.difference(ids)
    if second_hop_ids:
        hop_placeholders = ",".join(["?"] * len(second_hop_ids))
        if case_id:
            extra_rows = con.execute(
                f"""
                SELECT
                  r.rel_id,
                  r.source_entity,
                  r.target_entity,
                  r.relation_type,
                  r.confidence,
                  re.doc_id,
                  d.filename,
                  re.page,
                  re.span_text,
                  re.chunk_id,
                  es.canonical_name AS source_name,
                  et.canonical_name AS target_name,
                  es.entity_type AS source_type,
                  et.entity_type AS target_type
                FROM relationships r
                JOIN relationship_evidence re ON r.rel_id = re.rel_id
                JOIN entities es ON es.entity_id = r.source_entity
                JOIN entities et ON et.entity_id = r.target_entity
                JOIN documents d ON d.doc_id = re.doc_id
                WHERE (
                  r.source_entity IN ({hop_placeholders})
                  OR r.target_entity IN ({hop_placeholders})
                )
                  AND d.case_id = ?
                ORDER BY r.confidence DESC
                LIMIT ?
                """,
                [*second_hop_ids, *second_hop_ids, case_id, limit],
            ).fetchall()
        else:
            extra_rows = con.execute(
                f"""
                SELECT
                  r.rel_id,
                  r.source_entity,
                  r.target_entity,
                  r.relation_type,
                  r.confidence,
                  re.doc_id,
                  d.filename,
                  re.page,
                  re.span_text,
                  re.chunk_id,
                  es.canonical_name AS source_name,
                  et.canonical_name AS target_name,
                  es.entity_type AS source_type,
                  et.entity_type AS target_type
                FROM relationships r
                JOIN relationship_evidence re ON r.rel_id = re.rel_id
                JOIN entities es ON es.entity_id = r.source_entity
                JOIN entities et ON et.entity_id = r.target_entity
                JOIN documents d ON d.doc_id = re.doc_id
                WHERE (
                  r.source_entity IN ({hop_placeholders})
                  OR r.target_entity IN ({hop_placeholders})
                )
                ORDER BY r.confidence DESC
                LIMIT ?
                """,
                [*second_hop_ids, *second_hop_ids, limit],
            ).fetchall()
        rows = [*rows, *extra_rows]
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r[0], r[1], r[2], r[5])
        deduped[key] = {
            "source": "graph",
            "rel_id": r[0],
            "source_entity": r[1],
            "target_entity": r[2],
            "relation_type": r[3],
            "confidence": float(r[4]),
            "doc_id": r[5],
            "doc_name": r[6],
            "page": r[7],
            "span_text": r[8],
            "chunk_id": r[9],
            "source_name": r[10],
            "target_name": r[11],
            "source_type": r[12],
            "target_type": r[13],
            "score": float(r[4]),
        }
    return list(deduped.values())[: limit * 2]
