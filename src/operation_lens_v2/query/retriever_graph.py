from __future__ import annotations

from typing import Any

from operation_lens_v2.ingestion.entity_schema import get_schema
from operation_lens_v2.ingestion.normaliser import normalise
from operation_lens_v2.store import get_graph_backend


def _resolve_entity_ids(con, names: list[str]) -> list[str]:
    # Try every registered entity type's normaliser so "Marcus Webb" (PERSON)
    # and "marcus.webb@x" (EMAIL) both resolve from free-form mentions. Also
    # include the raw surface + a lowered fallback so direct DB inserts match.
    cleaned_names = [n.strip() for n in names if n and n.strip()]
    if not cleaned_names:
        return []
    known_types = sorted(get_schema().known_types())
    candidate_names: list[str] = []
    for name in cleaned_names:
        candidate_names.append(name)
        for entity_type in known_types:
            candidate_names.append(normalise(name, entity_type))
    candidate_names = list(dict.fromkeys(c for c in candidate_names if c))
    if not candidate_names:
        return []
    placeholders = ",".join(["?"] * len(candidate_names))
    rows = con.execute(
        f"""
        SELECT entity_id FROM entities
        WHERE canonical_name IN ({placeholders})
           OR lower(canonical_name) IN ({placeholders})
        """,
        [*candidate_names, *[c.lower() for c in candidate_names]],
    ).fetchall()
    entity_ids = [r[0] for r in rows]
    return list(dict.fromkeys(entity_ids))


def retrieve_graph(
    con,
    entity_names: list[str],
    limit: int = 50,
    case_id: str | None = None,
) -> list[dict[str, Any]]:
    ids = _resolve_entity_ids(con, entity_names)
    return get_graph_backend(con).retrieve_expanded_graph(ids, limit=limit, case_id=case_id)
