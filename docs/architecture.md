# Architecture

Operation Lens is a local-first analysis stack built around one evidence flow rather than separate product silos.

## Core runtime

- FastAPI serves the API and the browser UI.
- DuckDB stores cases, documents, chunks, entities, aliases, relationships, facts, warnings, and audits.
- LanceDB stores chunk embeddings keyed by `chunk_id`.
- Ollama provides local embedding, extraction, and reasoning models.

## Ingestion path

Every supported evidence type is normalized into:

- `document_id`
- `source_type`
- `source_metadata`
- `text_blocks`
- `attachments`
- `derived_facts`
- `parser_warnings`

Those text blocks are chunked and pushed into the same retrieval path used by PDFs, which keeps search, citation, and export behavior consistent.

## Schema and domain packs

- Base extraction behavior lives in `config/entity_schema.json`.
- Domain packs live in `config/domain_packs/*.json`.
- Case-level selection determines the resolved schema used during ingest and UI template/dashboard defaults.

## Query path

The query layer combines:

- exact identifier matches
- full-text search
- vector retrieval
- graph retrieval

Results are reranked and returned with citations from the local corpus.

## Demoability

Phase 4 adds a deterministic synthetic demo case that is generated locally and ingested through the normal pipeline. This means the demo exercises the real application flow rather than a special-case showcase path.
