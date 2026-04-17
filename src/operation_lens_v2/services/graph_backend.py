"""Graph backend abstraction — algorithm layer separated from storage.

Today the backend is DuckDB + networkx (loaded on demand). Tomorrow it can be
KuzuDB or Neo4j: the protocol is the contract. Algorithms take a GraphBackend,
not a DuckDB connection, so the call sites survive a storage swap.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Protocol

import duckdb
import networkx as nx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EdgeFilter:
    """Constraints applied when materialising a subgraph for an algorithm."""

    case_id: str | None = None
    min_confidence: float = 0.0
    exclude_relation_types: tuple[str, ...] = ()
    include_relation_types: tuple[str, ...] = ()


@dataclass
class GraphSnapshot:
    """An in-memory networkx graph plus id→label bookkeeping."""

    graph: nx.MultiDiGraph
    entity_labels: dict[str, dict[str, Any]] = field(default_factory=dict)

    def node_info(self, entity_id: str) -> dict[str, Any]:
        return self.entity_labels.get(entity_id, {"entity_id": entity_id})


class GraphBackend(Protocol):
    """Everything the algorithm layer needs from storage."""

    def build_snapshot(self, edge_filter: EdgeFilter) -> GraphSnapshot: ...

    def resolve_entity_id(self, name_or_id: str) -> str | None: ...

    def one_hop(
        self,
        entity_id: str,
        edge_filter: EdgeFilter,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    def edge_evidence(self, rel_ids: list[str], per_edge: int = 3) -> dict[str, list[dict[str, Any]]]: ...


class DuckDBGraphBackend:
    """Concrete backend that pulls edges out of DuckDB into networkx."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con

    # ------------------------------------------------------------------
    # Snapshot materialisation
    # ------------------------------------------------------------------
    def build_snapshot(self, edge_filter: EdgeFilter) -> GraphSnapshot:
        sql, params = self._edges_sql(edge_filter)
        rows = self.con.execute(sql, params).fetchall()

        graph: nx.MultiDiGraph = nx.MultiDiGraph()
        entity_labels: dict[str, dict[str, Any]] = {}
        for row in rows:
            (
                rel_id,
                source_id,
                target_id,
                relation_type,
                confidence,
                evidence_count,
                source_name,
                target_name,
                source_type,
                target_type,
                source_mentions,
                target_mentions,
            ) = row

            evidence_count = int(evidence_count or 0)
            conf = float(confidence or 0.0)
            # Edge weight for shortest-path: lower is better. Stronger confidence
            # and more evidence shrink the weight so good edges win.
            weight = _edge_weight(conf, evidence_count)

            if source_id not in entity_labels:
                entity_labels[source_id] = {
                    "entity_id": source_id,
                    "canonical_name": source_name,
                    "entity_type": source_type,
                    "mention_count": int(source_mentions or 0),
                }
            if target_id not in entity_labels:
                entity_labels[target_id] = {
                    "entity_id": target_id,
                    "canonical_name": target_name,
                    "entity_type": target_type,
                    "mention_count": int(target_mentions or 0),
                }

            graph.add_edge(
                source_id,
                target_id,
                key=rel_id,
                rel_id=rel_id,
                relation_type=relation_type,
                confidence=conf,
                evidence_count=evidence_count,
                weight=weight,
            )

        # Ensure isolated entities from filter-matched subgraph still appear as
        # nodes (they do once they have any edge above, which is the only case
        # we care about for path/centrality/community work).
        for entity_id, meta in entity_labels.items():
            if entity_id in graph:
                graph.nodes[entity_id].update(meta)
        return GraphSnapshot(graph=graph, entity_labels=entity_labels)

    def resolve_entity_id(self, name_or_id: str) -> str | None:
        if not name_or_id:
            return None
        probe = name_or_id.strip()
        if not probe:
            return None
        # Direct primary-key hit.
        row = self.con.execute(
            "SELECT entity_id FROM entities WHERE entity_id = ?",
            [probe],
        ).fetchone()
        if row:
            return row[0]
        # Case-insensitive canonical name or alias.
        lowered = probe.lower()
        row = self.con.execute(
            """
            SELECT e.entity_id
            FROM entities e
            LEFT JOIN entity_aliases ea ON ea.entity_id = e.entity_id
            WHERE lower(e.canonical_name) = ?
               OR lower(ea.alias_text) = ?
            ORDER BY e.mention_count DESC
            LIMIT 1
            """,
            [lowered, lowered],
        ).fetchone()
        return row[0] if row else None

    def one_hop(
        self,
        entity_id: str,
        edge_filter: EdgeFilter,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        sql, params = self._edges_sql(
            edge_filter,
            extra_where="AND (r.source_entity = ? OR r.target_entity = ?)",
            extra_params=[entity_id, entity_id],
            limit=limit,
        )
        rows = self.con.execute(sql, params).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            (
                rel_id,
                source_id,
                target_id,
                relation_type,
                confidence,
                evidence_count,
                source_name,
                target_name,
                source_type,
                target_type,
                source_mentions,
                target_mentions,
            ) = row
            results.append(
                {
                    "rel_id": rel_id,
                    "source": source_id,
                    "target": target_id,
                    "type": relation_type,
                    "confidence": float(confidence or 0.0),
                    "evidence_count": int(evidence_count or 0),
                    "source_entity": {
                        "entity_id": source_id,
                        "canonical_name": source_name,
                        "entity_type": source_type,
                        "mention_count": int(source_mentions or 0),
                    },
                    "target_entity": {
                        "entity_id": target_id,
                        "canonical_name": target_name,
                        "entity_type": target_type,
                        "mention_count": int(target_mentions or 0),
                    },
                }
            )
        return results

    def edge_evidence(
        self,
        rel_ids: list[str],
        per_edge: int = 3,
    ) -> dict[str, list[dict[str, Any]]]:
        if not rel_ids:
            return {}
        placeholders = ",".join(["?"] * len(rel_ids))
        rows = self.con.execute(
            f"""
            WITH ranked AS (
              SELECT
                re.rel_id,
                re.doc_id,
                d.filename,
                re.page,
                re.chunk_id,
                re.span_text,
                re.extraction_method,
                row_number() OVER (
                  PARTITION BY re.rel_id
                  ORDER BY re.page NULLS LAST, re.evidence_id
                ) AS rn
              FROM relationship_evidence re
              JOIN documents d ON d.doc_id = re.doc_id
              WHERE re.rel_id IN ({placeholders})
            )
            SELECT rel_id, doc_id, filename, page, chunk_id, span_text, extraction_method
            FROM ranked
            WHERE rn <= ?
            """,
            [*rel_ids, max(1, per_edge)],
        ).fetchall()
        out: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            out.setdefault(row[0], []).append(
                {
                    "doc_id": row[1],
                    "doc_name": row[2],
                    "page": row[3] if row[3] is not None else "?",
                    "chunk_id": row[4],
                    "span_text": row[5] or "",
                    "extraction_method": row[6],
                }
            )
        return out

    # ------------------------------------------------------------------
    # SQL helpers
    # ------------------------------------------------------------------
    def _edges_sql(
        self,
        edge_filter: EdgeFilter,
        extra_where: str = "",
        extra_params: list[Any] | None = None,
        limit: int | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = ["1 = 1"]
        params: list[Any] = []

        if edge_filter.min_confidence > 0.0:
            clauses.append("r.confidence >= ?")
            params.append(edge_filter.min_confidence)

        if edge_filter.exclude_relation_types:
            placeholders = ",".join(["?"] * len(edge_filter.exclude_relation_types))
            clauses.append(f"upper(r.relation_type) NOT IN ({placeholders})")
            params.extend(t.upper() for t in edge_filter.exclude_relation_types)

        if edge_filter.include_relation_types:
            placeholders = ",".join(["?"] * len(edge_filter.include_relation_types))
            clauses.append(f"upper(r.relation_type) IN ({placeholders})")
            params.extend(t.upper() for t in edge_filter.include_relation_types)

        # Case-scoping: an edge counts as belonging to a case if ANY piece of
        # evidence came from a document in that case.
        case_join = ""
        if edge_filter.case_id:
            case_join = """
                AND EXISTS (
                  SELECT 1 FROM relationship_evidence cre
                  JOIN documents cd ON cd.doc_id = cre.doc_id
                  WHERE cre.rel_id = r.rel_id AND cd.case_id = ?
                )
            """
            params.append(edge_filter.case_id)

        extra_clause = f" {extra_where} " if extra_where else ""

        sql = f"""
            SELECT
              r.rel_id,
              r.source_entity,
              r.target_entity,
              r.relation_type,
              r.confidence,
              count(re.evidence_id) AS evidence_count,
              es.canonical_name AS source_name,
              et.canonical_name AS target_name,
              es.entity_type AS source_type,
              et.entity_type AS target_type,
              es.mention_count AS source_mentions,
              et.mention_count AS target_mentions
            FROM relationships r
            JOIN entities es ON es.entity_id = r.source_entity
            JOIN entities et ON et.entity_id = r.target_entity
            LEFT JOIN relationship_evidence re ON re.rel_id = r.rel_id
            WHERE {" AND ".join(clauses)}
              {case_join}
              {extra_clause}
            GROUP BY
              r.rel_id, r.source_entity, r.target_entity, r.relation_type, r.confidence,
              es.canonical_name, et.canonical_name, es.entity_type, et.entity_type,
              es.mention_count, et.mention_count
        """
        if extra_params:
            params.extend(extra_params)

        if limit is not None:
            sql += " ORDER BY r.confidence DESC, evidence_count DESC LIMIT ?"
            params.append(limit)
        else:
            sql += " ORDER BY r.confidence DESC, evidence_count DESC"

        return sql, params


# ----------------------------------------------------------------------
# Edge weighting
# ----------------------------------------------------------------------
_MIN_CONFIDENCE_FLOOR = 0.05


def _edge_weight(confidence: float, evidence_count: int) -> float:
    """Return the traversal weight for an edge. Lower == preferred.

    We punish low-confidence edges hard (so pathfinder avoids MENTIONED_WITH
    noise) and give a small bonus for edges with multiple corroborating spans.
    """
    safe_conf = max(confidence, _MIN_CONFIDENCE_FLOOR)
    evidence_boost = 1.0 + math.log1p(max(0, evidence_count)) * 0.25
    return 1.0 / (safe_conf * evidence_boost)
