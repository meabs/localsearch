from __future__ import annotations

from pathlib import Path

from operation_lens_v2.ingestion.email_eml import parse_eml


def test_parse_eml_extracts_message_and_attachment(tmp_path: Path):
    eml_path = tmp_path / "sample.eml"
    eml_path.write_text(
        "From: sender@example.com\n"
        "To: a@example.com, b@example.com\n"
        "Subject: Test Mail\n"
        "Date: Mon, 01 Jan 2024 10:00:00 +0000\n"
        "MIME-Version: 1.0\n"
        "Content-Type: multipart/mixed; boundary=sep\n\n"
        "--sep\n"
        "Content-Type: text/plain; charset=utf-8\n\n"
        "Hello team.\n"
        "--sep\n"
        "Content-Type: application/pdf\n"
        "Content-Disposition: attachment; filename=note.pdf\n\n"
        "PDFDATA\n"
        "--sep--\n",
        encoding="utf-8",
    )

    doc = parse_eml(eml_path)
    assert doc.subject == "Test Mail"
    assert len(doc.messages) == 1
    assert "Hello team." in doc.messages[0].body
    assert len(doc.attachments) == 1
