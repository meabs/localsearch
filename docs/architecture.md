# Architecture Notes

## Source Of Truth

- DuckDB stores documents, chunks, entities, aliases, relationships, relationship evidence, and audit tables.
- LanceDB stores vector embeddings keyed by `chunk_id`.
- Ollama is the default runtime for all local reasoning agents.
- OpenRouter is optional and should only be used when a request explicitly enables cloud reasoning.

## Runtime Components

- `api/` exposes FastAPI routes for ingest, query, graph, cases, timeline, and audit.
- `runtime.py` owns shared process resources such as the DuckDB connection and HTTP clients.
- `frontend/` provides the browser UI, including the scope selector for document, case, and corpus investigation.
- `query/` contains the parser, scope enforcement, investigator tools, writer, retrieval stack, and claim validation.

## Data Flow

### Ingestion pipeline

- Extract PDF text.
- Chunk text into retrievable spans.
- Run entity extraction and alias normalization.
- Extract typed relationships and supporting evidence.
- Persist graph data to DuckDB.
- Generate embeddings and persist them to LanceDB.

### Query pipeline

- Parse the user question and requested scope.
- Enforce scope as `document`, `case`, or `corpus`.
- Route to one of two answer paths:
  - Investigator path for narrative briefings and broader analytical questions.
  - Legacy retrieval path for deterministic retrieval, reranking, and claim validation.

## Investigator Agent

- The investigator agent is implemented with PydanticAI and targets Ollama through its OpenAI-compatible `/v1` interface.
- Tools are thin async wrappers over local DuckDB and LanceDB queries.
- Every tool enforces the active scope at the data layer rather than relying on prompt instructions alone.
- The investigator produces a structured `InvestigationReport`.
- A local writer model turns that report into the final briefing shown in the UI.

### Investigator toolset

- `search_entities`
- `get_entity_profile`
- `get_relationships`
- `get_cooccurrence`
- `get_timeline`
- `fetch_chunk`
- `list_documents`
- `get_document_entities`
- `walk_graph`

## Scope Model

- `document` scope restricts the agent to one or more explicit `doc_id` values.
- `case` scope restricts the agent to documents tagged with a given `case_scope`.
- `corpus` scope allows investigation across the whole indexed corpus.
- Scope violations raise explicit errors so the agent can retry with valid in-scope identifiers.

## Local-First Reasoning Policy

- The system should prefer Ollama-backed local models first.
- OpenRouter is disabled by default in local configuration.
- Even when cloud reasoning is enabled, local output should win when a local answer succeeds.
- Any OpenRouter call must redact span text and avoid sending raw evidence outside the machine.

## Current Model Roles

- `LOCAL_REASONING_MODEL`: `deepseek-r1:latest` for the legacy local answer path.
- `INVESTIGATOR_MODEL`: `gemma4:26b` for the tool-using investigator loop.
- `WRITER_MODEL`: `deepseek-r1:latest` for deeper narrative briefing synthesis.
- `CRITIC_MODEL`: `llama3.1:8b-instruct-q4_K_M` for lightweight critique and structured checks.
- `LOCAL_EXTRACTION_MODEL`: `llama3.1:8b-instruct-q4_K_M` for claim extraction and validation.
- `LOCAL_EMBED_MODEL`: embedding generation for LanceDB.

### Why this split

- `gemma4:26b` is the default investigator because it supports tool calling over Ollama `/v1` in the current local setup.
- `deepseek-r1:latest` remains the writer and legacy reasoning model because it gives richer narrative analysis when tools are not required.
- Smaller 8B models stay on critic and extraction work to keep validation passes cheap and responsive.

## Response Paths

### Investigator path

- Scope-aware query enters `run_investigator_query`.
- The investigator gathers evidence through tools and returns `InvestigationReport`.
- The writer composes a narrative briefing with sections such as `ASSESSMENT`, `KEY FINDINGS`, and `CONFIDENCE POSTURE`.

### Legacy path

- Query enters `run_query`.
- Exact, FTS, vector, and graph retrieval produce candidate evidence.
- Reranking and evidence-packet assembly feed the local answer generator.
- Claim validation checks extracted claims against cited evidence spans before returning the answer.

## Operational Notes

- Keep Ollama models installed and aligned with `config/.env`.
- Restart the API after changing `.env` so model routing changes take effect.
- On Windows, reuse the shared DuckDB runtime connection to avoid file locking between routes.
