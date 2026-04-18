from __future__ import annotations

from pathlib import Path

from operation_lens_v2.ingestion.extractor import extract_pdf_text
from operation_lens_v2.ingestion.parsers.base import BaseParser, ParsedDocument, ParsedTextBlock


class PdfParser(BaseParser):
    parser_name = "pdf"
    supported_extensions = (".pdf",)

    def parse(self, path: Path, *, document_id: str) -> ParsedDocument:
        pages, ocr_used = extract_pdf_text(path)
        return ParsedDocument(
            document_id=document_id,
            source_type="pdf",
            source_metadata={"ocr_used": ocr_used, "page_count": len(pages)},
            text_blocks=[
                ParsedTextBlock(
                    text=text,
                    page=page_no,
                    source_label=f"page {page_no}",
                    provenance_type="ocr_text" if ocr_used else "native_text",
                )
                for page_no, text in pages
                if text.strip()
            ],
            parser_name=self.parser_name,
        )
