# Phase 3: Domain Packs and Richer Evidence Types

Phase 3 makes Operation Lens adaptable across analysis domains while keeping a single local-first ingestion and provenance flow.

## What changed

- Added declarative domain packs under `config/domain_packs/`.
- Added deterministic merge logic over `config/entity_schema.json`.
- Added case-level domain pack selection plus resolved-schema inspection.
- Added a parser registry for `pdf`, `txt`, `csv`, `json`, `eml`, `html`, and common image formats.
- Normalized non-PDF evidence into the same `documents -> chunks -> retrieval -> citations` flow already used for PDFs.
- Added image metadata extraction, optional local OCR, and perceptual hashing for duplicate detection.
- Added case-aware query templates and a simple case dashboard configuration endpoint.

## New API surface

- `GET /config/domain-packs`
- `POST /cases/{case_ref}/domain-pack`
- `GET /cases/{case_ref}/resolved-schema`
- `GET /cases/{case_ref}/dashboard-config`
- `GET /query/templates?case_ref=...`

## Provenance model

Each parser normalizes output into:

- `document_id`
- `source_type`
- `source_metadata`
- `text_blocks`
- `attachments`
- `derived_facts`
- `parser_warnings`

Text blocks are chunked into the same retrieval tables as PDFs. Provenance-aware fields such as `source_label` and `provenance_type` are carried through chunk storage and vector rows. OCR-derived and EXIF-derived facts are also persisted separately for inspection.

## Local image handling

Image ingestion is local-first and degrades gracefully:

- OCR uses `pytesseract` only when available locally.
- EXIF extraction uses Pillow when available.
- Duplicate detection uses perceptual hashing plus content hashing.

If local libraries are unavailable, ingestion still succeeds and records parser warnings.
