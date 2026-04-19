from __future__ import annotations

from fastapi import APIRouter, HTTPException

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import connect

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("/{entity_id}/cases")
def entity_cases(entity_id: str) -> dict[str, object]:
    """Return the distinct cases that reference an entity through its source docs."""
    con = connect(settings.duckdb_path)
    entity = con.execute(
        "SELECT canonical_name, entity_type FROM entities WHERE entity_id = ?",
        [entity_id],
    ).fetchone()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    rows = con.execute(
        """
        SELECT DISTINCT c.case_id, c.case_ref, c.case_name
        FROM entity_aliases ea
        JOIN documents d ON d.doc_id = ea.source_doc
        JOIN cases c ON c.case_id = d.case_id
        WHERE ea.entity_id = ?
        ORDER BY c.case_ref, c.case_id
        """,
        [entity_id],
    ).fetchall()

    cases = [
        {
            "case_id": row[0],
            "case_ref": row[1],
            "case_name": row[2],
        }
        for row in rows
    ]
    return {
        "entity_id": entity_id,
        "canonical_name": entity[0],
        "entity_type": entity[1],
        "case_ids": [row["case_id"] for row in cases],
        "cases": cases,
    }
