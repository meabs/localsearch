from __future__ import annotations

import itertools
import logging
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _prepend_path(path: Path) -> None:
    path_text = str(path)
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if path_text not in parts:
        os.environ["PATH"] = os.pathsep.join([path_text, *parts])


def _configured_ffmpeg_path() -> Path | None:
    raw = (settings.ffmpeg_path or "").strip().strip('"')
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_file():
        return candidate
    if candidate.is_dir():
        executable = candidate / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if executable.exists():
            return executable
    return None


def find_ffmpeg_executable() -> Path | None:
    configured = _configured_ffmpeg_path()
    if configured is not None:
        return configured

    discovered = shutil.which("ffmpeg")
    if discovered:
        return Path(discovered)

    candidates: list[Path] = []
    if os.name == "nt":
        candidates.extend(
            [
                Path("C:/ffmpeg/bin/ffmpeg.exe"),
                Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
                Path("C:/Program Files/Git/usr/bin/ffmpeg.exe"),
            ]
        )
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            winget_root = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
            if winget_root.exists():
                try:
                    candidates.extend(winget_root.glob("**/ffmpeg.exe"))
                except OSError:
                    logger.debug("Unable to scan WinGet packages for ffmpeg", exc_info=True)
    else:
        candidates.extend([Path("/usr/local/bin/ffmpeg"), Path("/opt/homebrew/bin/ffmpeg")])

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def ensure_ffmpeg_on_path() -> Path | None:
    executable = find_ffmpeg_executable()
    if executable is not None:
        _prepend_path(executable.parent)
    return executable


def _extract_frame(media_path: Path, output_path: Path) -> bool:
    ffmpeg = ensure_ffmpeg_on_path()
    if ffmpeg is None:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        [
            str(ffmpeg),
            "-y",
            "-ss",
            "00:00:01",
            "-i",
            str(media_path),
            "-frames:v",
            "1",
            str(output_path),
        ],
        [str(ffmpeg), "-y", "-i", str(media_path), "-frames:v", "1", str(output_path)],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return True
        logger.debug("ffmpeg frame extraction failed: %s", result.stderr[-600:])
    return False


def _detect_objects(image_path: Path) -> tuple[list[dict[str, object]], str]:
    model_name = (settings.media_object_detection_model or "").strip()
    if not model_name:
        return [], "object_detection=disabled"
    try:
        from ultralytics import YOLO  # type: ignore[import]
    except ImportError:
        return [], "object_detection=ultralytics_missing"

    try:
        model = YOLO(model_name)
        results = model(str(image_path), verbose=False)
    except Exception as exc:
        logger.warning("Object detection failed for %s: %s", image_path, exc)
        return [], f"object_detection_error={type(exc).__name__}: {exc}"

    detections: list[dict[str, object]] = []
    for result in results:
        names = getattr(result, "names", {}) or {}
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        xyxy = getattr(boxes, "xyxy", None)
        conf = getattr(boxes, "conf", None)
        cls = getattr(boxes, "cls", None)
        if xyxy is None or conf is None or cls is None:
            continue
        for bbox, confidence, class_idx in zip(
            xyxy.tolist(),
            conf.tolist(),
            cls.tolist(),
            strict=False,
        ):
            label = names.get(int(class_idx), str(int(class_idx)))
            detections.append(
                {
                    "label": str(label),
                    "confidence": float(confidence),
                    "bbox": [float(v) for v in bbox],
                }
            )
    return detections, f"object_detection={model_name}"


def _contains(outer: list[float], inner: list[float]) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _near(a: list[float], b: list[float], image_width: int, image_height: int) -> bool:
    ax = (a[0] + a[2]) / 2
    ay = (a[1] + a[3]) / 2
    bx = (b[0] + b[2]) / 2
    by = (b[1] + b[3]) / 2
    diagonal = max((image_width**2 + image_height**2) ** 0.5, 1.0)
    distance = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
    return distance / diagonal <= 0.22


def extract_media_objects(
    con,
    *,
    media_path: Path,
    doc_id: str,
    case_id: str,
) -> dict[str, object]:
    suffix = media_path.suffix.lower()
    asset_id = duck_store.upsert_media_asset(
        con,
        doc_id=doc_id,
        case_id=case_id,
        filename=media_path.name,
        filepath=str(media_path),
        media_type="image" if suffix in IMAGE_SUFFIXES else "audio_video",
    )

    frame_path: Path | None = None
    timestamp_seconds = 0.0
    notes: list[str] = []
    if suffix in IMAGE_SUFFIXES:
        frame_path = media_path
        notes.append("frame_source=original_image")
    else:
        frame_path = Path("data/media_frames") / doc_id / "frame_000001.jpg"
        timestamp_seconds = 1.0
        if not _extract_frame(media_path, frame_path):
            notes.append("frame_extraction=unavailable")
            frame_path = None

    frame_count = 0
    detection_count = 0
    relation_count = 0
    if frame_path is not None:
        frame_id = duck_store.insert_media_frame(
            con,
            asset_id=asset_id,
            doc_id=doc_id,
            timestamp_seconds=timestamp_seconds,
            frame_index=0,
            image_path=str(frame_path),
        )
        frame_count = 1
        detections, detector_note = _detect_objects(frame_path)
        notes.append(detector_note)
        detection_ids: list[tuple[str, dict[str, object]]] = []
        try:
            with Image.open(frame_path) as img:
                width, height = img.size
        except Exception:
            width, height = 1, 1

        for item in detections:
            bbox = item["bbox"]
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            detection_id = duck_store.insert_media_detection(
                con,
                frame_id=frame_id,
                asset_id=asset_id,
                doc_id=doc_id,
                label=str(item["label"]),
                confidence=float(item["confidence"]),
                x1=float(bbox[0]),
                y1=float(bbox[1]),
                x2=float(bbox[2]),
                y2=float(bbox[3]),
            )
            detection_ids.append((detection_id, item))
            detection_count += 1

        for (left_id, left), (right_id, right) in itertools.combinations(detection_ids, 2):
            left_bbox = left["bbox"]
            right_bbox = right["bbox"]
            if not isinstance(left_bbox, list) or not isinstance(right_bbox, list):
                continue
            relation_type = ""
            confidence = min(float(left["confidence"]), float(right["confidence"]))
            if _contains(left_bbox, right_bbox):
                relation_type = "CONTAINS"
            elif _contains(right_bbox, left_bbox):
                relation_type = "INSIDE"
            elif _near(left_bbox, right_bbox, width, height):
                relation_type = "NEAR"
            if not relation_type:
                continue
            duck_store.insert_media_object_relationship(
                con,
                source_detection_id=left_id,
                target_detection_id=right_id,
                relation_type=relation_type,
                confidence=confidence,
                frame_id=frame_id,
                asset_id=asset_id,
            )
            relation_count += 1

    return {
        "asset_id": asset_id,
        "frames": frame_count,
        "detections": detection_count,
        "object_relationships": relation_count,
        "notes": "; ".join(notes),
    }
