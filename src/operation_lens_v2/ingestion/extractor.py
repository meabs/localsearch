from __future__ import annotations

import logging
import re
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)

_OCR_MIN_CHARS = 50
_GPS_IFD_TAG = 0x8825


def read_image_gps(image_path: Path) -> tuple[float, float] | None:
    """Extract decimal-degree latitude/longitude from EXIF GPS tags.

    Returns None when the file has no GPS IFD or the required sub-tags are
    absent. Never raises — corrupt EXIF logs at DEBUG and yields None so the
    ingestion pipeline can continue on OCR alone.
    """
    try:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS
    except ImportError:
        logger.warning("Pillow not available — skipping EXIF GPS read for %s", image_path)
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
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text


def ocr_image(pil_image, *, doc_id: str, page_no: int, lang: str = "eng") -> str:
    """Attempt Tesseract OCR on a PIL image. Returns extracted text or ''."""
    try:
        import pytesseract  # type: ignore[import]

        # Deskew via pillow-based heuristic if available.
        try:
            from PIL import ImageOps  # noqa: F401 — imported for its side-effects on Image

            pil_image = pil_image.convert("L")  # greyscale improves Tesseract accuracy
        except Exception:
            pass

        text = pytesseract.image_to_string(pil_image, lang=lang)
        return _clean_text(text)
    except ImportError:
        logger.warning(
            "pytesseract / Pillow not available — OCR skipped for doc=%s page=%d",
            doc_id,
            page_no,
        )
        return ""
    except Exception as exc:
        logger.warning("OCR failed for doc=%s page=%d: %s", doc_id, page_no, exc)
        return ""


def _ocr_page(page, doc_id: str, page_no: int) -> str:
    """Attempt Tesseract OCR on a pdfplumber page. Returns extracted text or ''."""
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
                return ""
        return ocr_image(pil_image, doc_id=doc_id, page_no=page_no, lang="eng")
    except ImportError:
        logger.warning(
            "Pillow not available — OCR skipped for doc=%s page=%d",
            doc_id,
            page_no,
        )
        return ""


def extract_pdf_text(pdf_path: Path) -> tuple[list[tuple[int, str]], bool]:
    """Extract text from PDF. Falls back to Tesseract OCR if text layer is absent or thin.

    Returns (pages, ocr_used) where pages is a list of (1-indexed page_no, text) tuples.
    """
    doc_id = pdf_path.stem
    pages: list[tuple[int, str]] = []
    ocr_used = False

    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            text = _clean_text(text)

            if len(text.strip()) < _OCR_MIN_CHARS:
                logger.warning(
                    "Sparse text layer on doc=%s page=%d — falling back to Tesseract OCR",
                    doc_id,
                    idx,
                )
                ocr_text = _ocr_page(page, doc_id, idx)
                if ocr_text.strip():
                    text = ocr_text
                    ocr_used = True

            pages.append((idx, text))

    return pages, ocr_used
