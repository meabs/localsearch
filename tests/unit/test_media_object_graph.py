from __future__ import annotations

import uuid

import pytest

from operation_lens_v2.api.routes.graph import media_frame_file, media_network
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store, media_ingest, media_objects


def test_media_object_graph_returns_assets_frames_and_objects(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "media-graph.duckdb"
    con = duck_store.init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    case_id = duck_store.create_case(con, case_ref="MEDIA_CASE", case_name="Media Case")
    doc_id = str(uuid.uuid4())
    duck_store.upsert_document(
        con,
        doc_id=doc_id,
        filename="clip.mp4",
        filepath="C:/evidence/clip.mp4",
        page_count=1,
        ocr_used=False,
        case_id=case_id,
        doc_format="media",
    )
    asset_id = duck_store.upsert_media_asset(
        con,
        doc_id=doc_id,
        case_id=case_id,
        filename="clip.mp4",
        filepath="C:/evidence/clip.mp4",
        media_type="audio_video",
    )
    frame_id = duck_store.insert_media_frame(
        con,
        asset_id=asset_id,
        doc_id=doc_id,
        timestamp_seconds=1.0,
        frame_index=0,
        image_path="data/media_frames/frame.jpg",
        description="Frame 1 shows a person standing beside a bag in an indoor room.",
    )
    person_id = duck_store.insert_media_detection(
        con,
        frame_id=frame_id,
        asset_id=asset_id,
        doc_id=doc_id,
        label="person",
        confidence=0.92,
        description="Person detected near the centre of the frame.",
        x1=1,
        y1=2,
        x2=30,
        y2=40,
    )
    bag_id = duck_store.insert_media_detection(
        con,
        frame_id=frame_id,
        asset_id=asset_id,
        doc_id=doc_id,
        label="bag",
        confidence=0.74,
        description="Bag detected on the right of the frame.",
        x1=35,
        y1=3,
        x2=50,
        y2=38,
    )
    duck_store.insert_media_object_relationship(
        con,
        source_detection_id=person_id,
        target_detection_id=bag_id,
        relation_type="NEAR",
        confidence=0.74,
        frame_id=frame_id,
        asset_id=asset_id,
    )

    result = media_network(case_ref="MEDIA_CASE")

    assert result["graph_type"] == "media_object"
    assert result["meta"]["asset_count"] == 1
    assert result["meta"]["frame_count"] == 1
    assert result["meta"]["detection_count"] == 2
    assert {node["entity_type"] for node in result["nodes"]} == {
        "MEDIA_ASSET",
        "MEDIA_FRAME",
        "MEDIA_OBJECT",
    }
    object_nodes = [node for node in result["nodes"] if node["entity_type"] == "MEDIA_OBJECT"]
    frame_nodes = [node for node in result["nodes"] if node["entity_type"] == "MEDIA_FRAME"]
    assert {node["description"] for node in object_nodes} == {
        "Person detected near the centre of the frame.",
        "Bag detected on the right of the frame.",
    }
    assert frame_nodes[0]["description"] == "Frame 1 shows a person standing beside a bag in an indoor room."
    assert any(edge["type"] == "NEAR" for edge in result["edges"])
    stored_frame = duck_store.get_media_frame(con, frame_id)
    assert stored_frame is not None
    assert stored_frame["image_path"] == "data/media_frames/frame.jpg"
    assert stored_frame["description"] == "Frame 1 shows a person standing beside a bag in an indoor room."


def test_find_ffmpeg_prefers_configured_executable(tmp_path, monkeypatch) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(settings, "ffmpeg_path", str(ffmpeg))

    assert media_objects.find_ffmpeg_executable() == ffmpeg


def test_media_object_graph_keeps_multiple_distinct_files(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "media-graph-multiple.duckdb"
    con = duck_store.init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    case_id = duck_store.create_case(con, case_ref="MEDIA_MULTI", case_name="Media Multi")
    for index, source_hash in enumerate(["hash-a", "hash-b", "hash-a"], start=1):
        doc_id = str(uuid.uuid4())
        duck_store.upsert_document(
            con,
            doc_id=doc_id,
            filename="clip.mp4",
            filepath=f"C:/evidence/session-{index}/clip.mp4",
            page_count=1,
            ocr_used=False,
            case_id=case_id,
            doc_format="media",
            source_hash=source_hash,
        )
        duck_store.upsert_media_asset(
            con,
            doc_id=doc_id,
            case_id=case_id,
            filename="clip.mp4",
            filepath=f"C:/evidence/session-{index}/clip.mp4",
            media_type="audio_video",
        )

    result = media_network(case_ref="MEDIA_MULTI")

    asset_nodes = [node for node in result["nodes"] if node["entity_type"] == "MEDIA_ASSET"]
    assert result["meta"]["asset_count"] == 2
    assert len(asset_nodes) == 2


def test_media_routes_do_not_run_schema_migrations_on_read(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "media-routes.duckdb"
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"not-a-real-jpeg-but-good-enough-for-file-serving")

    con = duck_store.init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    case_id = duck_store.create_case(con, case_ref="MEDIA_RO", case_name="Media Read Only")
    doc_id = str(uuid.uuid4())
    duck_store.upsert_document(
        con,
        doc_id=doc_id,
        filename="clip.mp4",
        filepath="C:/evidence/clip.mp4",
        page_count=1,
        ocr_used=False,
        case_id=case_id,
        doc_format="media",
    )
    asset_id = duck_store.upsert_media_asset(
        con,
        doc_id=doc_id,
        case_id=case_id,
        filename="clip.mp4",
        filepath="C:/evidence/clip.mp4",
        media_type="audio_video",
    )
    frame_id = duck_store.insert_media_frame(
        con,
        asset_id=asset_id,
        doc_id=doc_id,
        timestamp_seconds=1.0,
        frame_index=0,
        image_path=str(frame_path),
        description="Frame kept on disk.",
    )

    def _unexpected_schema_touch(_con) -> None:
        raise AssertionError("read route should not run schema migrations")

    monkeypatch.setattr(duck_store, "ensure_media_object_tables", _unexpected_schema_touch)

    network_result = media_network(case_ref="MEDIA_RO")
    response = media_frame_file(frame_id)

    assert network_result["meta"]["asset_count"] == 1
    assert response.path == frame_path


async def _embed_stub(_text: str) -> list[float]:
    return [0.1] * 768


@pytest.mark.asyncio
async def test_media_object_vector_rows_index_detection_text(monkeypatch) -> None:
    monkeypatch.setattr(media_ingest.embedder, "embed_text", _embed_stub)

    chunks, rows = await media_ingest._media_object_vector_rows(
        doc_id="doc-1",
        media_graph={
            "evidence_texts": [
                {
                    "frame_id": "frame-1",
                    "frame_index": 1,
                    "description": "Frame 2 shows a person carrying a suitcase through a hallway.",
                    "text": "Frame summary for clip.mp4 at 2.0s: Frame 2 shows a person carrying a suitcase through a hallway.",
                },
                {
                    "detection_id": "det-1",
                    "frame_index": 2,
                    "description": "Person detected in clip.mp4 at 3.0s near the centre.",
                    "text": (
                        "Person detected in clip.mp4 at 3.0s near the centre. "
                        "Bounding box [1, 2, 3, 4]."
                    ),
                }
            ]
        },
    )

    assert chunks[0].chunk_id == "media-frame:frame-1"
    assert chunks[0].page == 2
    assert rows[0]["text"].startswith("Frame summary")
    assert chunks[1].chunk_id == "media-object:det-1"
    assert chunks[1].page == 3
    assert rows[1]["text"].startswith("Person detected")
