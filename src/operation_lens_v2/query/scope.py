"""Investigation scope — restricts agent tools to a document, a case, or the full corpus.

Scope enforcement happens at the tool layer (SQL filters), not the prompt. An agent that
tries to read outside its scope receives a ScopeViolation, which PydanticAI surfaces back
to the model as a tool error — so the model cannot silently escape the scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InvestigationScope(str, Enum):
    """Where the investigator is allowed to look."""

    DOCUMENT = "document"
    CASE = "case"
    CORPUS = "corpus"


class ScopeViolation(Exception):
    """Raised when a tool is asked to return evidence outside the active scope."""


@dataclass(frozen=True)
class ScopeContext:
    """Runtime description of the investigation's allowed evidence surface."""

    mode: InvestigationScope
    doc_ids: tuple[str, ...] = field(default_factory=tuple)
    case_scope: str | None = None

    def describe(self) -> str:
        """Human-readable scope label for system prompts."""
        if self.mode is InvestigationScope.DOCUMENT:
            return f"DOCUMENT ({', '.join(self.doc_ids) or 'unknown'})"
        if self.mode is InvestigationScope.CASE:
            return f"CASE ({self.case_scope or 'unknown'})"
        return "CORPUS (all documents)"

    def allows_doc(self, doc_id: str | None) -> bool:
        """Check whether a given doc_id is within scope. CORPUS/CASE scope defers to
        tool-level SQL filters; only DOCUMENT scope requires an explicit doc_id check."""
        if doc_id is None:
            return self.mode is not InvestigationScope.DOCUMENT
        if self.mode is InvestigationScope.DOCUMENT:
            return doc_id in self.doc_ids
        return True


def build_scope(
    mode: str | InvestigationScope,
    *,
    doc_id: str | None = None,
    doc_ids: list[str] | tuple[str, ...] | None = None,
    case_scope: str | None = None,
) -> ScopeContext:
    """Construct a validated ScopeContext.

    DOCUMENT mode requires at least one doc_id. CASE mode requires a case_scope.
    CORPUS mode accepts neither — extras are ignored so callers can pass through
    request bodies without branching.
    """
    resolved_mode = InvestigationScope(mode) if isinstance(mode, str) else mode

    collected_ids: tuple[str, ...] = ()
    if doc_ids:
        collected_ids = tuple(doc_ids)
    elif doc_id:
        collected_ids = (doc_id,)

    if resolved_mode is InvestigationScope.DOCUMENT:
        if not collected_ids:
            raise ValueError("DOCUMENT scope requires at least one doc_id")
        return ScopeContext(mode=resolved_mode, doc_ids=collected_ids)

    if resolved_mode is InvestigationScope.CASE:
        if not case_scope:
            raise ValueError("CASE scope requires case_scope")
        return ScopeContext(mode=resolved_mode, case_scope=case_scope)

    return ScopeContext(mode=resolved_mode)
