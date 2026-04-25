from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store
from operation_lens_v2.query.pipeline import run_query


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _load_fixture(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"name": path.stem, "cases": payload}
    if isinstance(payload, dict):
        return payload
    raise ValueError("Fixture must be a JSON object or list.")


def _returned_doc_ids(response: dict[str, object]) -> set[str]:
    doc_ids: set[str] = set()
    for item in response.get("retrieval_sources", []) or []:
        doc_id = str(item.get("document_id") or item.get("doc_id") or "").strip()
        if doc_id:
            doc_ids.add(doc_id)
    for item in response.get("top_results", []) or []:
        doc_id = str(item.get("document_id") or item.get("doc_id") or "").strip()
        if doc_id:
            doc_ids.add(doc_id)
    return doc_ids


def _supported_claim_texts(response: dict[str, object]) -> list[str]:
    supported: list[str] = []
    for claim in response.get("claims", []) or []:
        status = str(claim.get("support_level") or claim.get("status") or "").strip().lower()
        validated = bool(claim.get("validated", False))
        if status in {"supported", "partially_supported"} or validated:
            supported.append(str(claim.get("text", "")).lower())
    return supported


def _validation_summary(response: dict[str, object], supported_claim_count: int) -> dict[str, int]:
    summary = response.get("validation_summary")
    if isinstance(summary, dict):
        return {
            "supported": int(summary.get("supported", 0) or 0),
            "weak": int(summary.get("weak", 0) or 0),
            "unsupported": int(summary.get("unsupported", 0) or 0),
        }
    return {"supported": supported_claim_count, "weak": 0, "unsupported": 0}


def _score_case(case: dict[str, object], response: dict[str, object]) -> dict[str, object]:
    expected_doc_ids = {str(item) for item in case.get("expected_doc_ids", [])}
    returned_doc_ids = _returned_doc_ids(response)
    retrieval_hits = len(expected_doc_ids.intersection(returned_doc_ids))
    retrieval_recall = (
        round(retrieval_hits / len(expected_doc_ids), 4) if expected_doc_ids else 1.0
    )

    expected_supported_claims = [
        str(item).lower() for item in case.get("expected_supported_claims", [])
    ]
    supported_text = _supported_claim_texts(response)
    grounded_hits = sum(
        1 for expected in expected_supported_claims if any(expected in actual for actual in supported_text)
    )
    grounded_recall = (
        round(grounded_hits / len(expected_supported_claims), 4)
        if expected_supported_claims
        else 1.0
    )

    return {
        "name": case.get("name") or case.get("query") or "unnamed-case",
        "query": case.get("query"),
        "retrieval_hits": retrieval_hits,
        "expected_doc_count": len(expected_doc_ids),
        "retrieval_recall": retrieval_recall,
        "supported_claim_hits": grounded_hits,
        "expected_supported_claim_count": len(expected_supported_claims),
        "grounded_recall": grounded_recall,
        "validation_summary": _validation_summary(response, len(supported_text)),
    }


async def run_eval_fixture(
    fixture_path: str | Path,
    *,
    db_path: str | None = None,
) -> dict[str, object]:
    path = Path(fixture_path)
    fixture = _load_fixture(path)
    cases = list(fixture.get("cases", []))
    started_at = _utc_now_iso()
    case_results: list[dict[str, object]] = []

    for case in cases:
        if not isinstance(case, dict):
            continue
        response = await run_query(
            str(case.get("query") or ""),
            case_ref=str(case.get("case_ref")) if case.get("case_ref") else None,
            recall_mode=str(case.get("recall_mode") or "auto"),
        )
        case_results.append(_score_case(case, response))

    completed_at = _utc_now_iso()
    metrics = {
        "case_count": len(case_results),
        "avg_retrieval_recall": round(
            sum(float(item.get("retrieval_recall", 0.0)) for item in case_results)
            / max(1, len(case_results)),
            4,
        ),
        "avg_grounded_recall": round(
            sum(float(item.get("grounded_recall", 0.0)) for item in case_results)
            / max(1, len(case_results)),
            4,
        ),
        "cases": case_results,
    }

    con = duck_store.init_db(db_path or settings.duckdb_path)
    eval_run_id = str(uuid4())
    duck_store.record_eval_run(
        con,
        eval_run_id=eval_run_id,
        name=str(fixture.get("name") or path.stem),
        started_at=started_at,
        completed_at=completed_at,
        metrics=metrics,
        fixture_path=str(path),
    )
    return {
        "eval_run_id": eval_run_id,
        "name": str(fixture.get("name") or path.stem),
        "started_at": started_at,
        "completed_at": completed_at,
        "metrics": metrics,
        "fixture_path": str(path),
    }


def run_eval_fixture_sync(
    fixture_path: str | Path,
    *,
    db_path: str | None = None,
) -> dict[str, object]:
    return asyncio.run(run_eval_fixture(fixture_path, db_path=db_path))
