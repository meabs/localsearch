# Tuning

## Core tuning knobs

Most runtime tuning lives in `config/.env`.

High-impact settings:

- `CHUNK_TARGET_TOKENS`
- `CHUNK_OVERLAP_TOKENS`
- `VECTOR_TOP_K`
- `FTS_TOP_K`
- `RERANK_TOP_N`
- `ALIAS_THRESHOLD`
- `GLINER_THRESHOLD`

## Domain tuning

Prefer schema and domain-pack edits before code changes:

- add entity prompts
- add regex extractors
- adjust normalization strategies
- add relation hints
- add deterministic relation patterns

## Retrieval tuning

If recall is weak:

- increase `VECTOR_TOP_K`
- increase `FTS_TOP_K`
- increase `RERANK_TOP_N`

If precision is noisy:

- tighten entity schema regexes
- reduce overly broad prompts
- review relationship patterns
