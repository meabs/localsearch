# Architecture Notes

## Source Of Truth

- DuckDB stores documents, chunks, entities, aliases, relationships, relationship evidence, and audit tables.
- LanceDB stores vector embeddings keyed by `chunk_id`.

## Pipelines

- Ingestion: extract -> chunk -> NER -> normalize -> relationship extraction -> persist.
- Query: parse -> exact/fts/vector/graph retrieve -> rerank -> evidence packet -> answer -> claim validation.
