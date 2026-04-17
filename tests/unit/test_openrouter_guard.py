"""Hard guard: raw evidence text and entity names must never appear in OpenRouter payloads.

If this fails, the cloud payload is no longer redacted structured evidence only.
"""

from __future__ import annotations

import json

from operation_lens_v2.query.llm_router import _build_openrouter_payload


def _make_packet() -> dict:
    return {
        "query_text": "What connects Webb to Khalil?",
        "case_scope": "OP_NIGHTFALL",
    }


def _make_findings(include_span_text: bool) -> list[dict]:
    citation = {"doc_id": "NF-INT-001", "page": 3, "doc_set": []}
    if include_span_text:
        citation["span_text"] = "Webb was observed at 14 Arkwright Road at 21:40."
    return [
        {
            "source": "Marcus Webb",
            "source_type": "PERSON",
            "target": "14 Arkwright Road",
            "target_type": "LOCATION",
            "relation_type": "OBSERVED_AT",
            "confidence": 0.85,
            "doc_count": 2,
            "evidence_count": 3,
            "citations": [citation],
            "doc_set": ["NF-INT-001"],
        }
    ]


def test_openrouter_payload_strips_span_text():
    """_build_openrouter_payload must exclude span_text from all citations."""
    packet = _make_packet()
    findings = _make_findings(include_span_text=True)

    payload = _build_openrouter_payload(packet, findings)
    serialised = json.dumps(payload)

    assert "span_text" not in serialised
    assert "Arkwright Road" not in serialised


def test_openrouter_payload_redacts_person_names():
    """PERSON entity names must be replaced with redacted structured tokens."""
    packet = _make_packet()
    findings = _make_findings(include_span_text=False)

    payload = _build_openrouter_payload(packet, findings)
    serialised = json.dumps(payload)
    finding = payload["findings"][0]

    assert finding["source"] == "PERSON"
    assert finding["target"] == "LOCATION"
    assert "Marcus Webb" not in serialised
    assert "14 Arkwright Road" not in serialised
    assert "PERSON" in serialised
    assert "LOCATION" in serialised


def test_openrouter_payload_contains_required_fields():
    """Payload must carry redacted entity types, relation types, confidence, and doc/page refs."""
    packet = _make_packet()
    findings = _make_findings(include_span_text=True)
    payload = _build_openrouter_payload(packet, findings)

    assert payload["query"] == "What connects Webb to Khalil?"
    finding = payload["findings"][0]
    assert finding["source"] == "PERSON"
    assert finding["target"] == "LOCATION"
    assert finding["relation_type"] == "OBSERVED_AT"
    assert finding["confidence"] == 0.85
    assert finding["citations"][0]["doc_id"] == "NF-INT-001"
    assert finding["citations"][0]["page"] == 3
    for cit in finding["citations"]:
        assert "span_text" not in cit
