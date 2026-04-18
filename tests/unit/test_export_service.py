from __future__ import annotations

import uuid

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import init_db
from operation_lens_v2.services.export_service import build_case_report, export_case_briefing


def _seed_case(con) -> str:
    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    entity_a = str(uuid.uuid4())
    entity_b = str(uuid.uuid4())
    rel_id = str(uuid.uuid4())

    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name, domain_pack) VALUES (?, ?, ?, ?)",
        [case_id, "OP_EXPORT", "Export Case", "investigations"],
    )
    con.execute(
        """
        INSERT INTO documents (doc_id, case_id, filename, filepath, page_count, source_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [doc_id, case_id, "report.txt", "/tmp/report.txt", 1, "txt"],
    )
    con.execute(
        """
        INSERT INTO chunks (chunk_id, doc_id, page, chunk_index, text, source_label, provenance_type, token_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            chunk_id,
            doc_id,
            1,
            0,
            "2026-04-18 Lena Hart met Jonah Vale at South Quay Locker 14.",
            "body",
            "native_text",
            12,
        ],
    )
    con.execute(
        "INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc) VALUES (?, ?, ?, ?)",
        [entity_a, "Lena Hart", "PERSON", doc_id],
    )
    con.execute(
        "INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc) VALUES (?, ?, ?, ?)",
        [entity_b, "South Quay Locker 14", "LOCATION", doc_id],
    )
    con.execute(
        "INSERT INTO entity_aliases (alias_id, entity_id, alias_text, source_doc, source_chunk) VALUES (?, ?, ?, ?, ?)",
        [str(uuid.uuid4()), entity_a, "Lena Hart", doc_id, chunk_id],
    )
    con.execute(
        "INSERT INTO entity_aliases (alias_id, entity_id, alias_text, source_doc, source_chunk) VALUES (?, ?, ?, ?, ?)",
        [str(uuid.uuid4()), entity_b, "South Quay Locker 14", doc_id, chunk_id],
    )
    con.execute(
        """
        INSERT INTO relationships (rel_id, source_entity, target_entity, relation_type, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        [rel_id, entity_a, entity_b, "OBSERVED_AT", 0.91],
    )
    con.execute(
        """
        INSERT INTO relationship_evidence (
          evidence_id, rel_id, chunk_id, doc_id, page, span_start, span_end, span_text, extraction_method
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(uuid.uuid4()),
            rel_id,
            chunk_id,
            doc_id,
            1,
            0,
            56,
            "Lena Hart met Jonah Vale at South Quay Locker 14.",
            "pattern",
        ],
    )
    return case_id


def test_build_case_report_and_export(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "export.duckdb"
    export_root = tmp_path / "exports"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    monkeypatch.setattr(settings, "export_root", str(export_root))
    _seed_case(con)

    report = build_case_report("OP_EXPORT")
    result = export_case_briefing("OP_EXPORT", format="md")
    html_result = export_case_briefing("OP_EXPORT", format="html")

    assert report["case_name"] == "Export Case"
    assert any("Lena Hart" in item for item in report["executive_summary"] + [citation["span_text"] for citation in report["appendix_citations"]])
    assert result.file_path.endswith(".md")
    assert html_result.file_path.endswith(".html")
    assert export_root.exists()
