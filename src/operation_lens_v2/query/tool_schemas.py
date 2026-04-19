"""Typed I/O models for the investigator agent's tools.

These models are the contract between tools (query/tools.py) and the PydanticAI agent
(query/investigator.py). Every evidence-carrying model exposes a .citation() so the
writer can compose a cite-every-claim briefing without re-plumbing provenance.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Provenance pointer for a single span of evidence."""

    doc_id: str
    doc_name: str | None = None
    page: int
    chunk_id: str | None = None
    span_text: str | None = None


class CitedModel(BaseModel):
    """Base for any tool return that carries provenance fields inline."""

    doc_id: str | None = None
    doc_name: str | None = None
    page: int | None = None
    chunk_id: str | None = None

    def citation(self) -> Citation | None:
        """Return a Citation if this row has a resolvable doc_id/page pair."""
        if not self.doc_id or self.page is None:
            return None
        return Citation(
            doc_id=self.doc_id,
            doc_name=self.doc_name,
            page=self.page,
            chunk_id=self.chunk_id,
        )


# ── Tool return types ─────────────────────────────────────────────────────────


class EntityHit(CitedModel):
    """A single entity surfaced by search_entities or get_document_entities."""

    entity_id: str
    canonical_name: str
    entity_type: str
    mention_count: int = 0
    doc_count: int = 0
    aliases: list[str] = Field(default_factory=list)


class EntityProfile(BaseModel):
    """Aggregate profile returned by get_entity_profile."""

    entity_id: str
    canonical_name: str
    entity_type: str
    mention_count: int
    doc_count: int
    first_seen_doc: str | None = None
    first_seen_page: int | None = None
    last_seen_doc: str | None = None
    last_seen_page: int | None = None
    top_aliases: list[str] = Field(default_factory=list)
    recent_citations: list[Citation] = Field(default_factory=list)


class RelationshipEdge(CitedModel):
    """A relationship edge plus one supporting span."""

    rel_id: str
    source_entity_id: str
    source_name: str
    source_type: str
    target_entity_id: str
    target_name: str
    target_type: str
    relation_type: str
    confidence: float
    span_text: str | None = None
    hop: int = 1


class CooccurrenceHit(CitedModel):
    """A chunk in which two entities are both mentioned."""

    chunk_id: str
    entity_a_id: str
    entity_b_id: str
    text: str


class DatedEvent(CitedModel):
    """A dated event — date entity plus surrounding chunk context."""

    event_time: str  # ISO-like string; we keep it textual to avoid lossy parsing
    entity_ids: list[str] = Field(default_factory=list)
    text: str


class ChunkText(BaseModel):
    """The full text of a chunk, used for precise quoting."""

    chunk_id: str
    doc_id: str
    doc_name: str | None = None
    page: int
    text: str


class DocumentSummary(BaseModel):
    """One document's footprint in the current scope."""

    doc_id: str
    doc_name: str
    case_scope: str | None = None
    page_count: int | None = None
    chunk_count: int = 0
    entity_count: int = 0


class GraphPath(BaseModel):
    """An evidence-backed path between two entities."""

    source_entity_id: str
    target_entity_id: str
    hops: int
    edges: list[RelationshipEdge]
    citations: list[Citation] = Field(default_factory=list)


# ── Final investigator report ────────────────────────────────────────────────


class AtomicFact(BaseModel):
    """A single supported claim with provenance. Fed to the writer and claim validator."""

    subject: str
    predicate: str
    object: str
    when: str | None = None
    where: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    citation: Citation


class InvestigationReport(BaseModel):
    """The investigator's structured output before writer composition."""

    hypothesis: str
    key_facts: list[AtomicFact] = Field(default_factory=list)
    paths: list[GraphPath] = Field(default_factory=list)
    timeline: list[DatedEvent] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    evidence_citations: list[Citation] = Field(default_factory=list)
