# Eval

## Practical local evaluation loop

1. Load the synthetic demo case.
2. Run the guided demo query.
3. Check whether the answer:
   - names the key people and places
   - uses grounded citations
   - references the right supporting files
4. Export a briefing and confirm it remains readable.

## Good lightweight regression checks

- ingest still works for PDFs
- non-PDF evidence is searchable
- image OCR and metadata remain distinguishable by provenance
- domain-aware templates change with the selected pack
- export still produces readable Markdown and HTML
