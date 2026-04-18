from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import get_case_by_ref, init_db


def _safe_case_name(case_ref: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in case_ref)


def _ts_slug() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _timeline_events(con, *, case_id: str, limit: int = 8) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT c.doc_id, d.filename, c.page, c.text
        FROM chunks c
        JOIN documents d ON d.doc_id = c.doc_id
        WHERE d.case_id = ?
          AND regexp_matches(
            c.text,
            '\\b(\\d{4}-\\d{2}-\\d{2}|\\d{1,2}[/-]\\d{1,2}[/-]\\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+\\d{1,2},?\\s+\\d{4})\\b',
            'i'
          )
        ORDER BY d.ingested_at, c.page, c.chunk_index
        LIMIT ?
        """,
        [case_id, limit],
    ).fetchall()
    return [
        {
            "doc_id": row[0],
            "filename": row[1],
            "page": row[2],
            "excerpt": (row[3] or "")[:260],
        }
        for row in rows
    ]


def _case_entities(con, *, case_id: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
          e.entity_id,
          e.canonical_name,
          e.entity_type,
          count(*) AS mention_rows,
          count(DISTINCT d.doc_id) AS doc_count
        FROM entities e
        JOIN entity_aliases ea ON ea.entity_id = e.entity_id
        JOIN documents d ON d.doc_id = ea.source_doc
        WHERE d.case_id = ?
        GROUP BY e.entity_id, e.canonical_name, e.entity_type
        ORDER BY mention_rows DESC, doc_count DESC, e.canonical_name
        LIMIT ?
        """,
        [case_id, limit],
    ).fetchall()
    return [
        {
            "entity_id": row[0],
            "canonical_name": row[1],
            "entity_type": row[2],
            "mention_count": int(row[3]),
            "doc_count": int(row[4]),
        }
        for row in rows
    ]


def _case_locations(con, *, case_id: str, limit: int = 8) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
          e.canonical_name,
          count(*) AS mention_rows,
          count(DISTINCT d.doc_id) AS doc_count
        FROM entities e
        JOIN entity_aliases ea ON ea.entity_id = e.entity_id
        JOIN documents d ON d.doc_id = ea.source_doc
        WHERE d.case_id = ? AND upper(e.entity_type) = 'LOCATION'
        GROUP BY e.canonical_name
        ORDER BY mention_rows DESC, doc_count DESC, e.canonical_name
        LIMIT ?
        """,
        [case_id, limit],
    ).fetchall()
    return [
        {
            "location": row[0],
            "mention_count": int(row[1]),
            "doc_count": int(row[2]),
        }
        for row in rows
    ]


def _case_relationships(con, *, case_id: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
          r.rel_id,
          src.canonical_name,
          src.entity_type,
          r.relation_type,
          tgt.canonical_name,
          tgt.entity_type,
          r.confidence,
          count(re.evidence_id) AS evidence_count
        FROM relationships r
        JOIN relationship_evidence re ON re.rel_id = r.rel_id
        JOIN documents d ON d.doc_id = re.doc_id
        JOIN entities src ON src.entity_id = r.source_entity
        JOIN entities tgt ON tgt.entity_id = r.target_entity
        WHERE d.case_id = ?
        GROUP BY
          r.rel_id, src.canonical_name, src.entity_type,
          r.relation_type, tgt.canonical_name, tgt.entity_type, r.confidence
        ORDER BY evidence_count DESC, r.confidence DESC
        LIMIT ?
        """,
        [case_id, limit],
    ).fetchall()
    relationships: list[dict[str, Any]] = []
    for row in rows:
        citation_rows = con.execute(
            """
            SELECT d.filename, re.page, re.span_text
            FROM relationship_evidence re
            JOIN documents d ON d.doc_id = re.doc_id
            WHERE re.rel_id = ?
            ORDER BY re.page, re.evidence_id
            LIMIT 3
            """,
            [str(row[0])],
        ).fetchall()
        citations = [
            {
                "doc_name": item[0],
                "page": item[1] if item[1] is not None else "?",
                "span_text": item[2] or "",
            }
            for item in citation_rows
        ]
        relationships.append(
            {
                "rel_id": row[0],
                "source_name": row[1],
                "source_type": row[2],
                "relation_type": row[3],
                "target_name": row[4],
                "target_type": row[5],
                "confidence": float(row[6] or 0.0),
                "evidence_count": int(row[7] or 0),
                "citations": citations,
            }
        )
    return relationships


def _case_documents(con, *, case_id: str, limit: int = 12) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT doc_id, filename, source_type, page_count, ingested_at
        FROM documents
        WHERE case_id = ?
        ORDER BY ingested_at, filename
        LIMIT ?
        """,
        [case_id, limit],
    ).fetchall()
    return [
        {
            "doc_id": row[0],
            "filename": row[1],
            "source_type": row[2],
            "page_count": int(row[3] or 0),
            "ingested_at": str(row[4]) if row[4] is not None else None,
        }
        for row in rows
    ]


def _render_md(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['case_name']} ({report['case_ref']})",
        "",
        "## Executive Summary",
    ]
    lines.extend(f"- {item}" for item in report["executive_summary"])
    lines.extend(["", "## Key Entities"])
    lines.extend(
        f"- {item['canonical_name']} ({item['entity_type']}) - mentions {item['mention_count']}, docs {item['doc_count']}"
        for item in report["key_entities"]
    )
    lines.extend(["", "## Key Relationships"])
    for rel in report["key_relationships"]:
        lines.append(
            f"- {rel['source_name']} [{rel['source_type']}] {rel['relation_type']} {rel['target_name']} [{rel['target_type']}] "
            f"(confidence {rel['confidence']:.2f}, evidence {rel['evidence_count']})"
        )
    lines.extend(["", "## Timeline Summary"])
    lines.extend(
        f"- {event['filename']} p.{event['page']}: {event['excerpt']}"
        for event in report["timeline_summary"]
    )
    lines.extend(["", "## Notable Locations"])
    lines.extend(
        f"- {item['location']} - mentions {item['mention_count']}, docs {item['doc_count']}"
        for item in report["notable_locations"]
    )
    lines.extend(
        [
            "",
            "## Analyst Notes",
            "- Add analyst judgement, caveats, and next investigative steps here.",
            "",
            "## Appendix: Cited Evidence",
        ]
    )
    for citation in report["appendix_citations"]:
        lines.append(
            f"- {citation['doc_name']} p.{citation['page']}: {citation['span_text']}"
        )
    return "\n".join(lines) + "\n"


def _render_html(report: dict[str, Any]) -> str:
    def items(values: list[str]) -> str:
        return "".join(f"<li>{html.escape(value)}</li>" for value in values)

    entity_items = items(
        [
            f"{item['canonical_name']} ({item['entity_type']}) - mentions {item['mention_count']}, docs {item['doc_count']}"
            for item in report["key_entities"]
        ]
    )
    rel_items = items(
        [
            f"{rel['source_name']} [{rel['source_type']}] {rel['relation_type']} {rel['target_name']} [{rel['target_type']}] "
            f"(confidence {rel['confidence']:.2f}, evidence {rel['evidence_count']})"
            for rel in report["key_relationships"]
        ]
    )
    timeline_items = items(
        [f"{event['filename']} p.{event['page']}: {event['excerpt']}" for event in report["timeline_summary"]]
    )
    location_items = items(
        [f"{item['location']} - mentions {item['mention_count']}, docs {item['doc_count']}" for item in report["notable_locations"]]
    )
    citation_items = items(
        [f"{item['doc_name']} p.{item['page']}: {item['span_text']}" for item in report["appendix_citations"]]
    )
    summary_items = items(report["executive_summary"])

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(report['case_name'])}</title>
  <style>
    body {{ font-family: Georgia, serif; margin: 2rem auto; max-width: 920px; line-height: 1.6; color: #18222d; }}
    h1, h2 {{ font-family: Arial, sans-serif; }}
    .meta {{ color: #52606d; margin-bottom: 2rem; }}
    section {{ margin-bottom: 1.8rem; }}
    li {{ margin-bottom: 0.45rem; }}
  </style>
</head>
<body>
  <h1>{html.escape(report['case_name'])}</h1>
  <div class="meta">Case reference: {html.escape(report['case_ref'])} | Domain pack: {html.escape(report['domain_pack'])}</div>
  <section><h2>Executive Summary</h2><ul>{summary_items}</ul></section>
  <section><h2>Key Entities</h2><ul>{entity_items}</ul></section>
  <section><h2>Key Relationships</h2><ul>{rel_items}</ul></section>
  <section><h2>Timeline Summary</h2><ul>{timeline_items}</ul></section>
  <section><h2>Notable Locations</h2><ul>{location_items}</ul></section>
  <section><h2>Analyst Notes</h2><ul><li>Add analyst judgement, caveats, and next investigative steps here.</li></ul></section>
  <section><h2>Appendix: Cited Evidence</h2><ul>{citation_items}</ul></section>
</body>
</html>
"""


def _render_pdf(report: dict[str, Any], output_path: Path) -> None:
    from fpdf import FPDF  # type: ignore[import]

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.multi_cell(0, 10, f"{report['case_name']} ({report['case_ref']})")
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 8, f"Domain pack: {report['domain_pack']}")

    sections = [
        ("Executive Summary", report["executive_summary"]),
        (
            "Key Entities",
            [
                f"{item['canonical_name']} ({item['entity_type']}) - mentions {item['mention_count']}, docs {item['doc_count']}"
                for item in report["key_entities"]
            ],
        ),
        (
            "Key Relationships",
            [
                f"{rel['source_name']} [{rel['source_type']}] {rel['relation_type']} {rel['target_name']} [{rel['target_type']}] "
                f"(confidence {rel['confidence']:.2f}, evidence {rel['evidence_count']})"
                for rel in report["key_relationships"]
            ],
        ),
        (
            "Timeline Summary",
            [f"{event['filename']} p.{event['page']}: {event['excerpt']}" for event in report["timeline_summary"]],
        ),
        (
            "Notable Locations",
            [f"{item['location']} - mentions {item['mention_count']}, docs {item['doc_count']}" for item in report["notable_locations"]],
        ),
        ("Analyst Notes", ["Add analyst judgement, caveats, and next investigative steps here."]),
        (
            "Appendix: Cited Evidence",
            [f"{item['doc_name']} p.{item['page']}: {item['span_text']}" for item in report["appendix_citations"]],
        ),
    ]
    for title, values in sections:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        for value in values or ["None recorded."]:
            pdf.multi_cell(0, 7, f"- {value}")
    pdf.output(str(output_path))


@dataclass(slots=True)
class ExportResult:
    case_ref: str
    format: str
    file_path: str
    download_name: str


def build_case_report(case_ref: str) -> dict[str, Any]:
    con = init_db(settings.duckdb_path)
    case = get_case_by_ref(con, case_ref)
    if not case:
        raise ValueError(f"Unknown case_ref: {case_ref}")

    case_id = str(case["case_id"])
    documents = _case_documents(con, case_id=case_id)
    entities = _case_entities(con, case_id=case_id)
    relationships = _case_relationships(con, case_id=case_id)
    timeline = _timeline_events(con, case_id=case_id)
    locations = _case_locations(con, case_id=case_id)
    appendix_citations = [
        citation
        for rel in relationships
        for citation in rel["citations"]
    ][:20]

    executive_summary = [
        f"{len(documents)} evidence items are currently attached to the case.",
        (
            f"Most prominent entities include {', '.join(item['canonical_name'] for item in entities[:3])}."
            if entities
            else "No entities have been extracted for this case yet."
        ),
        (
            f"Highest-signal relationship: {relationships[0]['source_name']} {relationships[0]['relation_type']} {relationships[0]['target_name']}."
            if relationships
            else "No typed relationships have been extracted for this case yet."
        ),
        (
            f"Timeline evidence spans {len(timeline)} dated passages across the current corpus."
            if timeline
            else "No dated timeline passages were detected in the current case corpus."
        ),
    ]

    return {
        "case_ref": case_ref,
        "case_name": str(case["case_name"]),
        "domain_pack": str(case.get("domain_pack") or "base"),
        "documents": documents,
        "executive_summary": executive_summary,
        "key_entities": entities,
        "key_relationships": relationships,
        "timeline_summary": timeline,
        "notable_locations": locations,
        "appendix_citations": appendix_citations,
    }


def export_case_briefing(case_ref: str, *, format: str) -> ExportResult:
    normalized = format.strip().lower()
    if normalized not in {"md", "html", "pdf"}:
        raise ValueError(f"Unsupported export format: {format}")

    report = build_case_report(case_ref)
    export_dir = settings.export_root_obj / _safe_case_name(case_ref)
    export_dir.mkdir(parents=True, exist_ok=True)
    slug = _ts_slug()
    filename = f"{_safe_case_name(case_ref)}-briefing-{slug}.{normalized}"
    output_path = export_dir / filename

    if normalized == "md":
        output_path.write_text(_render_md(report), encoding="utf-8")
    elif normalized == "html":
        output_path.write_text(_render_html(report), encoding="utf-8")
    else:
        _render_pdf(report, output_path)

    return ExportResult(
        case_ref=case_ref,
        format=normalized,
        file_path=str(output_path.resolve()),
        download_name=filename,
    )
