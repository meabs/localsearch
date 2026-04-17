from __future__ import annotations

import asyncio
import logging

from operation_lens_v2.config import settings
from operation_lens_v2.runtime import get_http_client

logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = 2.0  # seconds


async def embed_text(text: str) -> list[float]:
    """Embed a single text using nomic-embed-text via Ollama."""
    client = get_http_client(base_url=settings.ollama_base_url, timeout=settings.ollama_timeout)
    payload = {"model": settings.local_embed_model, "prompt": text}
    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = await client.post("/api/embeddings", json=payload)
            resp.raise_for_status()
            data = resp.json()
            if "embedding" not in data:
                raise ValueError(f"Ollama response missing 'embedding' key: {data}")
            return data["embedding"]
        except Exception as exc:
            last_exc = exc
            logger.warning("embed_text attempt %d/%d failed: %s", attempt + 1, _RETRY_ATTEMPTS, exc)
            if attempt < _RETRY_ATTEMPTS - 1:
                await asyncio.sleep(_RETRY_BACKOFF)
    raise RuntimeError(f"embed_text failed after {_RETRY_ATTEMPTS} attempts") from last_exc


async def embed_batch(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Embed a list of texts in batches to avoid OOM."""
    results: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        for text in batch:
            results.append(await embed_text(text))
    return results
