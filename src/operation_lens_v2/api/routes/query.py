from fastapi import APIRouter

from operation_lens_v2.api.schemas import QueryRequest
from operation_lens_v2.query.pipeline import run_query

router = APIRouter(prefix="/query", tags=["query"])


QUERY_TEMPLATES: list[dict[str, str]] = [
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
def query_templates() -> dict[str, list[dict[str, str]]]:
    """Return saved query templates for common investigator workflows."""
    return {"templates": QUERY_TEMPLATES}
