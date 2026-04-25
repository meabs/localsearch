"""Scope-enforced investigator tools.

Every tool takes a ScopeContext and a DuckDB connection and returns typed models from
tool_schemas.py. Scope is applied at the SQL layer — an agent cannot escape its scope
because the tools physically refuse to return out-of-scope rows.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import duckdb

from operation_lens_v2.query.scope import (
    InvestigationScope,
    ScopeContext,
    ScopeViolation,
)
from operation_lens_v2.query.tool_schemas import (
    ChunkText,
    Citation,
    CooccurrenceHit,
    DatedEvent,
    DocumentSummary,
    EntityHit,
    EntityProfile,
    GraphPath,
    RelationshipEdge,
)

logger = logging.getLogger(__name__)

DEFAULT_TOOL_LIMIT = 25


# ── Scope helpers ─────────────────────────────────────────────────────────────


def _scope_doc_id_filter(
    scope: ScopeContext,
    doc_id_column: str,
) -> tuple[str, list[Any]]:
    """Return a SQL fragment + params restricting the given doc_id column to scope."""
    if scope.mode is InvestigationScope.DOCUMENT:
        if not scope.doc_ids:
            raise ScopeViolation("DOCUMENT scope has no doc_ids set")
        placeholders = ",".join(["?"] * len(scope.doc_ids))
        return f"{doc_id_column} IN ({placeholders})", list(scope.doc_ids)
    if scope.mode is InvestigationScope.CASE:
        if not scope.case_scope:
            raise ScopeViolation("CASE scope has no case_scope set")
        return (
            f"{doc_id_column} IN ("
            "  SELECT d.doc_id FROM documents d"
            "  JOIN cases ca ON ca.case_id = d.case_id"
            "  WHERE ca.case_ref = ?"
            ")"
        ), [scope.case_scope]
    return "1=1", []


def _ensure_doc_in_scope(
    scope: ScopeContext,
    con: duckdb.DuckDBPyConnection,
    doc_id: str,
) -> None:
    """Raise ScopeViolation if doc_id is not reachable in the current scope."""
    if scope.mode is InvestigationScope.DOCUMENT:
        if doc_id not in scope.doc_ids:
            raise ScopeViolation(f"doc_id {doc_id!r} is outside the investigation scope")
        return
    if scope.mode is InvestigationScope.CASE:
        row = con.execute(
            """
            SELECT 1 FROM documents d
            JOIN cases ca ON ca.case_id = d.case_id
            WHERE d.doc_id = ? AND ca.case_ref = ?
            """,
            [doc_id, scope.case_scope],
        ).fetchone()
        if not row:
            raise ScopeViolation(
                f"doc_id {doc_id!r} is outside case_scope {scope.case_scope!r}"
            )


def _resolve_entity_ids(
    scope: ScopeContext,
    con: duckdb.DuckDBPyConnection,
    identifiers: Iterable[str] | None,
) -> list[str]:
    """Resolve raw names or aliases to in-scope entity IDs."""
    if not identifiers:
        return []

    scope_sql, scope_params = _scope_doc_id_filter(scope, "ea.source_doc")
    resolved: list[str] = []
    seen: set[str] = set()

    for raw_identifier in identifiers:
        identifier = str(raw_identifier or "").strip()
        if not identifier:
            continue

        rows = con.execute(
            f"""
            SELECT
              e.entity_id,
              COALESCE(e.mention_count, 0) AS mention_count,
              COUNT(DISTINCT ea.source_doc) AS doc_count
            FROM entities e
            JOIN entity_aliases ea ON ea.entity_id = e.entity_id
            WHERE {scope_sql}
              AND (
                e.entity_id = ?
                OR lower(e.canonical_name) = lower(?)
                OR lower(ea.alias_text) = lower(?)
              )
            GROUP BY e.entity_id, e.mention_count
            ORDER BY mention_count DESC, doc_count DESC, e.entity_id
            """,
            [*scope_params, identifier, identifier, identifier],
        ).fetchall()

        if not rows:
            like = f"%{identifier.lower()}%"
            rows = con.execute(
                f"""
                SELECT
                  e.entity_id,
                  COALESCE(e.mention_count, 0) AS mention_count,
                  COUNT(DISTINCT ea.source_doc) AS doc_count
                FROM entities e
                JOIN entity_aliases ea ON ea.entity_id = e.entity_id
                WHERE {scope_sql}
                  AND (
                    lower(e.canonical_name) LIKE ?
                    OR lower(ea.alias_text) LIKE ?
                  )
                GROUP BY e.entity_id, e.mention_count
                ORDER BY mention_count DESC, doc_count DESC, e.entity_id
                """,
                [*scope_params, like, like],
            ).fetchall()

        for row in rows:
            entity_id = str(row[0])
            if entity_id not in seen:
                seen.add(entity_id)
                resolved.append(entity_id)

    return resolved


# ── Tools ─────────────────────────────────────────────────────────────────────


async def search_entities(
    scope: ScopeContext,
    query: str,
    con: duckdb.DuckDBPyConnection,
    *,
    entity_type: str | None = None,
    limit: int = DEFAULT_TOOL_LIMIT,
) -> list[EntityHit]:
    """Find entities whose canonical name or any alias matches the query.

    Case-insensitive substring match. Results are scoped — only entities with at least
    one alias attached to an in-scope document are returned.
    """
    scope_sql, scope_params = _scope_doc_id_filter(scope, "ea.source_doc")
    params: list[Any] = [f"%{query.lower()}%", f"%{query.lower()}%"]
    type_sql = ""
    if entity_type:
        type_sql = " AND e.entity_type = ?"
        params.append(entity_type)
    params.extend(scope_params)
    params.append(min(limit, DEFAULT_TOOL_LIMIT))

    rows = con.execute(
        f"""
        SELECT
          e.entity_id,
          e.canonical_name,
          e.entity_type,
          COALESCE(e.mention_count, 0) AS mention_count,
          COUNT(DISTINCT ea.source_doc) AS doc_count,
          ARRAY_AGG(DISTINCT ea.alias_text) AS aliases
        FROM entities e
        JOIN entity_aliases ea ON ea.entity_id = e.entity_id
        WHERE (lower(e.canonical_name) LIKE ? OR lower(ea.alias_text) LIKE ?)
          {type_sql}
          AND {scope_sql}
        GROUP BY e.entity_id, e.canonical_name, e.entity_type, e.mention_count
        ORDER BY mention_count DESC, doc_count DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    return [
        EntityHit(
            entity_id=row[0],
            canonical_name=row[1],
            entity_type=row[2],
            mention_count=int(row[3] or 0),
            doc_count=int(row[4] or 0),
            aliases=list(row[5] or [])[:5],
        )
        for row in rows
    ]


async def get_entity_profile(
    scope: ScopeContext,
    entity_id: str,
    con: duckdb.DuckDBPyConnection,
    *,
    citation_limit: int = 5,
) -> EntityProfile | None:
    """Return a profile for one entity, restricted to the current scope."""
    resolved_ids = _resolve_entity_ids(scope, con, [entity_id])
    if not resolved_ids:
        return None
    entity_id = resolved_ids[0]

    scope_sql, scope_params = _scope_doc_id_filter(scope, "ea.source_doc")

    base = con.execute(
        f"""
        SELECT
          e.entity_id,
          e.canonical_name,
          e.entity_type,
          COUNT(DISTINCT ea.alias_id) AS mention_count,
          COUNT(DISTINCT ea.source_doc) AS doc_count
        FROM entities e
        LEFT JOIN entity_aliases ea ON ea.entity_id = e.entity_id AND {scope_sql}
        WHERE e.entity_id = ?
        GROUP BY e.entity_id, e.canonical_name, e.entity_type
        """,
        [*scope_params, entity_id],
    ).fetchone()
    if not base:
        return None
    if int(base[3] or 0) == 0:
        # No aliases reachable in scope — entity exists but isn't visible here.
        return None

    aliases = [
        row[0]
        for row in con.execute(
            f"""
            SELECT DISTINCT ea.alias_text
            FROM entity_aliases ea
            WHERE ea.entity_id = ? AND {scope_sql}
            LIMIT 10
            """,
            [entity_id, *scope_params],
        ).fetchall()
    ]

    citations_rows = con.execute(
        f"""
        SELECT
          ea.source_doc,
          d.filename,
          c.page,
          c.chunk_id
        FROM entity_aliases ea
        JOIN documents d ON d.doc_id = ea.source_doc
        LEFT JOIN chunks c ON c.chunk_id = ea.source_chunk
        WHERE ea.entity_id = ? AND {scope_sql}
        ORDER BY c.page NULLS LAST
        LIMIT ?
        """,
        [entity_id, *scope_params, citation_limit],
    ).fetchall()

    citations = [
        Citation(
            doc_id=row[0],
            doc_name=row[1],
            page=int(row[2]) if row[2] is not None else 0,
            chunk_id=row[3],
        )
        for row in citations_rows
    ]

    first_seen = citations[0] if citations else None
    last_seen = citations[-1] if citations else None

    return EntityProfile(
        entity_id=base[0],
        canonical_name=base[1],
        entity_type=base[2],
        mention_count=int(base[3] or 0),
        doc_count=int(base[4] or 0),
        first_seen_doc=first_seen.doc_name if first_seen else None,
        first_seen_page=first_seen.page if first_seen else None,
        last_seen_doc=last_seen.doc_name if last_seen else None,
        last_seen_page=last_seen.page if last_seen else None,
        top_aliases=aliases,
        recent_citations=citations,
    )


async def get_relationships(
    scope: ScopeContext,
    entity_id: str,
    con: duckdb.DuckDBPyConnection,
    *,
    depth: int = 1,
    limit: int = DEFAULT_TOOL_LIMIT,
) -> list[RelationshipEdge]:
    """Return graph edges touching entity_id, scoped to evidence from in-scope docs."""
    resolved_ids = _resolve_entity_ids(scope, con, [entity_id])
    if not resolved_ids:
        return []
    entity_id = resolved_ids[0]

    scope_sql, scope_params = _scope_doc_id_filter(scope, "re.doc_id")
    depth = max(1, min(depth, 2))
    limit = min(limit, DEFAULT_TOOL_LIMIT)

    first_hop_rows = con.execute(
        f"""
        SELECT
          r.rel_id,
          r.source_entity,
          es.canonical_name,
          es.entity_type,
          r.target_entity,
          et.canonical_name,
          et.entity_type,
          r.relation_type,
          r.confidence,
          re.doc_id,
          d.filename,
          re.page,
          re.chunk_id,
          re.span_text
        FROM relationships r
        JOIN entities es ON es.entity_id = r.source_entity
        JOIN entities et ON et.entity_id = r.target_entity
        JOIN relationship_evidence re ON re.rel_id = r.rel_id
        LEFT JOIN documents d ON d.doc_id = re.doc_id
        WHERE (r.source_entity = ? OR r.target_entity = ?) AND {scope_sql}
        ORDER BY r.confidence DESC
        LIMIT ?
        """,
        [entity_id, entity_id, *scope_params, limit],
    ).fetchall()

    edges: list[RelationshipEdge] = [_edge_from_row(row, hop=1) for row in first_hop_rows]

    if depth < 2 or not edges:
        return edges

    neighbour_ids = {
        edge.target_entity_id if edge.source_entity_id == entity_id else edge.source_entity_id
        for edge in edges
    }
    neighbour_ids.discard(entity_id)
    if not neighbour_ids:
        return edges

    placeholders = ",".join(["?"] * len(neighbour_ids))
    second_hop_rows = con.execute(
        f"""
        SELECT
          r.rel_id,
          r.source_entity,
          es.canonical_name,
          es.entity_type,
          r.target_entity,
          et.canonical_name,
          et.entity_type,
          r.relation_type,
          r.confidence,
          re.doc_id,
          d.filename,
          re.page,
          re.chunk_id,
          re.span_text
        FROM relationships r
        JOIN entities es ON es.entity_id = r.source_entity
        JOIN entities et ON et.entity_id = r.target_entity
        JOIN relationship_evidence re ON re.rel_id = r.rel_id
        LEFT JOIN documents d ON d.doc_id = re.doc_id
        WHERE (r.source_entity IN ({placeholders}) OR r.target_entity IN ({placeholders}))
          AND r.source_entity <> ? AND r.target_entity <> ?
          AND {scope_sql}
        ORDER BY r.confidence DESC
        LIMIT ?
        """,
        [
            *neighbour_ids,
            *neighbour_ids,
            entity_id,
            entity_id,
            *scope_params,
            limit,
        ],
    ).fetchall()

    seen: set[str] = {edge.rel_id for edge in edges}
    for row in second_hop_rows:
        if row[0] in seen:
            continue
        seen.add(row[0])
        edges.append(_edge_from_row(row, hop=2))

    return edges[:limit]


def _edge_from_row(row: tuple[Any, ...], *, hop: int) -> RelationshipEdge:
    return RelationshipEdge(
        rel_id=row[0],
        source_entity_id=row[1],
        source_name=row[2],
        source_type=row[3],
        target_entity_id=row[4],
        target_name=row[5],
        target_type=row[6],
        relation_type=row[7],
        confidence=float(row[8] or 0.0),
        doc_id=row[9],
        doc_name=row[10],
        page=int(row[11]) if row[11] is not None else None,
        chunk_id=row[12],
        span_text=row[13],
        hop=hop,
    )


async def get_cooccurrence(
    scope: ScopeContext,
    entity_a_id: str,
    entity_b_id: str,
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = DEFAULT_TOOL_LIMIT,
) -> list[CooccurrenceHit]:
    """Find chunks where both entities appear (via entity_aliases.source_chunk)."""
    resolved_a = _resolve_entity_ids(scope, con, [entity_a_id])
    resolved_b = _resolve_entity_ids(scope, con, [entity_b_id])
    if not resolved_a or not resolved_b:
        return []
    entity_a_id = resolved_a[0]
    entity_b_id = resolved_b[0]

    scope_sql, scope_params = _scope_doc_id_filter(scope, "c.doc_id")
    limit = min(limit, DEFAULT_TOOL_LIMIT)

    rows = con.execute(
        f"""
        SELECT DISTINCT
          c.chunk_id,
          c.doc_id,
          d.filename,
          c.page,
          c.text
        FROM chunks c
        JOIN documents d ON d.doc_id = c.doc_id
        WHERE c.chunk_id IN (
          SELECT source_chunk FROM entity_aliases WHERE entity_id = ?
        )
        AND c.chunk_id IN (
          SELECT source_chunk FROM entity_aliases WHERE entity_id = ?
        )
        AND {scope_sql}
        LIMIT ?
        """,
        [entity_a_id, entity_b_id, *scope_params, limit],
    ).fetchall()

    return [
        CooccurrenceHit(
            chunk_id=row[0],
            doc_id=row[1],
            doc_name=row[2],
            page=int(row[3]) if row[3] is not None else 0,
            entity_a_id=entity_a_id,
            entity_b_id=entity_b_id,
            text=row[4] or "",
        )
        for row in rows
    ]


async def get_timeline(
    scope: ScopeContext,
    con: duckdb.DuckDBPyConnection,
    *,
    entity_ids: list[str] | None = None,
    limit: int = DEFAULT_TOOL_LIMIT,
) -> list[DatedEvent]:
    """Return dated events from relationship_evidence.event_time, ordered chronologically."""
    scope_sql, scope_params = _scope_doc_id_filter(scope, "re.doc_id")
    limit = min(limit, DEFAULT_TOOL_LIMIT)

    entity_filter = ""
    entity_params: list[Any] = []
    if entity_ids:
        resolved_entity_ids = _resolve_entity_ids(scope, con, entity_ids)
        if not resolved_entity_ids:
            return []
        placeholders = ",".join(["?"] * len(resolved_entity_ids))
        entity_filter = (
            f" AND (r.source_entity IN ({placeholders}) OR r.target_entity IN ({placeholders}))"
        )
        entity_params = [*resolved_entity_ids, *resolved_entity_ids]

    rows = con.execute(
        f"""
        SELECT
          re.event_time,
          re.doc_id,
          d.filename,
          re.page,
          re.chunk_id,
          re.span_text,
          r.source_entity,
          r.target_entity
        FROM relationship_evidence re
        JOIN relationships r ON r.rel_id = re.rel_id
        LEFT JOIN documents d ON d.doc_id = re.doc_id
        WHERE re.event_time IS NOT NULL
          AND {scope_sql}
          {entity_filter}
        ORDER BY re.event_time ASC
        LIMIT ?
        """,
        [*scope_params, *entity_params, limit],
    ).fetchall()

    return [
        DatedEvent(
            event_time=str(row[0]),
            doc_id=row[1],
            doc_name=row[2],
            page=int(row[3]) if row[3] is not None else 0,
            chunk_id=row[4],
            text=row[5] or "",
            entity_ids=[e for e in (row[6], row[7]) if e],
        )
        for row in rows
    ]


async def fetch_chunk(
    scope: ScopeContext,
    chunk_id: str,
    con: duckdb.DuckDBPyConnection,
) -> ChunkText | None:
    """Return the full text of a single chunk, or raise ScopeViolation if out of scope."""
    row = con.execute(
        """
        SELECT c.chunk_id, c.doc_id, d.filename, c.page, c.text
        FROM chunks c
        LEFT JOIN documents d ON d.doc_id = c.doc_id
        WHERE c.chunk_id = ?
        """,
        [chunk_id],
    ).fetchone()
    if not row:
        return None
    _ensure_doc_in_scope(scope, con, row[1])
    return ChunkText(
        chunk_id=row[0],
        doc_id=row[1],
        doc_name=row[2],
        page=int(row[3]) if row[3] is not None else 0,
        text=row[4] or "",
    )


async def list_documents(
    scope: ScopeContext,
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = DEFAULT_TOOL_LIMIT,
) -> list[DocumentSummary]:
    """Enumerate documents available in the current scope with entity and chunk counts."""
    scope_sql, scope_params = _scope_doc_id_filter(scope, "d.doc_id")
    limit = min(limit, DEFAULT_TOOL_LIMIT)

    rows = con.execute(
        f"""
        SELECT
          d.doc_id,
          d.filename,
          ca.case_ref,
          d.page_count,
          (SELECT COUNT(*) FROM chunks c WHERE c.doc_id = d.doc_id) AS chunk_count,
          (SELECT COUNT(DISTINCT ea.entity_id) FROM entity_aliases ea WHERE ea.source_doc = d.doc_id) AS entity_count
        FROM documents d
        LEFT JOIN cases ca ON ca.case_id = d.case_id
        WHERE {scope_sql}
        ORDER BY d.ingested_at DESC
        LIMIT ?
        """,
        [*scope_params, limit],
    ).fetchall()

    return [
        DocumentSummary(
            doc_id=row[0],
            doc_name=row[1] or row[0],
            case_scope=row[2],
            page_count=int(row[3]) if row[3] is not None else None,
            chunk_count=int(row[4] or 0),
            entity_count=int(row[5] or 0),
        )
        for row in rows
    ]


async def get_document_entities(
    scope: ScopeContext,
    doc_id: str,
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = DEFAULT_TOOL_LIMIT,
) -> list[EntityHit]:
    """All entities mentioned in one document, with mention counts."""
    _ensure_doc_in_scope(scope, con, doc_id)
    limit = min(limit, DEFAULT_TOOL_LIMIT)

    rows = con.execute(
        """
        SELECT
          e.entity_id,
          e.canonical_name,
          e.entity_type,
          COUNT(ea.alias_id) AS mention_count
        FROM entities e
        JOIN entity_aliases ea ON ea.entity_id = e.entity_id
        WHERE ea.source_doc = ?
        GROUP BY e.entity_id, e.canonical_name, e.entity_type
        ORDER BY mention_count DESC
        LIMIT ?
        """,
        [doc_id, limit],
    ).fetchall()

    return [
        EntityHit(
            entity_id=row[0],
            canonical_name=row[1],
            entity_type=row[2],
            mention_count=int(row[3] or 0),
            doc_count=1,
            doc_id=doc_id,
        )
        for row in rows
    ]


async def walk_graph(
    scope: ScopeContext,
    source_entity_id: str,
    target_entity_id: str,
    con: duckdb.DuckDBPyConnection,
    *,
    max_hops: int = 3,
    max_paths: int = 5,
) -> list[GraphPath]:
    """BFS for evidence-backed paths between two entities. Returns at most max_paths."""
    resolved_source = _resolve_entity_ids(scope, con, [source_entity_id])
    resolved_target = _resolve_entity_ids(scope, con, [target_entity_id])
    if not resolved_source or not resolved_target:
        return []
    source_entity_id = resolved_source[0]
    target_entity_id = resolved_target[0]

    max_hops = max(1, min(max_hops, 4))

    # Walk the neighbour graph breadth-first, but on each emitted hop pull the
    # supporting edge straight from get_relationships so every hop carries evidence.
    queue: list[tuple[str, list[RelationshipEdge]]] = [(source_entity_id, [])]
    visited: set[str] = {source_entity_id}
    found: list[GraphPath] = []

    while queue and len(found) < max_paths:
        current_id, trail = queue.pop(0)
        if len(trail) >= max_hops:
            continue
        edges = await get_relationships(scope, current_id, con, depth=1, limit=DEFAULT_TOOL_LIMIT)
        for edge in edges:
            neighbour = (
                edge.target_entity_id
                if edge.source_entity_id == current_id
                else edge.source_entity_id
            )
            if neighbour == target_entity_id:
                path_edges = [*trail, edge]
                found.append(
                    GraphPath(
                        source_entity_id=source_entity_id,
                        target_entity_id=target_entity_id,
                        hops=len(path_edges),
                        edges=path_edges,
                        citations=_citations_from_edges(path_edges),
                    )
                )
                if len(found) >= max_paths:
                    break
            elif neighbour not in visited:
                visited.add(neighbour)
                queue.append((neighbour, [*trail, edge]))

    return found


def _citations_from_edges(edges: Iterable[RelationshipEdge]) -> list[Citation]:
    out: list[Citation] = []
    seen: set[tuple[str, int]] = set()
    for edge in edges:
        cite = edge.citation()
        if cite is None:
            continue
        key = (cite.doc_id, cite.page)
        if key in seen:
            continue
        seen.add(key)
        out.append(cite)
    return out
