"""Storage backends (graph, and future vector / audit adapters)."""

from __future__ import annotations

import duckdb

from operation_lens_v2.store.graph_backend import DuckGraphBackend, GraphBackend


def get_graph_backend(con: duckdb.DuckDBPyConnection) -> GraphBackend:
    """Return the default graph backend for a DuckDB connection."""
    return DuckGraphBackend(con)


__all__ = ["DuckGraphBackend", "GraphBackend", "get_graph_backend"]
