from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path

import html2text

from operation_lens_v2.ingestion.email_threads import AttachmentRef, EmailMessage, ThreadDocument


def parse_eml(path: Path) -> ThreadDocument:
    """Parse a .eml file into a shared ThreadDocument model."""
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())

    subject = str(message.get("subject") or "").strip()
    sender = str(message.get("from") or "").strip()
    recipients = [
        str(value).strip()
        for value in [
            message.get("to") or "",
            message.get("cc") or "",
            message.get("bcc") or "",
        ]
        if str(value).strip()
    ]
    timestamp = str(message.get("date") or "").strip()
    attachments: list[AttachmentRef] = []
    body_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            disposition = (part.get_content_disposition() or "").lower()
            content_type = (part.get_content_type() or "").lower()
            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if disposition == "attachment" or filename:
                attachments.append(
                    AttachmentRef(
                        filename=filename or f"attachment-{len(attachments) + 1}",
                        mime_type=content_type or "application/octet-stream",
                        size=len(payload),
                    )
                )
                continue
            if content_type == "text/plain":
                body_parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
            elif content_type == "text/html":
                html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                body_parts.append(html2text.html2text(html))
    else:
        payload = message.get_payload(decode=True) or b""
        content = payload.decode(message.get_content_charset() or "utf-8", errors="replace")
        if (message.get_content_type() or "").lower() == "text/html":
            content = html2text.html2text(content)
        body_parts.append(content)

    body = "\n\n".join(part.strip() for part in body_parts if part.strip())
    return ThreadDocument(
        thread_id=str(message.get("message-id") or path.stem),
        subject=subject or path.stem,
        messages=[
            EmailMessage(
                sender=sender,
                recipients=recipients,
                subject=subject,
                timestamp=timestamp,
                body=body,
            )
        ],
        attachments=attachments,
    )
