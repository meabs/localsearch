#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _parse_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _settings() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    env_path = root / "config" / ".env"
    example_path = root / "config" / ".env.example"
    merged = _parse_env_file(example_path)
    merged.update(_parse_env_file(env_path))
    return merged


def main() -> int:
    cfg = _settings()
    base_url = cfg.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    required = [
        cfg.get("LOCAL_REASONING_MODEL", ""),
        cfg.get("LOCAL_EXTRACTION_MODEL", ""),
        cfg.get("LOCAL_EMBED_MODEL", ""),
    ]
    required = [item for item in required if item]

    print(f"Ollama endpoint: {base_url}")
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        print(f"Ollama not reachable: {exc}")
        return 1

    installed = {item.get("name", "") for item in payload.get("models", [])}
    installed_base = {name.split(":")[0] for name in installed}
    missing: list[str] = []
    for model in required:
        if model in installed or model.split(":")[0] in installed_base:
            print(f"OK   {model}")
        else:
            print(f"MISS {model}")
            missing.append(model)

    if missing:
        print("\nNext steps:")
        for model in missing:
            print(f"  ollama pull {model}")
        return 2

    print("\nAll required models are available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
