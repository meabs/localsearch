from __future__ import annotations

import os
from pathlib import Path

import httpx


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y")


def infer_case_ref(filename: str) -> str:
    if filename.startswith("OP_IRONVALE"):
        return "OP_IRONVALE"
    if filename.startswith("OP_SEAGLASS"):
        return "OP_SEAGLASS"
    if filename.startswith("NF-"):
        return "OP_NIGHTFALL"
    if filename.startswith("OC-"):
        return "OP_CHESTER"
    return "UNASSIGNED"


def main() -> None:
    base = "http://127.0.0.1:8000"
    force = _env_bool("INGEST_FORCE")
    root = Path(os.environ.get("INGEST_ROOT", "data/pdfs"))
    inputs = sorted(
        [
            *root.glob("*.pdf"),
            *root.glob("*.csv"),
            *root.glob("*.tsv"),
            *root.glob("*.jpg"),
            *root.glob("*.jpeg"),
            *root.glob("*.png"),
            *root.glob("*.bmp"),
            *root.glob("*.tif"),
            *root.glob("*.tiff"),
        ]
    )
    # Large first-run model downloads (e.g., GLiNER) can exceed short timeouts.
    with httpx.Client(timeout=1800.0) as client:
        for input_path in inputs:
            case_ref = infer_case_ref(input_path.name)
            resp = client.post(
                f"{base}/ingest",
                json={
                    "pdf_path": str(input_path.resolve()),
                    "case_ref": case_ref,
                    "case_name": case_ref.replace("_", " ").title(),
                    "force": force,
                },
            )
            print(input_path.name, resp.status_code, case_ref)


if __name__ == "__main__":
    main()
