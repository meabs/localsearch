from __future__ import annotations

import uuid

from operation_lens_v2.api.routes.graph import network
from operation_lens_v2.config import settings
from operation_lens_v2.ingestion.duck_store import init_db

INSERT_ENTITY_SQL = """
    INSERT INTO entities (entity_id, canonical_name, entity_type, first_seen_doc)
    VALUES (?, ?, ?, ?)
"""
INSERT_RELATIONSHIP_SQL = """
    INSERT INTO relationships (
        rel_id, source_entity, target_entity, relation_type, confidence
    ) VALUES (?, ?, ?, ?, ?)
"""
INSERT_DOCUMENT_SQL = """
    INSERT INTO documents (doc_id, case_id, filename, filepath, page_count)
    VALUES (?, ?, ?, ?, ?)
"""
INSERT_CHUNK_SQL = """
    INSERT INTO chunks (chunk_id, doc_id, page, chunk_index, text, token_count)
    VALUES (?, ?, ?, ?, ?, ?)
"""


def test_graph_network_returns_empty_on_entity_miss(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "graph.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    source_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    rel_id = str(uuid.uuid4())

    con.execute(INSERT_ENTITY_SQL, [source_id, "Marcus Webb", "PERSON", None])
    con.execute(INSERT_ENTITY_SQL, [target_id, "14 Arkwright Road", "LOCATION", None])
    con.execute(INSERT_RELATIONSHIP_SQL, [rel_id, source_id, target_id, "OBSERVED_AT", 0.9])

    result = network(entity="Unknown Person")

    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["meta"]["entity_found"] is False


def test_graph_network_returns_edge_citations(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "graph-citations.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    rel_id = str(uuid.uuid4())
    evidence_id = str(uuid.uuid4())

    con.execute(INSERT_DOCUMENT_SQL, [doc_id, None, "intel-note.pdf", "/tmp/intel-note.pdf", 1])
    con.execute(
        INSERT_CHUNK_SQL,
        [chunk_id, doc_id, 3, 0, "Marcus Webb was observed at 14 Arkwright Road.", 8],
    )
    con.execute(INSERT_ENTITY_SQL, [source_id, "Marcus Webb", "PERSON", doc_id])
    con.execute(INSERT_ENTITY_SQL, [target_id, "14 Arkwright Road", "LOCATION", doc_id])
    con.execute(INSERT_RELATIONSHIP_SQL, [rel_id, source_id, target_id, "OBSERVED_AT", 0.9])
    con.execute(
        """
        INSERT INTO relationship_evidence (
          evidence_id, rel_id, chunk_id, doc_id, page,
          span_start, span_end, span_text, extraction_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            evidence_id,
            rel_id,
            chunk_id,
            doc_id,
            3,
            0,
            43,
            "Marcus Webb was observed at 14 Arkwright Road.",
            "pattern",
        ],
    )

    result = network(entity="Marcus Webb")

    assert result["meta"]["entity_found"] is True
    assert result["edges"]
    citation = result["edges"][0]["citations"][0]
    assert citation["doc_name"] == "intel-note.pdf"
    assert citation["page"] == 3
    assert "Marcus Webb was observed" in citation["span_text"]
