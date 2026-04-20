from __future__ import annotations

from pathlib import Path

import piexif
import pytest
from PIL import Image, ImageDraw

from operation_lens_v2.ingestion import duck_store, extractor, image_ingest
from operation_lens_v2.models import ExtractedEntity


def _dms(deg: float) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    abs_deg = abs(deg)
    d = int(abs_deg)
    m_float = (abs_deg - d) * 60
    m = int(m_float)
    s = int(round((m_float - m) * 60 * 100))
    return ((d, 1), (m, 1), (s, 100))


def _write_geotagged_jpeg(
    path: Path, lat: float, lon: float, text: str = "Suspect photo from seized device"
) -> None:
    img = Image.new("RGB", (900, 260), "white")
    ImageDraw.Draw(img).text((20, 120), text, fill="black")

    gps_ifd = {
        piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
        piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
        piexif.GPSIFD.GPSLatitude: _dms(lat),
        piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
        piexif.GPSIFD.GPSLongitude: _dms(lon),
    }
    exif_bytes = piexif.dump(
        {"0th": {}, "Exif": {}, "GPS": gps_ifd, "1st": {}, "thumbnail": None}
    )
    img.save(path, "JPEG", exif=exif_bytes)


def test_read_image_gps_parses_embedded_coords(tmp_path: Path):
    path = tmp_path / "trafalgar.jpg"
    _write_geotagged_jpeg(path, lat=51.5074, lon=-0.1278)

    result = extractor.read_image_gps(path)

    assert result is not None
    lat, lon = result
    assert abs(lat - 51.5074) < 1e-3
    assert abs(lon - (-0.1278)) < 1e-3


def test_read_image_gps_returns_none_when_absent(tmp_path: Path):
    path = tmp_path / "plain.jpg"
    Image.new("RGB", (400, 200), "white").save(path, "JPEG")

    assert extractor.read_image_gps(path) is None


@pytest.mark.asyncio
async def test_ingest_image_with_gps_creates_geocoded_location(
    tmp_path: Path, monkeypatch
):
    image_path = tmp_path / "geo_evidence.jpg"
    _write_geotagged_jpeg(image_path, lat=51.5074, lon=-0.1278)

    monkeypatch.setattr(
        image_ingest.extractor,
        "ocr_image",
        lambda *_args, **_kwargs: "Suspect photo seized at scene",
    )
    monkeypatch.setattr(image_ingest.ner_rules, "extract_rule_entities", lambda _t: [])
    monkeypatch.setattr(
        image_ingest.ner_gliner,
        "extract_general_entities",
        lambda _t: [
            ExtractedEntity(text="scene", entity_type="LOCATION", start=21, end=26, confidence=0.6)
        ],
    )
    monkeypatch.setattr(image_ingest.ner_llm, "extract_llm_entities", _async_empty)
    monkeypatch.setattr(
        image_ingest.relationship_extractor, "extract_relationships", _async_empty
    )
    monkeypatch.setattr(image_ingest.embedder, "embed_text", _async_embed)

    db_path = tmp_path / "evidence.duckdb"
    result = await image_ingest.ingest_image(
        image_path, db_path=str(db_path), case_ref="CASE_GPS"
    )

    con = duck_store.init_db(str(db_path))

    rows = con.execute(
        """
        SELECT e.canonical_name, e.latitude, e.longitude, e.geocode_provider,
               e.geocode_display_name
        FROM entities e
        JOIN entity_aliases ea ON ea.entity_id = e.entity_id
        WHERE ea.source_doc = ? AND e.geocode_provider IS NOT NULL
        """,
        [result["doc_id"]],
    ).fetchall()

    print(f"\n--- geocoded entities on doc {result['doc_id']} ---")
    for row in rows:
        print(row)

    chunk_text = con.execute(
        "SELECT text FROM chunks WHERE doc_id = ?", [result["doc_id"]]
    ).fetchone()[0]
    print(f"--- chunk text ---\n{chunk_text}\n---")

    assert rows, "expected at least one entity with a geocode from EXIF"
    lat_by_provider = {row[3]: (row[1], row[2]) for row in rows}
    assert "exif" in lat_by_provider
    lat, lon = lat_by_provider["exif"]
    assert abs(lat - 51.5074) < 1e-3
    assert abs(lon - (-0.1278)) < 1e-3
    assert "EXIF GPS" in chunk_text


async def _async_embed(_text: str) -> list[float]:
    return [0.1] * 768


async def _async_empty(*_args, **_kwargs):
    return []
