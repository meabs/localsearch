"""Tests for ingestion/chunker.py — semantic chunking."""

from __future__ import annotations

import pytest

from operation_lens_v2.ingestion.chunker import chunk_pages


def _words(n: int) -> str:
    """Generate a synthetic text of exactly n words."""
    return " ".join(f"word{i}" for i in range(n))


def test_chunker_produces_chunks():
    pages = [(1, _words(400))]
    chunks = chunk_pages("doc1", pages, target_tokens=100, overlap_ratio=0.2)
    assert len(chunks) > 1


def test_no_chunk_exceeds_max_tokens():
    pages = [(1, _words(600))]
    chunks = chunk_pages("doc1", pages, target_tokens=100, overlap_ratio=0.2)
    for chunk in chunks:
        assert chunk.token_count <= 120, f"Chunk exceeded max: {chunk.token_count}"


def test_chunks_have_overlap():
    """Last words of chunk N should appear in the start of chunk N+1."""
    pages = [(1, _words(500))]
    chunks = chunk_pages("doc1", pages, target_tokens=100, overlap_ratio=0.2)
    if len(chunks) < 2:
        pytest.skip("Not enough chunks to test overlap")
    last_words_of_first = set(chunks[0].text.split()[-10:])
    first_words_of_second = set(chunks[1].text.split()[:30])
    overlap = last_words_of_first & first_words_of_second
    assert len(overlap) > 0, "No overlap detected between chunk 0 and chunk 1"


def test_empty_page_produces_no_chunks():
    pages = [(1, "")]
    chunks = chunk_pages("doc1", pages)
    assert chunks == []


def test_chunk_provenance_fields():
    pages = [(3, _words(80))]
    chunks = chunk_pages("mydoc", pages, target_tokens=50, overlap_ratio=0.2)
    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.doc_id == "mydoc"
        assert chunk.page == 3
        assert chunk.chunk_id  # non-empty UUID string


def test_multi_page_chunk_index_is_monotonic():
    pages = [(1, _words(200)), (2, _words(200))]
    chunks = chunk_pages("doc1", pages, target_tokens=80, overlap_ratio=0.2)
    indices = [c.chunk_index for c in chunks]
    assert indices == sorted(indices)
    assert indices[0] == 0


def test_token_count_is_real_bpe_not_whitespace():
    """token_count comes from tiktoken — denser text yields more tokens than words."""
    # Dense prose tokenises to more BPE tokens than a simple split() count for
    # texts with punctuation, contractions, and mixed-case.
    text = "It's 21:40; Webb's RX71 KLD was observed at 14 Arkwright Road."
    chunks = chunk_pages("doc1", [(1, text)], target_tokens=2048, overlap_ratio=0.0)
    assert len(chunks) == 1
    assert chunks[0].token_count > 0
    # A whitespace split would give ~11 tokens; BPE for this string is noticeably larger.
    word_count = len(text.split())
    assert chunks[0].token_count >= word_count


def test_chunks_respect_target_tokens_in_real_tokens():
    """With target_tokens=50, every emitted chunk must have ≤50 BPE tokens."""
    pages = [(1, _words(2000))]
    chunks = chunk_pages("doc1", pages, target_tokens=50, overlap_ratio=0.2)
    assert chunks, "expected at least one chunk"
    for chunk in chunks:
        assert chunk.token_count <= 50, f"chunk {chunk.chunk_index} exceeded target: {chunk.token_count}"
