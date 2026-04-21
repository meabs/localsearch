from __future__ import annotations

import asyncio

import pytest

from operation_lens_v2.query.claim_validator import validate_claims
from operation_lens_v2.query.evidence_builder import build_evidence_packet


@pytest.mark.asyncio
async def test_claim_validator_supported_status() -> None:
    payload = {
        "answer": "x",
        "claims": [
            {
                "text": "Marcus Webb was observed at Arkwright Road",
                "citations": [
                    {"span_text": "Marcus Webb was observed at Arkwright Road at 21:40."}
                ],
                "confidence": 0.1,
            }
        ],
    }
    out = await validate_claims(payload)
    assert out["claims"][0]["status"] == "SUPPORTED"


@pytest.mark.asyncio
async def test_claim_validator_rebuilds_answer_from_validated_claims(monkeypatch) -> None:
    async def fake_extract(answer_text: str) -> list[str]:
        return [
            "Marcus Webb was observed at Arkwright Road",
            "Danny Marsh controlled the site",
        ]

    monkeypatch.setattr(
        "operation_lens_v2.query.claim_validator._extract_claims_from_answer",
        fake_extract,
    )

    payload = {
        "answer": ("Marcus Webb was observed at Arkwright Road. Danny Marsh controlled the site."),
        "claims": [
            {
                "text": "Marcus Webb was observed at Arkwright Road",
                "citations": [
                    {
                        "doc_id": "DOC-1",
                        "doc_name": "intel-1.pdf",
                        "page": 4,
                        "span_text": "Marcus Webb was observed at Arkwright Road at 21:40.",
                    }
                ],
                "confidence": 0.9,
            }
        ],
    }

    out = await validate_claims(payload)

    assert "Marcus Webb was observed at Arkwright Road" in out["answer"]
    assert "Danny Marsh controlled the site" not in out["answer"]
    assert len(out["claims"]) == 2
    assert out["claims"][1]["status"] == "UNSUPPORTED"


@pytest.mark.asyncio
async def test_claim_validator_concurrency_preserves_order_and_fallback(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    class FakeClient:
        async def post(self, path: str, json: dict):
            prompt = str(json.get("prompt", ""))
            if "Claim: slow claim supported" in prompt:
                await asyncio.sleep(0.05)
                return FakeResponse(
                    {
                        "response": (
                            '{"supported": true, "partial": false, '
                            '"confidence": 0.9, "note": "exact match"}'
                        )
                    }
                )
            if "Claim: fallback claim supported" in prompt:
                await asyncio.sleep(0.01)
                raise RuntimeError("transient failure")
            raise AssertionError(f"Unexpected prompt: {prompt}")

    monkeypatch.setattr(
        "operation_lens_v2.query.claim_validator.get_http_client",
        lambda **_: FakeClient(),
    )

    payload = {
        "answer": "",
        "claims": [
            {
                "text": "slow claim supported",
                "citations": [{"span_text": "slow claim supported in evidence"}],
                "confidence": 0.2,
            },
            {
                "text": "fallback claim supported",
                "citations": [{"span_text": "fallback claim supported in evidence"}],
                "confidence": 0.2,
            },
        ],
    }

    out = await validate_claims(payload)

    assert [claim["text"] for claim in out["claims"]] == [
        "slow claim supported",
        "fallback claim supported",
    ]
    assert out["claims"][0]["status"] == "SUPPORTED"
    assert out["claims"][1]["status"] == "SUPPORTED"


@pytest.mark.asyncio
async def test_claim_without_citations_is_forced_unsupported(monkeypatch) -> None:
    async def fake_extract(answer_text: str) -> list[str]:
        return ["Marcus Webb was observed at Arkwright Road"]

    monkeypatch.setattr(
        "operation_lens_v2.query.claim_validator._extract_claims_from_answer",
        fake_extract,
    )

    payload = {
        "answer": "Marcus Webb was observed at Arkwright Road [DOC-1, p.4].",
        "claims": [{"text": "Marcus Webb was observed at Arkwright Road", "citations": []}],
    }
    out = await validate_claims(payload)
    assert out["claims"][0]["status"] == "UNSUPPORTED"
    assert out["grounding_controls"]["unsupported_claim_count"] == 1


@pytest.mark.asyncio
async def test_strict_mode_omits_partial_claims_from_answer(monkeypatch) -> None:
    async def fake_validate(claim_text: str, evidence_spans: list[str]) -> dict[str, object]:
        if "supported claim" in claim_text:
            return {"status": "SUPPORTED", "confidence": 0.9, "note": "ok"}
        return {"status": "PARTIALLY_SUPPORTED", "confidence": 0.5, "note": "partial"}

    monkeypatch.setattr(
        "operation_lens_v2.query.claim_validator._validate_single_claim",
        fake_validate,
    )

    payload = {
        "answer": "supported claim [DOC-1, p.1]. partial claim [DOC-1, p.1].",
        "claims": [
            {
                "text": "supported claim",
                "citations": [{"doc_id": "DOC-1", "page": 1, "span_text": "supported claim"}],
            },
            {
                "text": "partial claim",
                "citations": [{"doc_id": "DOC-1", "page": 1, "span_text": "partial claim"}],
            },
        ],
    }
    out = await validate_claims(payload)
    assert "supported claim" in out["answer"]
    assert "partial claim" not in out["answer"]


@pytest.mark.asyncio
async def test_validator_preserves_inline_citation_answer_without_structured_claims() -> None:
    payload = {
        "answer": (
            "KEY FINDINGS\n"
            "- Surveillance noted two meetings at North Yard [OC-INT-003.pdf, p.1]\n"
            "CONFIDENCE POSTURE\n"
            "Excerpt-backed summary."
        ),
        "claims": [],
    }

    out = await validate_claims(payload)

    assert "North Yard" in out["answer"]
    assert out["claims"] == []
    assert out["grounding_controls"]["validation_skipped"] is True


@pytest.mark.asyncio
async def test_claim_validator_uses_multi_hop_graph_evidence(monkeypatch) -> None:
    async def fake_validate(claim_text: str, evidence_spans: list[str]) -> dict[str, object]:
        assert any(
            "Graph traversal links Marcus Webb to Rania Khalil" in span
            for span in evidence_spans
        )
        return {"status": "SUPPORTED", "confidence": 0.8, "note": "multi-hop"}

    monkeypatch.setattr(
        "operation_lens_v2.query.claim_validator._validate_single_claim",
        fake_validate,
    )

    payload = {
        "answer": "",
        "claims": [{"text": "Marcus Webb is connected to Rania Khalil", "citations": []}],
        "evidence_packet": {
            "graph_evidence": [
                {
                    "source_name": "Marcus Webb",
                    "target_name": "RX71 KLD",
                    "span_text": "Marcus Webb used RX71 KLD.",
                    "doc_id": "D1",
                    "page": 1,
                    "chunk_id": "C1",
                },
                {
                    "source_name": "RX71 KLD",
                    "target_name": "Rania Khalil",
                    "span_text": "Rania Khalil drove RX71 KLD.",
                    "doc_id": "D2",
                    "page": 2,
                    "chunk_id": "C2",
                },
            ]
        },
    }

    out = await validate_claims(payload)

    assert out["claims"][0]["status"] == "SUPPORTED"
    assert {c["doc_id"] for c in out["claims"][0]["citations"]} == {"D1", "D2"}


@pytest.mark.asyncio
async def test_claim_validator_tracks_negative_evidence(monkeypatch) -> None:
    async def fake_validate(claim_text: str, evidence_spans: list[str]) -> dict[str, object]:
        assert any("denied knowing" in span for span in evidence_spans)
        return {"status": "SUPPORTED", "confidence": 0.85, "note": "explicit denial"}

    monkeypatch.setattr(
        "operation_lens_v2.query.claim_validator._validate_single_claim",
        fake_validate,
    )

    payload = {
        "answer": "",
        "claims": [{"text": "Marcus Webb denied knowing Rania Khalil", "citations": []}],
        "evidence_packet": {
            "negative_evidence": [
                {
                    "kind": "explicit_denial",
                    "text": "Marcus Webb denied knowing Rania Khalil during interview.",
                    "citation": {
                        "doc_id": "D3",
                        "page": 4,
                        "chunk_id": "C9",
                        "span_text": "Marcus Webb denied knowing Rania Khalil during interview.",
                    },
                }
            ]
        },
    }

    out = await validate_claims(payload)

    assert out["claims"][0]["negative_evidence"]
    assert out["grounding_controls"]["negative_evidence_count"] == 1


@pytest.mark.asyncio
async def test_claim_validator_supports_multi_hop_graph_evidence(monkeypatch) -> None:
    async def no_extraction(answer_text: str) -> list[str]:
        return []

    monkeypatch.setattr(
        "operation_lens_v2.query.claim_validator._extract_claims_from_answer",
        no_extraction,
    )

    payload = {
        "answer": "",
        "claims": [{"text": "Marcus Webb is linked to Rania Khalil", "citations": []}],
        "evidence_packet": {
            "relationships": [
                {
                    "source_name": "Marcus Webb",
                    "target_name": "RX71 KLD",
                    "relation_type": "OWNS",
                    "doc_id": "doc-1",
                    "doc_name": "intel-1.pdf",
                    "page": 1,
                    "chunk_id": "chunk-1",
                    "span_text": "Marcus Webb admits owning RX71 KLD.",
                },
                {
                    "source_name": "RX71 KLD",
                    "target_name": "Rania Khalil",
                    "relation_type": "DRIVEN_BY",
                    "doc_id": "doc-2",
                    "doc_name": "surv-1.pdf",
                    "page": 2,
                    "chunk_id": "chunk-2",
                    "span_text": "RX71 KLD driver identified as Rania Khalil.",
                },
            ]
        },
    }

    out = await validate_claims(payload)

    assert out["claims"][0]["status"] == "SUPPORTED"
    assert {citation["doc_id"] for citation in out["claims"][0]["citations"]} == {
        "doc-1",
        "doc-2",
    }


@pytest.mark.asyncio
async def test_claim_validator_records_explicit_denial_evidence(monkeypatch) -> None:
    async def no_extraction(answer_text: str) -> list[str]:
        return []

    monkeypatch.setattr(
        "operation_lens_v2.query.claim_validator._extract_claims_from_answer",
        no_extraction,
    )

    payload = {
        "answer": "",
        "claims": [{"text": "Webb denied knowing Khalil", "citations": []}],
        "evidence_packet": {
            "negative_evidence": [
                {
                    "kind": "explicit_denial",
                    "text": "Webb denied knowing Khalil during interview.",
                    "citation": {
                        "doc_id": "doc-denial",
                        "doc_name": "interview.pdf",
                        "page": 3,
                        "chunk_id": "chunk-denial",
                        "span_text": "Webb denied knowing Khalil during interview.",
                    },
                }
            ]
        },
    }

    out = await validate_claims(payload)

    assert out["claims"][0]["status"] == "SUPPORTED"
    assert out["claims"][0]["negative_evidence"][0]["kind"] == "explicit_denial"
    assert out["grounding_controls"]["negative_evidence_count"] == 1


def test_evidence_builder_tracks_explicit_denials() -> None:
    packet = build_evidence_packet(
        query_text="Did Webb know Khalil?",
        query_intent="general_query",
        entities_resolved=[],
        ranked_results=[
            {
                "source": "fts",
                "chunk_id": "chunk-denial",
                "doc_id": "doc-denial",
                "doc_name": "interview.pdf",
                "page": 3,
                "text": "Webb denied knowing Khalil during interview.",
            }
        ],
    )

    assert packet["negative_evidence"]
    assert packet["negative_evidence"][0]["kind"] == "explicit_denial"
    assert "Webb denied knowing Khalil" in packet["negative_evidence"][0]["text"]
