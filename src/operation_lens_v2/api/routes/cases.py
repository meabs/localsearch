from fastapi import APIRouter

from operation_lens_v2.api.schemas import CreateCaseRequest
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import (
    create_case,
    list_case_documents,
    list_cases,
)
from operation_lens_v2.runtime import get_duck_connection

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("")
def cases_list() -> dict[str, object]:
    con = get_duck_connection(settings.duckdb_path)
    return {"cases": list_cases(con)}


@router.post("")
def cases_create(payload: CreateCaseRequest) -> dict[str, str]:
    con = get_duck_connection(settings.duckdb_path)
    case_id = create_case(
        con,
        case_ref=payload.case_ref,
        case_name=payload.case_name,
    )
    return {"case_id": case_id, "case_ref": payload.case_ref, "case_name": payload.case_name}


@router.get("/{case_ref}/documents")
def case_documents(case_ref: str) -> dict[str, object]:
    con = get_duck_connection(settings.duckdb_path)
    return {"case_ref": case_ref, "documents": list_case_documents(con, case_ref=case_ref)}
