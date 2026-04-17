from __future__ import annotations

import asyncio
import contextlib
import logging
import re
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
from operation_lens_v2.store import get_graph_backend

logger = logging.getLogger(__name__)

INVENTORY_RESULT_LIMIT = 40
INVENTORY_DISPLAY_LIMIT = 20
INVENTORY_CITATION_LIMIT = 3
TOP_RESULTS_LIMIT = 10
DOCUMENT_CHUNK_LIMIT = 12
RELATIONSHIP_INVENTORY_ASSOCIATION_LIMIT = 2
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
RELATIONSHIP_TEXT = {
    "ASSOCIATED_WITH": "is associated with",
    "OBSERVED_AT": "was observed at",
    "LINKED_TO": "is linked to",
    "MENTIONED_WITH": "was mentioned with",
    "INFERRED_LINK": "is inferentially linked to",
}
FOLLOW_UP_CONTEXT_RE = re.compile(
    r"\b("
    r"he|she|they|them|their|there|that|those|it|him|her|his|hers|same|again|too"
    r")\b"
)


def _inventory_name_filter_sql(entity_type: str) -> tuple[str, list[str]]:
    if entity_type == "LOCATION":
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
    if entity_type == "PHONE":
        return (
            """
              AND regexp_full_match(
                regexp_replace(trim(e.canonical_name), '[^0-9]', '', 'g'),
                '^[0-9]{10,15}$'
              )
              AND lower(trim(e.canonical_name)) NOT LIKE 'number ending %'
              AND EXISTS (
                SELECT 1
                FROM entity_aliases ea_phone
                LEFT JOIN chunks c_phone ON c_phone.chunk_id = ea_phone.source_chunk
                WHERE ea_phone.entity_id = e.entity_id
                  AND (
                    lower(coalesce(c_phone.text, '')) LIKE '%phone%'
                    OR lower(coalesce(c_phone.text, '')) LIKE '%contact number%'
                    OR lower(coalesce(c_phone.text, '')) LIKE '%msisdn%'
                    OR lower(coalesce(c_phone.text, '')) LIKE '%call data%'
                    OR lower(coalesce(c_phone.text, '')) LIKE '%telephony%'
                    OR lower(coalesce(c_phone.text, '')) LIKE '%handset%'
                    OR lower(coalesce(c_phone.text, '')) LIKE '%contacted%'
                    OR lower(coalesce(c_phone.text, '')) LIKE '%calls%'
                    OR lower(coalesce(c_phone.text, '')) LIKE '%tower%'
                    OR lower(coalesce(c_phone.text, '')) LIKE '%cell site%'
                  )
              )
            """,
            [],
        )
    return "", []


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
              PARTITION BY coalesce(d.filename, d.doc_id), c.page
              ORDER BY length(coalesce(c.text, '')) DESC, ea.alias_id
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


def _resolve_document_scope(
    con,
    *,
    document_refs: list[str],
    case_id: str | None,
) -> list[dict[str, str]]:
    normalized_refs = list(
        dict.fromkeys(ref.strip().lower() for ref in document_refs if isinstance(ref, str) and ref.strip())
    )
    if not normalized_refs:
        return []

    placeholders = ",".join(["?"] * len(normalized_refs))
    params: list[object] = [*normalized_refs]
    case_filter = ""
    if case_id:
        case_filter = "AND case_id = ?"
        params.append(case_id)

    rows = con.execute(
        f"""
        SELECT doc_id, filename
        FROM documents
        WHERE lower(filename) IN ({placeholders})
        {case_filter}
        ORDER BY filename
        """,
        params,
    ).fetchall()
    return [{"doc_id": row[0], "filename": row[1]} for row in rows]


def _document_chunk_rows(con, *, doc_ids: set[str], limit: int = DOCUMENT_CHUNK_LIMIT) -> list[dict[str, object]]:
    if not doc_ids:
        return []
    placeholders = ",".join(["?"] * len(doc_ids))
    rows = con.execute(
        f"""
        SELECT c.chunk_id, c.doc_id, d.filename, c.page, c.text
        FROM chunks c
        JOIN documents d ON d.doc_id = c.doc_id
        WHERE c.doc_id IN ({placeholders})
        ORDER BY c.page, c.chunk_index
        LIMIT ?
        """,
        [*doc_ids, limit],
    ).fetchall()
    return [
        {
            "source": "document",
            "chunk_id": row[0],
            "doc_id": row[1],
            "doc_name": row[2],
            "page": row[3],
            "text": row[4] or "",
            "score": 0.95,
        }
        for row in rows
    ]


def _filter_rows_to_documents(
    rows: list[dict[str, object]],
    *,
    doc_ids: set[str],
) -> list[dict[str, object]]:
    if not doc_ids:
        return rows
    return [row for row in rows if row.get("doc_id") in doc_ids]


def _attach_document_names(
    rows: list[dict[str, object]],
    *,
    document_names: dict[str, str],
) -> list[dict[str, object]]:
    if not document_names:
        return rows
    enriched: list[dict[str, object]] = []
    for row in rows:
        row_copy = dict(row)
        doc_id = str(row_copy.get("doc_id") or "")
        if doc_id and not row_copy.get("doc_name") and doc_id in document_names:
            row_copy["doc_name"] = document_names[doc_id]
        enriched.append(row_copy)
    return enriched


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


def _inventory_relationships(
    con,
    *,
    entity_id: str,
    case_id: str | None,
) -> list[dict[str, object]]:
    return get_graph_backend(con).inventory_relationships_for_entity(
        entity_id, case_id=case_id
    )


def _run_relationship_inventory_query(
    con,
    *,
    query_text: str,
    case_ref: str | None,
    case_id: str | None,
    entity_type: str,
    recall_mode: str = "auto",
) -> dict[str, object]:
    rows = _inventory_rows(con, entity_type=entity_type, case_id=case_id)
    query_id = str(uuid4())
    claims: list[dict[str, object]] = []
    lines = ["KEY FINDINGS"]

    for row in rows[:INVENTORY_DISPLAY_LIMIT]:
        entity_id = str(row[0])
        entity_name = str(row[1])
        associations = _inventory_relationships(con, entity_id=entity_id, case_id=case_id)
        if associations:
            for association in associations[:RELATIONSHIP_INVENTORY_ASSOCIATION_LIMIT]:
                relation_type = str(association.get("relation_type") or "LINKED_TO")
                relation_phrase = RELATIONSHIP_TEXT.get(
                    relation_type,
                    f"has relation {relation_type} with",
                )
                citations = list(association.get("citations", []))[:INVENTORY_CITATION_LIMIT]
                claim_text = f"{entity_name} {relation_phrase} {association.get('other_name')}"
                citation_suffix = f" {_citation_text(citations)}" if citations else ""
                lines.append(f"- {claim_text}{citation_suffix}")
                claims.append(
                    {
                        "text": claim_text,
                        "citations": citations,
                        "confidence": max(0.6, float(association.get("confidence", 0.0))),
                    }
                )
            continue

        citations = _inventory_citations(con, entity_id=entity_id)
        citation_suffix = f" {_citation_text(citations)}" if citations else ""
        lines.append(
            f"- {entity_name} identified in case evidence, but no typed association was extracted{citation_suffix}"
        )
        claims.append(
            {
                "text": entity_name,
                "citations": citations,
                "confidence": 0.55,
            }
        )

    if len(lines) == 1:
        lines.append(f"No {entity_type.lower()} entities found for this scope.")

    lines.extend(
        [
            "CONFIDENCE POSTURE",
            "Association inventory prioritises typed graph relationships and falls back to direct alias citations when links are absent.",
            "EVIDENCE GAPS",
            "Some identifiers may appear in the corpus without a typed relationship; missing links usually indicate extraction gaps rather than confirmed isolation.",
        ]
    )

    con.execute(
        "INSERT INTO queries (query_id, query_text, intent, llm_backend) VALUES (?, ?, ?, ?)",
        [query_id, query_text, "entity_relationship_inventory_query", "structured-sql"],
    )
    return {
        "query_id": query_id,
        "intent": "entity_relationship_inventory_query",
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


def _missing_document_result(
    *,
    query_text: str,
    case_ref: str | None,
    recall_mode: str,
    document_refs: list[str],
) -> dict[str, object]:
    query_id = str(uuid4())
    requested = ", ".join(document_refs)
    return {
        "query_id": query_id,
        "intent": "document_query",
        "entities": [],
        "entities_resolved": [],
        "case_scope": case_ref or "ALL_CASES",
        "recall_mode": recall_mode,
        "backend": "structured-sql",
        "answer": (
            "KEY FINDINGS\n"
            f"No indexed document matched: {requested}.\n"
            "CONFIDENCE POSTURE\n"
            "Filename matching is exact and currently limited to ingested document names.\n"
            "EVIDENCE GAPS\n"
            "If the document was recently added, it may still need to be ingested into the evidence store."
        ),
        "claims": [],
        "result_count": 0,
        "top_results": [],
    }


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


def _query_needs_history_context(
    query_text: str,
    parsed_current: dict[str, object],
) -> bool:
    trimmed = str(query_text or "").strip()
    if not trimmed:
        return False

    if (
        parsed_current.get("entities")
        or parsed_current.get("document_refs")
        or parsed_current.get("inventory_target")
        or parsed_current.get("intent") != "general_query"
    ):
        return False

    lowered = trimmed.lower()
    token_count = len(re.findall(r"\w+", lowered))
    if token_count > 6:
        return False

    return lowered.startswith("and ") or "what about" in lowered or bool(
        FOLLOW_UP_CONTEXT_RE.search(lowered)
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
    timer = StageTimer(label=f"query:{query_text[:80]}")
    con = get_duck_connection(settings.duckdb_path)
    with timer.measure("parse"):
        parsed = parser.parse_query(query_text)
    effective_query = (
        _compose_query_with_history(query_text, chat_history)
        if _query_needs_history_context(query_text, parsed)
        else query_text
    )
    resolved_recall_mode, candidate_multiplier, min_doc_coverage = _resolve_recall_strategy(
        requested_mode=recall_mode,
        parsed_recall_priority=bool(parsed.get("recall_priority")),
    )
    vector_limit = max(1, settings.vector_top_k * candidate_multiplier)
    fts_limit = max(1, settings.fts_top_k * candidate_multiplier)
    graph_limit = max(50, settings.rerank_top_n * candidate_multiplier * 2)
    with timer.measure("case_lookup"):
        case_id = get_case_id_by_ref(con, case_ref) if case_ref else None
    with timer.measure("document_lookup"):
        document_scope = _resolve_document_scope(
            con,
            document_refs=list(parsed.get("document_refs", [])),
            case_id=case_id,
        )
    scoped_doc_ids = {doc["doc_id"] for doc in document_scope}
    document_names = {doc["doc_id"]: doc["filename"] for doc in document_scope}
    if parsed.get("document_refs") and not scoped_doc_ids:
        result = _missing_document_result(
            query_text=effective_query,
            case_ref=case_ref,
            recall_mode=resolved_recall_mode,
            document_refs=list(parsed.get("document_refs", [])),
        )
        timer.log_summary()
        return result
    inventory_target = parsed.get("inventory_target")

    if isinstance(inventory_target, str) and inventory_target:
        with timer.measure("inventory_query"):
            if parsed.get("relationship_focus"):
                result = _run_relationship_inventory_query(
                    con,
                    query_text=effective_query,
                    case_ref=case_ref,
                    case_id=case_id,
                    entity_type=inventory_target,
                    recall_mode=resolved_recall_mode,
                )
            else:
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

        if scoped_doc_ids:
            with timer.measure("document_filter"):
                exact = _filter_rows_to_documents(exact, doc_ids=scoped_doc_ids)
                fts = _filter_rows_to_documents(fts, doc_ids=scoped_doc_ids)
                graph = _filter_rows_to_documents(graph, doc_ids=scoped_doc_ids)
                vector = _filter_rows_to_documents(vector, doc_ids=scoped_doc_ids)
                exact = _attach_document_names(exact, document_names=document_names)
                fts = _attach_document_names(fts, document_names=document_names)
                graph = _attach_document_names(graph, document_names=document_names)
                vector = _attach_document_names(vector, document_names=document_names)
                document_chunks = _document_chunk_rows(
                    con,
                    doc_ids=scoped_doc_ids,
                    limit=max(DOCUMENT_CHUNK_LIMIT, settings.rerank_top_n),
                )
        else:
            document_chunks = []

        if case_id:
            with timer.measure("case_doc_filter"):
                allowed_doc_ids = get_doc_ids_for_case(con, case_id)
                vector = [row for row in vector if row.get("doc_id") in allowed_doc_ids]
                if document_chunks:
                    document_chunks = [
                        row for row in document_chunks if row.get("doc_id") in allowed_doc_ids
                    ]

        with timer.measure("rerank"):
            ranked = reranker.rerank_results([*exact, *fts, *vector, *graph, *document_chunks])
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
                query_intent=str(parsed["intent"]),
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
