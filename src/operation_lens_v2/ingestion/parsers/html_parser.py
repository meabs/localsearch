from __future__ import annotations

import html
import re
from pathlib import Path

from operation_lens_v2.ingestion.parsers.base import BaseParser, ParsedDocument, ParsedTextBlock

_SCRIPT_RE = re.compile(r"<(script|style)\b.*?>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(raw_html: str) -> str:
    cleaned = _SCRIPT_RE.sub(" ", raw_html)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


class HtmlParser(BaseParser):
    parser_name = "html"
    supported_extensions = (".html", ".htm")

    def parse(self, path: Path, *, document_id: str) -> ParsedDocument:
        raw_html = path.read_text(encoding="utf-8")
        text = html_to_text(raw_html)
        return ParsedDocument(
            document_id=document_id,
            source_type="html",
            source_metadata={"title_hint": path.stem},
            text_blocks=[ParsedTextBlock(text=text, page=1, source_label="body")],
            parser_name=self.parser_name,
        )
