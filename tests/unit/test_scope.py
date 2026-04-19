"""Tests for investigation scope construction and validation."""

from __future__ import annotations

import pytest

from operation_lens_v2.query.scope import (
    InvestigationScope,
    ScopeContext,
    ScopeViolation,
    build_scope,
)
from operation_lens_v2.query.tool_schemas import (
    AtomicFact,
    Citation,
    EntityHit,
    InvestigationReport,
    RelationshipEdge,
)


def test_build_scope_corpus_is_default_shape():
    scope = build_scope("corpus")
    assert scope.mode is InvestigationScope.CORPUS
    assert scope.doc_ids == ()
    assert scope.case_scope is None


def test_build_scope_document_requires_doc_id():
    with pytest.raises(ValueError):
        build_scope("document")


def test_build_scope_document_accepts_single_doc_id():
    scope = build_scope("document", doc_id="NF-INT-001")
    assert scope.mode is InvestigationScope.DOCUMENT
    assert scope.doc_ids == ("NF-INT-001",)


def test_build_scope_document_accepts_multiple_doc_ids():
    scope = build_scope("document", doc_ids=["a", "b"])
    assert scope.doc_ids == ("a", "b")


def test_build_scope_case_requires_case_scope():
    with pytest.raises(ValueError):
        build_scope("case")


def test_build_scope_case_populates_case_scope():
    scope = build_scope("case", case_scope="OP_NIGHTFALL")
    assert scope.mode is InvestigationScope.CASE
    assert scope.case_scope == "OP_NIGHTFALL"


def test_build_scope_rejects_unknown_mode():
    with pytest.raises(ValueError):
        build_scope("galaxy")


def test_scope_accepts_enum_directly():
    scope = build_scope(InvestigationScope.CORPUS)
    assert scope.mode is InvestigationScope.CORPUS


def test_scope_describe_document_lists_ids():
    scope = build_scope("document", doc_ids=["a", "b"])
    assert "a" in scope.describe()
    assert "b" in scope.describe()


def test_scope_describe_case_shows_case():
    scope = build_scope("case", case_scope="OP_NIGHTFALL")
    assert "OP_NIGHTFALL" in scope.describe()


def test_scope_describe_corpus_mentions_all():
    scope = build_scope("corpus")
    assert "all" in scope.describe().lower()


def test_scope_allows_doc_document_mode_enforces_membership():
    scope = build_scope("document", doc_id="a")
    assert scope.allows_doc("a") is True
    assert scope.allows_doc("b") is False


def test_scope_allows_doc_corpus_admits_any():
    scope = build_scope("corpus")
    assert scope.allows_doc("anything") is True


def test_scope_allows_doc_case_defers_to_sql_filter():
    scope = build_scope("case", case_scope="OP_X")
    assert scope.allows_doc("anything") is True


def test_scope_is_frozen():
    scope = build_scope("corpus")
    with pytest.raises(Exception):
        scope.mode = InvestigationScope.CORPUS  # type: ignore[misc]


def test_scope_violation_is_exception():
    assert issubclass(ScopeViolation, Exception)


# ── Tool schema round-trips ────────────────────────────────────────────────


def test_citation_requires_doc_id_and_page():
    c = Citation(doc_id="NF-INT-001", page=1)
    assert c.doc_id == "NF-INT-001"
    assert c.page == 1


def test_entity_hit_citation_from_cited_model():
    hit = EntityHit(
        entity_id="e1",
        canonical_name="Marcus Webb",
        entity_type="PERSON",
        doc_id="NF-INT-001",
        page=1,
        chunk_id="c1",
    )
    cite = hit.citation()
    assert cite is not None
    assert cite.doc_id == "NF-INT-001"
    assert cite.chunk_id == "c1"


def test_entity_hit_without_provenance_has_no_citation():
    hit = EntityHit(entity_id="e1", canonical_name="x", entity_type="PERSON")
    assert hit.citation() is None


def test_relationship_edge_round_trip():
    edge = RelationshipEdge(
        rel_id="r1",
        source_entity_id="a",
        source_name="A",
        source_type="PERSON",
        target_entity_id="b",
        target_name="B",
        target_type="LOCATION",
        relation_type="OBSERVED_AT",
        confidence=0.9,
        doc_id="d1",
        page=1,
    )
    dumped = edge.model_dump()
    restored = RelationshipEdge.model_validate(dumped)
    assert restored.rel_id == "r1"
    assert restored.citation() is not None


def test_atomic_fact_confidence_bounds():
    with pytest.raises(ValueError):
        AtomicFact(
            subject="a",
            predicate="p",
            object="b",
            confidence=1.5,
            citation=Citation(doc_id="d", page=1),
        )


def test_investigation_report_defaults_are_empty_collections():
    report = InvestigationReport(hypothesis="Insufficient evidence.")
    assert report.key_facts == []
    assert report.paths == []
    assert report.timeline == []
    assert report.gaps == []
    assert report.next_actions == []
    assert report.confidence == "MEDIUM"


def test_investigation_report_json_round_trip():
    report = InvestigationReport(
        hypothesis="Webb and Khalil linked via RX71 KLD.",
        key_facts=[
            AtomicFact(
                subject="Marcus Webb",
                predicate="owns",
                object="RX71 KLD",
                confidence=0.9,
                citation=Citation(doc_id="NF-INT-001", page=1),
            )
        ],
        confidence="MEDIUM",
    )
    restored = InvestigationReport.model_validate_json(report.model_dump_json())
    assert restored.hypothesis == report.hypothesis
    assert restored.key_facts[0].object == "RX71 KLD"
