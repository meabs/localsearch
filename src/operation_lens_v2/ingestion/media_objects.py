from __future__ import annotations

import base64
import itertools
import json
import logging
import os
import re
import shutil
import subprocess
from collections import Counter
from functools import lru_cache
from pathlib import Path

import httpx
from PIL import Image

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_GENERIC_PRIORITY_LABELS = {"weapon", "firearm"}
_FIREARM_TOKENS = {"gun", "firearm", "pistol", "revolver", "rifle", "shotgun"}
_BLADE_TOKENS = {"knife", "blade", "machete", "dagger"}


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


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


def _ffprobe_executable(ffmpeg: Path) -> Path | None:
    candidate = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if candidate.exists():
        return candidate
    discovered = shutil.which("ffprobe")
    return Path(discovered) if discovered else None


def _media_duration_seconds(media_path: Path, ffmpeg: Path) -> float | None:
    ffprobe = _ffprobe_executable(ffmpeg)
    if ffprobe is None:
        return None
    result = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        logger.debug("ffprobe duration failed: %s", result.stderr[-600:])
        return None
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def _frame_timestamps(media_path: Path, ffmpeg: Path) -> list[float]:
    max_frames = max(1, int(settings.media_object_frame_count or 1))
    duration = _media_duration_seconds(media_path, ffmpeg)
    if duration is None:
        return [0.0]
    if max_frames == 1:
        return [round(max(0.0, duration / 2), 3)]

    start_margin = 0.0 if duration <= 2 else min(0.4, duration * 0.03)
    end_margin = 0.0 if duration <= 2 else min(0.4, duration * 0.03)
    usable_start = min(start_margin, duration)
    usable_end = max(usable_start, duration - end_margin)
    if usable_end == usable_start:
        return [round(usable_start, 3)]

    step = (usable_end - usable_start) / max(max_frames - 1, 1)
    timestamps: list[float] = []
    seen: set[float] = set()
    for idx in range(max_frames):
        timestamp = round(min(duration, max(0.0, usable_start + step * idx)), 3)
        if timestamp in seen:
            continue
        seen.add(timestamp)
        timestamps.append(timestamp)
    return timestamps or [0.0]


def _extract_frame_at(media_path: Path, output_path: Path, timestamp_seconds: float) -> bool:
    ffmpeg = ensure_ffmpeg_on_path()
    if ffmpeg is None:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        [
            str(ffmpeg),
            "-y",
            "-ss",
            f"{timestamp_seconds:.3f}",
            "-i",
            str(media_path),
            "-frames:v",
            "1",
            str(output_path),
        ],
        [
            str(ffmpeg),
            "-y",
            "-i",
            str(media_path),
            "-ss",
            f"{timestamp_seconds:.3f}",
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


def _parse_model_names(value: str) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;\n]+", value or ""):
        model_name = part.strip()
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)
        models.append(model_name)
    return models


def _priority_labels() -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;\n]+", settings.media_object_priority_labels or ""):
        label = re.sub(r"\s+", " ", part.strip().lower())
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


@lru_cache(maxsize=8)
def _load_yolo_model(model_name: str, open_vocab: bool = False):
    if open_vocab:
        from ultralytics import YOLOWorld  # type: ignore[import]

        return YOLOWorld(model_name)
    from ultralytics import YOLO  # type: ignore[import]

    return YOLO(model_name)


def _canonical_object_label(label: str) -> str:
    normalized = re.sub(r"[_\-]+", " ", str(label or "").strip().lower())
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _label_family(label: str) -> str:
    normalized = _canonical_object_label(label)
    tokens = set(normalized.split())
    if tokens & _FIREARM_TOKENS:
        return "firearm"
    if tokens & _BLADE_TOKENS:
        return "blade"
    if normalized in _GENERIC_PRIORITY_LABELS:
        return "weapon"
    return normalized


def _is_priority_label(label: str) -> bool:
    normalized = _canonical_object_label(label)
    if normalized in _priority_labels():
        return True
    family = _label_family(normalized)
    return family in {"firearm", "blade", "weapon"}


def _bbox_iou(left: list[float], right: list[float]) -> float:
    left_x1, left_y1, left_x2, left_y2 = left
    right_x1, right_y1, right_x2, right_y2 = right
    inter_x1 = max(left_x1, right_x1)
    inter_y1 = max(left_y1, right_y1)
    inter_x2 = min(left_x2, right_x2)
    inter_y2 = min(left_y2, right_y2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    left_area = max(0.0, left_x2 - left_x1) * max(0.0, left_y2 - left_y1)
    right_area = max(0.0, right_x2 - right_x1) * max(0.0, right_y2 - right_y1)
    denom = left_area + right_area - inter_area
    return inter_area / denom if denom > 0 else 0.0


def _label_rank(label: str) -> tuple[int, int]:
    normalized = _canonical_object_label(label)
    family = _label_family(normalized)
    is_generic = 1 if normalized in _GENERIC_PRIORITY_LABELS else 0
    specificity = len(normalized.replace(" ", ""))
    return (0 if family in {"firearm", "blade"} else is_generic, specificity)


def _choose_detection(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    left_rank = _label_rank(str(left.get("label") or ""))
    right_rank = _label_rank(str(right.get("label") or ""))
    left_priority = 1 if left.get("priority") else 0
    right_priority = 1 if right.get("priority") else 0
    left_score = (left_priority, left_rank[0] == 0, left_rank[1], float(left.get("confidence") or 0))
    right_score = (right_priority, right_rank[0] == 0, right_rank[1], float(right.get("confidence") or 0))
    winner = right if right_score > left_score else left
    merged_sources = {
        *[str(item).strip() for item in str(left.get("source") or "").split("+") if str(item).strip()],
        *[str(item).strip() for item in str(right.get("source") or "").split("+") if str(item).strip()],
    }
    winner["source"] = "+".join(sorted(merged_sources))
    winner["priority"] = bool(left.get("priority") or right.get("priority"))
    return winner


def _merge_detections(detections: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for candidate in sorted(
        detections,
        key=lambda item: (bool(item.get("priority")), float(item.get("confidence") or 0)),
        reverse=True,
    ):
        bbox = candidate.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        chosen = False
        for index, current in enumerate(merged):
            current_bbox = current.get("bbox")
            if not isinstance(current_bbox, list) or len(current_bbox) != 4:
                continue
            if _label_family(str(current.get("label") or "")) != _label_family(str(candidate.get("label") or "")):
                continue
            if _bbox_iou(current_bbox, bbox) < 0.55:
                continue
            merged[index] = _choose_detection(current, candidate)
            chosen = True
            break
        if not chosen:
            merged.append(dict(candidate))
    return merged


def _run_detection_model(
    image_path: Path,
    *,
    model_name: str,
    open_vocab: bool = False,
    class_labels: list[str] | None = None,
) -> tuple[list[dict[str, object]], str]:
    try:
        model = _load_yolo_model(model_name, open_vocab=open_vocab)
    except ImportError:
        return [], "object_detection=ultralytics_missing"
    except Exception as exc:
        detector_type = "open_vocab" if open_vocab else "detector"
        logger.warning("Object %s load failed for %s: %s", detector_type, model_name, exc)
        return [], f"{detector_type}_load_error={type(exc).__name__}: {exc}"

    try:
        if open_vocab and class_labels and hasattr(model, "set_classes"):
            model.set_classes(class_labels)
        results = model(
            str(image_path),
            conf=float(settings.media_object_confidence or 0.25),
            verbose=False,
        )
    except Exception as exc:
        detector_type = "open_vocab" if open_vocab else "detector"
        logger.warning("Object %s failed for %s: %s", detector_type, image_path, exc)
        return [], f"{detector_type}_error={type(exc).__name__}: {exc}"

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
            label = _canonical_object_label(names.get(int(class_idx), str(int(class_idx))))
            detections.append(
                {
                    "label": label,
                    "confidence": float(confidence),
                    "bbox": [float(v) for v in bbox],
                    "source": model_name,
                    "priority": _is_priority_label(label),
                }
            )
    detector_label = "open_vocab" if open_vocab else "object_detection"
    return detections, f"{detector_label}={model_name}"


def _detect_objects(image_path: Path) -> tuple[list[dict[str, object]], str]:
    detections: list[dict[str, object]] = []
    notes: list[str] = []

    model_names = _parse_model_names(settings.media_object_detection_model or "")
    if not model_names:
        return [], "object_detection=disabled"

    for model_name in model_names:
        found, note = _run_detection_model(image_path, model_name=model_name)
        notes.append(note)
        detections.extend(found)

    open_vocab_model = (settings.media_object_open_vocab_model or "").strip()
    priority_labels = _priority_labels()
    if open_vocab_model and priority_labels:
        found, note = _run_detection_model(
            image_path,
            model_name=open_vocab_model,
            open_vocab=True,
            class_labels=priority_labels,
        )
        notes.append(note)
        detections.extend(found)

    merged = _merge_detections(detections)
    merged.sort(
        key=lambda item: (bool(item.get("priority")), float(item.get("confidence") or 0)),
        reverse=True,
    )
    return merged, "; ".join(notes)


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


def _bbox_position(bbox: list[float], image_width: int, image_height: int) -> str:
    width = max(float(image_width), 1.0)
    height = max(float(image_height), 1.0)
    center_x = ((bbox[0] + bbox[2]) / 2) / width
    center_y = ((bbox[1] + bbox[3]) / 2) / height
    horizontal = "left" if center_x < 0.34 else "right" if center_x > 0.66 else "centre"
    vertical = "upper" if center_y < 0.34 else "lower" if center_y > 0.66 else "middle"
    if horizontal == "centre" and vertical == "middle":
        return "near the centre of the frame"
    return f"in the {vertical} {horizontal} of the frame"


def _bbox_scale(bbox: list[float], image_width: int, image_height: int) -> str:
    image_area = max(float(image_width * image_height), 1.0)
    area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 0.0)
    ratio = area / image_area
    if ratio >= 0.30:
        return "large"
    if ratio >= 0.08:
        return "medium-sized"
    return "small"


def _ordered_detection_facts(
    detections: list[dict[str, object]],
    *,
    image_width: int,
    image_height: int,
) -> list[str]:
    facts: list[str] = []
    for index, item in enumerate(detections[:10], start=1):
        bbox = item.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        label = str(item.get("label") or "object")
        confidence = float(item.get("confidence") or 0)
        position = _bbox_position(bbox, image_width, image_height)
        scale = _bbox_scale(bbox, image_width, image_height)
        priority = "priority object" if item.get("priority") else "object"
        facts.append(
            f"{index}. {label} ({confidence:.2f}, {priority}, {scale}, {position}, detector={item.get('source') or 'unknown'})"
        )
    return facts


def _default_frame_description(
    *,
    media_name: str,
    frame_index: int,
    timestamp_seconds: float,
    detections: list[dict[str, object]],
    image_width: int,
    image_height: int,
) -> str:
    frame_ref = f"Frame {frame_index + 1} of {media_name}"
    if timestamp_seconds > 0:
        frame_ref = f"{frame_ref} at {timestamp_seconds:.1f}s"
    if not detections:
        return f"{frame_ref} contains no confident object detections."

    label_counts = Counter(str(item.get("label") or "object") for item in detections)
    summary = ", ".join(
        f"{count} {label}{'' if count == 1 else 's'}"
        for label, count in label_counts.most_common(4)
    )
    key_facts = "; ".join(_ordered_detection_facts(detections[:3], image_width=image_width, image_height=image_height))
    priority = [str(item.get("label") or "") for item in detections if item.get("priority")]
    if priority:
        flagged = ", ".join(dict.fromkeys(priority))
        return f"{frame_ref} shows {summary}. Priority items noted: {flagged}. Key placements: {key_facts}."
    return f"{frame_ref} shows {summary}. Key placements: {key_facts}."


def _ollama_frame_description(
    *,
    image_path: Path,
    media_name: str,
    frame_index: int,
    timestamp_seconds: float,
    detections: list[dict[str, object]],
    image_width: int,
    image_height: int,
) -> tuple[str | None, str | None]:
    model_name = (settings.local_vision_model or "").strip() or (settings.local_extraction_model or "").strip()
    if not model_name:
        return None, None

    prompt = (
        "You are reviewing a sampled evidence frame.\n"
        "Return a JSON object only with keys frame_description and priority_findings.\n"
        "frame_description must be one sentence of 18 to 40 words.\n"
        "Use cautious language and mention weapons, firearms, blades, masks, bags, vehicles, or suspicious carry items explicitly when present.\n"
        "Do not invent objects that are not supported by the supplied evidence.\n\n"
        f"Media: {media_name}\n"
        f"Frame number: {frame_index + 1}\n"
        f"Timestamp seconds: {timestamp_seconds:.3f}\n"
        f"Image size: {image_width}x{image_height}\n"
        f"Priority labels to watch: {', '.join(_priority_labels()) or 'none'}\n"
        "Detections:\n"
        f"{chr(10).join(_ordered_detection_facts(detections, image_width=image_width, image_height=image_height)) or 'none'}\n"
    )
    payload: dict[str, object] = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    if (settings.local_vision_model or "").strip():
        try:
            payload["images"] = [base64.b64encode(image_path.read_bytes()).decode("ascii")]
        except OSError as exc:
            logger.debug("Vision frame read failed for %s: %s", image_path, exc)

    try:
        with httpx.Client(base_url=settings.ollama_base_url, timeout=settings.ollama_timeout) as client:
            response = client.post("/api/generate", json=payload)
            response.raise_for_status()
            raw_text = str(response.json().get("response") or "").strip()
    except Exception as exc:
        logger.warning("Local frame description failed for %s: %s", image_path, exc)
        return None, f"frame_summary_error={type(exc).__name__}"

    try:
        data = json.loads(_strip_markdown_fences(raw_text))
    except Exception as exc:
        logger.warning("Local frame summary JSON parse failure: %s | raw: %.200s", exc, raw_text)
        return None, "frame_summary_parse_error"

    if not isinstance(data, dict):
        return None, "frame_summary_invalid_response"
    description = str(data.get("frame_description") or "").strip()
    if not description:
        return None, "frame_summary_empty"
    return description, f"frame_summary_model={model_name}"


def _frame_description(
    *,
    image_path: Path,
    media_name: str,
    frame_index: int,
    timestamp_seconds: float,
    detections: list[dict[str, object]],
    image_width: int,
    image_height: int,
) -> tuple[str, str | None]:
    llm_description, note = _ollama_frame_description(
        image_path=image_path,
        media_name=media_name,
        frame_index=frame_index,
        timestamp_seconds=timestamp_seconds,
        detections=detections,
        image_width=image_width,
        image_height=image_height,
    )
    if llm_description:
        return llm_description, note
    return (
        _default_frame_description(
            media_name=media_name,
            frame_index=frame_index,
            timestamp_seconds=timestamp_seconds,
            detections=detections,
            image_width=image_width,
            image_height=image_height,
        ),
        note,
    )


def _describe_detection(
    *,
    media_name: str,
    label: str,
    confidence: float,
    frame_index: int,
    timestamp_seconds: float,
    bbox: list[float],
    image_width: int,
    image_height: int,
    priority: bool = False,
) -> str:
    position = _bbox_position(bbox, image_width, image_height)
    scale = _bbox_scale(bbox, image_width, image_height)
    priority_prefix = "Priority item: " if priority else ""
    return (
        f"{priority_prefix}{label.title()} detected in {media_name} at {timestamp_seconds:.1f}s "
        f"(sampled frame {frame_index + 1}); the detector places a {scale} "
        f"{label} {position} with confidence {confidence:.2f}."
    )


def extract_media_objects(
    con,
    *,
    media_path: Path,
    doc_id: str,
    case_id: str,
) -> dict[str, object]:
    suffix = media_path.suffix.lower()
    media_path = media_path.resolve()
    asset_id = duck_store.upsert_media_asset(
        con,
        doc_id=doc_id,
        case_id=case_id,
        filename=media_path.name,
        filepath=str(media_path),
        media_type="image" if suffix in IMAGE_SUFFIXES else "audio_video",
    )

    frame_specs: list[tuple[Path, float]] = []
    notes: list[str] = []
    if suffix in IMAGE_SUFFIXES:
        frame_specs.append((media_path, 0.0))
        notes.append("frame_source=original_image")
    else:
        ffmpeg = ensure_ffmpeg_on_path()
        if ffmpeg is None:
            notes.append("frame_extraction=ffmpeg_unavailable")
        else:
            timestamps = _frame_timestamps(media_path, ffmpeg)
            frame_root = (_PROJECT_ROOT / "data" / "media_frames" / doc_id).resolve()
            for index, timestamp_seconds in enumerate(timestamps):
                frame_path = frame_root / f"frame_{index + 1:06d}.jpg"
                if _extract_frame_at(media_path, frame_path, timestamp_seconds):
                    frame_specs.append((frame_path, timestamp_seconds))
            if not frame_specs:
                notes.append("frame_extraction=unavailable")
            else:
                notes.append(f"frames_sampled={len(frame_specs)}")

    frame_count = 0
    detection_count = 0
    relation_count = 0
    evidence_texts: list[dict[str, object]] = []
    detector_notes: set[str] = set()
    for frame_index, (frame_path, timestamp_seconds) in enumerate(frame_specs):
        try:
            with Image.open(frame_path) as img:
                width, height = img.size
        except Exception:
            width, height = 1, 1

        detections, detector_note = _detect_objects(frame_path)
        detector_notes.add(detector_note)
        frame_description, summary_note = _frame_description(
            image_path=frame_path,
            media_name=media_path.name,
            frame_index=frame_index,
            timestamp_seconds=timestamp_seconds,
            detections=detections,
            image_width=width,
            image_height=height,
        )
        if summary_note:
            detector_notes.add(summary_note)

        frame_id = duck_store.insert_media_frame(
            con,
            asset_id=asset_id,
            doc_id=doc_id,
            timestamp_seconds=timestamp_seconds,
            frame_index=frame_index,
            image_path=str(frame_path),
            description=frame_description,
        )
        frame_count += 1
        evidence_texts.append(
            {
                "frame_id": frame_id,
                "frame_index": frame_index,
                "timestamp_seconds": timestamp_seconds,
                "label": f"frame {frame_index + 1}",
                "description": frame_description,
                "frame_path": str(frame_path),
                "kind": "frame",
                "text": (
                    f"Frame summary for {media_path.name} at {timestamp_seconds:.1f}s "
                    f"(sampled frame {frame_index + 1}): {frame_description}"
                ),
            }
        )

        detection_ids: list[tuple[str, dict[str, object]]] = []
        for item in detections:
            bbox = item.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            label = str(item.get("label") or "object")
            confidence = float(item.get("confidence") or 0)
            bbox_values = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
            description = _describe_detection(
                media_name=media_path.name,
                label=label,
                confidence=confidence,
                frame_index=frame_index,
                timestamp_seconds=timestamp_seconds,
                bbox=bbox_values,
                image_width=width,
                image_height=height,
                priority=bool(item.get("priority")),
            )
            detection_id = duck_store.insert_media_detection(
                con,
                frame_id=frame_id,
                asset_id=asset_id,
                doc_id=doc_id,
                label=label,
                confidence=confidence,
                description=description,
                x1=bbox_values[0],
                y1=bbox_values[1],
                x2=bbox_values[2],
                y2=bbox_values[3],
            )
            detection_ids.append((detection_id, item))
            evidence_texts.append(
                {
                    "detection_id": detection_id,
                    "frame_id": frame_id,
                    "frame_index": frame_index,
                    "timestamp_seconds": timestamp_seconds,
                    "label": label,
                    "confidence": confidence,
                    "bbox": bbox_values,
                    "description": description,
                    "frame_description": frame_description,
                    "frame_path": str(frame_path),
                    "kind": "detection",
                    "text": (
                        f"Frame context: {frame_description} "
                        f"Detection detail: {description} Bounding box "
                        f"[{bbox_values[0]:.1f}, {bbox_values[1]:.1f}, "
                        f"{bbox_values[2]:.1f}, {bbox_values[3]:.1f}]."
                    ),
                }
            )
            detection_count += 1

        for (left_id, left), (right_id, right) in itertools.combinations(detection_ids, 2):
            left_bbox = left.get("bbox")
            right_bbox = right.get("bbox")
            if not isinstance(left_bbox, list) or not isinstance(right_bbox, list):
                continue
            relation_type = ""
            confidence = min(float(left.get("confidence") or 0), float(right.get("confidence") or 0))
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
    notes.extend(sorted(detector_notes))

    return {
        "asset_id": asset_id,
        "frames": frame_count,
        "detections": detection_count,
        "object_relationships": relation_count,
        "evidence_texts": evidence_texts,
        "notes": "; ".join(notes),
    }
