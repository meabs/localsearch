from fastapi import APIRouter

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import connect

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/queries")
def get_queries(limit: int = 50) -> dict[str, object]:
    con = connect(settings.duckdb_path)
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
