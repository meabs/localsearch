# Operation Lens v2

Operation Lens v2 is a local-first evidence analysis platform for:

- ingesting mixed evidence formats
- extracting entities and relationships
- querying with grounded citations
- demonstrating value quickly with a synthetic demo case
- exporting readable case briefings

## Feature overview

### Evidence and ingestion

- mixed-format evidence ingestion for PDF, TXT, CSV, JSON, EML, HTML, and common image formats
- normalized provenance across native text, OCR-derived text, and metadata-derived facts
- optional local image OCR, EXIF extraction, and perceptual-hash duplicate detection
- case-scoped ingestion with local storage and idempotent ingest behavior

### Analysis

- entity extraction, alias normalization, and typed relationship extraction
- exact, full-text, vector, and graph retrieval in one query flow
- entity graph exploration, profile views, and timeline extraction
- domain packs that extend the base schema without forking the ingestion pipeline

### UI and demoability

- browser UI for case management, querying, graph inspection, and dashboard widgets
- guided synthetic demo case with a ready-made query path
- domain-aware templates and dashboard defaults
- case briefing export in Markdown, HTML, and PDF

The stack stays local by default:

- FastAPI for the API and UI shell
- DuckDB for structured evidence and graph state
- LanceDB for embeddings
- Ollama for local embedding, extraction, and reasoning

Cloud reasoning remains optional and disabled by default.

## Quickstart

### 1. Bootstrap the project

```powershell
python scripts/bootstrap_local.py
```

That script is idempotent and will:

- verify Python
- create `.venv` if needed
- install dependencies
- create `config/.env` from the example if missing
- check Ollama reachability
- check required models
- initialize local storage and DuckDB

### 2. Activate the environment

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Load the synthetic demo case

```powershell
python scripts/load_demo_case.py
```

Demo case:

- case ref: `OP_DEMO_SIGNAL`
- domain pack: `investigations`
- suggested query:
  `What connects Lena Hart to South Quay Locker and what evidence supports that connection?`

### 4. Start the API

```powershell
uvicorn operation_lens_v2.api.main:app --reload
```

### 5. Open the UI

- [http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui)

From there you can:

- open the demo case
- run the guided demo query
- inspect domain-aware templates
- explore the graph, timeline, and case dashboard
- export a briefing in Markdown, HTML, or PDF

## Supported evidence formats

- PDF
- TXT / log / markdown
- CSV
- JSON
- EML
- HTML
- common image formats with optional local OCR and EXIF extraction

All formats normalize into the same local provenance flow:

- `documents`
- `chunks`
- citations
- derived facts
- parser warnings

## Export briefing

Case briefing export is available at:

- `POST /cases/{case_ref}/export?format=md`
- `POST /cases/{case_ref}/export?format=html`
- `POST /cases/{case_ref}/export?format=pdf`

Exports are written under `data/exports/<case_ref>/`.

## Domain packs

Domain packs live under `config/domain_packs/` and extend `config/entity_schema.json`.

Current packs:

- `investigations`
- `fraud_finance`
- `cyber_infra`
- `supply_chain`

Each case can select a pack, inspect its resolved schema, and automatically receive pack-aware templates and dashboard widgets.

## Analyst workflow at a glance

1. Create or open a case.
2. Select the right domain pack.
3. Upload mixed evidence locally.
4. Ask a briefing question and inspect citations.
5. Review graph, timeline, and dashboard views.
6. Export a readable case briefing.

## Useful scripts

- `python scripts/bootstrap_local.py`
- `python scripts/check_models.py`
- `python scripts/load_demo_case.py`
- `python scripts/check_env.py`

## Useful endpoints

- `GET /health`
- `GET /config/domain-packs`
- `GET /cases`
- `POST /cases`
- `POST /cases/{case_ref}/domain-pack`
- `GET /cases/{case_ref}/resolved-schema`
- `GET /cases/{case_ref}/dashboard-config`
- `POST /ingest/upload`
- `POST /query`
- `GET /query/templates?case_ref=...`
- `POST /cases/{case_ref}/export?format=md|html|pdf`

## Documentation map

- [Quickstart](docs/quickstart.md)
- [Architecture](docs/architecture.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Tuning](docs/tuning.md)
- [Eval](docs/eval.md)
- [Domain Packs](docs/domain_packs.md)
- [Exports](docs/exports.md)
- [Phase 3 notes](docs/phase3.md)
- [Phase 4 notes](docs/phase4.md)

## Testing

```powershell
python -m ruff check .
python -m pytest
```

If graph tests fail during collection, check that optional local dependencies like `networkx` were installed into the active virtual environment.

## License

This repository is released under `PolyForm Noncommercial 1.0.0`.

- See [LICENSE](LICENSE)
- See [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)
