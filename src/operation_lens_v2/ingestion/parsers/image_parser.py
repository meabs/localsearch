from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from operation_lens_v2.ingestion.parsers.base import (
    BaseParser,
    DerivedFact,
    ParsedDocument,
    ParsedTextBlock,
    ParserWarning,
)


def _average_hash(image) -> str:
    grayscale = image.convert("L").resize((8, 8))
    pixels = list(grayscale.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel >= avg else "0" for pixel in pixels)
    return f"{int(bits, 2):016x}"


def _extract_exif(image) -> dict[str, Any]:
    exif = {}
    try:
        raw = image.getexif()
    except Exception:
        return exif
    if not raw:
        return exif
    for key, value in raw.items():
        label = str(key)
        if value in (None, ""):
            continue
        exif[label] = str(value)
    return exif


class ImageParser(BaseParser):
    parser_name = "image"
    supported_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff")
    supported_mime_prefixes = ("image/",)

    def parse(self, path: Path, *, document_id: str) -> ParsedDocument:
        warnings: list[ParserWarning] = []
        text_blocks: list[ParsedTextBlock] = []
        derived_facts: list[DerivedFact] = []
        perceptual_hash: str | None = None

        try:
            from PIL import Image  # type: ignore[import]
        except ImportError:
            warnings.append(
                ParserWarning(
                    warning_code="pillow_missing",
                    message="Pillow is not installed; image metadata extraction skipped.",
                )
            )
            return ParsedDocument(
                document_id=document_id,
                source_type="image",
                source_metadata={},
                text_blocks=[],
                parser_warnings=warnings,
                parser_name=self.parser_name,
            )

        with Image.open(path) as image:
            exif = _extract_exif(image)
            perceptual_hash = _average_hash(image)
            metadata_summary = [
                f"filename: {path.name}",
                f"format: {image.format or path.suffix.lstrip('.')}",
                f"dimensions: {image.width}x{image.height}",
                f"mode: {image.mode}",
                f"perceptual_hash: {perceptual_hash}",
            ]
            if exif:
                metadata_summary.extend(f"{key}: {value}" for key, value in exif.items())
            text_blocks.append(
                ParsedTextBlock(
                    text="\n".join(metadata_summary),
                    page=1,
                    source_label="image metadata",
                    provenance_type="metadata_exif",
                )
            )
            derived_facts.append(
                DerivedFact(
                    fact_type="image_perceptual_hash",
                    fact_value=perceptual_hash,
                    provenance_type="metadata_exif",
                    source_label="image metadata",
                )
            )
            for key, value in exif.items():
                derived_facts.append(
                    DerivedFact(
                        fact_type=f"exif_{key}",
                        fact_value=str(value),
                        provenance_type="metadata_exif",
                        source_label="image metadata",
                    )
                )

            try:
                import pytesseract  # type: ignore[import]

                ocr_text = pytesseract.image_to_string(image, lang="eng").strip()
            except ImportError:
                ocr_text = ""
                warnings.append(
                    ParserWarning(
                        warning_code="ocr_unavailable",
                        message="pytesseract is not installed; OCR was skipped for this image.",
                    )
                )
            except Exception as exc:
                ocr_text = ""
                warnings.append(
                    ParserWarning(
                        warning_code="ocr_failed",
                        message=f"OCR failed for this image: {exc}",
                    )
                )

        if ocr_text:
            text_blocks.append(
                ParsedTextBlock(
                    text=ocr_text,
                    page=2,
                    source_label="ocr text",
                    provenance_type="ocr_text",
                )
            )
            derived_facts.append(
                DerivedFact(
                    fact_type="ocr_text_excerpt",
                    fact_value=ocr_text[:500],
                    provenance_type="ocr_text",
                    source_label="ocr text",
                    confidence=0.8,
                )
            )

        return ParsedDocument(
            document_id=document_id,
            source_type="image",
            source_metadata={
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "exif_present": bool(exif),
            },
            text_blocks=text_blocks,
            derived_facts=derived_facts,
            parser_warnings=warnings,
            parser_name=self.parser_name,
            perceptual_hash=perceptual_hash,
        )
