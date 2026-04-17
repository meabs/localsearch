#!/usr/bin/env python3
"""Environment validation script for Operation Lens v2.

Checks:
  - Ollama is running and reachable
  - All three required models are present in `ollama list`
  - DuckDB is writable
  - LanceDB directory is writable
  - Python dependencies are importable

Exit code 0 only if all checks pass.
"""
from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path

import httpx

# ── Add src/ to path so the package is importable without install ──────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from operation_lens_v2.config import settings  # noqa: E402 (after sys.path fix)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_all_pass = True


def check(label: str, ok: bool, detail: str = "") -> None:
    global _all_pass
    status = PASS if ok else FAIL
    suffix = f"  — {detail}" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    if not ok:
        _all_pass = False


# ── 1. Ollama reachable ────────────────────────────────────────────────────────
print("\nOllama")
try:
    resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5.0)
    resp.raise_for_status()
    tags_data = resp.json()
    check("Ollama server reachable", True, settings.ollama_base_url)
except Exception as exc:
    check("Ollama server reachable", False, str(exc))
    tags_data = {"models": []}

# ── 2. Required models present ────────────────────────────────────────────────
print("\nModels")
available_models = {m.get("name", "").split(":")[0] for m in tags_data.get("models", [])}
available_full = {m.get("name", "") for m in tags_data.get("models", [])}

REQUIRED_MODELS = [
    (settings.local_reasoning_model, "reasoning"),
    (settings.local_extraction_model, "extraction"),
    (settings.local_embed_model, "embeddings"),
]
for model_name, role in REQUIRED_MODELS:
    present = model_name in available_full or model_name.split(":")[0] in available_models
    check(f"{model_name}  ({role})", present)

# ── 3. DuckDB writable ────────────────────────────────────────────────────────
print("\nStorage")
try:
    import duckdb

    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=True) as tf:
        con = duckdb.connect(tf.name)
        con.execute("CREATE TABLE _test (x INT); DROP TABLE _test;")
        con.close()
    check("DuckDB writable", True)
except Exception as exc:
    check("DuckDB writable", False, str(exc))

# ── 4. LanceDB directory writable ─────────────────────────────────────────────
try:
    import lancedb

    with tempfile.TemporaryDirectory() as td:
        db = lancedb.connect(td)
        check("LanceDB writable", True)
except Exception as exc:
    check("LanceDB writable", False, str(exc))

# Data directories exist (or can be created).
for label, path_str in [("DuckDB data dir", settings.duckdb_path), ("PDF root", settings.pdf_root)]:
    p = Path(path_str).parent
    try:
        p.mkdir(parents=True, exist_ok=True)
        check(f"{label} directory accessible", True, str(p))
    except Exception as exc:
        check(f"{label} directory accessible", False, str(exc))

# ── 5. Python dependencies importable ─────────────────────────────────────────
print("\nPython dependencies")
DEPS = [
    ("pdfplumber", "pdfplumber"),
    ("pytesseract", "pytesseract"),
    ("PIL (Pillow)", "PIL"),
    ("gliner", "gliner"),
    ("duckdb", "duckdb"),
    ("lancedb", "lancedb"),
    ("pyarrow", "pyarrow"),
    ("tiktoken", "tiktoken"),
    ("rapidfuzz", "rapidfuzz"),
    ("httpx", "httpx"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("pydantic", "pydantic"),
    ("fpdf2", "fpdf"),
]
for label, module in DEPS:
    try:
        importlib.import_module(module)
        check(label, True)
    except ImportError as exc:
        check(label, False, str(exc))

# ── Summary ───────────────────────────────────────────────────────────────────
print()
if _all_pass:
    print("\033[32mAll checks passed. Environment is ready.\033[0m\n")
    sys.exit(0)
else:
    print("\033[31mOne or more checks failed. Resolve the issues above before ingesting.\033[0m\n")
    sys.exit(1)
