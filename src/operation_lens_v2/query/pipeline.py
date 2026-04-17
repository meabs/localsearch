from __future__ import annotations

import asyncio
import contextlib
import logging
from uuid import uuid4

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import get_case_id_by_ref, get_doc_ids_for_case
from operation_lens_v2.query import (
    claim_validator,
    evidence_builder,
    llm_router,
    parser,
    reranker,
    retriever_exact,
    retriever_fts,
    retriever_graph,
    retriever_vector,
)
from operation_lens_v2.runtime import StageTimer, get_duck_connection

logger = logging.getLogger(__name__)

INVENTORY_RESULT_LIMIT = 40
INVENTORY_DISPLAY_LIMIT = 20
INVENTORY_CITATION_LIMIT = 3
TOP_RESULTS_LIMIT = 10
INVENTORY_CONFIDENCE_POSTURE = (
    "direct inventory results backed by exact alias mentions and chunk citations."
)
INVENTORY_EVIDENCE_GAP = (
    "Entity quality depends on extraction and normalisation rules; "
    "low-quality or malformed names are filtered where possible."
)
CHAT_HISTORY_TURN_LIMIT = 6
CHAT_HISTORY_LINE_LIMIT = 18
VALID_RECALL_MODES = {"fast", "balanced", "exhaustive", "auto"}


def _inventory_name_filter_sql(entity_type: str) -> tuple[str, list[str]]:
    if entity_type != "LOCATION":
        return "", []
    return (
        """
          AND length(trim(e.canonical_name)) >= 4
          AND lower(trim(e.canonical_name)) NOT LIKE 'to %'
          AND lower(trim(e.canonical_name)) NOT LIKE 'from %'
          AND lower(trim(e.canonical_name)) NOT LIKE 'than %'
          AND lower(trim(e.canonical_name)) NOT LIKE 'near %'
          AND lower(trim(e.canonical_name)) NOT LIKE 'at %'
          AND lower(trim(e.canonical_name)) NOT LIKE 'in %'
          AND lower(trim(e.canonical_name)) NOT LIKE 'on %'
        """,
        [],
    )


def _inventory_rows(con, *, entity_type: str, case_id: str | None) -> list[tuple]:
    name_filter_sql, name_filter_params = _inventory_name_filter_sql(entity_type)
    params: list[object] = [entity_type]
    joins = """
        LEFT JOIN entity_aliases ea ON ea.entity_id = e.entity_id
        LEFT JOIN documents d ON d.doc_id = ea.source_doc
    """
    case_filter = ""

    if case_id:
        joins = """
            JOIN entity_aliases ea ON ea.entity_id = e.entity_id
            JOIN documents d ON d.doc_id = ea.source_doc
        """
        case_filter = "AND d.case_id = ?"
        params.append(case_id)

    params.extend(name_filter_params)
    params.append(INVENTORY_RESULT_LIMIT)
    return con.execute(
        f"""
        SELECT
          e.entity_id,
          e.canonical_name,
          e.entity_type,
          max(e.mention_count) AS mention_count,
          count(DISTINCT ea.source_doc) AS doc_count,
          string_agg(DISTINCT d.filename, ', ' ORDER BY d.filename) AS docs
        FROM entities e
        {joins}
        WHERE e.entity_type = ?
        {case_filter}
        {name_filter_sql}
        GROUP BY e.entity_id, e.canonical_name, e.entity_type
        ORDER BY mention_count DESC, doc_count DESC, e.canonical_name
        LIMIT ?
        """,
        params,
    ).fetchall()


def _inventory_citations(con, *, entity_id: str) -> list[dict[str, object]]:
    rows = con.execute(
        """
        WITH ranked AS (
          SELECT
            d.doc_id,
            d.filename,
            c.page,
            c.text,
            ea.source_chunk,
            row_number() OVER (
              PARTITION BY d.doc_id, c.page, ea.source_chunk
              ORDER BY ea.alias_id
            ) AS rn
          FROM entity_aliases ea
          JOIN documents d ON d.doc_id = ea.source_doc
          LEFT JOIN chunks c ON c.chunk_id = ea.source_chunk
          WHERE ea.entity_id = ?
        )
        SELECT doc_id, filename, page, text
        FROM ranked
        WHERE rn = 1
        ORDER BY page NULLS LAST, filename
        LIMIT ?
        """,
        [entity_id, INVENTORY_CITATION_LIMIT],
    ).fetchall()
    return [
        {
            "doc_id": row[0],
            "doc_name": row[1],
            "page": row[2] if row[2] is not None else "?",
            "span_text": row[3] or "",
        }
        for row in rows
    ]


def _citation_text(citations: list[dict[str, object]]) -> str:
    return ", ".join(
        f"[{citation.get('doc_name') or citation.get('doc_id')}, p.{citation.get('page')}]"
        for citation in citations
    )


def _inventory_answer_lines(
    *, entity_type: str, rows: list[tuple], claims: list[dict[str, object]]
) -> list[str]:
    lines = ["KEY FINDINGS"]
    heading = f"Key {entity_type.lower()}s"

    for row, claim in zip(rows[:INVENTORY_DISPLAY_LIMIT], claims, strict=False):
        citation_suffix = f" {_citation_text(claim['citations'])}" if claim["citations"] else ""
        lines.append(f"- {row[1]} (mentions {int(row[3])}, docs {int(row[4])}){citation_suffix}")

    if len(lines) == 1:
        lines.append(f"No {entity_type.lower()} entities found for this scope.")

    lines.extend(
        [
            "CONFIDENCE POSTURE",
            f"{heading.capitalize()} are {INVENTORY_CONFIDENCE_POSTURE}",
            "EVIDENCE GAPS",
            INVENTORY_EVIDENCE_GAP,
        ]
    )
    return lines


def _run_inventory_query(
    con,
    *,
    query_text: str,
    case_ref: str | None,
    case_id: str | None,
    entity_type: str,
    recall_mode: str = "auto",
) -> dict[str, object]:
    """Return direct entity inventory answers for list-style questions."""
    rows = _inventory_rows(con, entity_type=entity_type, case_id=case_id)
    query_id = str(uuid4())
    claims = [
        {
            "text": row[1],
            "citations": _inventory_citations(con, entity_id=row[0]),
            "confidence": 0.8,
        }
        for row in rows[:INVENTORY_DISPLAY_LIMIT]
    ]
    lines = _inventory_answer_lines(entity_type=entity_type, rows=rows, claims=claims)

    con.execute(
        "INSERT INTO queries (query_id, query_text, intent, llm_backend) VALUES (?, ?, ?, ?)",
        [query_id, query_text, "entity_inventory_query", "structured-sql"],
    )
    return {
        "query_id": query_id,
        "intent": "entity_inventory_query",
        "entities": [],
        "entities_resolved": [],
        "case_scope": case_ref or "ALL_CASES",
        "recall_mode": recall_mode,
        "backend": "structured-sql",
        "answer": "\n".join(lines),
        "claims": claims,
        "result_count": len(rows),
        "top_results": [],
    }


def _resolve_entities(con, names: list[str]) -> list[dict[str, str]]:
    """Resolve entity names to canonical records from DuckDB."""
    lowered_names = list(dict.fromkeys(name.strip().lower() for name in names if name.strip()))
    if not lowered_names:
        return []
    placeholders = ",".join(["?"] * len(lowered_names))
    rows = con.execute(
        f"""
        SELECT entity_id, canonical_name, entity_type
        FROM entities
        WHERE lower(canonical_name) IN ({placeholders})
        """,
        lowered_names,
    ).fetchall()
    return [{"entity_id": row[0], "canonical_name": row[1], "entity_type": row[2]} for row in rows]


def _compose_query_with_history(
    query_text: str,
    chat_history: list[dict[str, str]] | None,
) -> str:
    if not chat_history:
        return query_text
    recent_turns = chat_history[-CHAT_HISTORY_TURN_LIMIT:]
    history_lines: list[str] = []
    for turn in recent_turns:
        role = str(turn.get("role", "")).strip().lower()
        content = str(turn.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        history_lines.append(f"{role.upper()}: {content}")
    if not history_lines:
        return query_text
    history_block = "\n".join(history_lines[-CHAT_HISTORY_LINE_LIMIT:])
    return (
        "Conversation context (most recent):\n"
        f"{history_block}\n\n"
        f"Current question:\n{query_text}"
    )


def _with_document_coverage(
    ranked: list[dict[str, object]],
    *,
    top_n: int,
    min_doc_coverage: int,
) -> list[dict[str, object]]:
    if not ranked or top_n <= 0:
        return []

    selected = list(ranked[:top_n])
    selected_keys = {
        str(item.get("chunk_id") or item.get("rel_id") or item.get("entity_id") or idx)
        for idx, item in enumerate(selected)
    }
    selected_docs = {str(item.get("doc_id")) for item in selected if item.get("doc_id")}

    if len(selected_docs) >= min_doc_coverage:
        return selected

    def _replace_index_for_doc_diversity() -> int | None:
        doc_counts: dict[str, int] = {}
        for row in selected:
            doc = str(row.get("doc_id") or "")
            if not doc:
                continue
            doc_counts[doc] = doc_counts.get(doc, 0) + 1
        replace_idx: int | None = None
        lowest_score = float("inf")
        for idx, row in enumerate(selected):
            doc = str(row.get("doc_id") or "")
            if doc_counts.get(doc, 0) <= 1:
                continue
            score = float(row.get("rank_score", row.get("score", 0.0)))
            if score < lowest_score:
                lowest_score = score
                replace_idx = idx
        return replace_idx

    for item in ranked[top_n:]:
        doc_id = item.get("doc_id")
        if not doc_id or str(doc_id) in selected_docs:
            continue
        key = str(item.get("chunk_id") or item.get("rel_id") or item.get("entity_id") or id(item))
        if key in selected_keys:
            continue
        replace_idx = _replace_index_for_doc_diversity()
        if replace_idx is None:
            break
        removed = selected[replace_idx]
        selected[replace_idx] = item
        selected_keys.discard(
            str(
                removed.get("chunk_id")
                or removed.get("rel_id")
                or removed.get("entity_id")
                or replace_idx
            )
        )
        selected_keys.add(key)
        selected_docs = {str(row.get("doc_id")) for row in selected if row.get("doc_id")}
        if len(selected_docs) >= min_doc_coverage:
            break

    selected.sort(
        key=lambda row: float(row.get("rank_score", row.get("score", 0.0))),
        reverse=True,
    )
    return selected


def _resolve_recall_strategy(
    *,
    requested_mode: str | None,
    parsed_recall_priority: bool,
) -> tuple[str, int, int]:
    mode = str(requested_mode or "auto").strip().lower()
    if mode not in VALID_RECALL_MODES:
        mode = "auto"

    balanced_multiplier = max(1, settings.hybrid_candidate_multiplier)
    exhaustive_multiplier = max(balanced_multiplier + 1, balanced_multiplier * 2)

    if mode == "fast":
        return "fast", 1, 1
    if mode == "balanced":
        return "balanced", balanced_multiplier, max(1, settings.min_doc_coverage)
    if mode == "exhaustive":
        return "exhaustive", exhaustive_multiplier, max(2, settings.min_doc_coverage + 2)

    if parsed_recall_priority or settings.hybrid_recall_default:
        return "balanced", balanced_multiplier, max(1, settings.min_doc_coverage)
    return "fast", 1, 1


async def run_query(
    query_text: str,
    case_ref: str | None = None,
    use_cloud: bool | None = None,
    chat_history: list[dict[str, str]] | None = None,
    recall_mode: str | None = None,
) -> dict[str, object]:
    """Orchestrate the query pipeline from parse through claim validation."""
    effective_query = _compose_query_with_history(query_text, chat_history)
    timer = StageTimer(label=f"query:{query_text[:80]}")
    con = get_duck_connection(settings.duckdb_path)
    with timer.measure("parse"):
        parsed = parser.parse_query(effective_query)
    resolved_recall_mode, candidate_multiplier, min_doc_coverage = _resolve_recall_strategy(
        requested_mode=recall_mode,
        parsed_recall_priority=bool(parsed.get("recall_priority")),
    )
    vector_limit = max(1, settings.vector_top_k * candidate_multiplier)
    fts_limit = max(1, settings.fts_top_k * candidate_multiplier)
    graph_limit = max(50, settings.rerank_top_n * candidate_multiplier * 2)
    with timer.measure("case_lookup"):
        case_id = get_case_id_by_ref(con, case_ref) if case_ref else None
    inventory_target = parsed.get("inventory_target")

    if isinstance(inventory_target, str) and inventory_target:
        with timer.measure("inventory_query"):
            result = _run_inventory_query(
                con,
                query_text=effective_query,
                case_ref=case_ref,
                case_id=case_id,
                entity_type=inventory_target,
                recall_mode=resolved_recall_mode,
            )
        timer.log_summary()
        return result

    vector_task = asyncio.create_task(
        retriever_vector.retrieve_vector(effective_query, limit=vector_limit)
    )
    try:
        with timer.measure("retrieve_exact"):
            exact = retriever_exact.retrieve_exact(
                con,
                effective_query,
                limit=max(20, settings.rerank_top_n * candidate_multiplier),
                case_id=case_id,
            )
        with timer.measure("retrieve_fts"):
            fts = retriever_fts.retrieve_fts(
                con,
                effective_query,
                limit=fts_limit,
                case_id=case_id,
            )
        with timer.measure("retrieve_graph"):
            graph = retriever_graph.retrieve_graph(
                con, parsed["entities"], limit=graph_limit, case_id=case_id
            )
        vector = await timer.await_stage("retrieve_vector", vector_task)

        if case_id:
            with timer.measure("case_doc_filter"):
                allowed_doc_ids = get_doc_ids_for_case(con, case_id)
                vector = [row for row in vector if row.get("doc_id") in allowed_doc_ids]

        with timer.measure("rerank"):
            ranked = reranker.rerank_results([*exact, *fts, *vector, *graph])
            ranked = _with_document_coverage(
                ranked,
                top_n=settings.rerank_top_n,
                min_doc_coverage=min_doc_coverage,
            )

        with timer.measure("resolve_entities"):
            entities_resolved = _resolve_entities(con, parsed["entities"])
        with timer.measure("build_evidence"):
            packet = evidence_builder.build_evidence_packet(
                query_text=effective_query,
                entities_resolved=entities_resolved,
                ranked_results=ranked,
                case_scope=case_ref or "ALL_CASES",
            )

        use_cloud_reasoning = (
            settings.allow_cloud_reasoning if use_cloud is None else bool(use_cloud)
        )

        answer_payload = await timer.await_stage(
            "generate_answer",
            llm_router.generate_answer(packet, use_cloud=use_cloud_reasoning),
        )
        validated = await timer.await_stage(
            "validate_claims",
            claim_validator.validate_claims(answer_payload),
        )

        query_id = str(packet.get("query_id") or uuid4())
        with timer.measure("persist_answer"):
            con.execute(
                (
                    "INSERT INTO queries "
                    "(query_id, query_text, intent, llm_backend) "
                    "VALUES (?, ?, ?, ?)"
                ),
                [query_id, effective_query, parsed["intent"], validated.get("backend", "local")],
            )

            for claim in validated.get("claims", []):
                con.execute(
                    """
                    INSERT INTO answer_spans (
                      span_id, query_id, claim_text, supporting_evidence, confidence, validated
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(uuid4()),
                        query_id,
                        claim.get("text", ""),
                        [f"{c.get('doc_id')}:{c.get('page')}" for c in claim.get("citations", [])],
                        claim.get("confidence", 0.0),
                        bool(claim.get("validated", False)),
                    ],
                )

        return {
            "query_id": query_id,
            "intent": parsed["intent"],
            "entities": parsed["entities"],
            "entities_resolved": entities_resolved,
            "case_scope": case_ref or "ALL_CASES",
            "backend": validated.get("backend"),
            "recall_mode": resolved_recall_mode,
            "answer": validated["answer"],
            "claims": validated["claims"],
            "result_count": len(ranked),
            "top_results": ranked[:TOP_RESULTS_LIMIT],
        }
    finally:
        if not vector_task.done():
            vector_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await vector_task
        timer.log_summary()
