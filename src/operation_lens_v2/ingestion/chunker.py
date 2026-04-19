"""Semantic chunker — real BPE tokens via tiktoken (cl100k_base).

Chunks are sized in true embedding-space tokens, not whitespace words, so
downstream embedding calls and the evidence-packet budget stay accurate.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from uuid import uuid4

from operation_lens_v2.models import Chunk

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _encoder():
    """Return the cl100k_base encoder (shared with OpenAI/embedding models)."""
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Return the true BPE token count for `text` (cl100k_base)."""
    if not text:
        return 0
    try:
        return len(_encoder().encode(text))
    except Exception as exc:
        logger.warning("tiktoken unavailable (%s); falling back to whitespace approx", exc)
        return max(1, len(text.split()))


def chunk_pages(
    doc_id: str,
    pages: list[tuple[int, str]],
    *,
    target_tokens: int = 320,
    overlap_ratio: float = 0.2,
    max_tokens: int | None = None,
) -> list[Chunk]:
    """Split pages into overlapping chunks sized by BPE tokens.

    `max_tokens` is a hard cap; defaults to `target_tokens * 1.15` (slight slack
    so the tail chunk isn't forced to split aggressively).
    """
    if target_tokens <= 0:
        raise ValueError("target_tokens must be positive")
    hard_cap = max_tokens if max_tokens is not None else int(target_tokens * 1.15)
    overlap_tokens = max(0, int(target_tokens * overlap_ratio))
    stride = max(1, target_tokens - overlap_tokens)

    chunks: list[Chunk] = []
    chunk_index = 0
    encoder = _encoder()

    for page_no, text in pages:
        if not text:
            continue
        try:
            tokens = encoder.encode(text)
        except Exception as exc:
            logger.warning("tiktoken encode failed on page %s (%s); skipping page", page_no, exc)
            continue
        if not tokens:
            continue

        start = 0
        while start < len(tokens):
            end = min(len(tokens), start + target_tokens)
            # Enforce hard cap defensively (target_tokens <= hard_cap by construction).
            if end - start > hard_cap:
                end = start + hard_cap
            window = tokens[start:end]
            chunk_text = encoder.decode(window)
            chunks.append(
                Chunk(
                    chunk_id=str(uuid4()),
                    doc_id=doc_id,
                    page=page_no,
                    chunk_index=chunk_index,
                    text=chunk_text,
                    token_count=len(window),
                )
            )
            chunk_index += 1
            if end == len(tokens):
                break
            start += stride
    return chunks
