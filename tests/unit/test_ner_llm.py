from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from operation_lens_v2.ingestion.ner_llm import extract_llm_entities
from operation_lens_v2.models import ExtractedEntity


@pytest.mark.asyncio
async def test_llm_entity_stage_parses_valid_json() -> None:
    fake_response = (
        '[{"text": "Marcus Webb", "entity_type": "PERSON", "confidence": 0.81}, '
        '{"text": "Glock 17", "entity_type": "WEAPON", "confidence": 0.72}]'
    )
    text = "Marcus Webb was seen carrying a Glock 17 near the depot."

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {"response": fake_response}
        entities = await extract_llm_entities(text)

    assert [entity.entity_type for entity in entities] == ["PERSON", "WEAPON"]
    assert entities[1].text == "Glock 17"


@pytest.mark.asyncio
async def test_llm_entity_stage_maps_common_type_aliases() -> None:
    fake_response = '[{"text": "Arkwright Holdings Ltd", "entity_type": "ORG", "confidence": 0.61}]'
    text = "Financial records link Arkwright Holdings Ltd to the transfer."

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {"response": fake_response}
        entities = await extract_llm_entities(text)

    assert len(entities) == 1
    assert entities[0].entity_type == "ORGANISATION"


@pytest.mark.asyncio
async def test_llm_entity_stage_ignores_missing_surface_text() -> None:
    fake_response = '[{"text": "Not In Text", "entity_type": "PERSON", "confidence": 0.7}]'
    text = "Marcus Webb attended the briefing."

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {"response": fake_response}
        entities = await extract_llm_entities(text)

    assert entities == []


@pytest.mark.asyncio
async def test_llm_entity_stage_handles_parse_failures() -> None:
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {"response": "not valid json"}
        entities = await extract_llm_entities("Some text")

    assert entities == []


@pytest.mark.asyncio
async def test_llm_entity_stage_uses_existing_entities_as_context() -> None:
    text = "Marcus Webb used device IMEI 123456789012345."
    existing = [ExtractedEntity(text="Marcus Webb", entity_type="PERSON", start=0, end=11)]

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json = lambda: {
            "response": (
                '[{"text": "IMEI 123456789012345", "entity_type": "SERIAL", "confidence": 0.66}]'
            )
        }
        entities = await extract_llm_entities(text, existing_entities=existing)

    assert len(entities) == 1
    assert entities[0].entity_type == "SERIAL"
