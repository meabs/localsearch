from __future__ import annotations

import json
from email.message import EmailMessage

from operation_lens_v2.ingestion.parser_registry import registry


def test_registry_resolves_supported_parsers(tmp_path) -> None:
    files = {
        "note.txt": "hello world",
        "table.csv": "name,value\nalpha,1\n",
        "data.json": json.dumps({"user": {"name": "Rae"}}),
        "page.html": "<html><body><h1>Hello</h1><p>world</p></body></html>",
    }
    for filename, contents in files.items():
        path = tmp_path / filename
        path.write_text(contents, encoding="utf-8")
        parser = registry.get_parser(path)
        assert parser is not None
        parsed = parser.parse(path, document_id="doc-1")
        assert parsed.text_blocks


def test_eml_parser_extracts_headers_and_attachments(tmp_path) -> None:
    message = EmailMessage()
    message["Subject"] = "Transfer advice"
    message["From"] = "alice@example.com"
    message["To"] = "bob@example.com"
    message.set_content("Payment was sent from account 1234.")
    message.add_attachment(b"ref,data", maintype="text", subtype="csv", filename="ledger.csv")
    path = tmp_path / "mail.eml"
    path.write_bytes(message.as_bytes())

    parser = registry.get_parser(path)
    parsed = parser.parse(path, document_id="doc-2")

    assert parsed.source_type == "eml"
    assert any(block.source_label == "headers" for block in parsed.text_blocks)
    assert parsed.attachments[0].filename == "ledger.csv"
