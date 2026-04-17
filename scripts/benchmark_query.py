from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from operation_lens_v2.config import settings
from operation_lens_v2.query.pipeline import run_query
from operation_lens_v2.runtime import close_runtime_resources


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * pct)))
    return ordered[index]


async def _run_benchmark(
    *,
    queries: list[str],
    iterations: int,
    case_ref: str | None,
) -> dict[str, object]:
    durations_ms: list[float] = []
    for _ in range(iterations):
        for query in queries:
            start = time.perf_counter()
            await run_query(query, case_ref=case_ref)
            durations_ms.append((time.perf_counter() - start) * 1000)

    return {
        "query_count": len(queries),
        "iterations": iterations,
        "runs": len(durations_ms),
        "duckdb_path": settings.duckdb_path,
        "lancedb_path": settings.lancedb_path,
        "p50_ms": round(statistics.median(durations_ms), 2) if durations_ms else 0.0,
        "p95_ms": round(_percentile(durations_ms, 0.95), 2),
        "min_ms": round(min(durations_ms), 2) if durations_ms else 0.0,
        "max_ms": round(max(durations_ms), 2) if durations_ms else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark repeated Operation Lens queries.")
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="Query to execute. Provide multiple times for a suite.",
    )
    parser.add_argument(
        "--query-file",
        type=Path,
        help="Optional newline-delimited query file.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Number of repetitions per query.",
    )
    parser.add_argument("--case-ref", default=None, help="Optional case scope.")
    args = parser.parse_args()

    queries = list(args.queries or [])
    if args.query_file:
        queries.extend(
            line.strip()
            for line in args.query_file.read_text(encoding="utf-8").splitlines()
            if line
        )
    if not queries:
        raise SystemExit("Provide at least one --query or a --query-file.")

    try:
        summary = asyncio.run(
            _run_benchmark(
                queries=queries,
                iterations=max(1, args.iterations),
                case_ref=args.case_ref,
            )
        )
        print(json.dumps(summary, indent=2))
    finally:
        asyncio.run(close_runtime_resources())


if __name__ == "__main__":
    main()
