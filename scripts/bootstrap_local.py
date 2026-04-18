#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT / ".venv"
CONFIG_DIR = ROOT / "config"
ENV_PATH = CONFIG_DIR / ".env"
ENV_EXAMPLE_PATH = CONFIG_DIR / ".env.example"


def _venv_python() -> Path:
    scripts_dir = "Scripts" if sys.platform.startswith("win") else "bin"
    executable = "python.exe" if sys.platform.startswith("win") else "python"
    return VENV_DIR / scripts_dir / executable


def _print_step(title: str) -> None:
    print(f"\n== {title} ==")


def _run(command: list[str], *, cwd: Path | None = None, check: bool = True) -> int:
    print(" ".join(command))
    result = subprocess.run(command, cwd=str(cwd or ROOT), check=False)
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result.returncode


def ensure_python_version() -> None:
    _print_step("Python version")
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is required.")
    print(f"Using Python {sys.version.split()[0]}")


def ensure_venv() -> None:
    _print_step("Virtual environment")
    if VENV_DIR.exists() and _venv_python().exists():
        print(f"Virtual environment already present at {VENV_DIR}")
        return
    print(f"Creating virtual environment at {VENV_DIR}")
    venv.create(VENV_DIR, with_pip=True)


def ensure_dependencies() -> None:
    _print_step("Dependencies")
    python_bin = str(_venv_python())
    _run([python_bin, "-m", "pip", "install", "--upgrade", "pip"])
    _run([python_bin, "-m", "pip", "install", "-e", ".[dev]"], cwd=ROOT)


def ensure_env_file() -> None:
    _print_step("Environment file")
    if ENV_PATH.exists():
        print(f"Using existing {ENV_PATH}")
        return
    shutil.copyfile(ENV_EXAMPLE_PATH, ENV_PATH)
    print(f"Created {ENV_PATH} from {ENV_EXAMPLE_PATH}")


def ensure_storage() -> None:
    _print_step("Local storage and schema")
    python_bin = str(_venv_python())
    code = (
        "from operation_lens_v2.config import settings;"
        "from operation_lens_v2.ingestion.duck_store import init_db;"
        "settings.evidence_root_obj.mkdir(parents=True, exist_ok=True);"
        "settings.export_root_obj.mkdir(parents=True, exist_ok=True);"
        "settings.lancedb_path_obj.mkdir(parents=True, exist_ok=True);"
        "init_db(settings.duckdb_path);"
        "print('DuckDB and local storage initialized.')"
    )
    _run([python_bin, "-c", code], cwd=ROOT)


def check_ollama_and_models() -> None:
    _print_step("Ollama and model checks")
    python_bin = str(_venv_python())
    model_check = _run([python_bin, str(ROOT / "scripts" / "check_models.py")], cwd=ROOT, check=False)
    if model_check == 1:
        print("Ollama is not reachable. Start Ollama, then rerun this bootstrap.")
    elif model_check == 2:
        print("One or more required models are missing. Pull them using the commands above.")
    else:
        print("Ollama and required models look ready.")


def print_next_steps() -> None:
    _print_step("Next commands")
    activate = ".\\.venv\\Scripts\\Activate.ps1" if sys.platform.startswith("win") else "source .venv/bin/activate"
    print(activate)
    print("python scripts/load_demo_case.py")
    print("uvicorn operation_lens_v2.api.main:app --reload")
    print("Open http://127.0.0.1:8000/ui")


def main() -> int:
    ensure_python_version()
    ensure_venv()
    ensure_dependencies()
    ensure_env_file()
    check_ollama_and_models()
    ensure_storage()
    print_next_steps()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
