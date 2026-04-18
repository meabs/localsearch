from fastapi import APIRouter

from operation_lens_v2.api.schemas import QueryRequest
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import get_case_by_ref, init_db
from operation_lens_v2.ingestion.entity_schema import resolve_schema_config
from operation_lens_v2.query.pipeline import run_query

router = APIRouter(prefix="/query", tags=["query"])


BASE_QUERY_TEMPLATES: list[dict[str, str]] = [
    {
        "template_id": "known_associates",
        "label": "Known associates",
        "query": "Who are the known associates of {subject}? Include evidence citations.",
        "description": "Map direct links around one named entity.",
    },
    {
        "template_id": "phones_by_case",
        "label": "Phones by case",
        "query": "List all phone numbers linked to this case and who they are associated with.",
        "description": "Inventory telecommunications entities in current case scope.",
    },
    {
        "template_id": "location_movements",
        "label": "Location movements",
        "query": (
            "Show location movements for {subject} in chronological order "
            "with source references."
        ),
        "description": "Build movement timeline for a person, vehicle, or alias.",
    },
]


@router.post("")
async def query_endpoint(payload: QueryRequest) -> dict[str, object]:
    """Run an investigative query against the evidence store."""
    return await run_query(
        payload.query,
        case_ref=payload.case_ref,
        use_cloud=payload.use_cloud,
        chat_history=payload.chat_history,
        recall_mode=payload.recall_mode,
    )


@router.get("/templates")
def query_templates(case_ref: str | None = None) -> dict[str, object]:
    """Return saved query templates for common investigator workflows."""
    templates = list(BASE_QUERY_TEMPLATES)
    selected_pack = "base"
    if case_ref:
        con = init_db(settings.duckdb_path)
        case = get_case_by_ref(con, case_ref)
        if case:
            selected_pack = str(case.get("domain_pack") or "base")
            resolved = resolve_schema_config(
                domain_pack=selected_pack,
                overrides=case.get("schema_overrides"),
            )
            ui = resolved.get("ui", {}) or {}
            templates.extend(list(ui.get("default_query_templates", []) or []))
    deduped: dict[str, dict[str, str]] = {}
    for template in templates:
        deduped[str(template.get("template_id") or template.get("label") or len(deduped))] = template
    return {
        "case_ref": case_ref,
        "domain_pack": selected_pack,
        "templates": list(deduped.values()),
    }
