from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PageQuality:
    page: int
    extraction_method: str
    ocr_confidence: float | None = None
    needs_review: bool = False
    redaction_count: int = 0
    evidence_gap: bool = False
    notes: str = ""


def score_ocr_quality(
    confidences: list[float],
    *,
    min_confidence: float,
) -> tuple[float | None, bool]:
    valid = [value for value in confidences if 0 <= value <= 100]
    if not valid:
        return None, True
    score = sum(valid) / len(valid) / 100.0
    return score, score < min_confidence


REDACTION_MARKER_RE = re.compile(
    r"(\[redacted\]|\bredacted\b|█{2,}|■{2,}|_{6,}|-{6,})",
    re.IGNORECASE,
)


def count_redaction_markers(text: str) -> int:
    """Count textual markers that indicate black-box or placeholder redactions."""
    return len(REDACTION_MARKER_RE.findall(text or ""))


def page_quality_from_text(
    *,
    page: int,
    text: str,
    extraction_method: str,
    ocr_confidence: float | None = None,
    min_confidence: float = 0.6,
) -> PageQuality:
    redactions = count_redaction_markers(text)
    needs_review = bool(ocr_confidence is not None and ocr_confidence < min_confidence)
    return PageQuality(
        page=page,
        extraction_method=extraction_method,
        ocr_confidence=ocr_confidence,
        needs_review=needs_review,
        redaction_count=redactions,
        evidence_gap=redactions > 0,
        notes="redaction markers detected" if redactions else "",
    )
