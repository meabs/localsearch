from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import connect

router = APIRouter(prefix="/timeline", tags=["timeline"])

# Regex to find date/time references in chunk text.
_DATE_RE = re.compile(
    r"\b"
    r"(?:"
    r"(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)"
    r"[,\s]+\d{1,2}(?:st|nd|rd|th)?"
    r"(?:[,\s]+\d{2,4})?)"
    r"|"
    r"(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?[,\s]+)?"  # optional day-of-week
    r"(?:\d{1,2}(?:st|nd|rd|th)?[,\s]+)?"  # optional day number
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)"
    r"(?:[,\s]+\d{2,4})?"  # optional year
    r"|"
    r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"  # DD/MM/YYYY or DD-MM-YYYY
    r"|"
    r"\d{4}[/\-]\d{2}[/\-]\d{2}"  # YYYY-MM-DD
    r")"
    r"(?:[T,\s]+(?:\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?))?"  # optional time
    r"\b",
    re.IGNORECASE,
)

_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\b")

_DATE_PARSE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d %B %Y %H:%M:%S",
    "%d %B %Y %H:%M",
    "%d %B %Y",
    "%d %b %Y %H:%M:%S",
    "%d %b %Y %H:%M",
    "%d %b %Y",
    "%B %d, %Y %H:%M:%S",
    "%B %d, %Y %H:%M",
    "%B %d, %Y",
    "%b %d, %Y %H:%M:%S",
    "%b %d, %Y %H:%M",
    "%b %d, %Y",
)

_WEEKDAY_PREFIX_RE = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?[,\s]+",
    re.IGNORECASE,
)
_ORDINAL_SUFFIX_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)
_ISO_Z_SUFFIX_RE = re.compile(r"Z$", re.IGNORECASE)


def _resolve_entity_ids(con, entity_filter: str) -> list[str]:
    entity_norm = entity_filter.strip().lower()
    if not entity_norm:
        return []

    entity_like = f"%{entity_norm}%"
    rows = con.execute(
        """
        SELECT DISTINCT e.entity_id
        FROM entities e
        LEFT JOIN entity_aliases ea ON ea.entity_id = e.entity_id
        WHERE lower(e.canonical_name) = ?
           OR lower(ea.alias_text) = ?
           OR lower(e.canonical_name) LIKE ?
           OR lower(ea.alias_text) LIKE ?
        ORDER BY e.entity_id
        LIMIT 25
        """,
        [entity_norm, entity_norm, entity_like, entity_like],
    ).fetchall()
    return [row[0] for row in rows]


def _normalize_date_text(date_text: str) -> str:
    normalized = date_text.strip()
    normalized = _WEEKDAY_PREFIX_RE.sub("", normalized)
    normalized = re.sub(r"\bat\b", " ", normalized, count=1, flags=re.IGNORECASE)
    normalized = _ORDINAL_SUFFIX_RE.sub(r"\1", normalized)
    normalized = _ISO_Z_SUFFIX_RE.sub("+00:00", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip(" ,")


def _parse_date_value(date_text: str, chunk_text: str, match_end: int) -> datetime | None:
    """Parse a chunk date into a sortable datetime when possible."""
    candidates = [_normalize_date_text(date_text)]

    tail = chunk_text[match_end : match_end + 32]
    time_match = _TIME_RE.search(tail)
    if time_match and time_match.start() <= 8:
        candidates.insert(0, f"{candidates[0]} {time_match.group(1)}")

    for candidate in candidates:
        iso_candidate = candidate.replace("T", " ")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(iso_candidate)
            except ValueError:
                parsed = None
        if parsed is not None:
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC).replace(tzinfo=None)
            return parsed

        for fmt in _DATE_PARSE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue

    return None


def _extract_events(
    con,
    doc_ids: list[str] | None,
    entity_filter: str | None,
    limit: int,
) -> list[dict[str, object]]:
    """Pull chunks that contain date/time references and build a timeline."""
    query = [
        "SELECT DISTINCT c.chunk_id, c.doc_id, c.page, c.text, d.filename",
        "FROM chunks c",
        "JOIN documents d ON d.doc_id = c.doc_id",
    ]
    params: list[object] = []
    where_clauses: list[str] = []

    if entity_filter:
        entity_ids = _resolve_entity_ids(con, entity_filter)
        if entity_ids:
            placeholders = ",".join(["?"] * len(entity_ids))
            query.append("JOIN entity_aliases ea ON ea.source_chunk = c.chunk_id")
            where_clauses.append(f"ea.entity_id IN ({placeholders})")
            params.extend(entity_ids)
        else:
            where_clauses.append("lower(c.text) LIKE ?")
            params.append(f"%{entity_filter.strip().lower()}%")

    if doc_ids:
        placeholders = ",".join(["?"] * len(doc_ids))
        where_clauses.append(f"c.doc_id IN ({placeholders})")
        params.extend(doc_ids)

    if where_clauses:
        query.append("WHERE " + " AND ".join(where_clauses))

    query.append("ORDER BY c.doc_id, c.page, c.chunk_index")
    query.append("LIMIT ?")
    params.append(limit * 3)

    rows = con.execute("\n".join(query), params).fetchall()

    events: list[tuple[tuple[int, datetime, str, int, str], dict[str, object]]] = []
    for chunk_id, doc_id, page, text, filename in rows:
        dates = _DATE_RE.findall(text)
        if not dates:
            continue
        # Use the first detected date as the display value and best-effort sort key.
        match = _DATE_RE.search(text)
        if not match:
            continue
        date_str = dates[0].strip()
        sort_key_dt = _parse_date_value(date_str, text, match.end())
        if sort_key_dt is None:
            sort_key = (1, datetime.max, doc_id, page, chunk_id)
        else:
            sort_key = (0, sort_key_dt, doc_id, page, chunk_id)
        # Build a short excerpt around the date occurrence.
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 160)
        excerpt = text[start:end].strip()
        events.append(
            (
                sort_key,
                {
                    "date": date_str,
                    "doc_id": doc_id,
                    "filename": filename,
                    "page": page,
                    "chunk_id": chunk_id,
                    "excerpt": excerpt,
                },
            )
        )

    events.sort(key=lambda item: item[0])
    return [event for _, event in events[:limit]]


@router.get("")
def timeline(
    doc_ids: str | None = None,
    entity: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    """Return chronologically ordered events extracted from chunk text.

    Query params:
      doc_ids  — comma-separated list of doc IDs to restrict to (optional)
      entity   — filter to chunks mentioning this entity name (optional)
      limit    — max events to return (default 50)
    """
    con = connect(settings.duckdb_path)
    doc_id_list: list[str] | None = None
    if doc_ids:
        doc_id_list = [d.strip() for d in doc_ids.split(",") if d.strip()]

    events = _extract_events(con, doc_id_list, entity, min(limit, 200))
    return {
        "count": len(events),
        "entity_filter": entity,
        "events": events,
    }
