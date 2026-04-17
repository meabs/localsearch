from __future__ import annotations

import uuid
from io import BytesIO

import pytest
from starlette.datastructures import UploadFile

from operation_lens_v2.api.routes.cases import case_documents
from operation_lens_v2.api.routes.ingest import ingest_upload_endpoint
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import init_db


def test_case_documents_lists_docs_for_selected_case(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "cases.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_TEST", "Test Case"],
    )
    con.execute(
        """
        INSERT INTO documents (doc_id, case_id, filename, filepath, page_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        [doc_id, case_id, "briefing.pdf", str(tmp_path / "briefing.pdf"), 4],
    )

    result = case_documents("OP_TEST")

    assert result["case_ref"] == "OP_TEST"
    assert len(result["documents"]) == 1
    assert result["documents"][0]["filename"] == "briefing.pdf"
    assert result["documents"][0]["page_count"] == "4"


@pytest.mark.asyncio
async def test_ingest_upload_endpoint_stores_pdf_then_calls_pipeline(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "pdf_root", str(tmp_path / "pdfs"))

    captured: dict[str, object] = {}

    async def fake_ingest_pdf(pdf_path, *, case_ref, case_name, force):
        captured["pdf_path"] = pdf_path
        captured["case_ref"] = case_ref
        captured["case_name"] = case_name
        captured["force"] = force
        return {
            "doc_id": "doc-123",
            "case_ref": case_ref,
            "pages": 2,
            "chunks": 5,
            "relationships": 1,
        }

    monkeypatch.setattr("operation_lens_v2.api.routes.ingest.ingest_pdf", fake_ingest_pdf)

    upload = UploadFile(filename="evidence.pdf", file=BytesIO(b"%PDF-1.4 test payload"))

    result = await ingest_upload_endpoint(
        file=upload,
        case_ref="OP_UPLOAD",
        case_name="Upload Case",
        force=False,
    )

    stored_path = tmp_path / "pdfs" / "OP_UPLOAD" / "evidence.pdf"
    assert stored_path.exists()
    assert stored_path.read_bytes() == b"%PDF-1.4 test payload"
    assert result["stored_path"] == str(stored_path)
    assert result["filename"] == "evidence.pdf"
    assert captured["pdf_path"] == stored_path
    assert captured["case_ref"] == "OP_UPLOAD"
    assert captured["case_name"] == "Upload Case"
    assert captured["force"] is False
