from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Entity and relation types are open strings: the canonical set lives in
# `config/entity_schema.json` and can be extended without code changes.
EntityType = str
RelationType = str
ClaimStatus = Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED"]


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    doc_id: str
    page: int
    chunk_index: int
    text: str
    token_count: int


@dataclass(slots=True)
class ExtractedEntity:
    text: str
    entity_type: EntityType
    start: int
    end: int
    confidence: float = 1.0


@dataclass(slots=True)
class RelationshipCandidate:
    source: str
    target: str
    relation_type: RelationType
    span_text: str
    span_start: int
    span_end: int
    confidence: float
    extraction_method: Literal["pattern", "llm", "co-occurrence"] = "pattern"
    metadata: dict[str, str] = field(default_factory=dict)
