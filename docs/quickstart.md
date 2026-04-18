# Quickstart

## Goal

Get a new user from clean checkout to useful demo in under 10 minutes.

## 1. Run bootstrap

```powershell
python scripts/bootstrap_local.py
```

## 2. Activate the environment

```powershell
.\.venv\Scripts\Activate.ps1
```

## 3. Load the demo case

```powershell
python scripts/load_demo_case.py
```

## 4. Start the app

```powershell
uvicorn operation_lens_v2.api.main:app --reload
```

## 5. Open the UI

- [http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui)

## Suggested first click path

1. Open the demo case `OP_DEMO_SIGNAL`.
2. Run the demo query:
   `What connects Lena Hart to South Quay Locker and what evidence supports that connection?`
3. Inspect citations and evidence excerpts.
4. Open the case dashboard widgets.
5. Export a Markdown or HTML briefing from the case view.

## What the demo proves

- aliases are resolved
- location links are visible
- timeline evidence is discoverable
- image OCR / metadata contributes local signal
- answers remain grounded with citations
