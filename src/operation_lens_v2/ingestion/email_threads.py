from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import chunker, duck_store, normaliser
from operation_lens_v2.models import Chunk, RelationshipCandidate

logger = logging.getLogger(__name__)

_EMAIL_ANGLE_RE = re.compile(r"<([^<>@\s]+@[^<>@\s]+)>")
_EMAIL_BRACKET_RE = re.compile(r"\[([^][]+@[^][]+)\]")
_WHITESPACE_RE = re.compile(r"\s+")


def _format_message_text(
    *,
    thread_id: str,
    source_file: str,
    subject: str,
    sender: str,
    recipients: list[str],
    timestamp: str,
    body: str,
) -> str:
    recipient_text = ", ".join(item for item in recipients if item) or "(none listed)"
    return "\n".join(
        [
            f"Thread ID: {thread_id}",
            f"Source File: {source_file or '(unknown)'}",
            f"Subject: {subject or '(no subject)'}",
            f"From: {sender or '(unknown sender)'}",
            f"To: {recipient_text}",
            f"Sent: {timestamp or '(unknown timestamp)'}",
            "",
            body or "",
        ]
    ).strip()


def _parse_messages(raw_messages: str) -> list[dict[str, object]]:
    if not raw_messages:
        return []
    try:
        loaded = json.loads(raw_messages)
    except json.JSONDecodeError as exc:
        logger.warning("Skipping unparsable message payload: %s", exc)
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def _extract_email(value: str) -> str | None:
    if not value:
        return None
    for pattern in (_EMAIL_ANGLE_RE, _EMAIL_BRACKET_RE):
        match = pattern.search(value)
        if match:
            return match.group(1).strip().lower()
    return None


def _clean_person_label(value: str) -> str:
    if not value:
        return ""
    cleaned = _EMAIL_ANGLE_RE.sub("", value)
    cleaned = _EMAIL_BRACKET_RE.sub("", cleaned)
    cleaned = cleaned.replace('"', " ")
    cleaned = re.sub(r"[<>\[\]()]", " ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(" ,;:-")
    return cleaned


def _canonical_person_label(value: str) -> str:
    cleaned = _clean_person_label(value)
    if cleaned:
        return cleaned
    return _extract_email(value) or value.strip()


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None

    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        pass

    for pattern in (
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _message_evidence_excerpt(
    *,
    sender: str,
    recipient: str,
    subject: str,
    timestamp: str,
    body: str,
) -> str:
    preview = (body or "").strip().replace("\n", " ")
    preview = _WHITESPACE_RE.sub(" ", preview)
    if len(preview) > 220:
        preview = preview[:217].rstrip() + "..."
    return (
        f"Email from {sender or '(unknown sender)'} to {recipient or '(unknown recipient)'} "
        f"about {subject or '(no subject)'} at {timestamp or '(unknown time)'}: {preview}"
    )


def _build_chunks(
    *,
    doc_id: str,
    thread_id: str,
    source_file: str,
    subject: str,
    messages: list[dict[str, object]],
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for index, message in enumerate(messages):
        sender = str(message.get("sender") or "")
        recipients = [
            str(item).strip()
            for item in (message.get("recipients") or [])
            if str(item).strip()
        ]
        timestamp = str(message.get("timestamp") or "")
        body = str(message.get("body") or "")
        text = _format_message_text(
            thread_id=thread_id,
            source_file=source_file,
            subject=subject,
            sender=sender,
            recipients=recipients,
            timestamp=timestamp,
            body=body,
        )
        chunks.append(
            Chunk(
                chunk_id=str(uuid4()),
                doc_id=doc_id,
                page=index + 1,
                chunk_index=index,
                text=text,
                token_count=chunker.count_tokens(text),
            )
        )
    return chunks


def _resolve_person_entity(
    *,
    con,
    raw_value: str,
    doc_id: str,
    chunk_id: str,
) -> str | None:
    display = _canonical_person_label(raw_value)
    if not display:
        return None
    email = _extract_email(raw_value)
    if email:
        existing = con.execute(
            """
            SELECT entity_id
            FROM entity_aliases
            WHERE lower(alias_text) = lower(?)
            ORDER BY alias_id
            LIMIT 1
            """,
            [email],
        ).fetchone()
        if existing:
            entity_id = existing[0]
            raw_text = raw_value.strip()
            if raw_text and raw_text.lower() != email:
                duck_store.register_alias(
                    con,
                    entity_id=entity_id,
                    alias_text=raw_text,
                    source_doc=doc_id,
                    source_chunk=chunk_id,
                )
            if display.lower() != email:
                duck_store.register_alias(
                    con,
                    entity_id=entity_id,
                    alias_text=display,
                    source_doc=doc_id,
                    source_chunk=chunk_id,
                )
            return entity_id
    entity_id = normaliser.resolve_entity(
        surface=display,
        entity_type="PERSON",
        con=con,
        source_doc=doc_id,
        source_chunk=chunk_id,
        threshold=settings.alias_threshold,
    )
    if email:
        duck_store.register_alias(
            con,
            entity_id=entity_id,
            alias_text=email,
            source_doc=doc_id,
            source_chunk=chunk_id,
        )
    raw_text = raw_value.strip()
    if raw_text and raw_text != display and raw_text.lower() != email:
        duck_store.register_alias(
            con,
            entity_id=entity_id,
            alias_text=raw_text,
            source_doc=doc_id,
            source_chunk=chunk_id,
        )
    return entity_id


async def ingest_email_thread_parquet(
    parquet_path: Path,
    *,
    db_path: str | None = None,
    case_ref: str = "UNASSIGNED",
    case_name: str | None = None,
    force: bool = False,
) -> dict[str, int | str]:
    db_path = db_path or settings.duckdb_path
    con = duck_store.init_db(db_path)
    resolved_case_name = case_name or case_ref.replace("_", " ").title()
    case_id = duck_store.create_case(con, case_ref=case_ref, case_name=resolved_case_name)

    event_id = str(uuid4())
    started_at = datetime.now(timezone.utc)
    started_perf = perf_counter()

    rows = con.execute(
        """
        SELECT thread_id, source_file, subject, messages, message_count
        FROM read_parquet(?)
        ORDER BY source_file, thread_id
        """,
        [str(parquet_path)],
    ).fetchall()

    ingested_threads = 0
    skipped_threads = 0
    chunk_count = 0
    relationship_count = 0

    for thread_id, source_file, subject, raw_messages, message_count in rows:
        virtual_path = f"{parquet_path.resolve()}#thread={thread_id}"
        if not force:
            existing = con.execute(
                "SELECT doc_id FROM documents WHERE filepath = ? AND case_id = ?",
                [virtual_path, case_id],
            ).fetchone()
            if existing:
                skipped_threads += 1
                continue

        doc_id = str(uuid4())
        parsed_messages = _parse_messages(str(raw_messages or ""))
        chunks = _build_chunks(
            doc_id=doc_id,
            thread_id=str(thread_id or ""),
            source_file=str(source_file or ""),
            subject=str(subject or ""),
            messages=parsed_messages,
        )
        page_count = len(chunks) or int(message_count or 0) or 1

        filename_subject = str(subject or "").strip() or "no subject"
        filename = f"{Path(str(source_file or parquet_path.name)).name} [{filename_subject}]"

        duck_store.upsert_document(
            con,
            doc_id=doc_id,
            filename=filename,
            filepath=virtual_path,
            page_count=page_count,
            ocr_used=False,
            case_id=case_id,
        )
        duck_store.insert_chunks(con, chunks)

        chunk_by_page = {chunk.page: chunk for chunk in chunks}
        for index, message in enumerate(parsed_messages, start=1):
            sender = str(message.get("sender") or "").strip()
            recipients = [
                str(item).strip()
                for item in (message.get("recipients") or [])
                if str(item).strip()
            ]
            body = str(message.get("body") or "")
            sent_at = str(message.get("timestamp") or "")
            event_time = _parse_timestamp(sent_at)
            chunk = chunk_by_page.get(index)
            if chunk is None:
                continue
            sender_entity_id = _resolve_person_entity(
                con=con,
                raw_value=sender,
                doc_id=doc_id,
                chunk_id=chunk.chunk_id,
            )
            if sender_entity_id is None:
                continue
            for recipient in recipients:
                recipient_entity_id = _resolve_person_entity(
                    con=con,
                    raw_value=recipient,
                    doc_id=doc_id,
                    chunk_id=chunk.chunk_id,
                )
                if recipient_entity_id is None or recipient_entity_id == sender_entity_id:
                    continue
                relation = RelationshipCandidate(
                    source=_canonical_person_label(sender),
                    target=_canonical_person_label(recipient),
                    relation_type="EMAILED",
                    span_text=_message_evidence_excerpt(
                        sender=sender,
                        recipient=recipient,
                        subject=str(subject or ""),
                        timestamp=sent_at,
                        body=body,
                    ),
                    span_start=0,
                    span_end=0,
                    confidence=1.0,
                    extraction_method="pattern",
                    metadata={},
                )
                relation.span_end = len(relation.span_text)
                rel_id = duck_store.create_relationship(
                    con,
                    source_entity=sender_entity_id,
                    target_entity=recipient_entity_id,
                    relation_type=relation.relation_type,
                    confidence=relation.confidence,
                    event_time=event_time,
                )
                duck_store.insert_relationship_evidence(
                    con,
                    rel_id=rel_id,
                    chunk_id=chunk.chunk_id,
                    doc_id=doc_id,
                    page=chunk.page,
                    rel=relation,
                    event_time=event_time,
                )
                relationship_count += 1

        ingested_threads += 1
        chunk_count += len(chunks)

    duration_ms = int((perf_counter() - started_perf) * 1000)
    status = "success" if ingested_threads else "skipped"
    duck_store.record_ingestion_event(
        con,
        event_id=event_id,
        case_id=case_id,
        doc_id=None,
        source_path=str(parquet_path),
        source_type="email_threads_parquet",
        status=status,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        duration_ms=duration_ms,
        pages=None,
        chunks=chunk_count,
        relationships_new=relationship_count,
        ocr_used=False,
        notes=json.dumps(
            {
                "threads": ingested_threads,
                "skipped_threads": skipped_threads,
            }
        ),
    )

    return {
        "case_ref": case_ref,
        "threads": ingested_threads,
        "chunks": chunk_count,
        "relationships": relationship_count,
        "skipped": skipped_threads,
        "event_id": event_id,
    }
