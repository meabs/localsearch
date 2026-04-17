from __future__ import annotations

import json
import logging
import re
from typing import Any

from operation_lens_v2.config import settings
from operation_lens_v2.runtime import gather_limited, get_http_client

logger = logging.getLogger(__name__)
WITHHELD_CLAIMS_GAP = (
    "{count} claim(s) were withheld because the evidence did not support them exactly."
)
OMITTED_CLAIMS_GAP = (
    "{count} claim(s) were omitted from findings because the evidence did not support them exactly."
)
VALIDATION_CONCURRENCY_LIMIT = 4
STRICT_FACTS_ONLY_MODE = True
CITATION_MARKER_RE = re.compile(r"\[[^\],]+,\s*p\.\s*\d+\]")


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


async def _extract_claims_from_answer(answer_text: str) -> list[str]:
    """Stage 1: use 8B model to extract discrete factual claims as a JSON list."""
    prompt = (
        "You are an evidence analyst. Extract every discrete factual claim from the text below.\n"
        "Return a JSON array of strings — one claim per element. No prose, no markdown.\n\n"
        f"Text:\n{answer_text[:3000]}\n\nJSON array of claims:"
    )
    try:
        client = get_http_client(base_url=settings.ollama_base_url, timeout=settings.ollama_timeout)
        resp = await client.post(
            "/api/generate",
            json={"model": settings.local_extraction_model, "prompt": prompt, "stream": False},
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        cleaned = _strip_markdown_fences(raw)
        claims = json.loads(cleaned)
        if isinstance(claims, list):
            return [str(c) for c in claims if c]
    except Exception as exc:
        logger.warning("Claim extraction LLM call failed: %s", exc)
    return []


async def _validate_single_claim(claim_text: str, evidence_spans: list[str]) -> dict[str, Any]:
    """Stage 2: use 8B model to validate a single claim against its evidence spans."""
    if not evidence_spans:
        return {"status": "UNSUPPORTED", "confidence": 0.2, "note": "No evidence spans available."}

    evidence_block = "\n---\n".join(evidence_spans[:5])
    prompt = (
        "You are an evidence analyst. Does the evidence support the claim?\n"
        'Return JSON only: {"supported": true/false, "partial": true/false, '
        '"confidence": 0.0-1.0, "note": "brief explanation"}\n\n'
        f"Claim: {claim_text}\n\nEvidence:\n{evidence_block}\n\nJSON:"
    )
    try:
        client = get_http_client(base_url=settings.ollama_base_url, timeout=settings.ollama_timeout)
        resp = await client.post(
            "/api/generate",
            json={"model": settings.local_extraction_model, "prompt": prompt, "stream": False},
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        cleaned = _strip_markdown_fences(raw)
        result = json.loads(cleaned)
        supported = bool(result.get("supported", False))
        partial = bool(result.get("partial", False))
        confidence = float(result.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        note = str(result.get("note", ""))
        if supported:
            status = "SUPPORTED"
        elif partial:
            status = "PARTIALLY_SUPPORTED"
        else:
            status = "UNSUPPORTED"
        return {"status": status, "confidence": confidence, "note": note}
    except Exception as exc:
        logger.warning("Claim validation LLM call failed: %s", exc)
        # Fallback: token-overlap heuristic
        return _heuristic_validate(claim_text, evidence_spans)


def _heuristic_validate(claim_text: str, evidence_spans: list[str]) -> dict[str, Any]:
    """Fallback heuristic when LLM validation fails."""
    claim_l = claim_text.lower()
    joined = " ".join(evidence_spans).lower()
    tokens = [t for t in claim_l.split() if len(t) > 3]
    if not tokens:
        return {"status": "UNSUPPORTED", "confidence": 0.2, "note": "Empty claim."}
    overlap = sum(1 for t in tokens if t in joined)
    ratio = overlap / len(tokens)
    if ratio >= 0.8:
        return {"status": "SUPPORTED", "confidence": 0.75, "note": "Heuristic: high token overlap."}
    if ratio >= 0.4:
        return {
            "status": "PARTIALLY_SUPPORTED",
            "confidence": 0.55,
            "note": "Heuristic: partial token overlap.",
        }
    return {"status": "UNSUPPORTED", "confidence": 0.2, "note": "Heuristic: insufficient overlap."}


def _citations_for_display(citations: list[dict[str, Any]]) -> str:
    parts = []
    for citation in citations[:3]:
        doc_label = citation.get("doc_name") or citation.get("doc_id") or "unknown-doc"
        page = citation.get("page", "?")
        parts.append(f"[{doc_label}, p.{page}]")
    return ", ".join(parts)


def _render_claim_line(claim: dict[str, Any]) -> str:
    status = str(claim.get("status", "UNSUPPORTED")).replace("_", " ").title()
    citation_text = _citations_for_display(claim.get("citations", []))
    suffix = f" {citation_text}" if citation_text else ""
    return (
        f"- {claim.get('text', '')} "
        f"({status}, confidence {claim.get('confidence', 0.0):.2f}){suffix}"
    )


def _render_validated_answer(claims: list[dict[str, Any]]) -> str:
    supported = [claim for claim in claims if claim.get("status") == "SUPPORTED"]
    partial = [claim for claim in claims if claim.get("status") == "PARTIALLY_SUPPORTED"]
    unsupported = [claim for claim in claims if claim.get("status") == "UNSUPPORTED"]

    if not supported and not partial:
        lines = [
            "KEY FINDINGS",
            "No supported claims could be confirmed from the cited evidence.",
            "CONFIDENCE POSTURE",
            "All extracted claims were unsupported or lacked exact evidence spans.",
        ]
        if unsupported:
            lines.extend(
                [
                    "EVIDENCE GAPS",
                    WITHHELD_CLAIMS_GAP.format(count=len(unsupported)),
                ]
            )
        return "\n".join(lines)

    lines = ["KEY FINDINGS"]
    claims_for_answer = supported if STRICT_FACTS_ONLY_MODE else [*supported, *partial]
    for claim in claims_for_answer:
        lines.append(_render_claim_line(claim))

    lines.extend(
        [
            "CONFIDENCE POSTURE",
            f"Supported claims: {len(supported)}",
            f"Partially supported claims: {len(partial)}",
        ]
    )
    withheld_count = len(unsupported) + (len(partial) if STRICT_FACTS_ONLY_MODE else 0)
    if withheld_count:
        lines.extend(
            [
                "EVIDENCE GAPS",
                OMITTED_CLAIMS_GAP.format(count=withheld_count),
            ]
        )
    return "\n".join(lines)


async def validate_claims(answer_payload: dict[str, Any]) -> dict[str, Any]:
    """Extract claims from the answer and validate each against cited evidence spans."""
    answer_text = answer_payload.get("answer", "")
    existing_claims: list[dict[str, Any]] = answer_payload.get("claims", [])

    extracted_claim_texts = await _extract_claims_from_answer(answer_text) if answer_text else []
    has_inline_citation_markers = bool(CITATION_MARKER_RE.search(str(answer_text)))
    citations_by_claim = {c.get("text", ""): c.get("citations", []) for c in existing_claims}

    if not existing_claims and has_inline_citation_markers:
        return {
            **answer_payload,
            "claims": [],
            "grounding_controls": {
                "strict_facts_only": STRICT_FACTS_ONLY_MODE,
                "inline_citation_markers_present": True,
                "supported_claim_count": 0,
                "partially_supported_claim_count": 0,
                "unsupported_claim_count": 0,
                "validation_skipped": True,
                "validation_skip_reason": (
                    "Answer already contains inline citations but no structured claim map was provided."
                ),
            },
        }

    if existing_claims:
        claim_texts = [c.get("text", "") for c in existing_claims]
        for extracted_claim in extracted_claim_texts:
            if extracted_claim and extracted_claim not in claim_texts:
                claim_texts.append(extracted_claim)
    else:
        claim_texts = extracted_claim_texts

    validation_coroutines = []
    for claim_text in claim_texts:
        citations = citations_by_claim.get(claim_text, [])
        spans = [str(c.get("span_text", "")) for c in citations if c.get("span_text")]
        validation_coroutines.append(_validate_single_claim(claim_text, spans))

    results = (
        await gather_limited(validation_coroutines, limit=VALIDATION_CONCURRENCY_LIMIT)
        if validation_coroutines
        else []
    )
    validated: list[dict[str, Any]] = []
    for claim_text, result in zip(claim_texts, results, strict=False):
        citations = citations_by_claim.get(claim_text, [])
        if not citations:
            result = {
                "status": "UNSUPPORTED",
                "confidence": 0.1,
                "note": "No citations attached to claim.",
            }
        validated.append(
            {
                "text": claim_text,
                "citations": citations,
                "status": result["status"],
                "confidence": result["confidence"],
                "note": result.get("note"),
                "validated": result["status"] != "UNSUPPORTED",
            }
        )

    rendered_answer = _render_validated_answer(validated) if validated else answer_text
    grounding_controls = {
        "strict_facts_only": STRICT_FACTS_ONLY_MODE,
        "inline_citation_markers_present": has_inline_citation_markers,
        "supported_claim_count": sum(
            1 for claim in validated if claim.get("status") == "SUPPORTED"
        ),
        "partially_supported_claim_count": sum(
            1 for claim in validated if claim.get("status") == "PARTIALLY_SUPPORTED"
        ),
        "unsupported_claim_count": sum(
            1 for claim in validated if claim.get("status") == "UNSUPPORTED"
        ),
    }
    return {
        **answer_payload,
        "answer": rendered_answer,
        "claims": validated,
        "grounding_controls": grounding_controls,
    }
