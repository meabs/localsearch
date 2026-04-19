from __future__ import annotations

import uuid

from operation_lens_v2.api.routes.entities import entity_cases
from operation_lens_v2.api.routes.graph import entity, network
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
INSERT_ALIAS_SQL = """
    INSERT INTO entity_aliases (alias_id, entity_id, alias_text, source_doc, source_chunk)
    VALUES (?, ?, ?, ?, ?)
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


def test_graph_entity_returns_summary_and_doc_count(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "graph-entity.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    case_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    entity_id = str(uuid.uuid4())
    con.execute(
        "INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)",
        [case_id, "OP_GRAPH", "Graph Case"],
    )
    con.execute(
        """
        INSERT INTO documents (doc_id, case_id, filename, filepath, page_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        [doc_id, case_id, "graph-note.pdf", "/tmp/graph-note.pdf", 1],
    )
    con.execute(
        INSERT_ENTITY_SQL,
        [entity_id, "Marcus Webb", "PERSON", doc_id],
    )
    con.execute(
        INSERT_CHUNK_SQL,
        [chunk_id, doc_id, 1, 0, "Marcus Webb was seen at the site.", 7],
    )
    con.execute(
        INSERT_ALIAS_SQL,
        [str(uuid.uuid4()), entity_id, "M Webb", doc_id, chunk_id],
    )

    result = entity(entity_id)

    assert result["entity"]["canonical_name"] == "Marcus Webb"
    assert result["entity"]["entity_type"] == "PERSON"
    assert result["doc_count"] == 1
    assert result["aliases"] == ["M Webb"]


def test_entity_cases_returns_distinct_case_ids(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "graph-cases.duckdb"
    con = init_db(str(db_path))
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))

    entity_id = str(uuid.uuid4())
    case_a = str(uuid.uuid4())
    case_b = str(uuid.uuid4())
    doc_a = str(uuid.uuid4())
    doc_b = str(uuid.uuid4())
    chunk_a = str(uuid.uuid4())
    chunk_b = str(uuid.uuid4())

    con.execute("INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)", [case_a, "OP_A", "Case A"])
    con.execute("INSERT INTO cases (case_id, case_ref, case_name) VALUES (?, ?, ?)", [case_b, "OP_B", "Case B"])
    con.execute(
        "INSERT INTO documents (doc_id, case_id, filename, filepath, page_count) VALUES (?, ?, ?, ?, ?)",
        [doc_a, case_a, "case-a.pdf", "/tmp/case-a.pdf", 1],
    )
    con.execute(
        "INSERT INTO documents (doc_id, case_id, filename, filepath, page_count) VALUES (?, ?, ?, ?, ?)",
        [doc_b, case_b, "case-b.pdf", "/tmp/case-b.pdf", 1],
    )
    con.execute(INSERT_CHUNK_SQL, [chunk_a, doc_a, 1, 0, "Marcus Webb was referenced in case A.", 7])
    con.execute(INSERT_CHUNK_SQL, [chunk_b, doc_b, 1, 0, "Marcus Webb was referenced in case B.", 7])
    con.execute(INSERT_ENTITY_SQL, [entity_id, "Marcus Webb", "PERSON", doc_a])
    con.execute(
        INSERT_ALIAS_SQL,
        [str(uuid.uuid4()), entity_id, "Marcus Webb", doc_a, chunk_a],
    )
    con.execute(
        INSERT_ALIAS_SQL,
        [str(uuid.uuid4()), entity_id, "M Webb", doc_b, chunk_b],
    )

    result = entity_cases(entity_id)

    assert result["case_ids"] == [case_a, case_b]
    assert [case["case_ref"] for case in result["cases"]] == ["OP_A", "OP_B"]
