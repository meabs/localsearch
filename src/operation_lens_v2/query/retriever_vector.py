from __future__ import annotations

import logging
from typing import Any

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.embedder import embed_text
from operation_lens_v2.runtime import get_vector_store

logger = logging.getLogger(__name__)


def _distance_to_similarity(distance: float) -> float:
    """Convert a lower-is-better vector distance into a higher-is-better score."""
    return 1.0 / (1.0 + max(distance, 0.0))


async def retrieve_vector(query: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Vector similarity search against LanceDB using nomic-embed-text embeddings."""
    top_k = limit if limit is not None else settings.vector_top_k
    store = get_vector_store(settings.lancedb_path)
    stored_dim = store.vector_dim()
    vec = await embed_text(query)
    if stored_dim is not None and stored_dim != len(vec):
        logger.warning(
            "Vector search skipped due to embedding dimension mismatch: query=%d table=%d. "
            "Rebuild vector index with current embedding model to restore vector search.",
            len(vec),
            stored_dim,
        )
        return []
    rows = store.search(vec, top_k=top_k)
    return [
        {
            "source": "vector",
            "chunk_id": row.get("chunk_id"),
            "doc_id": row.get("doc_id"),
            "page": row.get("page"),
            "text": row.get("text", ""),
            "source_label": row.get("source_label"),
            "provenance_type": row.get("provenance_type"),
            "score": _distance_to_similarity(float(row.get("_distance", 0.0))),
            "distance": float(row.get("_distance", 0.0)),
        }
        for row in rows
    ]
