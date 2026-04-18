# Troubleshooting

## Bootstrap completed but Ollama checks failed

Confirm Ollama is running locally:

```powershell
ollama list
```

If the service is not running, start Ollama and rerun:

```powershell
python scripts/check_models.py
```

## Required models are missing

Pull the configured models:

```powershell
ollama pull qwen3.5:32b-instruct-q4_K_M
ollama pull qwen3:8b-instruct-q4_K_M
ollama pull nomic-embed-text
```

If your machine uses different local models, update `config/.env`.

## The demo case did not appear in the UI

Run:

```powershell
python scripts/load_demo_case.py
```

Then refresh the UI and select `OP_DEMO_SIGNAL`.

## Query results are empty for a non-PDF file

Check:

- the file uploaded successfully
- the case scope matches the uploaded case
- the text really exists in the file
- the ingest was not skipped because the file already existed in the same case

## Image OCR did not produce text

OCR is local and optional. If `pytesseract` or the local Tesseract runtime is unavailable, image ingest still succeeds but OCR-derived text may be absent.

## Export failed for PDF

Markdown and HTML are the primary exports. PDF export depends on the local Python PDF dependency path succeeding in the active environment.
