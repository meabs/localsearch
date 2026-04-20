from __future__ import annotations

from pathlib import Path

from operation_lens_v2.ingestion.email_threads import AttachmentRef, EmailMessage, ThreadDocument


def parse_msg(path: Path) -> ThreadDocument:
    """Parse an Outlook .msg file into a shared ThreadDocument model."""
    import extract_msg  # type: ignore[import]

    message = extract_msg.Message(str(path))
    attachments = [
        AttachmentRef(
            filename=str(getattr(item, "longFilename", "") or getattr(item, "filename", "") or "attachment"),
            mime_type="application/octet-stream",
            size=len(getattr(item, "data", b"") or b""),
        )
        for item in message.attachments
    ]

    recipients = [str(message.to or "").strip()]
    cc_value = str(message.cc or "").strip()
    if cc_value:
        recipients.append(cc_value)

    return ThreadDocument(
        thread_id=str(message.messageId or path.stem),
        subject=str(message.subject or path.stem),
        messages=[
            EmailMessage(
                sender=str(message.sender or ""),
                recipients=[item for item in recipients if item],
                subject=str(message.subject or ""),
                timestamp=str(message.date or ""),
                body=str(message.body or ""),
            )
        ],
        attachments=attachments,
    )
