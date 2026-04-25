from __future__ import annotations

import mimetypes
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store
from operation_lens_v2.ingestion.duck_store import get_case_id_by_ref
from operation_lens_v2.ingestion.entity_schema import get_schema
from operation_lens_v2.ingestion.media_objects import _extract_frame_at
from operation_lens_v2.runtime import get_duck_connection
from operation_lens_v2.services.geocoder import GeocoderDisabled, geocode_entity
from operation_lens_v2.services.graph_algorithms import (
    centrality_report,
    detect_communities,
    expand_neighbourhood,
    shortest_path,
)
from operation_lens_v2.services.graph_backend import DuckDBGraphBackend, EdgeFilter

router = APIRouter(prefix="/graph", tags=["graph"])
IMAGE_UPLOAD_PARAM = File(...)
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DATE_TIME_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})(?:[T\s]+(\d{1,2}:\d{2}(?::\d{2})?))?\b"
)
_UK_DATE_TIME_RE = re.compile(
    r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?:[,\s]+(\d{1,2}:\d{2}(?::\d{2})?))?\b"
)


def _attachments_root() -> Path:
    root = _PROJECT_ROOT / "data" / "attachments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_media_frame_path(raw_path: str) -> Path:
    stored = Path(raw_path)
    if stored.is_absolute():
        return stored
    project_path = _PROJECT_ROOT / stored
    if project_path.exists():
        return project_path
    cwd_path = Path.cwd() / stored
    if cwd_path.exists():
        return cwd_path
    return project_path


def _safe_filename(filename: str) -> str:
    raw = Path(filename or "attachment").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return cleaned or "attachment"


def _extract_seen_at(text: str) -> str | None:
    match = _DATE_TIME_RE.search(text)
    if match:
        date_part = match.group(1)
        time_part = match.group(2) or "00:00:00"
        if len(time_part.split(":")) == 2:
            time_part = f"{time_part}:00"
        return f"{date_part}T{time_part}"

    uk_match = _UK_DATE_TIME_RE.search(text)
    if not uk_match:
        return None
    day, month, year, time_part = uk_match.groups()
    year_int = int(year)
    if year_int < 100:
        year_int += 2000
    if time_part is None:
        time_part = "00:00:00"
    elif len(time_part.split(":")) == 2:
        time_part = f"{time_part}:00"
    try:
        dt = datetime(year_int, int(month), int(day))
    except ValueError:
        return None
    return f"{dt.strftime('%Y-%m-%d')}T{time_part}"


def _entity_timeline_events(con, entity_id: str, limit: int) -> list[dict[str, object]]:
    rows = con.execute(
        """
        SELECT
          d.doc_id,
          d.filename,
          c.page,
          c.text
        FROM entity_aliases ea
        JOIN documents d ON d.doc_id = ea.source_doc
        LEFT JOIN chunks c ON c.chunk_id = ea.source_chunk
        WHERE ea.entity_id = ?
        ORDER BY d.doc_id, c.page NULLS LAST
        LIMIT ?
        """,
        [entity_id, max(1, limit)],
    ).fetchall()

    events: list[dict[str, object]] = []
    for row in rows:
        text = str(row[3] or "")
        seen_at = _extract_seen_at(text)
        if not seen_at:
            continue
        events.append(
            {
                "seen_at": seen_at,
                "doc_id": row[0],
                "filename": row[1],
                "page": row[2],
                "excerpt": text[:280],
            }
        )
    events.sort(key=lambda item: item["seen_at"])
    return events[:limit]


@router.get("/schema")
def schema() -> dict[str, object]:
    """Expose the dynamic entity-type registry (types, colours, relation hints) to the UI."""
    s = get_schema()
    return {
        "entity_types": [
            {
                "name": type_def.name,
                "color": type_def.color,
                "prompts": list(type_def.gliner_prompts),
                "aliases": list(type_def.llm_aliases),
                "normalise_strategy": type_def.normalise.strategy,
                "has_regex": type_def.regex is not None,
            }
            for type_def in sorted(s.entity_types.values(), key=lambda t: t.name)
        ],
        "relation_hints": list(s.relation_hints),
    }


def _load_edge_citations(con, rel_ids: list[str]) -> dict[str, list[dict[str, object]]]:
    if not rel_ids:
        return {}
    rows = con.execute(
        """
        WITH ranked AS (
          SELECT
            re.rel_id,
            re.doc_id,
            d.filename,
            re.page,
            re.chunk_id,
            re.span_text,
            re.extraction_method,
            row_number() OVER (
              PARTITION BY re.rel_id
              ORDER BY re.page NULLS LAST, re.evidence_id
            ) AS rn
          FROM relationship_evidence re
          JOIN documents d ON d.doc_id = re.doc_id
          WHERE re.rel_id IN ({})
        )
        SELECT rel_id, doc_id, filename, page, chunk_id, span_text, extraction_method
        FROM ranked
        WHERE rn <= 3
        ORDER BY rel_id, page NULLS LAST, filename
        """.format(",".join(["?"] * len(rel_ids))),
        rel_ids,
    ).fetchall()
    citations: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        citations.setdefault(row[0], []).append(
            {
                "doc_id": row[1],
                "doc_name": row[2],
                "page": row[3] if row[3] is not None else "?",
                "chunk_id": row[4],
                "span_text": row[5] or "",
                "extraction_method": row[6],
            }
        )
    return citations


@router.get("/entities")
def entities(limit: int = 100) -> dict[str, object]:
    con = get_duck_connection(settings.duckdb_path)
    rows = con.execute(
        "SELECT entity_id, canonical_name, entity_type, mention_count FROM entities LIMIT ?",
        [limit],
    ).fetchall()
    return {
        "entities": [
            {
                "entity_id": row[0],
                "canonical_name": row[1],
                "entity_type": row[2],
                "mention_count": row[3],
            }
            for row in rows
        ]
    }


@router.get("/network")
def network(
    limit: int = 300,
    entity: str | None = None,
    hops: int = 2,
    case_ref: str | None = None,
) -> dict[str, object]:
    con = get_duck_connection(settings.duckdb_path)

    # Resolve an optional case filter up front so both the seeded and
    # unseeded branches can share the same EXISTS-clause shape. Relationships
    # are attached to a case via their evidence rows (documents.case_id).
    case_id: str | None = None
    if case_ref:
        case_id = get_case_id_by_ref(con, case_ref)
        if case_id is None:
            return {
                "focus_entity": entity,
                "focus_node_ids": [],
                "hops": hops,
                "case_ref": case_ref,
                "nodes": [],
                "edges": [],
                "meta": {
                    "node_count": 0,
                    "edge_count": 0,
                    "avg_confidence": 0.0,
                    "entity_found": False,
                    "case_found": False,
                },
            }
    case_filter_sql = (
        " AND EXISTS ("
        "SELECT 1 FROM relationship_evidence re2 "
        "JOIN documents d2 ON d2.doc_id = re2.doc_id "
        "WHERE re2.rel_id = r.rel_id AND d2.case_id = ?"
        ")"
        if case_id
        else ""
    )

    focus_node_ids: list[str] = []
    if entity:
        entity_norm = entity.strip().lower()
        seed_rows = con.execute(
            """
            SELECT DISTINCT e.entity_id
            FROM entities e
            LEFT JOIN entity_aliases ea ON ea.entity_id = e.entity_id
            WHERE lower(e.canonical_name) = ?
               OR lower(ea.alias_text) = ?
            """,
            [entity_norm, entity_norm],
        ).fetchall()
        seed_ids = {row[0] for row in seed_rows}
        if not seed_ids:
            return {
                "focus_entity": entity,
                "focus_node_ids": [],
                "hops": hops,
                "nodes": [],
                "edges": [],
                "meta": {
                    "node_count": 0,
                    "edge_count": 0,
                    "avg_confidence": 0.0,
                    "entity_found": False,
                },
            }
        focus_node_ids = sorted(seed_ids)
        edge_rows = con.execute(
            """
            WITH RECURSIVE walk(node_id, depth) AS (
              SELECT entity_id, 0
              FROM entities
              WHERE entity_id IN ({seed_ids})
              UNION
              SELECT
                CASE
                  WHEN r.source_entity = walk.node_id THEN r.target_entity
                  ELSE r.source_entity
                END,
                walk.depth + 1
              FROM walk
              JOIN relationships r
                ON r.source_entity = walk.node_id OR r.target_entity = walk.node_id
              WHERE walk.depth < ?
            ),
            latest_attachments AS (
              SELECT entity_id, attachment_id
              FROM (
                SELECT
                  entity_id,
                  attachment_id,
                  row_number() OVER (
                    PARTITION BY entity_id
                    ORDER BY uploaded_at DESC, attachment_id DESC
                  ) AS rn
                FROM entity_attachments
              )
              WHERE rn = 1
            )
            SELECT
              r.rel_id,
              r.source_entity,
              r.target_entity,
              r.relation_type,
              r.confidence,
              es.canonical_name AS source_name,
              et.canonical_name AS target_name,
              es.entity_type AS source_type,
              et.entity_type AS target_type,
              es.mention_count AS source_mentions,
              et.mention_count AS target_mentions,
              count(re.evidence_id) AS evidence_count,
              count(DISTINCT re.doc_id) AS distinct_doc_count,
              la_src.attachment_id AS source_attachment_id,
              la_tgt.attachment_id AS target_attachment_id,
              es.latitude AS source_lat, es.longitude AS source_lon,
              et.latitude AS target_lat, et.longitude AS target_lon
            FROM relationships r
            JOIN entities es ON es.entity_id = r.source_entity
            JOIN entities et ON et.entity_id = r.target_entity
            LEFT JOIN relationship_evidence re ON re.rel_id = r.rel_id
            LEFT JOIN latest_attachments la_src ON la_src.entity_id = es.entity_id
            LEFT JOIN latest_attachments la_tgt ON la_tgt.entity_id = et.entity_id
            WHERE r.source_entity IN (SELECT DISTINCT node_id FROM walk)
              AND r.target_entity IN (SELECT DISTINCT node_id FROM walk)
              {case_filter}
            GROUP BY
              r.rel_id, r.source_entity, r.target_entity, r.relation_type, r.confidence,
              es.canonical_name, et.canonical_name, es.entity_type, et.entity_type,
              es.mention_count, et.mention_count, la_src.attachment_id, la_tgt.attachment_id,
              es.latitude, es.longitude, et.latitude, et.longitude
            ORDER BY r.confidence DESC, evidence_count DESC
            LIMIT ?
            """.format(
                seed_ids=",".join(["?"] * len(seed_ids)),
                case_filter=case_filter_sql,
            ),
            [
                *seed_ids,
                max(0, hops),
                *([case_id] if case_id else []),
                limit,
            ],
        ).fetchall()
    else:
        edge_rows = con.execute(
            f"""
            WITH latest_attachments AS (
              SELECT entity_id, attachment_id
              FROM (
                SELECT
                  entity_id,
                  attachment_id,
                  row_number() OVER (
                    PARTITION BY entity_id
                    ORDER BY uploaded_at DESC, attachment_id DESC
                  ) AS rn
                FROM entity_attachments
              )
              WHERE rn = 1
            )
            SELECT
              r.rel_id,
              r.source_entity,
              r.target_entity,
              r.relation_type,
              r.confidence,
              es.canonical_name AS source_name,
              et.canonical_name AS target_name,
              es.entity_type AS source_type,
              et.entity_type AS target_type,
              es.mention_count AS source_mentions,
              et.mention_count AS target_mentions,
              count(re.evidence_id) AS evidence_count,
              count(DISTINCT re.doc_id) AS distinct_doc_count,
              la_src.attachment_id AS source_attachment_id,
              la_tgt.attachment_id AS target_attachment_id,
              es.latitude AS source_lat, es.longitude AS source_lon,
              et.latitude AS target_lat, et.longitude AS target_lon
            FROM relationships r
            JOIN entities es ON es.entity_id = r.source_entity
            JOIN entities et ON et.entity_id = r.target_entity
            LEFT JOIN relationship_evidence re ON re.rel_id = r.rel_id
            LEFT JOIN latest_attachments la_src ON la_src.entity_id = es.entity_id
            LEFT JOIN latest_attachments la_tgt ON la_tgt.entity_id = et.entity_id
            WHERE 1 = 1
              {case_filter_sql}
            GROUP BY
              r.rel_id, r.source_entity, r.target_entity, r.relation_type, r.confidence,
              es.canonical_name, et.canonical_name, es.entity_type, et.entity_type,
              es.mention_count, et.mention_count, la_src.attachment_id, la_tgt.attachment_id,
              es.latitude, es.longitude, et.latitude, et.longitude
            ORDER BY r.confidence DESC
            LIMIT ?
            """,
            [
                *([case_id] if case_id else []),
                max(limit, 1),
            ],
        ).fetchall()

    edge_rows = edge_rows[:limit]
    edge_citations = _load_edge_citations(con, [str(row[0]) for row in edge_rows])
    nodes: dict[str, dict[str, object]] = {}
    edges: list[dict[str, object]] = []
    for row in edge_rows:
        source_id = row[1]
        target_id = row[2]
        if source_id not in nodes:
            nodes[source_id] = {
                "id": source_id,
                "label": row[5],
                "entity_type": row[7],
                "mention_count": row[9],
                "attachment_id": row[13],
                "latitude": float(row[15]) if row[15] is not None else None,
                "longitude": float(row[16]) if row[16] is not None else None,
            }
        if target_id not in nodes:
            nodes[target_id] = {
                "id": target_id,
                "label": row[6],
                "entity_type": row[8],
                "mention_count": row[10],
                "attachment_id": row[14],
                "latitude": float(row[17]) if row[17] is not None else None,
                "longitude": float(row[18]) if row[18] is not None else None,
            }
        edges.append(
            {
                "id": row[0],
                "source": source_id,
                "target": target_id,
                "type": row[3],
                "confidence": float(row[4]),
                "evidence_count": int(row[11]),
                "distinct_doc_count": int(row[12] or 0),
                "citations": edge_citations.get(str(row[0]), []),
            }
        )
    return {
        "focus_entity": entity,
        "focus_node_ids": focus_node_ids,
        "hops": hops,
        "case_ref": case_ref,
        "nodes": list(nodes.values()),
        "edges": edges,
        "meta": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "avg_confidence": (
                round(sum(edge["confidence"] for edge in edges) / len(edges), 3) if edges else 0.0
            ),
            "entity_found": bool(focus_node_ids) if entity else True,
        },
    }


@router.get("/media-network")
def media_network(case_ref: str | None = None, limit: int = 500) -> dict[str, object]:
    """Return the separate media-object graph: assets, frames, detections and spatial links."""
    con = get_duck_connection(settings.duckdb_path)
    return duck_store.list_media_object_graph(con, case_ref=case_ref, limit=limit)


@router.get("/media-frames/{frame_id}")
def media_frame_file(frame_id: str) -> FileResponse:
    """Serve an extracted media frame so the UI can show the visual evidence."""
    con = get_duck_connection(settings.duckdb_path)
    frame = duck_store.get_media_frame(con, frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Media frame not found")
    path = _resolve_media_frame_path(str(frame["image_path"]))
    if (not path.exists() or not path.is_file()) and frame.get("asset_id"):
        asset = con.execute(
            "SELECT filepath, media_type FROM media_assets WHERE asset_id = ?",
            [frame["asset_id"]],
        ).fetchone()
        if asset:
            source_path = Path(str(asset[0]))
            if source_path.exists() and str(asset[1] or "") != "image":
                target_path = _resolve_media_frame_path(str(frame["image_path"]))
                if _extract_frame_at(source_path, target_path, float(frame.get("timestamp_seconds") or 0)):
                    path = target_path
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Media frame file missing")
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return FileResponse(path=path, media_type=media_type, filename=path.name)


@router.get("/locations")
def locations(case_ref: str | None = None, limit: int = 500) -> dict[str, object]:
    """Return geocoded LOCATION entities for the case-wide map view.

    Joins entities → entity_aliases → documents to collect which documents
    each location appears in, so pins can expand into source references.
    """
    con = get_duck_connection(settings.duckdb_path)
    if case_ref:
        rows = con.execute(
            """
            SELECT
              e.entity_id,
              e.canonical_name,
              e.latitude,
              e.longitude,
              e.geocode_display_name,
              e.mention_count,
              list(DISTINCT d.filename) AS doc_names
            FROM entities e
            LEFT JOIN entity_aliases ea ON ea.entity_id = e.entity_id
            LEFT JOIN documents d ON d.doc_id = ea.source_doc
            LEFT JOIN cases c ON c.case_id = d.case_id
            WHERE e.entity_type = 'LOCATION'
              AND e.latitude IS NOT NULL
              AND e.longitude IS NOT NULL
              AND lower(c.case_ref) = lower(?)
            GROUP BY
              e.entity_id, e.canonical_name, e.latitude, e.longitude,
              e.geocode_display_name, e.mention_count
            ORDER BY e.mention_count DESC
            LIMIT ?
            """,
            [case_ref, limit],
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT
              e.entity_id,
              e.canonical_name,
              e.latitude,
              e.longitude,
              e.geocode_display_name,
              e.mention_count,
              list(DISTINCT d.filename) AS doc_names
            FROM entities e
            LEFT JOIN entity_aliases ea ON ea.entity_id = e.entity_id
            LEFT JOIN documents d ON d.doc_id = ea.source_doc
            WHERE e.entity_type = 'LOCATION'
              AND e.latitude IS NOT NULL
              AND e.longitude IS NOT NULL
            GROUP BY
              e.entity_id, e.canonical_name, e.latitude, e.longitude,
              e.geocode_display_name, e.mention_count
            ORDER BY e.mention_count DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
    pins = [
        {
            "entity_id": row[0],
            "canonical_name": row[1],
            "latitude": float(row[2]),
            "longitude": float(row[3]),
            "display_name": row[4],
            "mention_count": int(row[5] or 0),
            "doc_names": [d for d in (row[6] or []) if d],
        }
        for row in rows
    ]
    return {
        "case_ref": case_ref,
        "pin_count": len(pins),
        "pins": pins,
    }


@router.get("/entity/{entity_id}/geocode")
async def entity_geocode(entity_id: str, force: bool = False) -> dict[str, object]:
    """Return cached geocode for an entity, or resolve on-demand via Nominatim."""
    con = get_duck_connection(settings.duckdb_path)
    row = con.execute(
        "SELECT canonical_name, entity_type FROM entities WHERE entity_id = ?",
        [entity_id],
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Entity not found")
    canonical_name, entity_type = row[0], row[1]
    try:
        result = await geocode_entity(
            con,
            entity_id=entity_id,
            canonical_name=canonical_name,
            force=force,
        )
    except GeocoderDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not result:
        return {
            "entity_id": entity_id,
            "canonical_name": canonical_name,
            "entity_type": entity_type,
            "geocode": None,
            "status": "no_match",
        }
    # Refresh from DB so we return the persisted timestamp.
    stored = duck_store.get_entity_geocode(con, entity_id) or result
    return {
        "entity_id": entity_id,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "geocode": stored,
        "status": "ok",
    }


@router.get("/entity/{entity_id}/attachments")
def list_entity_attachments(entity_id: str) -> dict[str, object]:
    """List photo attachments linked to an entity."""
    con = get_duck_connection(settings.duckdb_path)
    row = con.execute(
        "SELECT canonical_name, entity_type FROM entities WHERE entity_id = ?",
        [entity_id],
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Entity not found")
    attachments = duck_store.list_attachments(con, entity_id)
    return {
        "entity_id": entity_id,
        "canonical_name": row[0],
        "entity_type": row[1],
        "attachments": attachments,
    }


@router.get("/entity/{entity_id}/profile")
def entity_profile(entity_id: str, timeline_limit: int = 30) -> dict[str, object]:
    """Return one entity profile with aliases, links, docs, timeline, and attachments."""
    con = get_duck_connection(settings.duckdb_path)
    entity = con.execute(
        """
        SELECT entity_id, canonical_name, entity_type, mention_count, confidence,
               latitude, longitude, geocode_display_name
        FROM entities
        WHERE entity_id = ?
        """,
        [entity_id],
    ).fetchone()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    alias_rows = con.execute(
        """
        SELECT DISTINCT alias_text
        FROM entity_aliases
        WHERE entity_id = ?
        ORDER BY alias_text
        LIMIT 100
        """,
        [entity_id],
    ).fetchall()

    doc_rows = con.execute(
        """
        SELECT
          d.doc_id,
          d.filename,
          min(c.page) AS first_page,
          count(DISTINCT ea.alias_id) AS mention_count
        FROM entity_aliases ea
        JOIN documents d ON d.doc_id = ea.source_doc
        LEFT JOIN chunks c ON c.chunk_id = ea.source_chunk
        WHERE ea.entity_id = ?
        GROUP BY d.doc_id, d.filename
        ORDER BY mention_count DESC, d.filename
        LIMIT 100
        """,
        [entity_id],
    ).fetchall()

    link_rows = con.execute(
        """
        SELECT
          r.rel_id,
          r.relation_type,
          r.confidence,
          re.doc_id,
          d.filename,
          re.page,
          re.span_text,
          CASE
            WHEN r.source_entity = ? THEN r.target_entity
            ELSE r.source_entity
          END AS linked_entity_id,
          linked.canonical_name AS linked_name,
          linked.entity_type AS linked_type
        FROM relationships r
        LEFT JOIN relationship_evidence re ON re.rel_id = r.rel_id
        LEFT JOIN documents d ON d.doc_id = re.doc_id
        JOIN entities linked
          ON linked.entity_id = CASE
            WHEN r.source_entity = ? THEN r.target_entity
            ELSE r.source_entity
          END
        WHERE r.source_entity = ? OR r.target_entity = ?
        ORDER BY r.confidence DESC, re.page NULLS LAST, d.filename
        LIMIT 300
        """,
        [entity_id, entity_id, entity_id, entity_id],
    ).fetchall()

    links: dict[str, dict[str, object]] = {}
    for row in link_rows:
        rel_id = str(row[0])
        if rel_id not in links:
            links[rel_id] = {
                "rel_id": rel_id,
                "relation_type": row[1],
                "confidence": float(row[2] or 0.0),
                "linked_entity": {
                    "entity_id": row[7],
                    "canonical_name": row[8],
                    "entity_type": row[9],
                },
                "citations": [],
            }
        if row[3] is not None:
            links[rel_id]["citations"].append(
                {
                    "doc_id": row[3],
                    "doc_name": row[4],
                    "page": row[5] if row[5] is not None else "?",
                    "span_text": row[6] or "",
                }
            )

    return {
        "entity": {
            "entity_id": entity[0],
            "canonical_name": entity[1],
            "entity_type": entity[2],
            "mention_count": int(entity[3] or 0),
            "confidence": float(entity[4] or 0.0),
            "geocode": {
                "latitude": float(entity[5]) if entity[5] is not None else None,
                "longitude": float(entity[6]) if entity[6] is not None else None,
                "display_name": entity[7],
            },
        },
        "aliases": [row[0] for row in alias_rows],
        "documents": [
            {
                "doc_id": row[0],
                "filename": row[1],
                "first_page": row[2] if row[2] is not None else "?",
                "mention_count": int(row[3] or 0),
            }
            for row in doc_rows
        ],
        "links": list(links.values()),
        "timeline": _entity_timeline_events(con, entity_id, min(200, timeline_limit)),
        "attachments": duck_store.list_attachments(con, entity_id),
    }


@router.post("/entity/{entity_id}/attachments")
async def upload_entity_attachment(
    entity_id: str,
    file: UploadFile = IMAGE_UPLOAD_PARAM,
    caption: str | None = None,
) -> dict[str, object]:
    """Store an image attachment under data/attachments/{entity_id}/."""
    con = get_duck_connection(settings.duckdb_path)
    row = con.execute(
        "SELECT 1 FROM entities WHERE entity_id = ?",
        [entity_id],
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Entity not found")

    content_type = (file.content_type or "").lower().strip()
    if content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported image type")

    filename = _safe_filename(file.filename or "attachment")
    entity_dir = _attachments_root() / entity_id
    entity_dir.mkdir(parents=True, exist_ok=True)
    destination = entity_dir / filename
    counter = 1
    while destination.exists():
        destination = entity_dir / f"{destination.stem}-{counter}{destination.suffix}"
        counter += 1

    size = 0
    try:
        with destination.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                handle.write(chunk)
                size += len(chunk)
    finally:
        await file.close()

    attachment_id = duck_store.create_attachment(
        con,
        entity_id=entity_id,
        filename=destination.name,
        mime_type=content_type,
        file_size=size,
        storage_path=str(destination.resolve()),
        caption=(caption or "").strip() or None,
    )
    return {
        "entity_id": entity_id,
        "attachment_id": attachment_id,
        "filename": destination.name,
        "file_size": size,
        "mime_type": content_type,
    }


@router.get("/attachments/{attachment_id}")
def get_attachment_file(attachment_id: str) -> FileResponse:
    """Serve attachment bytes from local disk."""
    con = get_duck_connection(settings.duckdb_path)
    attachment = duck_store.get_attachment(con, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = Path(str(attachment["storage_path"]))
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file missing")
    media_type = str(attachment.get("mime_type") or mimetypes.guess_type(path.name)[0] or "")
    return FileResponse(path=path, media_type=media_type, filename=str(attachment["filename"]))


@router.get("/entity/{entity_id}/vehicle-track")
def vehicle_track(entity_id: str) -> dict[str, object]:
    """Build an ANPR-style track for VEHICLE nodes from observed-at evidence."""
    con = get_duck_connection(settings.duckdb_path)
    vehicle_row = con.execute(
        "SELECT canonical_name, entity_type FROM entities WHERE entity_id = ?",
        [entity_id],
    ).fetchone()
    if not vehicle_row:
        raise HTTPException(status_code=404, detail="Entity not found")
    if str(vehicle_row[1] or "").upper() != "VEHICLE":
        return {
            "entity_id": entity_id,
            "plate": vehicle_row[0],
            "entity_type": vehicle_row[1],
            "points": [],
        }

    rows = con.execute(
        """
        SELECT
          re.doc_id,
          d.filename,
          re.page,
          re.span_text,
          loc.entity_id AS location_entity_id,
          loc.canonical_name AS location_name,
          loc.latitude,
          loc.longitude,
          rel.relation_type
        FROM relationships rel
        JOIN relationship_evidence re ON re.rel_id = rel.rel_id
        JOIN documents d ON d.doc_id = re.doc_id
        JOIN entities src ON src.entity_id = rel.source_entity
        JOIN entities tgt ON tgt.entity_id = rel.target_entity
        JOIN entities loc
          ON (
            (rel.source_entity = ? AND rel.target_entity = loc.entity_id)
            OR
            (rel.target_entity = ? AND rel.source_entity = loc.entity_id)
          )
        WHERE (rel.source_entity = ? OR rel.target_entity = ?)
          AND upper(loc.entity_type) = 'LOCATION'
          AND loc.latitude IS NOT NULL
          AND loc.longitude IS NOT NULL
          AND (
            upper(rel.relation_type) = 'OBSERVED_AT'
            OR upper(re.span_text) LIKE '%ANPR%'
            OR upper(re.span_text) LIKE '%SIGHTING%'
          )
        ORDER BY re.doc_id, re.page
        LIMIT 500
        """,
        [entity_id, entity_id, entity_id, entity_id],
    ).fetchall()

    points: list[dict[str, object]] = []
    for row in rows:
        seen_at = _extract_seen_at(str(row[3] or ""))
        points.append(
            {
                "doc_id": row[0],
                "filename": row[1],
                "page": row[2],
                "excerpt": row[3] or "",
                "location_entity_id": row[4],
                "location_name": row[5],
                "latitude": float(row[6]),
                "longitude": float(row[7]),
                "relation_type": row[8],
                "seen_at": seen_at,
            }
        )
    points.sort(
        key=lambda p: (
            p.get("seen_at") is None,
            p.get("seen_at") or "",
            p["doc_id"],
            p["page"],
        )
    )
    return {
        "entity_id": entity_id,
        "plate": vehicle_row[0],
        "entity_type": vehicle_row[1],
        "points": points,
    }


# ----------------------------------------------------------------------
# Phase 8 — graph algorithms: path finding, expansion, centrality, communities
# ----------------------------------------------------------------------
def _split_csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


def _edge_filter_from_query(
    con,
    *,
    case_ref: str | None,
    min_confidence: float,
    exclude_relation_types: str | None,
    include_relation_types: str | None,
) -> EdgeFilter:
    case_id: str | None = None
    if case_ref:
        case_id = get_case_id_by_ref(con, case_ref)
        if case_id is None:
            raise HTTPException(status_code=404, detail=f"Unknown case_ref: {case_ref}")
    min_conf = max(0.0, min(1.0, float(min_confidence)))
    return EdgeFilter(
        case_id=case_id,
        min_confidence=min_conf,
        exclude_relation_types=_split_csv(exclude_relation_types),
        include_relation_types=_split_csv(include_relation_types),
    )


@router.get("/path")
def path(
    source: str,
    target: str,
    k: int = 3,
    min_confidence: float = 0.0,
    exclude_relation_types: str | None = None,
    include_relation_types: str | None = None,
    case_ref: str | None = None,
    cutoff_hops: int | None = 6,
) -> dict[str, object]:
    """Return up to k confidence-weighted paths between two entities.

    `source` and `target` accept either entity_id or canonical name / alias.
    Constraints (`min_confidence`, `exclude_relation_types`) trim the graph
    before traversal so pathfinding avoids noise edges (e.g. MENTIONED_WITH).
    """
    con = get_duck_connection(settings.duckdb_path)
    backend = DuckDBGraphBackend(con)
    source_id = backend.resolve_entity_id(source)
    target_id = backend.resolve_entity_id(target)
    if not source_id:
        raise HTTPException(status_code=404, detail=f"Source not found: {source}")
    if not target_id:
        raise HTTPException(status_code=404, detail=f"Target not found: {target}")

    edge_filter = _edge_filter_from_query(
        con,
        case_ref=case_ref,
        min_confidence=min_confidence,
        exclude_relation_types=exclude_relation_types,
        include_relation_types=include_relation_types,
    )
    paths = shortest_path(
        backend,
        source_id=source_id,
        target_id=target_id,
        edge_filter=edge_filter,
        k=max(1, min(10, int(k))),
        cutoff_hops=cutoff_hops,
    )
    return {
        "source": {"query": source, "entity_id": source_id},
        "target": {"query": target, "entity_id": target_id},
        "paths": paths,
        "path_count": len(paths),
        "constraints": {
            "min_confidence": edge_filter.min_confidence,
            "exclude_relation_types": list(edge_filter.exclude_relation_types),
            "include_relation_types": list(edge_filter.include_relation_types),
            "case_ref": case_ref,
            "cutoff_hops": cutoff_hops,
        },
    }


@router.get("/expand")
def expand(
    entity_id: str,
    limit: int = 30,
    min_confidence: float = 0.0,
    exclude_relation_types: str | None = None,
    include_relation_types: str | None = None,
    case_ref: str | None = None,
) -> dict[str, object]:
    """One-hop expansion around a node. Accepts entity_id OR a name/alias."""
    con = get_duck_connection(settings.duckdb_path)
    backend = DuckDBGraphBackend(con)
    resolved = backend.resolve_entity_id(entity_id)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity_id}")

    edge_filter = _edge_filter_from_query(
        con,
        case_ref=case_ref,
        min_confidence=min_confidence,
        exclude_relation_types=exclude_relation_types,
        include_relation_types=include_relation_types,
    )
    bundle = expand_neighbourhood(
        backend,
        entity_id=resolved,
        edge_filter=edge_filter,
        limit=max(1, min(200, int(limit))),
    )
    return bundle


@router.get("/centrality")
def centrality(
    metric: str = "degree",
    top_n: int = 20,
    min_confidence: float = 0.0,
    exclude_relation_types: str | None = None,
    include_relation_types: str | None = None,
    case_ref: str | None = None,
) -> dict[str, object]:
    """Rank entities by degree / betweenness / pagerank centrality."""
    con = get_duck_connection(settings.duckdb_path)
    backend = DuckDBGraphBackend(con)
    edge_filter = _edge_filter_from_query(
        con,
        case_ref=case_ref,
        min_confidence=min_confidence,
        exclude_relation_types=exclude_relation_types,
        include_relation_types=include_relation_types,
    )
    return centrality_report(
        backend,
        edge_filter=edge_filter,
        metric=metric,
        top_n=max(1, min(200, int(top_n))),
    )


@router.get("/communities")
def communities(
    min_confidence: float = 0.0,
    resolution: float = 1.0,
    exclude_relation_types: str | None = None,
    include_relation_types: str | None = None,
    case_ref: str | None = None,
) -> dict[str, object]:
    """Louvain community detection. `resolution`>1 fragments more aggressively."""
    con = get_duck_connection(settings.duckdb_path)
    backend = DuckDBGraphBackend(con)
    edge_filter = _edge_filter_from_query(
        con,
        case_ref=case_ref,
        min_confidence=min_confidence,
        exclude_relation_types=exclude_relation_types,
        include_relation_types=include_relation_types,
    )
    return detect_communities(
        backend,
        edge_filter=edge_filter,
        resolution=max(0.1, min(5.0, float(resolution))),
    )
