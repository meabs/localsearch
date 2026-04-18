from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path

from operation_lens_v2.ingestion.parsers.base import (
    BaseParser,
    ParsedAttachment,
    ParsedDocument,
    ParsedTextBlock,
)
from operation_lens_v2.ingestion.parsers.html_parser import html_to_text


class EmlParser(BaseParser):
    parser_name = "eml"
    supported_extensions = (".eml",)

    def parse(self, path: Path, *, document_id: str) -> ParsedDocument:
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        attachments: list[ParsedAttachment] = []
        text_blocks: list[ParsedTextBlock] = []

        headers = [
            f"subject: {message.get('subject', '')}",
            f"from: {message.get('from', '')}",
            f"to: {message.get('to', '')}",
            f"cc: {message.get('cc', '')}",
            f"date: {message.get('date', '')}",
        ]
        text_blocks.append(
            ParsedTextBlock(
                text="\n".join(headers),
                page=1,
                source_label="headers",
                provenance_type="header_metadata",
            )
        )

        body_index = 2
        if message.is_multipart():
            for part in message.walk():
                disposition = (part.get_content_disposition() or "").lower()
                content_type = (part.get_content_type() or "").lower()
                filename = part.get_filename()
                payload = part.get_payload(decode=True) or b""
                if disposition == "attachment" or filename:
                    attachments.append(
                        ParsedAttachment(
                            filename=filename or f"attachment-{len(attachments) + 1}",
                            mime_type=content_type or "application/octet-stream",
                            file_size=len(payload),
                            metadata={"content_type": content_type},
                        )
                    )
                    continue
                if content_type == "text/plain":
                    text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                elif content_type == "text/html":
                    text = html_to_text(
                        payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    )
                else:
                    continue
                if text.strip():
                    text_blocks.append(
                        ParsedTextBlock(
                            text=text,
                            page=body_index,
                            source_label=f"body {body_index - 1}",
                        )
                    )
                    body_index += 1
        else:
            payload = message.get_payload(decode=True) or b""
            text = payload.decode(message.get_content_charset() or "utf-8", errors="replace")
            if message.get_content_type() == "text/html":
                text = html_to_text(text)
            if text.strip():
                text_blocks.append(ParsedTextBlock(text=text, page=2, source_label="body 1"))

        return ParsedDocument(
            document_id=document_id,
            source_type="eml",
            source_metadata={
                "subject": message.get("subject", ""),
                "from": message.get("from", ""),
                "to": message.get("to", ""),
                "attachment_count": len(attachments),
            },
            text_blocks=text_blocks,
            attachments=attachments,
            parser_name=self.parser_name,
        )
