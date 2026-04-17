"""Graph storage abstraction: DuckDB today; algorithms (Phase 8) depend on this interface."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import duckdb


@runtime_checkable
class GraphBackend(Protocol):
    """Read-oriented graph API over relationship storage."""

    def retrieve_expanded_graph(
        self,
        entity_ids: list[str],
        *,
        limit: int = 50,
        case_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return deduped relationship rows for first- and second-hop expansion."""
        ...

    def inventory_relationships_for_entity(
        self,
        entity_id: str,
        *,
        case_id: str | None = None,
    ) -> list[dict[str, object]]:
        """Typed associations for an entity, grouped with citations (inventory / LLM context)."""
        ...


class DuckGraphBackend:
    """DuckDB-backed graph reads from ``relationships`` and ``relationship_evidence``."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con

    def retrieve_expanded_graph(
        self,
        entity_ids: list[str],
        *,
        limit: int = 50,
        case_id: str | None = None,
    ) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(entity_ids))
        if not ids:
            return []
        placeholders = ",".join(["?"] * len(ids))
        if case_id:
            rows = self._con.execute(
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
                  et.entity_type AS target_type,
                  coalesce(re.event_time, r.event_time) AS event_time
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
            rows = self._con.execute(
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
                  et.entity_type AS target_type,
                  coalesce(re.event_time, r.event_time) AS event_time
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
        first_hop_ids: set[str] = set(ids)
        for row in rows:
            first_hop_ids.add(row[1])
            first_hop_ids.add(row[2])
        second_hop_ids = first_hop_ids.difference(ids)
        if second_hop_ids:
            hop_placeholders = ",".join(["?"] * len(second_hop_ids))
            if case_id:
                extra_rows = self._con.execute(
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
                      et.entity_type AS target_type,
                      coalesce(re.event_time, r.event_time) AS event_time
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
                extra_rows = self._con.execute(
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
                      et.entity_type AS target_type,
                      coalesce(re.event_time, r.event_time) AS event_time
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
            et = r[14]
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
                "event_time": et,
                "score": float(r[4]),
            }
        return list(deduped.values())[: limit * 2]

    def inventory_relationships_for_entity(
        self,
        entity_id: str,
        *,
        case_id: str | None = None,
    ) -> list[dict[str, object]]:
        params: list[object] = [entity_id, entity_id, entity_id]
        case_filter = ""
        if case_id:
            case_filter = "AND d.case_id = ?"
            params.append(case_id)
        params.append(12)
        rows = self._con.execute(
            f"""
            SELECT
              other.entity_id,
              other.canonical_name,
              other.entity_type,
              r.relation_type,
              r.confidence,
              d.doc_id,
              d.filename,
              re.page,
              coalesce(re.span_text, c.text, '')
            FROM relationships r
            JOIN entities other
              ON other.entity_id = CASE
                WHEN r.source_entity = ? THEN r.target_entity
                ELSE r.source_entity
              END
            LEFT JOIN relationship_evidence re ON re.rel_id = r.rel_id
            LEFT JOIN documents d ON d.doc_id = re.doc_id
            LEFT JOIN chunks c ON c.chunk_id = re.chunk_id
            WHERE (r.source_entity = ? OR r.target_entity = ?)
            {case_filter}
            ORDER BY r.confidence DESC, other.canonical_name
            LIMIT ?
            """,
            params,
        ).fetchall()

        grouped: dict[tuple[str, str], dict[str, object]] = {}
        for row in rows:
            key = (str(row[0]), str(row[3]))
            if key not in grouped:
                grouped[key] = {
                    "other_entity_id": row[0],
                    "other_name": row[1],
                    "other_type": row[2],
                    "relation_type": row[3],
                    "confidence": float(row[4] or 0.0),
                    "citations": [],
                }
            entry = grouped[key]
            entry["confidence"] = max(entry["confidence"], float(row[4] or 0.0))
            if row[5] or row[6]:
                citation = {
                    "doc_id": row[5],
                    "doc_name": row[6] or row[5],
                    "page": row[7] if row[7] is not None else "?",
                    "span_text": row[8] or "",
                }
                if citation not in entry["citations"]:
                    entry["citations"].append(citation)

        associations = list(grouped.values())
        associations.sort(
            key=lambda item: (
                0 if str(item.get("other_type", "")).upper() == "PERSON" else 1,
                -float(item.get("confidence", 0.0)),
                str(item.get("other_name", "")).lower(),
            )
        )
        return associations
