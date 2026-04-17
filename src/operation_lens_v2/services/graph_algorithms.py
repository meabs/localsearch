"""Graph algorithms: shortest / k-shortest paths, centrality, communities.

Algorithms take a GraphBackend and return serialisable dicts — not networkx
objects — so the API layer can ship results to the UI without further
translation.
"""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from operation_lens_v2.services.graph_backend import (
    EdgeFilter,
    GraphBackend,
    GraphSnapshot,
)

logger = logging.getLogger(__name__)

# Betweenness on the full graph is O(N*E). For anything beyond a few hundred
# nodes we sample pivots rather than exhaust them — a well-known networkx
# convention. Upper bound keeps response time predictable.
_BETWEENNESS_SAMPLE_CEILING = 200

# Community detection works on an undirected view — direction rarely matters
# for "who clusters with whom" questions.
_DEFAULT_COMMUNITY_RESOLUTION = 1.0


# ----------------------------------------------------------------------
# Path finding
# ----------------------------------------------------------------------
def shortest_path(
    backend: GraphBackend,
    *,
    source_id: str,
    target_id: str,
    edge_filter: EdgeFilter,
    k: int = 1,
    cutoff_hops: int | None = None,
) -> list[dict[str, Any]]:
    """Return up to k confidence-weighted paths between two entities.

    Each result carries node sequence + the real edges walked, so the UI can
    reconstruct every hop back to its source evidence.
    """
    if not source_id or not target_id or source_id == target_id:
        return []

    snapshot = backend.build_snapshot(edge_filter)
    graph = snapshot.graph
    if source_id not in graph or target_id not in graph:
        return []

    # Investigative pathfinding treats edges as undirected — a vehicle
    # observed at a depot is reachable *from* the depot for link analysis,
    # even though the stored edge is Vehicle→Location.
    simple = _collapse_to_undirected(graph)

    paths: list[list[str]] = []
    try:
        generator = nx.shortest_simple_paths(simple, source_id, target_id, weight="weight")
        for path in generator:
            if cutoff_hops is not None and len(path) - 1 > cutoff_hops:
                break
            paths.append(path)
            if len(paths) >= max(1, k):
                break
    except nx.NetworkXNoPath:
        return []
    except nx.NodeNotFound:
        return []

    rel_ids_needed: list[str] = []
    hydrated: list[dict[str, Any]] = []
    for path in paths:
        edges_on_path: list[dict[str, Any]] = []
        total_weight = 0.0
        min_conf = 1.0
        for a, b in zip(path, path[1:], strict=False):
            best = _best_edge_between(graph, a, b)
            if best is None:
                edges_on_path = []
                break
            rel_ids_needed.append(best["rel_id"])
            edges_on_path.append(best)
            total_weight += float(best["weight"])
            min_conf = min(min_conf, float(best["confidence"]))
        if not edges_on_path:
            continue
        hydrated.append(
            {
                "nodes": [snapshot.node_info(node_id) for node_id in path],
                "edges": edges_on_path,
                "total_weight": round(total_weight, 4),
                "min_confidence": round(min_conf, 4),
                "hops": len(edges_on_path),
            }
        )

    # Attach citations so the UI can render spans directly under each hop.
    evidence_map = backend.edge_evidence(rel_ids_needed)
    for path in hydrated:
        for edge in path["edges"]:
            edge["citations"] = evidence_map.get(edge["rel_id"], [])
    return hydrated


def _collapse_to_simple(graph: nx.MultiDiGraph) -> nx.DiGraph:
    simple: nx.DiGraph = nx.DiGraph()
    for u, v, data in graph.edges(data=True):
        weight = float(data.get("weight", 1.0))
        if simple.has_edge(u, v):
            if weight < float(simple[u][v]["weight"]):
                simple[u][v].update(data)
                simple[u][v]["weight"] = weight
        else:
            simple.add_edge(u, v, **data)
    return simple


def _collapse_to_undirected(graph: nx.MultiDiGraph) -> nx.Graph:
    """Flatten the directed multigraph to an undirected view, keeping the
    strongest (lowest-weight) edge between any two nodes regardless of
    orientation. Pathfinding and centrality run on this view."""
    undirected: nx.Graph = nx.Graph()
    for u, v, data in graph.edges(data=True):
        weight = float(data.get("weight", 1.0))
        if undirected.has_edge(u, v):
            if weight < float(undirected[u][v].get("weight", 1.0)):
                undirected[u][v].clear()
                undirected[u][v].update(data)
        else:
            undirected.add_edge(u, v, **data)
    return undirected


def _best_edge_between(graph: nx.MultiDiGraph, u: str, v: str) -> dict[str, Any] | None:
    """Pick the lightest-weight edge between two nodes, considering both
    directions — the simple view was built from the directed multigraph, so
    the walker may have crossed an edge in either orientation."""
    candidates: list[tuple[dict[str, Any], str]] = []
    if graph.has_edge(u, v):
        for data in graph.get_edge_data(u, v).values():
            candidates.append((data, "forward"))
    if graph.has_edge(v, u):
        for data in graph.get_edge_data(v, u).values():
            candidates.append((data, "reverse"))
    if not candidates:
        return None
    best_data, direction = min(candidates, key=lambda pair: float(pair[0].get("weight", 1.0)))
    source = u if direction == "forward" else v
    target = v if direction == "forward" else u
    return {
        "rel_id": best_data.get("rel_id"),
        "relation_type": best_data.get("relation_type"),
        "confidence": float(best_data.get("confidence", 0.0)),
        "evidence_count": int(best_data.get("evidence_count", 0)),
        "weight": float(best_data.get("weight", 1.0)),
        "direction": direction,
        "source": source,
        "target": target,
    }


# ----------------------------------------------------------------------
# Neighbourhood expansion
# ----------------------------------------------------------------------
def expand_neighbourhood(
    backend: GraphBackend,
    *,
    entity_id: str,
    edge_filter: EdgeFilter,
    limit: int = 30,
) -> dict[str, Any]:
    """One-hop expansion — the click-to-expand primitive for the UI.

    Returns a minimal node+edge bundle ready to merge into an existing graph
    without re-fetching the whole subgraph.
    """
    edges = backend.one_hop(entity_id, edge_filter, limit=limit)
    rel_ids = [e["rel_id"] for e in edges]
    evidence_map = backend.edge_evidence(rel_ids)
    nodes: dict[str, dict[str, Any]] = {}
    slim_edges: list[dict[str, Any]] = []
    for edge in edges:
        for key in ("source_entity", "target_entity"):
            node = edge[key]
            nodes.setdefault(node["entity_id"], node)
        slim_edges.append(
            {
                "rel_id": edge["rel_id"],
                "source": edge["source"],
                "target": edge["target"],
                "type": edge["type"],
                "confidence": edge["confidence"],
                "evidence_count": edge["evidence_count"],
                "citations": evidence_map.get(edge["rel_id"], []),
            }
        )
    return {
        "entity_id": entity_id,
        "nodes": list(nodes.values()),
        "edges": slim_edges,
    }


# ----------------------------------------------------------------------
# Centrality
# ----------------------------------------------------------------------
def centrality_report(
    backend: GraphBackend,
    *,
    edge_filter: EdgeFilter,
    metric: str = "degree",
    top_n: int = 20,
) -> dict[str, Any]:
    """Rank entities by a centrality metric.

    degree       — mentions-adjusted connection count (fast, always on)
    betweenness  — brokerage; who sits on the most shortest paths
    pagerank     — steady-state importance under weighted random walk
    """
    metric = metric.lower().strip()
    snapshot = backend.build_snapshot(edge_filter)
    graph = snapshot.graph
    if graph.number_of_nodes() == 0:
        return {"metric": metric, "entities": [], "node_count": 0, "edge_count": 0}

    undirected = _collapse_to_undirected(graph)
    if metric == "betweenness":
        sample = (
            None
            if undirected.number_of_nodes() <= _BETWEENNESS_SAMPLE_CEILING
            else _BETWEENNESS_SAMPLE_CEILING
        )
        scores = nx.betweenness_centrality(undirected, k=sample, weight="weight", normalized=True)
    elif metric == "pagerank":
        scores = nx.pagerank(undirected, weight="weight")
    else:  # degree (default)
        metric = "degree"
        scores = nx.degree_centrality(undirected)

    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)[: max(1, top_n)]
    entities = [
        {
            **snapshot.node_info(node_id),
            "score": round(float(score), 6),
            "degree": undirected.degree(node_id),
        }
        for node_id, score in ranked
    ]
    return {
        "metric": metric,
        "entities": entities,
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
    }


# ----------------------------------------------------------------------
# Community detection
# ----------------------------------------------------------------------
def detect_communities(
    backend: GraphBackend,
    *,
    edge_filter: EdgeFilter,
    resolution: float = _DEFAULT_COMMUNITY_RESOLUTION,
    seed: int = 17,
    max_communities: int = 40,
) -> dict[str, Any]:
    """Louvain community detection. Returns at most `max_communities` groups,
    ordered by size. Tiny 1-node components are folded into a residual bucket.
    """
    snapshot = backend.build_snapshot(edge_filter)
    graph = snapshot.graph
    if graph.number_of_nodes() == 0:
        return {"communities": [], "modularity": 0.0, "node_count": 0}

    undirected = _collapse_to_undirected(graph)
    try:
        partitions = nx.community.louvain_communities(
            undirected, weight="weight", resolution=resolution, seed=seed
        )
    except Exception as exc:  # noqa: BLE001 — surface a clean response, not a 500.
        logger.warning("Louvain failed (%s); falling back to connected components", exc)
        partitions = list(nx.connected_components(undirected))

    groups = sorted((set(p) for p in partitions), key=len, reverse=True)
    materialised: list[dict[str, Any]] = []
    for idx, group in enumerate(groups[:max_communities]):
        if len(group) < 2:
            continue
        members = [snapshot.node_info(node_id) for node_id in group]
        members.sort(key=lambda meta: int(meta.get("mention_count", 0) or 0), reverse=True)
        materialised.append(
            {
                "community_id": idx,
                "size": len(group),
                "members": members[:25],
                "member_count_shown": min(len(group), 25),
            }
        )

    try:
        modularity = float(
            nx.community.modularity(undirected, groups, weight="weight", resolution=resolution)
        )
    except Exception:  # noqa: BLE001
        modularity = 0.0

    return {
        "communities": materialised,
        "modularity": round(modularity, 4),
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "group_count": len(groups),
    }
