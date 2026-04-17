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
        patch("operation_lens_v2.ingestion.extractor._ocr_page", return_value=ocr_result),
    ):
        pages, ocr_used = extract_pdf_text(Path("scanned.pdf"))

    assert ocr_used is True
    assert pages[0][1] == ocr_result


def test_clean_text_removes_control_chars():
    from operation_lens_v2.ingestion.extractor import _clean_text

    dirty = "Hello\x01\x07World\x1fEnd"
    cleaned = _clean_text(dirty)
    assert cleaned == "HelloWorldEnd"
