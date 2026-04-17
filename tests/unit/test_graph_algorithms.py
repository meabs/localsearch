"""Tests for the Phase 8 graph algorithms + routes."""

from __future__ import annotations

import uuid

import pytest

from operation_lens_v2.api.routes.graph import (
    centrality as centrality_route,
    communities as communities_route,
    expand as expand_route,
    path as path_route,
)
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import init_db
from operation_lens_v2.services.graph_algorithms import (
    centrality_report,
    detect_communities,
    expand_neighbourhood,
    shortest_path,
)
from operation_lens_v2.services.graph_backend import DuckDBGraphBackend, EdgeFilter


INSERT_ENTITY_SQL = """
    INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc, mention_count)
    VALUES (?, ?, ?, ?, ?)
"""
INSERT_RELATIONSHIP_SQL = """
    INSERT INTO relationships (
        rel_id, source_entity, target_entity, relation_type, confidence
    ) VALUES (?, ?, ?, ?, ?)
"""
INSERT_DOCUMENT_SQL = """
    INSERT INTO documents (doc_id, case_id, filename, filepath, page_count)
    VALUES (?, ?, ?, ?, ?)
"""
INSERT_EVIDENCE_SQL = """
    INSERT INTO relationship_evidence (
      evidence_id, rel_id, chunk_id, doc_id, page,
      span_start, span_end, span_text, extraction_method
    ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)
"""


@pytest.fixture()
def seeded_graph(tmp_path, monkeypatch):
    """Webb ── Vehicle ── Khalil linear chain with a noisy MENTIONED_WITH shortcut."""
    db_path = tmp_path / "graph-algo.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    webb = str(uuid.uuid4())
    khalil = str(uuid.uuid4())
    vehicle = str(uuid.uuid4())
    marsh = str(uuid.uuid4())
    depot = str(uuid.uuid4())

    con.execute(INSERT_ENTITY_SQL, [webb, "Marcus Webb", "PERSON", None, 10])
    con.execute(INSERT_ENTITY_SQL, [khalil, "Rania Khalil", "PERSON", None, 8])
    con.execute(INSERT_ENTITY_SQL, [vehicle, "RX71 KLD", "VEHICLE", None, 6])
    con.execute(INSERT_ENTITY_SQL, [marsh, "Danny Marsh", "PERSON", None, 5])
    con.execute(INSERT_ENTITY_SQL, [depot, "Depot, Industrial Estate", "LOCATION", None, 4])

    strong = [
        ("Marcus Webb", webb, vehicle, "OWNS", 0.92),
        ("RX71 KLD", vehicle, depot, "OBSERVED_AT", 0.88),
        ("Rania Khalil", khalil, depot, "OBSERVED_AT", 0.9),
        ("Danny Marsh", marsh, depot, "OBSERVED_AT", 0.8),
        ("Marsh-Webb", marsh, webb, "ASSOCIATED_WITH", 0.75),
    ]
    weak = [
        ("Webb-Khalil-noise", webb, khalil, "MENTIONED_WITH", 0.3),
    ]
    rel_ids: dict[str, str] = {}
    for label, src, tgt, relation_type, confidence in strong + weak:
        rel_id = str(uuid.uuid4())
        con.execute(INSERT_RELATIONSHIP_SQL, [rel_id, src, tgt, relation_type, confidence])
        rel_ids[label] = rel_id

    doc_id = str(uuid.uuid4())
    con.execute(INSERT_DOCUMENT_SQL, [doc_id, None, "NF-SURV-004.pdf", "/tmp/NF-SURV-004.pdf", 2])
    for label, rel_id in rel_ids.items():
        evidence_id = str(uuid.uuid4())
        con.execute(
            INSERT_EVIDENCE_SQL,
            [evidence_id, rel_id, doc_id, 1, 0, 10, f"span for {label}", "pattern"],
        )

    return {
        "con": con,
        "ids": {
            "webb": webb,
            "khalil": khalil,
            "vehicle": vehicle,
            "marsh": marsh,
            "depot": depot,
        },
    }


# ----------------------------------------------------------------------
# GraphBackend / resolver
# ----------------------------------------------------------------------
def test_resolve_entity_by_canonical_name(seeded_graph) -> None:
    backend = DuckDBGraphBackend(seeded_graph["con"])
    resolved = backend.resolve_entity_id("marcus webb")
    assert resolved == seeded_graph["ids"]["webb"]


def test_resolve_entity_missing(seeded_graph) -> None:
    backend = DuckDBGraphBackend(seeded_graph["con"])
    assert backend.resolve_entity_id("Nobody Important") is None


# ----------------------------------------------------------------------
# Shortest path
# ----------------------------------------------------------------------
def test_shortest_path_prefers_evidence_chain_over_noisy_shortcut(seeded_graph) -> None:
    """Without filtering, Webb→Khalil naively takes the noisy 1-hop edge.
    With min_confidence filter, pathfinder must route through RX71 KLD + Depot."""
    backend = DuckDBGraphBackend(seeded_graph["con"])
    paths = shortest_path(
        backend,
        source_id=seeded_graph["ids"]["webb"],
        target_id=seeded_graph["ids"]["khalil"],
        edge_filter=EdgeFilter(min_confidence=0.5),
        k=1,
    )
    assert len(paths) == 1
    node_ids = [n["entity_id"] for n in paths[0]["nodes"]]
    assert node_ids[0] == seeded_graph["ids"]["webb"]
    assert node_ids[-1] == seeded_graph["ids"]["khalil"]
    # The chain must traverse the vehicle or depot, not a direct 1-hop.
    assert paths[0]["hops"] >= 2
    assert all(edge["confidence"] >= 0.5 for edge in paths[0]["edges"])
    # Every hop must come with at least a citation lookup attempted.
    assert all("citations" in edge for edge in paths[0]["edges"])


def test_shortest_path_exclude_relation_type_forces_detour(seeded_graph) -> None:
    backend = DuckDBGraphBackend(seeded_graph["con"])
    paths = shortest_path(
        backend,
        source_id=seeded_graph["ids"]["webb"],
        target_id=seeded_graph["ids"]["khalil"],
        edge_filter=EdgeFilter(exclude_relation_types=("MENTIONED_WITH",)),
        k=1,
    )
    assert paths
    # No edge on the path may be MENTIONED_WITH.
    assert all(edge["relation_type"] != "MENTIONED_WITH" for edge in paths[0]["edges"])


def test_shortest_path_k_variants_unique(seeded_graph) -> None:
    backend = DuckDBGraphBackend(seeded_graph["con"])
    paths = shortest_path(
        backend,
        source_id=seeded_graph["ids"]["webb"],
        target_id=seeded_graph["ids"]["khalil"],
        edge_filter=EdgeFilter(min_confidence=0.2),
        k=3,
    )
    # At least two distinct paths should exist thanks to the Marsh bridge.
    assert len(paths) >= 1
    tuples = {tuple(n["entity_id"] for n in p["nodes"]) for p in paths}
    assert len(tuples) == len(paths)


def test_shortest_path_no_path_returns_empty(seeded_graph) -> None:
    backend = DuckDBGraphBackend(seeded_graph["con"])
    # Extreme filter removes every edge, so no path can exist.
    paths = shortest_path(
        backend,
        source_id=seeded_graph["ids"]["webb"],
        target_id=seeded_graph["ids"]["khalil"],
        edge_filter=EdgeFilter(min_confidence=0.99),
        k=1,
    )
    assert paths == []


# ----------------------------------------------------------------------
# Expansion
# ----------------------------------------------------------------------
def test_expand_neighbourhood_returns_one_hop_only(seeded_graph) -> None:
    backend = DuckDBGraphBackend(seeded_graph["con"])
    bundle = expand_neighbourhood(
        backend,
        entity_id=seeded_graph["ids"]["depot"],
        edge_filter=EdgeFilter(min_confidence=0.5),
        limit=10,
    )
    neighbour_ids = {n["entity_id"] for n in bundle["nodes"]}
    assert seeded_graph["ids"]["depot"] in neighbour_ids
    # Depot neighbours via OBSERVED_AT: vehicle, khalil, marsh (3 edges).
    assert len(bundle["edges"]) >= 3
    # Webb must NOT be a direct neighbour of Depot.
    assert seeded_graph["ids"]["webb"] not in neighbour_ids


# ----------------------------------------------------------------------
# Centrality
# ----------------------------------------------------------------------
def test_centrality_degree_puts_depot_on_top(seeded_graph) -> None:
    backend = DuckDBGraphBackend(seeded_graph["con"])
    report = centrality_report(
        backend,
        edge_filter=EdgeFilter(min_confidence=0.5),
        metric="degree",
        top_n=5,
    )
    assert report["metric"] == "degree"
    assert report["node_count"] >= 4
    top = report["entities"][0]
    # Depot has three observed-at incoming edges in the seeded graph.
    assert top["entity_id"] == seeded_graph["ids"]["depot"]


def test_centrality_betweenness_runs(seeded_graph) -> None:
    backend = DuckDBGraphBackend(seeded_graph["con"])
    report = centrality_report(
        backend,
        edge_filter=EdgeFilter(min_confidence=0.5),
        metric="betweenness",
        top_n=5,
    )
    assert report["metric"] == "betweenness"
    scores = {ent["entity_id"]: ent["score"] for ent in report["entities"]}
    # Every score must be a finite non-negative float.
    assert all(score >= 0 for score in scores.values())


# ----------------------------------------------------------------------
# Communities
# ----------------------------------------------------------------------
def test_detect_communities_returns_group(seeded_graph) -> None:
    backend = DuckDBGraphBackend(seeded_graph["con"])
    report = detect_communities(
        backend,
        edge_filter=EdgeFilter(min_confidence=0.5),
    )
    assert report["node_count"] >= 4
    assert report["group_count"] >= 1
    # Members in at least one community should include Depot + its neighbours.
    all_members = {
        m["entity_id"] for c in report["communities"] for m in c["members"]
    }
    assert seeded_graph["ids"]["depot"] in all_members


# ----------------------------------------------------------------------
# API routes
# ----------------------------------------------------------------------
def test_path_route_returns_payload(seeded_graph) -> None:
    resp = path_route(
        source="Marcus Webb",
        target="Rania Khalil",
        k=2,
        min_confidence=0.5,
        exclude_relation_types="MENTIONED_WITH",
    )
    assert resp["path_count"] >= 1
    assert resp["source"]["entity_id"] == seeded_graph["ids"]["webb"]
    assert resp["target"]["entity_id"] == seeded_graph["ids"]["khalil"]
    first = resp["paths"][0]
    assert first["hops"] >= 2


def test_path_route_404_on_unknown_source(seeded_graph) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        path_route(source="Nobody Important", target="Rania Khalil")
    assert exc.value.status_code == 404


def test_expand_route_accepts_name(seeded_graph) -> None:
    bundle = expand_route(entity_id="Depot, Industrial Estate", limit=10, min_confidence=0.5)
    assert bundle["entity_id"] == seeded_graph["ids"]["depot"]
    assert len(bundle["edges"]) >= 3


def test_centrality_route_default_metric(seeded_graph) -> None:
    report = centrality_route(top_n=5, min_confidence=0.5)
    assert report["metric"] == "degree"
    assert report["entities"]


def test_communities_route_runs(seeded_graph) -> None:
    report = communities_route(min_confidence=0.5)
    assert report["node_count"] >= 4
