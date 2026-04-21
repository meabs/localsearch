from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.quality import PageQuality, score_ocr_quality

logger = logging.getLogger(__name__)

_OCR_MIN_CHARS = 50
_GPS_IFD_TAG = 0x8825
_BLACK_FILL_VALUES = {"0", "0.0", 0, (0, 0, 0), (0.0, 0.0, 0.0)}


@dataclass(frozen=True, slots=True)
class OcrResult:
    text: str
    method: str
    confidence: float | None
    needs_review: bool


@dataclass(frozen=True, slots=True)
class PdfExtractionResult:
    pages: list[tuple[int, str]]
    ocr_used: bool
    page_quality: list[PageQuality]


def read_image_gps(image_path: Path) -> tuple[float, float] | None:
    """Extract decimal-degree latitude/longitude from EXIF GPS tags."""
    try:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS
    except ImportError:
        logger.warning("Pillow not available - skipping EXIF GPS read for %s", image_path)
        return None

    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            gps_ifd = exif.get_ifd(_GPS_IFD_TAG)
    except Exception as exc:
        logger.debug("EXIF read failed for %s: %s", image_path, exc)
        return None

    if not gps_ifd:
        return None

    gps = {GPSTAGS.get(tag, tag): value for tag, value in gps_ifd.items()}
    lat_dms = gps.get("GPSLatitude")
    lat_ref = gps.get("GPSLatitudeRef")
    lon_dms = gps.get("GPSLongitude")
    lon_ref = gps.get("GPSLongitudeRef")
    if not (lat_dms and lat_ref and lon_dms and lon_ref):
        return None

    try:
        lat = float(lat_dms[0]) + float(lat_dms[1]) / 60 + float(lat_dms[2]) / 3600
        lon = float(lon_dms[0]) + float(lon_dms[1]) / 60 + float(lon_dms[2]) / 3600
    except (TypeError, ValueError, IndexError, ZeroDivisionError) as exc:
        logger.debug("EXIF GPS parse failed for %s: %s", image_path, exc)
        return None

    if isinstance(lat_ref, bytes):
        lat_ref = lat_ref.decode("ascii", errors="ignore")
    if isinstance(lon_ref, bytes):
        lon_ref = lon_ref.decode("ascii", errors="ignore")
    if lat_ref.upper().startswith("S"):
        lat = -lat
    if lon_ref.upper().startswith("W"):
        lon = -lon

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        logger.debug("EXIF GPS out of range for %s: lat=%s lon=%s", image_path, lat, lon)
        return None

    return lat, lon


def _clean_text(text: str) -> str:
    """Strip null bytes and non-printable control characters."""
    text = text.replace("\x00", "")
    return re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def _tesseract_ocr_result(pil_image, *, doc_id: str, page_no: int, lang: str = "eng") -> OcrResult:
    try:
        import pytesseract  # type: ignore[import]

        try:
            pil_image = pil_image.convert("L")
        except Exception:
            pass

        text = _clean_text(pytesseract.image_to_string(pil_image, lang=lang))
        confidences: list[float] = []
        try:
            data = pytesseract.image_to_data(
                pil_image,
                lang=lang,
                output_type=pytesseract.Output.DICT,
            )
            confidences = [float(value) for value in data.get("conf", []) if str(value) != "-1"]
        except Exception as exc:
            logger.debug(
                "Tesseract confidence read failed for doc=%s page=%d: %s",
                doc_id,
                page_no,
                exc,
            )
        confidence, needs_review = score_ocr_quality(
            confidences,
            min_confidence=settings.ocr_low_confidence_threshold,
        )
        if len(text.strip()) < _OCR_MIN_CHARS:
            needs_review = True
        return OcrResult(
            text=text,
            method="tesseract",
            confidence=confidence,
            needs_review=needs_review,
        )
    except ImportError:
        logger.warning(
            "pytesseract / Pillow not available - OCR skipped for doc=%s page=%d",
            doc_id,
            page_no,
        )
        return OcrResult(
            text="",
            method="tesseract_unavailable",
            confidence=None,
            needs_review=True,
        )
    except Exception as exc:
        logger.warning("OCR failed for doc=%s page=%d: %s", doc_id, page_no, exc)
        return OcrResult(text="", method="tesseract_failed", confidence=None, needs_review=True)


def _trocr_image_result(pil_image, *, doc_id: str, page_no: int) -> OcrResult:
    """Try handwritten OCR via TrOCR when the optional runtime is installed."""
    try:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel  # type: ignore[import]
    except ImportError:
        return OcrResult(text="", method="trocr_unavailable", confidence=None, needs_review=True)

    try:
        processor = TrOCRProcessor.from_pretrained(settings.trocr_model)
        model = VisionEncoderDecoderModel.from_pretrained(settings.trocr_model)
        pixel_values = processor(images=pil_image.convert("RGB"), return_tensors="pt").pixel_values
        generated_ids = model.generate(pixel_values)
        text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
    except Exception as exc:
        logger.warning("TrOCR failed for doc=%s page=%d: %s", doc_id, page_no, exc)
        return OcrResult(text="", method="trocr_failed", confidence=None, needs_review=True)

    text = _clean_text(text)
    return OcrResult(
        text=text,
        method="trocr",
        confidence=None,
        needs_review=len(text.strip()) < _OCR_MIN_CHARS,
    )


def ocr_image_result(pil_image, *, doc_id: str, page_no: int, lang: str = "eng") -> OcrResult:
    """Attempt Tesseract first, then TrOCR for sparse likely-handwritten notes."""
    primary = _tesseract_ocr_result(pil_image, doc_id=doc_id, page_no=page_no, lang=lang)
    if primary.text.strip() and not primary.needs_review:
        return primary
    fallback = _trocr_image_result(pil_image, doc_id=doc_id, page_no=page_no)
    if fallback.text.strip():
        return fallback
    return primary


def ocr_image(pil_image, *, doc_id: str, page_no: int, lang: str = "eng") -> str:
    """Attempt OCR on a PIL image. Returns extracted text or ''."""
    return ocr_image_result(pil_image, doc_id=doc_id, page_no=page_no, lang=lang).text


def _ocr_page_result(page, doc_id: str, page_no: int) -> OcrResult:
    try:
        from PIL import Image  # type: ignore[import]

        pil_image = page.to_image(resolution=300).original
        if not isinstance(pil_image, Image.Image):
            try:
                pil_image = Image.fromarray(pil_image)
            except Exception:
                logger.warning(
                    "OCR render did not return a PIL-compatible image for doc=%s page=%d",
                    doc_id,
                    page_no,
                )
                return OcrResult("", "render_failed", None, True)
        return ocr_image_result(pil_image, doc_id=doc_id, page_no=page_no, lang="eng")
    except ImportError:
        logger.warning("Pillow not available - OCR skipped for doc=%s page=%d", doc_id, page_no)
        return OcrResult("", "pillow_unavailable", None, True)


def _ocr_page(page, doc_id: str, page_no: int) -> str:
    return _ocr_page_result(page, doc_id, page_no).text


def _is_black_rect(rect: dict) -> bool:
    fill = rect.get("non_stroking_color", rect.get("fill"))
    if fill in _BLACK_FILL_VALUES:
        return True
    if isinstance(fill, (list, tuple)):
        return all(isinstance(value, (int, float)) and float(value) <= 0.03 for value in fill)
    return False


def _redaction_count(page) -> int:
    rects = getattr(page, "rects", []) or []
    count = 0
    for rect in rects:
        try:
            width = abs(float(rect.get("x1", 0)) - float(rect.get("x0", 0)))
            height = abs(float(rect.get("y1", 0)) - float(rect.get("y0", 0)))
        except (AttributeError, TypeError, ValueError):
            continue
        if width >= 8 and height >= 8 and _is_black_rect(rect):
            count += 1
    return count


def extract_pdf_text(pdf_path: Path) -> tuple[list[tuple[int, str]], bool]:
    """Extract text from PDF, preserving the legacy return shape."""
    result = extract_pdf_text_detailed(pdf_path)
    return result.pages, result.ocr_used


def extract_pdf_text_detailed(pdf_path: Path) -> PdfExtractionResult:
    """Extract text from PDF plus OCR quality and black-box redaction markers."""
    doc_id = pdf_path.stem
    pages: list[tuple[int, str]] = []
    page_quality: list[PageQuality] = []
    ocr_used = False

    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            text = _clean_text(page.extract_text() or "")
            method = "text_layer"
            confidence: float | None = 1.0 if text.strip() else None
            needs_review = False

            if len(text.strip()) < _OCR_MIN_CHARS:
                logger.warning(
                    "Sparse text layer on doc=%s page=%d - falling back to OCR",
                    doc_id,
                    idx,
                )
                ocr_result = _ocr_page_result(page, doc_id, idx)
                method = ocr_result.method
                confidence = ocr_result.confidence
                needs_review = ocr_result.needs_review
                if ocr_result.text.strip():
                    text = ocr_result.text
                    ocr_used = True

            redactions = _redaction_count(page)
            if redactions:
                marker = f"[REDACTION: {redactions} black-box redaction(s) detected; evidence gap]"
                text = f"{text}\n\n{marker}".strip()

            pages.append((idx, text))
            page_quality.append(
                PageQuality(
                    page=idx,
                    extraction_method=method,
                    ocr_confidence=confidence,
                    needs_review=needs_review,
                    redaction_count=redactions,
                    evidence_gap=redactions > 0,
                    notes="black-box redaction detected" if redactions else "",
                )
            )

    return PdfExtractionResult(pages=pages, ocr_used=ocr_used, page_quality=page_quality)
