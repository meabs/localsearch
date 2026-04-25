from __future__ import annotations

import argparse
import json

from operation_lens_v2.eval_harness import run_eval_fixture_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a fixture-based local evaluation.")
    parser.add_argument("fixture", help="Path to the JSON fixture file")
    parser.add_argument("--db-path", dest="db_path", default=None, help="Optional DuckDB path")
    args = parser.parse_args()

    result = run_eval_fixture_sync(args.fixture, db_path=args.db_path)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
