from __future__ import annotations

import logging
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

import duckdb

from operation_lens_v2.ingestion import duck_store
from operation_lens_v2.models import RelationshipCandidate

logger = logging.getLogger(__name__)

_SERIAL_PREFIXES = ("IMEI", "DEVICEID")
_GPS_ENTITY_TYPE = "LOCATION"
_GPS_RADIUS_METERS = 50.0


@dataclass(slots=True)
class _EvidenceRow:
    entity_id: str
    canonical_name: str
    entity_type: str
    doc_id: str
    chunk_id: str
    page: int
    span_text: str
    latitude: float | None = None
    longitude: float | None = None


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6_371_000.0
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2.0) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2.0) ** 2
    return 2.0 * earth_radius_m * asin(min(1.0, sqrt(a)))


def _is_serial_identifier(canonical_name: str) -> bool:
    compact = canonical_name.replace(" ", "").upper()
    return compact.startswith(_SERIAL_PREFIXES)


def _get_existing_rel_id(
    con: duckdb.DuckDBPyConnection, source_entity: str, target_entity: str
) -> str | None:
    row = con.execute(
        """
        SELECT rel_id
        FROM relationships
        WHERE relation_type = 'SHARED_METADATA'
          AND (
            (source_entity = ? AND target_entity = ?)
            OR (source_entity = ? AND target_entity = ?)
          )
        LIMIT 1
        """,
        [source_entity, target_entity, target_entity, source_entity],
    ).fetchone()
    return row[0] if row else None


def _existing_evidence_docs(con: duckdb.DuckDBPyConnection, rel_id: str) -> set[str]:
    rows = con.execute(
        "SELECT DISTINCT doc_id FROM relationship_evidence WHERE rel_id = ?",
        [rel_id],
    ).fetchall()
    return {row[0] for row in rows if row and row[0]}


def _make_evidence_candidate(span_text: str, source: str, target: str) -> RelationshipCandidate:
    return RelationshipCandidate(
        source=source,
        target=target,
        relation_type="SHARED_METADATA",
        span_text=span_text,
        span_start=0,
        span_end=len(span_text),
        confidence=0.95,
        extraction_method="cross_doc",
    )


def _insert_group(
    con: duckdb.DuckDBPyConnection,
    *,
    entity_ids: list[str],
    evidence_rows: list[_EvidenceRow],
    span_text: str,
) -> int:
    docs = {row.doc_id for row in evidence_rows}
    if len(docs) < 2:
        return 0

    unique_ids = list(dict.fromkeys(entity_ids))
    if not unique_ids:
        return 0
    source_entity = unique_ids[0]
    target_entity = unique_ids[1] if len(unique_ids) > 1 else unique_ids[0]
    if source_entity != target_entity and source_entity > target_entity:
        source_entity, target_entity = target_entity, source_entity

    rel_id = _get_existing_rel_id(con, source_entity, target_entity)
    if rel_id is None:
        rel_id = duck_store.create_relationship(
            con,
            source_entity=source_entity,
            target_entity=target_entity,
            relation_type="SHARED_METADATA",
            confidence=0.95,
        )
        existing_docs: set[str] = set()
    else:
        existing_docs = _existing_evidence_docs(con, rel_id)

    rel = _make_evidence_candidate(span_text, source_entity, target_entity)
    inserted = 0
    for row in evidence_rows:
        if row.doc_id in existing_docs:
            continue
        duck_store.insert_relationship_evidence(
            con,
            rel_id=rel_id,
            chunk_id=row.chunk_id,
            doc_id=row.doc_id,
            page=row.page,
            rel=rel,
        )
        inserted += 1
    return inserted


def _load_exact_identifier_rows(con: duckdb.DuckDBPyConnection) -> list[_EvidenceRow]:
    rows = con.execute(
        """
        SELECT
          e.entity_id,
          e.canonical_name,
          e.entity_type,
          ea.alias_text,
          ea.source_doc,
          ea.source_chunk,
          c.page
        FROM entities e
        JOIN entity_aliases ea ON ea.entity_id = e.entity_id
        LEFT JOIN chunks c ON c.chunk_id = ea.source_chunk
        WHERE e.entity_type IN ('PHONE', 'MAC_ADDRESS', 'CRYPTO_WALLET', 'SERIAL')
        ORDER BY e.entity_type, e.canonical_name, ea.source_doc, ea.alias_id
        """,
    ).fetchall()
    out: list[_EvidenceRow] = []
    for row in rows:
        entity_id = str(row[0])
        canonical_name = str(row[1] or "")
        entity_type = str(row[2] or "")
        if entity_type == "SERIAL" and not _is_serial_identifier(canonical_name):
            continue
        if row[4] is None or row[5] is None or row[6] is None:
            continue
        out.append(
            _EvidenceRow(
                entity_id=entity_id,
                canonical_name=canonical_name,
                entity_type=entity_type,
                doc_id=str(row[4]),
                chunk_id=str(row[5]),
                page=int(row[6]),
                span_text=str(row[3] or canonical_name),
            )
        )
    return out


def _load_gps_rows(con: duckdb.DuckDBPyConnection) -> list[_EvidenceRow]:
    rows = con.execute(
        """
        SELECT
          e.entity_id,
          e.canonical_name,
          e.entity_type,
          e.latitude,
          e.longitude,
          ea.alias_text,
          ea.source_doc,
          ea.source_chunk,
          c.page
        FROM entities e
        JOIN entity_aliases ea ON ea.entity_id = e.entity_id
        LEFT JOIN chunks c ON c.chunk_id = ea.source_chunk
        WHERE e.entity_type = ?
          AND e.latitude IS NOT NULL
          AND e.longitude IS NOT NULL
        ORDER BY e.canonical_name, ea.source_doc, ea.alias_id
        """,
        [_GPS_ENTITY_TYPE],
    ).fetchall()
    out: list[_EvidenceRow] = []
    for row in rows:
        if row[6] is None or row[7] is None or row[8] is None:
            continue
        out.append(
            _EvidenceRow(
                entity_id=str(row[0]),
                canonical_name=str(row[1] or ""),
                entity_type=str(row[2] or ""),
                latitude=float(row[3]),
                longitude=float(row[4]),
                span_text=str(row[5] or row[1] or ""),
                doc_id=str(row[6]),
                chunk_id=str(row[7]),
                page=int(row[8]),
            )
        )
    return out


def _group_exact_metadata(rows: list[_EvidenceRow]) -> list[tuple[str, list[str], list[_EvidenceRow]]]:
    grouped: dict[tuple[str, str], list[_EvidenceRow]] = {}
    for row in rows:
        grouped.setdefault((row.entity_type, row.canonical_name), []).append(row)
    results: list[tuple[str, list[str], list[_EvidenceRow]]] = []
    for (_, canonical_name), group_rows in grouped.items():
        if len({row.doc_id for row in group_rows}) < 2:
            continue
        entity_ids = list(dict.fromkeys(row.entity_id for row in group_rows))
        results.append((canonical_name, entity_ids, group_rows))
    return results


def _cluster_gps_rows(rows: list[_EvidenceRow]) -> list[tuple[str, list[str], list[_EvidenceRow]]]:
    if len(rows) < 2:
        return []

    parent = list(range(len(rows)))

    def find(idx: int) -> int:
        while parent[idx] != idx:
            parent[idx] = parent[parent[idx]]
            idx = parent[idx]
        return idx

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for idx, row in enumerate(rows):
        for other_idx in range(idx + 1, len(rows)):
            other = rows[other_idx]
            if row.doc_id == other.doc_id:
                continue
            if _haversine_meters(
                row.latitude or 0.0,
                row.longitude or 0.0,
                other.latitude or 0.0,
                other.longitude or 0.0,
            ) <= _GPS_RADIUS_METERS:
                union(idx, other_idx)

    clusters: dict[int, list[_EvidenceRow]] = {}
    for idx, row in enumerate(rows):
        clusters.setdefault(find(idx), []).append(row)

    grouped: list[tuple[str, list[str], list[_EvidenceRow]]] = []
    for cluster_rows in clusters.values():
        if len({row.doc_id for row in cluster_rows}) < 2:
            continue
        entity_ids = list(dict.fromkeys(row.entity_id for row in cluster_rows))
        grouped.append(("GPS within 50m", entity_ids, cluster_rows))
    return grouped


def link_shared_metadata(con: duckdb.DuckDBPyConnection) -> int:
    """Create cross-document SHARED_METADATA edges for shared identifier values."""
    inserted = 0
    for span_text, entity_ids, group_rows in _group_exact_metadata(_load_exact_identifier_rows(con)):
        inserted += _insert_group(
            con,
            entity_ids=entity_ids,
            evidence_rows=group_rows,
            span_text=span_text,
        )

    for span_text, entity_ids, group_rows in _cluster_gps_rows(_load_gps_rows(con)):
        inserted += _insert_group(
            con,
            entity_ids=entity_ids,
            evidence_rows=group_rows,
            span_text=span_text,
        )

    logger.info("Shared metadata linker inserted %d evidence rows", inserted)
    return inserted
