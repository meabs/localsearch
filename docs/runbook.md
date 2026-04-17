# Operation Lens v2 Runbook

## Environment

- Use Python 3.11+ in a virtual environment.
- Install package with dev extras.

## Start

- `uvicorn operation_lens_v2.api.main:app --reload`
- Open `http://127.0.0.1:8000/ui`.

## Ingest

- POST `/ingest` with `{"pdf_path":"data/pdfs/sample.pdf"}`.

## Query

- POST `/query` with `{"query":"What locations is Marcus Webb connected to?"}`.

## Audit

- GET `/audit/queries` for query history.
