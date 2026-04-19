from __future__ import annotations

import logging
from threading import Lock
from typing import Any

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.entity_schema import get_schema
from operation_lens_v2.models import ExtractedEntity

logger = logging.getLogger(__name__)


def _normalize_label_key(label: str) -> str:
    return " ".join(str(label).lower().split())


def _build_label_map() -> dict[str, str]:
    """Map every GLiNER prompt (from the schema) to its canonical type."""
    m: dict[str, str] = {}
    for prompt, canonical in get_schema().gliner_label_specs():
        key = _normalize_label_key(prompt)
        if key in m and m[key] != canonical:
            logger.warning(
                "GLiNER label map: prompt %r maps to %r and %r — using %r",
                key,
                m[key],
                canonical,
                canonical,
            )
        m[key] = canonical
    return m


class _GLiNERLoader:
    """Thread-safe lazy loader for the GLiNER model.

    Replaces a module-level `global _model`. The loader owns its own state;
    callers receive the model via `load()` / `get()` and never touch globals.
    `MemoryError` propagates — we never swallow OOM.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._lock = Lock()

    def load(self) -> Any:
        with self._lock:
            if self._model is None:
                from gliner import GLiNER  # type: ignore[import]

                logger.info("Loading GLiNER model: %s", settings.gliner_model)
                self._model = GLiNER.from_pretrained(settings.gliner_model)
                logger.info("GLiNER model loaded.")
            return self._model

    def get(self) -> Any:
        if self._model is None:
            return self.load()
        return self._model

    def reset(self) -> None:
        with self._lock:
            self._model = None


_loader = _GLiNERLoader()


def load_gliner_model() -> Any:
    """Load GLiNER model (thread-safe, cached). Returns the model instance."""
    return _loader.load()


def _get_model() -> Any:
    return _loader.get()


def _merge_overlapping(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    """Merge overlapping spans, keeping the highest-confidence match."""
    if not entities:
        return entities
    entities = sorted(entities, key=lambda e: (e.start, -e.end))
    merged: list[ExtractedEntity] = [entities[0]]
    for ent in entities[1:]:
        prev = merged[-1]
        if ent.start < prev.end:
            if getattr(ent, "confidence", 1.0) > getattr(prev, "confidence", 1.0):
                merged[-1] = ent
        else:
            merged.append(ent)
    return merged


def extract_general_entities(text: str) -> list[ExtractedEntity]:
    """Extract general named entities using GLiNER, with labels sourced from the schema."""
    try:
        model = _get_model()
    except (ImportError, ModuleNotFoundError) as exc:
        logger.warning("GLiNER not installed (%s) — skipping general NER.", exc)
        return []
    except (OSError, RuntimeError) as exc:
        # Model-weight download/load failure — degrade for this chunk.
        # MemoryError is deliberately NOT caught here: spec requires OOM to surface.
        logger.warning("GLiNER load failed (%s) — skipping general NER for this chunk.", exc)
        return []

    label_map = _build_label_map()
    if not label_map:
        logger.warning("Entity schema has no GLiNER prompts — skipping.")
        return []

    labels = sorted(label_map.keys())
    try:
        raw = model.predict_entities(text, labels, threshold=settings.gliner_threshold)
    except (ValueError, RuntimeError) as exc:
        # Runtime includes torch inference failures. MemoryError still propagates.
        logger.warning("GLiNER prediction failed: %s", exc)
        return []

    entities: list[ExtractedEntity] = []
    for item in raw:
        label = _normalize_label_key(str(item.get("label", "")))
        entity_type = label_map.get(label)
        if not entity_type:
            continue
        entities.append(
            ExtractedEntity(
                text=item["text"],
                entity_type=entity_type,
                start=item["start"],
                end=item["end"],
                confidence=float(item.get("score", 1.0)),
            )
        )

    return _merge_overlapping(entities)
