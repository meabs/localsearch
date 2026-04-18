from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from operation_lens_v2.ingestion.parsers.base import BaseParser, ParsedDocument, ParsedTextBlock


def _flatten(prefix: str, value: Any, rows: list[str]) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            _flatten(next_prefix, inner, rows)
        return
    if isinstance(value, list):
        for idx, inner in enumerate(value):
            _flatten(f"{prefix}[{idx}]", inner, rows)
        return
    rows.append(f"{prefix}: {value}")


class JsonParser(BaseParser):
    parser_name = "json"
    supported_extensions = (".json",)

    def parse(self, path: Path, *, document_id: str) -> ParsedDocument:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows: list[str] = []
        _flatten("", payload, rows)
        blocks = [
            ParsedTextBlock(text=row, page=idx, source_label=f"field {idx}")
            for idx, row in enumerate(rows, start=1)
            if row.strip()
        ]
        return ParsedDocument(
            document_id=document_id,
            source_type="json",
            source_metadata={"root_type": type(payload).__name__, "field_count": len(rows)},
            text_blocks=blocks,
            parser_name=self.parser_name,
        )
