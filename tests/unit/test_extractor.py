"""Tests for ingestion/extractor.py — PDF text extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_fake_pdf(pages_text: list[str]) -> MagicMock:
    """Return a mock pdfplumber PDF with the given page texts."""
    mock_pages = []
    for text in pages_text:
        page = MagicMock()
        page.extract_text.return_value = text
        page.to_image.return_value = MagicMock(original=MagicMock())
        mock_pages.append(page)
    pdf = MagicMock()
    pdf.__enter__ = lambda s: s
    pdf.__exit__ = MagicMock(return_value=False)
    pdf.pages = mock_pages
    return pdf


def test_extract_returns_one_tuple_per_page():
    from operation_lens_v2.ingestion.extractor import extract_pdf_text

    fake_pdf = _make_fake_pdf(["Page one text.", "Page two text."])
    with patch("operation_lens_v2.ingestion.extractor.pdfplumber.open", return_value=fake_pdf):
        pages, ocr_used = extract_pdf_text(Path("dummy.pdf"))

    assert len(pages) == 2
    assert pages[0] == (1, "Page one text.")
    assert pages[1] == (2, "Page two text.")
    assert ocr_used is False


def test_extract_page_numbers_are_one_indexed():
    from operation_lens_v2.ingestion.extractor import extract_pdf_text

    fake_pdf = _make_fake_pdf(["A", "B", "C"])
    with patch("operation_lens_v2.ingestion.extractor.pdfplumber.open", return_value=fake_pdf):
        pages, _ = extract_pdf_text(Path("dummy.pdf"))

    assert [p for p, _ in pages] == [1, 2, 3]


def test_extract_strips_null_bytes():
    from operation_lens_v2.ingestion.extractor import extract_pdf_text

    fake_pdf = _make_fake_pdf(["Hello\x00World", "Normal text here."])
    with patch("operation_lens_v2.ingestion.extractor.pdfplumber.open", return_value=fake_pdf):
        pages, _ = extract_pdf_text(Path("dummy.pdf"))

    assert "\x00" not in pages[0][1]
    assert "Hello" in pages[0][1]


def test_ocr_fallback_triggered_on_thin_text():
    """When extract_text returns fewer than 50 chars, OCR should be attempted."""
    from operation_lens_v2.ingestion.extractor import extract_pdf_text

    fake_pdf = _make_fake_pdf(["short"])  # < 50 chars triggers OCR
    ocr_result = "OCR extracted text from scanned page with enough content to matter."

    with (
        patch("operation_lens_v2.ingestion.extractor.pdfplumber.open", return_value=fake_pdf),
        patch(
            "operation_lens_v2.ingestion.extractor._ocr_page_result",
            return_value=_ocr_result(ocr_result),
        ),
    ):
        pages, ocr_used = extract_pdf_text(Path("scanned.pdf"))

    assert ocr_used is True
    assert pages[0][1] == ocr_result


def _ocr_result(text: str):
    from operation_lens_v2.ingestion.extractor import OcrResult

    return OcrResult(text=text, method="tesseract", confidence=0.9, needs_review=False)


def test_clean_text_removes_control_chars():
    from operation_lens_v2.ingestion.extractor import _clean_text

    dirty = "Hello\x01\x07World\x1fEnd"
    cleaned = _clean_text(dirty)
    assert cleaned == "HelloWorldEnd"


def test_extract_detailed_marks_black_box_redactions():
    from operation_lens_v2.ingestion.extractor import extract_pdf_text_detailed

    fake_pdf = _make_fake_pdf(["This page has readable text long enough to avoid OCR fallback."])
    fake_pdf.pages[0].rects = [
        {"x0": 10, "x1": 80, "y0": 10, "y1": 30, "non_stroking_color": (0, 0, 0)}
    ]
    with patch("operation_lens_v2.ingestion.extractor.pdfplumber.open", return_value=fake_pdf):
        result = extract_pdf_text_detailed(Path("redacted.pdf"))

    assert "[REDACTION: 1 black-box redaction" in result.pages[0][1]
    assert result.page_quality[0].redaction_count == 1
    assert result.page_quality[0].evidence_gap is True


def test_ocr_result_falls_back_to_trocr_for_sparse_tesseract():
    from operation_lens_v2.ingestion.extractor import OcrResult, ocr_image_result

    with (
        patch(
            "operation_lens_v2.ingestion.extractor._tesseract_ocr_result",
            return_value=OcrResult("smudged", "tesseract", 0.2, True),
        ),
        patch(
            "operation_lens_v2.ingestion.extractor._trocr_image_result",
            return_value=OcrResult("handwritten appointment note", "trocr", None, False),
        ),
    ):
        result = ocr_image_result(MagicMock(), doc_id="doc", page_no=1)

    assert result.method == "trocr"
    assert result.text == "handwritten appointment note"
