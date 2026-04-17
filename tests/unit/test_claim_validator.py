from __future__ import annotations

import asyncio

import pytest

from operation_lens_v2.query.claim_validator import validate_claims


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
