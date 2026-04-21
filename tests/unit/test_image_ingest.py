from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from operation_lens_v2.ingestion import duck_store, image_ingest
from operation_lens_v2.ingestion.extractor import OcrResult
from operation_lens_v2.models import ExtractedEntity


@pytest.mark.asyncio
async def test_ingest_image_persists_thumbnail_and_format(tmp_path: Path, monkeypatch):
    image_path = tmp_path / "plate.png"
    image = Image.new("RGB", (600, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 80), "Plate RX71 KLD at 14 Arkwright Road", fill="black")
    image.save(image_path)

    monkeypatch.setattr(
        image_ingest.extractor,
        "ocr_image_result",
        lambda *_args, **_kwargs: OcrResult(
            text="Plate RX71 KLD at 14 Arkwright Road",
            method="tesseract",
            confidence=0.91,
            needs_review=False,
        ),
    )
    monkeypatch.setattr(
        image_ingest.ner_rules,
        "extract_rule_entities",
        lambda _text: [
            ExtractedEntity(text="RX71 KLD", entity_type="VEHICLE", start=6, end=14),
            ExtractedEntity(text="14 Arkwright Road", entity_type="LOCATION", start=18, end=35),
        ],
    )
    monkeypatch.setattr(image_ingest.ner_gliner, "extract_general_entities", lambda _text: [])
    monkeypatch.setattr(image_ingest.ner_llm, "extract_llm_entities", _async_empty)
    monkeypatch.setattr(
        image_ingest.relationship_extractor,
        "extract_relationships",
        _async_rel_empty,
    )
    monkeypatch.setattr(image_ingest.embedder, "embed_text", _async_embed)

    result = await image_ingest.ingest_image(
        image_path,
        db_path=str(tmp_path / "evidence.duckdb"),
        case_ref="CASE_IMAGE",
    )

    con = duck_store.init_db(str(tmp_path / "evidence.duckdb"))
    row = con.execute(
        """
        SELECT format, thumbnail_blob IS NOT NULL, ocr_failed, source_hash IS NOT NULL
        FROM documents WHERE doc_id = ?
        """,
        [result["doc_id"]],
    ).fetchone()
    assert row == ("image", True, False, True)
    quality = duck_store.list_page_quality(con, result["doc_id"])
    assert quality[0]["ocr_confidence"] == pytest.approx(0.91)
    assert quality[0]["needs_review"] is False


async def _async_embed(_text: str) -> list[float]:
    return [0.1] * 768


async def _async_empty(*_args, **_kwargs):
    return []


async def _async_rel_empty(*_args, **_kwargs):
    return []
