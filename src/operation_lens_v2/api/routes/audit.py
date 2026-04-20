"""Audit surface: query log + ingestion history + entity-review actions.

All endpoints here are read-safe except:

- ``POST /audit/entities/{entity_id}/confirm`` -- marks a flagged entity as
  human-approved (lifts confidence to 1.0, stamps ``reviewed_by``).
- ``DELETE /audit/entities/{entity_id}`` -- prunes a false-positive entity
  and its aliases/relationships/evidence from the graph.

The flagging heuristic is intentionally simple: an entity is ``flagged``
when its stored confidence is below ``settings.low_confidence_threshold``
*and* it has not been human-reviewed. That matches the contract the
ingestion pipeline keeps: ``ExtractedEntity.confidence`` is stored on
``entities.confidence`` and lifted by ``bump_entity_confidence`` whenever
a stronger mention arrives, so a flagged entity is one no extractor has
ever been sure about.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store
from operation_lens_v2.runtime import get_duck_connection

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/queries")
def get_queries(limit: int = 50) -> dict[str, object]:
    con = get_duck_connection(settings.duckdb_path)
    rows = con.execute(
        """
        SELECT query_id, query_text, intent, llm_backend, submitted_at
        FROM queries
        ORDER BY submitted_at DESC
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    return {
        "queries": [
            {
                "query_id": row[0],
                "query_text": row[1],
                "intent": row[2],
                "llm_backend": row[3],
                "submitted_at": str(row[4]),
            }
            for row in rows
        ]
    }


@router.get("/ingestions")
def list_ingestions(
    case_ref: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    """Return ingestion events for the Audit view, newest first."""
    con = get_duck_connection(settings.duckdb_path)
    events = duck_store.list_ingestion_events(
        con,
        case_ref=case_ref,
        status=status,
        limit=max(1, min(limit, 500)),
    )
    return {
        "count": len(events),
        "case_ref": case_ref,
        "status": status,
        "low_confidence_threshold": settings.low_confidence_threshold,
        "ingestions": events,
    }


@router.get("/ingestions/{event_id}")
def get_ingestion(event_id: str) -> dict[str, object]:
    """Return one ingestion's metadata plus every entity it attested.

    ``entities`` is ordered confidence-ascending so reviewers see the
    candidates that most need attention first. Each row carries a
    ``flagged`` bool derived from ``low_confidence_threshold``; the UI
    uses that to decide whether to render the Confirm/Remove actions.
    """
    con = get_duck_connection(settings.duckdb_path)
    event = duck_store.get_ingestion_event(con, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Unknown ingestion event")

    entities: list[dict[str, object]] = []
    doc_id = event.get("doc_id")
    if doc_id:
        entities = duck_store.list_entities_for_doc(
            con,
            str(doc_id),
            low_confidence_threshold=settings.low_confidence_threshold,
        )
    low_confidence_count = sum(1 for item in entities if item.get("low_confidence"))
    return {
        "ingestion": event,
        "entities": entities,
        "entity_count": len(entities),
        "low_confidence_count": low_confidence_count,
        "low_confidence_threshold": settings.low_confidence_threshold,
    }


@router.post("/entities/{entity_id}/confirm")
def confirm_entity(entity_id: str, reviewed_by: str | None = None) -> dict[str, object]:
    """Mark a flagged entity as human-confirmed (lifts confidence to 1.0)."""
    con = get_duck_connection(settings.duckdb_path)
    updated = duck_store.confirm_entity(con, entity_id, reviewed_by=reviewed_by)
    if updated is None:
        raise HTTPException(status_code=404, detail="Unknown entity")
    return {"entity": updated}


@router.delete("/entities/{entity_id}")
def remove_entity(entity_id: str) -> dict[str, object]:
    """Cascade-remove a false-positive entity and everything hanging off it."""
    con = get_duck_connection(settings.duckdb_path)
    removed = duck_store.delete_entity_cascade(con, entity_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Unknown entity")
    return {"entity_id": entity_id, "removed": removed}
