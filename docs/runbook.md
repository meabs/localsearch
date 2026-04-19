# Operation Lens v2 — Runbook

Operating notes for a running instance. For installation and first-time setup, see the
[main README](https://github.com/meabs/localsearch#readme).

## Environment

- Python 3.11+ in a virtual environment.
- Install the package with dev extras: `pip install -e .[dev]`.
- `config/.env` copied from `config/.env.example` and edited for local paths and model names.
- Ollama running (`ollama serve`) with the models referenced in `config/.env`.

## Start the server

```powershell
uvicorn operation_lens_v2.api.main:app --reload
```

Then open `http://127.0.0.1:8000/ui`.

Health check:

```powershell
curl http://127.0.0.1:8000/health
```

## Ingest

Single file:

```powershell
curl -X POST http://127.0.0.1:8000/ingest/file `
  -H "Content-Type: application/json" `
  -d '{"pdf_path":"data/pdfs/sample.pdf","case_ref":"OP_TEST"}'
```

Batch via helper (reads top-level files in `data/pdfs/`, infers `case_ref` from filename prefix):

```powershell
.\.venv\Scripts\python scripts\ingest_cases.py
```

Force re-ingest of already-indexed files:

```powershell
$env:INGEST_FORCE="true"
.\.venv\Scripts\python scripts\ingest_cases.py
```

## Query

```powershell
curl -X POST http://127.0.0.1:8000/query `
  -H "Content-Type: application/json" `
  -d '{"query":"What locations is Marcus Webb connected to?","case_ref":"OP_TEST"}'
```

## Audit

```powershell
curl http://127.0.0.1:8000/audit/queries
```

## Common issues

- **Queries hang or time out.** Confirm Ollama is running and the configured models are loaded
  (`ollama ps`). First call after a model load is slow.
- **Semantic search is weak.** The embedding model was changed after ingestion — re-ingest or
  rebuild the LanceDB index.
- **Geocoding errors.** Check `GEOCODING_ENABLED`, network reachability to `NOMINATIM_BASE_URL`,
  and that `NOMINATIM_USER_AGENT` is set.
- **Stay fully local.** Set `ALLOW_CLOUD_REASONING=false` in `config/.env`. This is the default.
