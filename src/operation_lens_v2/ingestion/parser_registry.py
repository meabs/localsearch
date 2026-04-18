from __future__ import annotations

import mimetypes
from pathlib import Path

from operation_lens_v2.ingestion.parsers.base import BaseParser
from operation_lens_v2.ingestion.parsers.csv_parser import CsvParser
from operation_lens_v2.ingestion.parsers.eml_parser import EmlParser
from operation_lens_v2.ingestion.parsers.html_parser import HtmlParser
from operation_lens_v2.ingestion.parsers.image_parser import ImageParser
from operation_lens_v2.ingestion.parsers.json_parser import JsonParser
from operation_lens_v2.ingestion.parsers.pdf_parser import PdfParser
from operation_lens_v2.ingestion.parsers.txt_parser import TxtParser


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: list[BaseParser] = []

    def register(self, parser: BaseParser) -> None:
        self._parsers.append(parser)

    def get_parser(self, path: Path, mime_type: str | None = None) -> BaseParser | None:
        guessed = mime_type or mimetypes.guess_type(path.name)[0]
        for parser in self._parsers:
            if parser.can_parse(path, guessed):
                return parser
        return None

    def supported_extensions(self) -> list[str]:
        return sorted({ext for parser in self._parsers for ext in parser.supported_extensions})


def build_default_registry() -> ParserRegistry:
    registry = ParserRegistry()
    for parser in (
        PdfParser(),
        TxtParser(),
        CsvParser(),
        JsonParser(),
        EmlParser(),
        HtmlParser(),
        ImageParser(),
    ):
        registry.register(parser)
    return registry


registry = build_default_registry()
