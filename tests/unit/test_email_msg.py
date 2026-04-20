from __future__ import annotations

from pathlib import Path

from operation_lens_v2.ingestion import email_msg


class _Attachment:
    def __init__(self, name: str, data: bytes) -> None:
        self.longFilename = name
        self.data = data


class _FakeMessage:
    def __init__(self, _path: str) -> None:
        self.messageId = "msg-123"
        self.subject = "Outlook Subject"
        self.sender = "sender@example.com"
        self.to = "to@example.com"
        self.cc = "cc@example.com"
        self.date = "2024-01-01"
        self.body = "Body content"
        self.attachments = [_Attachment("report.pdf", b"x")]


def test_parse_msg_extracts_content(monkeypatch, tmp_path: Path):
    class _Module:
        Message = _FakeMessage

    monkeypatch.setitem(__import__("sys").modules, "extract_msg", _Module())
    msg_path = tmp_path / "sample.msg"
    msg_path.write_text("stub", encoding="utf-8")

    doc = email_msg.parse_msg(msg_path)
    assert doc.thread_id == "msg-123"
    assert doc.subject == "Outlook Subject"
    assert doc.messages[0].sender == "sender@example.com"
    assert len(doc.attachments) == 1
