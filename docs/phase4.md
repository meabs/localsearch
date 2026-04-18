# Phase 4: Distribution and Demoability

Phase 4 focuses on making the project easy to install, easy to demo, and easy to explain.

## Delivered

- `scripts/bootstrap_local.py` for idempotent local setup
- `scripts/check_models.py` for direct Ollama model checks
- `scripts/load_demo_case.py` for deterministic synthetic demo loading
- synthetic demo seed generation under `data/demo_seed/`
- case briefing export in Markdown, HTML, and PDF
- UI affordances for opening the demo case, running a demo query, and exporting a briefing
- refreshed docs for quickstart, troubleshooting, architecture, tuning, eval, domain packs, and exports

## Demo case

The synthetic case `OP_DEMO_SIGNAL` demonstrates:

- aliasing
- bridge identifiers such as phone, vehicle, and account
- location links
- timeline sequence
- image OCR / metadata signal
- grounded answer flow with citations

## Operator path

1. Run bootstrap.
2. Load the demo case.
3. Start the app.
4. Open the demo case in the UI.
5. Run the guided demo query.
6. Export the briefing.
