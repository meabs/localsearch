from __future__ import annotations

import uuid

from operation_lens_v2.api.routes.audit import bulk_entity_action
from operation_lens_v2.api.schemas import AuditBulkEntityRequest
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import create_case, init_db, upsert_document


def test_bulk_confirm_and_reject(tmp_path, monkeypatch):
    db_path = tmp_path / "bulk.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    case_id = create_case(con, case_ref="OP_BULK", case_name="Bulk Case")
    doc_id = str(uuid.uuid4())
    upsert_document(
        con,
        doc_id=doc_id,
        filename="bulk.pdf",
        filepath="/tmp/bulk.pdf",
        page_count=1,
        ocr_used=False,
        case_id=case_id,
    )

    entity_ids = []
    for i in range(4):
        entity_id = str(uuid.uuid4())
        con.execute(
            "INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc, confidence) VALUES (?, ?, 'PERSON', ?, 0.3)",
            [entity_id, f"Entity {i}", doc_id],
        )
        entity_ids.append(entity_id)

    confirm_payload = AuditBulkEntityRequest(action="confirm", entity_ids=entity_ids[:2])
    confirm_result = bulk_entity_action(confirm_payload)
    assert confirm_result["updated"] == 2
    row = con.execute("SELECT confidence FROM entities WHERE entity_id = ?", [entity_ids[0]]).fetchone()
    assert row[0] == 1.0

    reject_payload = AuditBulkEntityRequest(action="reject", entity_ids=entity_ids[2:])
    reject_result = bulk_entity_action(reject_payload)
    assert reject_result["updated"] == 2
    row = con.execute("SELECT count(*) FROM entities WHERE entity_id IN (?, ?)", [entity_ids[2], entity_ids[3]]).fetchone()
    assert row[0] == 0
