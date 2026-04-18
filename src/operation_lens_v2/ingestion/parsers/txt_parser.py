from __future__ import annotations

from pathlib import Path

from operation_lens_v2.ingestion.parsers.base import BaseParser, ParsedDocument, ParsedTextBlock, ParserWarning


class TxtParser(BaseParser):
    parser_name = "txt"
    supported_extensions = (".txt", ".log", ".md")

    def parse(self, path: Path, *, document_id: str) -> ParsedDocument:
        warnings: list[ParserWarning] = []
        try:
            text = path.read_text(encoding="utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")
            encoding = "latin-1"
            warnings.append(
                ParserWarning(
                    warning_code="encoding_fallback",
                    message="Fell back to latin-1 decoding for this text file.",
                )
            )
        return ParsedDocument(
            document_id=document_id,
            source_type="txt",
            source_metadata={"encoding": encoding},
            text_blocks=[ParsedTextBlock(text=text, page=1, source_label="body")],
            parser_warnings=warnings,
            parser_name=self.parser_name,
        )
