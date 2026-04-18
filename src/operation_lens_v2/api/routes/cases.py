from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from operation_lens_v2.api.schemas import CaseDomainPackRequest, CreateCaseRequest
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.entity_schema import resolve_schema_config
from operation_lens_v2.ingestion.duck_store import (
    create_case,
    get_case_by_ref,
    init_db,
    list_case_documents,
    list_cases,
    set_case_domain_pack,
)
from operation_lens_v2.services.export_service import _safe_case_name, export_case_briefing

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("")
def cases_list() -> dict[str, object]:
    con = init_db(settings.duckdb_path)
    return {"cases": list_cases(con)}


@router.post("")
def cases_create(payload: CreateCaseRequest) -> dict[str, str]:
    con = init_db(settings.duckdb_path)
    case_id = create_case(
        con,
        case_ref=payload.case_ref,
        case_name=payload.case_name,
        domain_pack=payload.domain_pack,
    )
    return {
        "case_id": case_id,
        "case_ref": payload.case_ref,
        "case_name": payload.case_name,
        "domain_pack": payload.domain_pack,
    }


@router.get("/{case_ref}/documents")
def case_documents(case_ref: str) -> dict[str, object]:
    con = init_db(settings.duckdb_path)
    return {"case_ref": case_ref, "documents": list_case_documents(con, case_ref=case_ref)}


@router.post("/{case_ref}/domain-pack")
def case_set_domain_pack(case_ref: str, payload: CaseDomainPackRequest) -> dict[str, object]:
    con = init_db(settings.duckdb_path)
    updated = set_case_domain_pack(
        con,
        case_ref=case_ref,
        domain_pack=payload.domain_pack,
        schema_overrides=payload.schema_overrides,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Case not found")
    return updated


@router.get("/{case_ref}/resolved-schema")
def case_resolved_schema(case_ref: str) -> dict[str, object]:
    con = init_db(settings.duckdb_path)
    case = get_case_by_ref(con, case_ref)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    resolved = resolve_schema_config(
        domain_pack=str(case.get("domain_pack") or "base"),
        overrides=case.get("schema_overrides"),
    )
    return {
        "case_ref": case_ref,
        "domain_pack": case.get("domain_pack", "base"),
        "resolved_schema": resolved,
    }


@router.get("/{case_ref}/dashboard-config")
def case_dashboard_config(case_ref: str) -> dict[str, object]:
    con = init_db(settings.duckdb_path)
    case = get_case_by_ref(con, case_ref)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    resolved = resolve_schema_config(
        domain_pack=str(case.get("domain_pack") or "base"),
        overrides=case.get("schema_overrides"),
    )
    ui = resolved.get("ui", {}) or {}
    return {
        "case_ref": case_ref,
        "domain_pack": case.get("domain_pack", "base"),
        "widgets": list(ui.get("default_dashboard_widgets", []) or []),
        "colors": dict(ui.get("colors", {}) or {}),
    }


@router.post("/{case_ref}/export")
def case_export(case_ref: str, format: str = "md") -> dict[str, object]:
    con = init_db(settings.duckdb_path)
    case = get_case_by_ref(con, case_ref)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        result = export_case_briefing(case_ref, format=format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "case_ref": case_ref,
        "format": result.format,
        "file_path": result.file_path,
        "download_url": f"/cases/exports/{Path(result.file_path).name}?case_ref={case_ref}",
        "download_name": result.download_name,
    }


@router.get("/exports/{filename}")
def case_export_download(filename: str, case_ref: str) -> FileResponse:
    path = (settings.export_root_obj / _safe_case_name(case_ref) / filename).resolve()
    export_root = settings.export_root_obj.resolve()
    if export_root not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="Export not found")
    media_type = "text/plain"
    if path.suffix.lower() == ".html":
        media_type = "text/html"
    elif path.suffix.lower() == ".pdf":
        media_type = "application/pdf"
    return FileResponse(path=path, media_type=media_type, filename=path.name)
