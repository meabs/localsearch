from __future__ import annotations

import json
from pathlib import Path

import pytest

from operation_lens_v2.config import settings
from operation_lens_v2.eval_harness import run_eval_fixture
from operation_lens_v2.ingestion.duck_store import init_db


@pytest.mark.asyncio
async def test_eval_harness_scores_fixture_and_records_run(tmp_path: Path, monkeypatch) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "name": "phase1-smoke",
                "cases": [
                    {
                        "name": "network-link-check",
                        "query": "What connects Marcus Webb to Sealand Industrial Park?",
                        "case_ref": "OP_PHASE1",
                        "expected_doc_ids": ["doc-1"],
                        "expected_supported_claims": [
                            "Marcus Webb was observed at Sealand Industrial Park"
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    db_path = tmp_path / "eval.duckdb"
    init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    async def fake_run_query(*_args, **_kwargs):
        return {
            "claims": [
                {
                    "text": "Marcus Webb was observed at Sealand Industrial Park",
                    "status": "SUPPORTED",
                    "validated": True,
                }
            ],
            "top_results": [{"doc_id": "doc-1"}],
        }

    monkeypatch.setattr("operation_lens_v2.eval_harness.run_query", fake_run_query)

    result = await run_eval_fixture(fixture_path, db_path=str(db_path))

    assert result["metrics"]["avg_retrieval_recall"] == 1.0
    assert result["metrics"]["avg_grounded_recall"] == 1.0

    con = init_db(str(db_path))
    stored = con.execute("SELECT name, fixture_path FROM eval_runs").fetchone()
    assert stored == ("phase1-smoke", str(fixture_path))
