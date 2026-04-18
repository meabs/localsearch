from __future__ import annotations

from uuid import uuid4

from operation_lens_v2.models import Chunk


def _approx_token_count(text: str) -> int:
    return max(1, len(text.split()))


def chunk_pages(
    doc_id: str,
    pages: list[tuple[int, str]],
    *,
    target_tokens: int = 320,
    overlap_ratio: float = 0.2,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    chunk_index = 0
    stride = max(1, int(target_tokens * (1 - overlap_ratio)))

    for page_no, text in pages:
        words = text.split()
        if not words:
            continue
        start = 0
        while start < len(words):
            end = min(len(words), start + target_tokens)
            chunk_text = " ".join(words[start:end])
            chunks.append(
                Chunk(
                    chunk_id=str(uuid4()),
                    doc_id=doc_id,
                    page=page_no,
                    chunk_index=chunk_index,
                    text=chunk_text,
                    token_count=_approx_token_count(chunk_text),
                )
            )
            chunk_index += 1
            if end == len(words):
                break
            start += stride
    return chunks


def chunk_text_blocks(
    doc_id: str,
    text_blocks: list[object],
    *,
    target_tokens: int = 320,
    overlap_ratio: float = 0.2,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    chunk_index = 0
    stride = max(1, int(target_tokens * (1 - overlap_ratio)))

    for block in text_blocks:
        text = getattr(block, "text", "")
        words = text.split()
        if not words:
            continue
        start = 0
        page_no = int(getattr(block, "page", 1) or 1)
        source_label = str(getattr(block, "source_label", "") or "")
        provenance_type = str(getattr(block, "provenance_type", "native_text") or "native_text")
        while start < len(words):
            end = min(len(words), start + target_tokens)
            chunk_text = " ".join(words[start:end])
            chunks.append(
                Chunk(
                    chunk_id=str(uuid4()),
                    doc_id=doc_id,
                    page=page_no,
                    chunk_index=chunk_index,
                    text=chunk_text,
                    token_count=_approx_token_count(chunk_text),
                    source_label=source_label,
                    provenance_type=provenance_type,
                )
            )
            chunk_index += 1
            if end == len(words):
                break
            start += stride
    return chunks
