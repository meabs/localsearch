from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from operation_lens_v2.config import settings
from operation_lens_v2.runtime import get_duck_connection

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/{doc_id}/thumbnail")
def get_thumbnail(doc_id: str) -> Response:
    """Return a stored image thumbnail for the requested document."""
    con = get_duck_connection(settings.duckdb_path)
    row = con.execute(
        "SELECT thumbnail_blob FROM documents WHERE doc_id = ?",
        [doc_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown document")
    blob = row[0]
    if blob is None:
        raise HTTPException(status_code=404, detail="Document has no thumbnail")
    return Response(content=bytes(blob), media_type="image/png")


@router.get("/{doc_id}/page/{page}")
def get_document_page(doc_id: str, page: int) -> dict[str, object]:
    """Return page text payload for source pivot modal."""
    con = get_duck_connection(settings.duckdb_path)
    doc = con.execute(
        "SELECT filename, filepath, format, page_count FROM documents WHERE doc_id = ?",
        [doc_id],
    ).fetchone()
    if doc is None:
        raise HTTPException(status_code=404, detail="Unknown document")
    rows = con.execute(
        """
        SELECT text
        FROM chunks
        WHERE doc_id = ? AND page = ?
        ORDER BY chunk_index
        """,
        [doc_id, page],
    ).fetchall()
    text = "\n\n".join(str(row[0] or "") for row in rows).strip()
    return {
        "doc_id": doc_id,
        "filename": doc[0],
        "page": page,
        "page_count": int(doc[3] or 0),
        "format": str(doc[2] or "pdf"),
        "text": text,
        "spans": [],
        "thumbnail_url": f"/documents/{doc_id}/thumbnail",
        "pdf_url": f"/documents/{doc_id}/pdf",
    }


@router.get("/{doc_id}/pdf")
def get_document_pdf(doc_id: str):
    """Serve original PDF file when available."""
    con = get_duck_connection(settings.duckdb_path)
    row = con.execute(
        "SELECT filepath, format FROM documents WHERE doc_id = ?",
        [doc_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown document")
    if str(row[1] or "").lower() != "pdf":
        raise HTTPException(status_code=404, detail="No PDF artifact for this document")
    path = Path(str(row[0]))
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found on disk")
    return FileResponse(path=path, media_type="application/pdf", filename=path.name)
