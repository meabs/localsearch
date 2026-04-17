"""Tests for ingestion/relationship_extractor.py — pattern + LLM extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from operation_lens_v2.ingestion.relationship_extractor import _run_patterns, extract_relationships
from operation_lens_v2.models import ExtractedEntity


def _ent(text: str, etype: str = "PERSON") -> ExtractedEntity:
    return ExtractedEntity(text=text, entity_type=etype, start=0, end=len(text))


# ── Pattern stage ──────────────────────────────────────────────────────────────


def test_observed_at_pattern():
    text = "Marcus Webb was observed at 14 Arkwright Road at 21:40."
    rels = _run_patterns(text)
    types = [r.relation_type for r in rels]
    assert "OBSERVED_AT" in types


def test_associated_with_pattern():
    text = "Marcus Webb is known to associate with Danny Marsh during the operation."
    rels = _run_patterns(text)
    types = [r.relation_type for r in rels]
    assert "ASSOCIATED_WITH" in types


def test_linked_to_pattern():
    text = "Rania Khalil is linked to Arkwright Holdings Ltd via financial records."
    rels = _run_patterns(text)
    types = [r.relation_type for r in rels]
    assert "LINKED_TO" in types


def test_pattern_confidence_is_correct():
    from operation_lens_v2.config import settings

    text = "Marcus Webb was observed at Depot Industrial Estate."
    rels = _run_patterns(text)
    if rels:
        assert rels[0].confidence == settings.pattern_confidence


def test_pattern_span_text_is_substring_of_input():
    text = "Marcus Webb was observed at Depot Industrial Estate at 14:22."
    rels = _run_patterns(text)
    for rel in rels:
        assert rel.span_text in text


# ── Co-occurrence fallback ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cooccurrence_fallback_when_no_patterns():
    text = "Witness noted two males near the industrial area but gave no names explicitly."
    entities = [_ent("Unknown Male A"), _ent("Unknown Male B")]

    # Mock the LLM to return empty so co-occurrence fires.
    with patch(
        "operation_lens_v2.ingestion.relationship_extractor._run_llm_stage",
        new_callable=AsyncMock,
        return_value=[],
    ):
        rels = await extract_relationships(text, entities)

    assert len(rels) >= 1
    assert rels[0].relation_type == "MENTIONED_WITH"
    assert rels[0].extraction_method == "co-occurrence"


# ── LLM stage ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_stage_parses_valid_json():
    from operation_lens_v2.ingestion.relationship_extractor import _run_llm_stage

    fake_response = (
        '[{"source": "Marcus Webb", "target": "Depot", '
        '"relation_type": "OBSERVED_AT", "span_text": "Webb at Depot", "confidence": 0.82}]'
    )
    entities = [_ent("Marcus Webb"), _ent("Depot", "LOCATION")]

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {"response": fake_response}
        rels = await _run_llm_stage("Webb was at the Depot.", entities)

    assert len(rels) == 1
    assert rels[0].relation_type == "OBSERVED_AT"
    assert rels[0].confidence == 0.82


@pytest.mark.asyncio
async def test_llm_stage_handles_json_parse_failure_gracefully():
    from operation_lens_v2.ingestion.relationship_extractor import _run_llm_stage

    entities = [_ent("Marcus Webb")]
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {"response": "not valid json at all {{"}
        rels = await _run_llm_stage("Some text.", entities)

    assert rels == []


@pytest.mark.asyncio
async def test_llm_confidence_is_clamped():
    from operation_lens_v2.config import settings
    from operation_lens_v2.ingestion.relationship_extractor import _run_llm_stage

    fake_response = (
        '[{"source": "A", "target": "B", "relation_type": "LINKED_TO", '
        '"span_text": "A linked to B", "confidence": 99.0}]'
    )
    entities = [_ent("A"), _ent("B")]
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {"response": fake_response}
        rels = await _run_llm_stage("A linked to B.", entities)

    assert rels[0].confidence <= settings.llm_confidence_max
