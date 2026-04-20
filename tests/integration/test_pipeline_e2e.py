from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from operation_lens_v2.ingestion import csv_ingest, duck_store, image_ingest
from operation_lens_v2.ingestion.email_eml import parse_eml


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_pipeline_pdf_csv_eml_image(tmp_path: Path, monkeypatch):
    fixtures = Path(__file__).parent / "fixtures"
    csv_path = fixtures / "sample.csv"
    eml_path = fixtures / "sample.eml"
    png_path = tmp_path / "sample.png"
    Image.new("RGB", (320, 120), "white").save(png_path)

    async def _embed(_text):
      return [0.1] * 768

    async def _empty(*_args, **_kwargs):
      return []

    monkeypatch.setattr(csv_ingest.embedder, "embed_text", _embed)
    monkeypatch.setattr(csv_ingest.ner_gliner, "extract_general_entities", lambda _text: [])
    monkeypatch.setattr(csv_ingest.ner_rules, "extract_rule_entities", lambda _text: [])
    monkeypatch.setattr(csv_ingest.ner_llm, "extract_llm_entities", _empty)
    monkeypatch.setattr(csv_ingest.relationship_extractor, "extract_relationships", _empty)

    monkeypatch.setattr(image_ingest.extractor, "ocr_image", lambda *_args, **_kwargs: "Plate RX71 KLD")
    monkeypatch.setattr(image_ingest.embedder, "embed_text", _embed)
    monkeypatch.setattr(image_ingest.ner_gliner, "extract_general_entities", lambda _text: [])
    monkeypatch.setattr(image_ingest.ner_rules, "extract_rule_entities", lambda _text: [])
    monkeypatch.setattr(image_ingest.ner_llm, "extract_llm_entities", _empty)
    monkeypatch.setattr(image_ingest.relationship_extractor, "extract_relationships", _empty)

    db_path = tmp_path / "evidence.duckdb"
    con = duck_store.init_db(str(db_path))

    class _VS:
        def upsert(self, _rows):
            return None

    csv_result = await csv_ingest.ingest_csv(
        csv_path=csv_path,
        case_ref="CASE_INTEGRATION",
        duck_con=con,
        lance_table=_VS(),
        gliner_model=None,
        ollama_client=None,
        config=csv_ingest.CsvIngestConfig(),
    )
    image_result = await image_ingest.ingest_image(
        png_path,
        db_path=str(db_path),
        case_ref="CASE_INTEGRATION",
    )
    eml_doc = parse_eml(eml_path)

    assert csv_result["format"] == "csv"
    assert image_result["format"] == "image"
    assert eml_doc.messages
