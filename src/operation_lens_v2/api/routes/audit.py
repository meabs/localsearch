from fastapi import APIRouter

from operation_lens_v2.config import settings
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
