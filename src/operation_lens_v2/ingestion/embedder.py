from __future__ import annotations

import asyncio
import logging

from operation_lens_v2.config import settings
from operation_lens_v2.runtime import get_http_client

logger = logging.getLogger(__name__)


async def embed_text(text: str) -> list[float]:
    """Embed a single text using nomic-embed-text via Ollama."""
    client = get_http_client(base_url=settings.ollama_base_url, timeout=settings.ollama_timeout)
    payload = {"model": settings.local_embed_model, "prompt": text}
    attempts = settings.embed_retry_attempts
    backoff = settings.embed_retry_backoff
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = await client.post("/api/embeddings", json=payload)
            resp.raise_for_status()
            data = resp.json()
            if "embedding" not in data:
                raise ValueError(f"Ollama response missing 'embedding' key: {data}")
            return data["embedding"]
        except Exception as exc:
            last_exc = exc
            logger.warning("embed_text attempt %d/%d failed: %s", attempt + 1, attempts, exc)
            if attempt < attempts - 1:
                await asyncio.sleep(backoff)
    raise RuntimeError(f"embed_text failed after {attempts} attempts") from last_exc


async def embed_batch(
    texts: list[str], batch_size: int | None = None
) -> list[list[float]]:
    """Embed a list of texts, issuing up to `batch_size` requests in parallel."""
    if not texts:
        return []
    size = batch_size if batch_size is not None else settings.embed_batch_size
    size = max(1, size)
    results: list[list[float]] = []
    for i in range(0, len(texts), size):
        batch = texts[i : i + size]
        batch_results = await asyncio.gather(*(embed_text(t) for t in batch))
        results.extend(batch_results)
    return results
