from __future__ import annotations

import csv
from pathlib import Path

from operation_lens_v2.ingestion.parsers.base import BaseParser, ParsedDocument, ParsedTextBlock


class CsvParser(BaseParser):
    parser_name = "csv"
    supported_extensions = (".csv",)

    def parse(self, path: Path, *, document_id: str) -> ParsedDocument:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            blocks: list[ParsedTextBlock] = []
            headers = list(reader.fieldnames or [])
            row_count = 0
            for idx, row in enumerate(reader, start=1):
                row_count += 1
                row_text = " | ".join(
                    f"{key}: {value}"
                    for key, value in row.items()
                    if key and value not in (None, "")
                )
                if not row_text.strip():
                    continue
                blocks.append(
                    ParsedTextBlock(
                        text=row_text,
                        page=idx,
                        source_label=f"row {idx}",
                        metadata={"row_number": idx},
                    )
                )
        return ParsedDocument(
            document_id=document_id,
            source_type="csv",
            source_metadata={"row_count": row_count, "columns": headers},
            text_blocks=blocks,
            parser_name=self.parser_name,
        )
