import re
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from operation_lens_v2.api.schemas import EmailThreadIngestRequest, IngestRequest
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.email_threads import ingest_email_thread_parquet
from operation_lens_v2.ingestion.pipeline import ingest_corpus, ingest_pdf
from operation_lens_v2.runtime import reset_duck_connection

router = APIRouter(prefix="/ingest", tags=["ingest"])
UPLOAD_FILE_PARAM = File(...)
CASE_REF_FORM = Form(...)
CASE_NAME_FORM = Form(default=None)
FORCE_FORM = Form(default=False)


def _sanitise_case_ref(case_ref: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", case_ref.strip()).strip("._")
    return cleaned or "UNASSIGNED"


def _build_upload_path(case_ref: str, original_name: str) -> Path:
    raw_filename = Path(original_name or "upload.pdf").name
    suffix = Path(raw_filename).suffix.lower()
    if suffix not in {".pdf", ".parquet"}:
        suffix = ".pdf"
    filename = f"{Path(raw_filename).stem or 'upload'}{suffix}"

    case_dir = settings.pdf_root_obj / _sanitise_case_ref(case_ref)
    case_dir.mkdir(parents=True, exist_ok=True)

    destination = case_dir / filename
    counter = 1
    while destination.exists():
        destination = case_dir / f"{destination.stem}-{counter}{destination.suffix}"
        counter += 1
    return destination


@router.post("/file")
async def ingest_file_endpoint(payload: IngestRequest) -> dict[str, object]:
    """Ingest a single PDF file into the evidence store."""
    pdf_path = Path(payload.pdf_path)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"PDF file not found: {payload.pdf_path}")
    result = await ingest_pdf(
        pdf_path,
        case_ref=payload.case_ref,
        case_name=payload.case_name,
        force=payload.force,
    )
    reset_duck_connection(settings.duckdb_path)
    return result


@router.post("/corpus")
async def ingest_corpus_endpoint(payload: IngestRequest) -> dict[str, object]:
    """Ingest all PDFs in a directory."""
    corpus_dir = Path(payload.pdf_path)
    if not corpus_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {payload.pdf_path}")
    results = await ingest_corpus(
        corpus_dir,
        case_ref=payload.case_ref,
        case_name=payload.case_name,
        force=payload.force,
    )
    reset_duck_connection(settings.duckdb_path)
    return {"ingested": len(results), "results": results}


@router.post("/email-threads/file")
async def ingest_email_threads_file_endpoint(payload: EmailThreadIngestRequest) -> dict[str, object]:
    """Ingest a parquet file containing email threads into the graph store."""
    parquet_path = Path(payload.parquet_path)
    if not parquet_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Parquet file not found: {payload.parquet_path}",
        )
    if parquet_path.suffix.lower() != ".parquet":
        raise HTTPException(status_code=400, detail="Only .parquet files are supported")
    result = await ingest_email_thread_parquet(
        parquet_path,
        case_ref=payload.case_ref,
        case_name=payload.case_name,
        force=payload.force,
    )
    reset_duck_connection(settings.duckdb_path)
    return result


# Legacy endpoint — keep for backwards compatibility with existing scripts.
@router.post("")
async def ingest_endpoint(payload: IngestRequest) -> dict[str, object]:
    return await ingest_file_endpoint(payload)


@router.post("/upload")
async def ingest_upload_endpoint(
    file: UploadFile = UPLOAD_FILE_PARAM,
    case_ref: str = CASE_REF_FORM,
    case_name: str | None = CASE_NAME_FORM,
    force: bool = FORCE_FORM,
) -> dict[str, object]:
    """Accept a PDF or Parquet upload directly from the browser and ingest it."""
    resolved_case_ref = case_ref.strip()
    if not resolved_case_ref:
        raise HTTPException(status_code=400, detail="case_ref is required")

    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".parquet"}:
        raise HTTPException(status_code=400, detail="Only PDF and Parquet uploads are supported")

    destination = _build_upload_path(resolved_case_ref, filename)
    try:
        with destination.open("wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                buffer.write(chunk)
    finally:
        await file.close()

    if suffix == ".parquet":
        result = await ingest_email_thread_parquet(
            destination,
            case_ref=resolved_case_ref,
            case_name=case_name,
            force=force,
        )
    else:
        result = await ingest_pdf(
            destination,
            case_ref=resolved_case_ref,
            case_name=case_name,
            force=force,
        )
    reset_duck_connection(settings.duckdb_path)
    return {
        "stored_path": str(destination),
        "filename": destination.name,
        **result,
    }
