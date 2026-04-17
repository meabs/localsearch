from __future__ import annotations

import pytest

from operation_lens_v2.query.llm_router import generate_answer


@pytest.mark.asyncio
async def test_generate_answer_document_summary_ignores_boilerplate(monkeypatch) -> None:
    async def fake_local_document_analysis(evidence_packet, chunks):
        return None

    monkeypatch.setattr(
        "operation_lens_v2.query.llm_router._local_document_analysis",
        fake_local_document_analysis,
    )

    payload = {
        "query_intent": "document_summary_query",
        "query_text": "Summarise key findings from OC-INT-003.pdf",
        "chunks": [
            {
                "doc_id": "doc-1",
                "doc_name": "OC-INT-003.pdf",
                "page": 1,
                "text": (
                    "OPERATION CHESTER | OFFICIAL-SENSITIVE | OC-INT-003 | DAILY INTELLIGENCE REPORT. "
                    "Analyst: PS 1102 Ainsworth. Grading (source): B - usually reliable. "
                    "Anonymous CrimeStoppers report dated 03/04/2024 references a property on the "
                    "Breckfield side of the estate being used for late-night meetings."
                ),
            }
        ],
        "relationships": [],
        "exact_matches": [],
    }

    out = await generate_answer(payload)

    assert "late-night meetings" in out["answer"]
    assert "OFFICIAL-SENSITIVE" not in out["answer"]
    assert out["claims"]


@pytest.mark.asyncio
async def test_generate_answer_document_summary_prefers_local_analysis(monkeypatch) -> None:
    async def fake_local_document_analysis(evidence_packet, chunks):
        return (
            "KEY FINDINGS\n"
            "- Two late-night meetings were reported at the Breckfield property [OC-INT-003.pdf, p.1]\n"
            "CONFIDENCE POSTURE\n"
            "Excerpt-backed analytic summary.\n"
            "EVIDENCE GAPS\n"
            "Single-source document summary."
        )

    monkeypatch.setattr(
        "operation_lens_v2.query.llm_router._local_document_analysis",
        fake_local_document_analysis,
    )

    payload = {
        "query_intent": "document_summary_query",
        "query_text": "Summarise key findings from OC-INT-003.pdf",
        "chunks": [
            {
                "doc_id": "doc-1",
                "doc_name": "OC-INT-003.pdf",
                "page": 1,
                "text": "Some source text.",
            }
        ],
        "relationships": [],
        "exact_matches": [],
    }

    out = await generate_answer(payload)

    assert "Two late-night meetings" in out["answer"]
    assert out["claims"] == []
