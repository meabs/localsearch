from __future__ import annotations

import csv
from pathlib import Path

from operation_lens_v2.models import PageRecord


def _format_row(row: dict[str, str | None], fieldnames: list[str]) -> str:
    parts: list[str] = []
    for field in fieldnames:
        value = row.get(field)
        parts.append(f"{field}: {'' if value is None else value}")
    return " | ".join(parts)


def extract_csv(path: Path) -> list[PageRecord]:
    """Stream a CSV file row-by-row and map each row to a page record."""
    pages: list[PageRecord] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = [name for name in (reader.fieldnames or []) if name is not None]
        for page_no, row in enumerate(reader, start=1):
            pages.append(PageRecord(page=page_no, text=_format_row(row, fieldnames)))
    return pages
