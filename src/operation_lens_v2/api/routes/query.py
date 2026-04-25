import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from operation_lens_v2.api.schemas import CaseReportRequest, QueryRequest
from operation_lens_v2.query import planner
from operation_lens_v2.query.pipeline import (
    run_case_intelligence_report,
    run_investigator_query,
    run_investigator_query_stream,
    run_query,
)

logger = logging.getLogger(__name__)

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
    """Run an investigative query against the evidence store.

    When `scope` is provided, routes through the PydanticAI investigator agent;
    otherwise falls through to the legacy retrieve/rerank/generate pipeline.
    """
    if payload.scope is not None:
        case_scope = payload.case_scope or payload.case_ref
        try:
            return await run_investigator_query(
                payload.query,
                scope_mode=payload.scope,
                doc_id=payload.doc_id,
                case_scope=case_scope,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await run_query(
        payload.query,
        case_ref=payload.case_ref,
        use_cloud=payload.use_cloud,
        chat_history=payload.chat_history,
        recall_mode=payload.recall_mode,
    )


@router.post("/case-report")
async def case_report_endpoint(payload: CaseReportRequest) -> dict[str, object]:
    """Generate a detailed case-wide intelligence briefing pack."""
    try:
        return await run_case_intelligence_report(
            payload.case_ref,
            prompt=payload.prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/stream")
async def query_stream_endpoint(payload: QueryRequest) -> StreamingResponse:
    """Stream the investigator run as Server-Sent Events.

    Only the investigator path streams. Requires ``scope`` to be set; otherwise
    returns 400 — the deterministic retrieve/rerank pipeline doesn't have natural
    streaming boundaries and should use ``POST /query`` instead.
    """
    if payload.scope is None:
        raise HTTPException(
            status_code=400,
            detail="streaming requires scope (corpus|case|document); use POST /query for non-streaming",
        )

    case_scope = payload.case_scope or payload.case_ref

    async def event_source():
        try:
            async for event in run_investigator_query_stream(
                payload.query,
                scope_mode=payload.scope,
                doc_id=payload.doc_id,
                case_scope=case_scope,
            ):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except ValueError as exc:
            yield (
                "data: "
                + json.dumps({"kind": "error", "error_type": "ValueError", "message": str(exc), "recoverable": False})
                + "\n\n"
            )
        except Exception as exc:  # noqa: BLE001 — surface the failure to the client
            logger.exception("streaming query failed")
            yield (
                "data: "
                + json.dumps(
                    {
                        "kind": "error",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:400],
                        "recoverable": False,
                    }
                )
                + "\n\n"
            )
        finally:
            yield "data: {\"kind\": \"end\"}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/templates")
def query_templates() -> dict[str, list[dict[str, str]]]:
    """Return saved query templates for common investigator workflows."""
    return {"templates": QUERY_TEMPLATES}


@router.get("/suggested-pivots")
async def suggested_pivots(query: str, planner_mode: bool = True) -> dict[str, object]:
    """Return planner-guided next pivots without running full retrieval."""
    trace = await planner.plan_query(query, planner_mode=planner_mode)
    return {
        "query": query,
        "planner_trace": trace,
        "suggested_pivots": list(trace.get("suggested_pivots") or []),
        "follow_up_questions": list(trace.get("follow_up_questions") or []),
        "rewrite_query": trace.get("rewrite_query") or query,
    }
