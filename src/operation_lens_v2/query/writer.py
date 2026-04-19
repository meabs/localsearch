"""Briefing writer that composes a narrative briefing from an InvestigationReport."""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from operation_lens_v2.config import Settings, settings as default_settings
from operation_lens_v2.query.prompts import WRITER_SYSTEM_PROMPT
from operation_lens_v2.query.tool_schemas import InvestigationReport

logger = logging.getLogger(__name__)


async def compose_briefing(
    report: InvestigationReport,
    query: str,
    *,
    settings: Settings = default_settings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Render the final plain-text briefing.

    If the writer model returns something malformed or too thin, we fall back to a
    deterministic renderer that preserves the intended narrative structure.
    """
    prompt = _build_prompt(report, query)
    owns_client = client is None
    http = client or httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=settings.ollama_timeout,
    )
    try:
        response = await http.post(
            "/api/generate",
            json={
                "model": settings.writer_model,
                "prompt": prompt,
                "system": WRITER_SYSTEM_PROMPT,
                "stream": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
        text = str(payload.get("response", "")).strip()
        if text and (
            _looks_like_briefing(text) or not _looks_like_partial_briefing(text)
        ):
            return text
        logger.warning("Writer returned an empty or malformed briefing; using fallback")
    except Exception as exc:
        logger.warning("Writer model call failed: %s", exc)
    finally:
        if owns_client:
            await http.aclose()

    return deterministic_briefing(report)


def _build_prompt(report: InvestigationReport, query: str) -> str:
    return (
        f"ORIGINAL QUERY\n{query}\n\n"
        f"INVESTIGATION REPORT (JSON)\n{report.model_dump_json(indent=2)}\n\n"
        "Compose the briefing now, following the section contract exactly."
    )


def deterministic_briefing(report: InvestigationReport) -> str:
    """Plain-text fallback when the writer model is unavailable or undershoots."""
    lines: list[str] = []

    lines.append("ASSESSMENT")
    lines.append(_render_assessment(report))
    lines.append("")

    lines.append("KEY FINDINGS")
    if report.key_facts:
        for fact in report.key_facts[:6]:
            cite = fact.citation
            cite_str = f"[{cite.doc_id}, p.{cite.page}]"
            lines.append(f"- {fact.subject} {fact.predicate} {fact.object} {cite_str}")
    else:
        lines.append("- No supported findings.")
    lines.append("")

    if _spans_multiple_docs(report) and report.paths:
        lines.append("CROSS-DOCUMENT LINKS")
        for path in report.paths[:5]:
            summary = " -> ".join(
                f"{edge.source_name} {edge.relation_type} {edge.target_name}"
                for edge in path.edges
            )
            cite = path.citations[0] if path.citations else None
            cite_str = f"[{cite.doc_id}, p.{cite.page}]" if cite else "[uncited]"
            lines.append(f"- {summary} {cite_str}")
        lines.append("")

    if report.timeline:
        lines.append("TIMELINE")
        for event in report.timeline[:8]:
            cite = event.citation()
            cite_str = f"[{cite.doc_id}, p.{cite.page}]" if cite else "[uncited]"
            lines.append(f"- {event.event_time}: {event.text} {cite_str}")
        lines.append("")

    lines.append("CONFIDENCE POSTURE")
    lines.append(_render_confidence_posture(report))
    lines.append("")

    lines.append("EVIDENCE GAPS")
    if report.gaps:
        lines.extend(f"- {gap}" for gap in report.gaps)
    else:
        lines.append("- None reported.")
    lines.append("")

    lines.append("SUGGESTED NEXT ACTIONS")
    if report.next_actions:
        lines.extend(f"- {action}" for action in report.next_actions)
    else:
        lines.append("- None reported.")

    return "\n".join(lines).rstrip() + "\n"


def _looks_like_briefing(text: str) -> bool:
    upper = text.upper()
    required_headers = ("ASSESSMENT", "KEY FINDINGS", "CONFIDENCE POSTURE", "EVIDENCE GAPS")
    return all(header in upper for header in required_headers)


def _looks_like_partial_briefing(text: str) -> bool:
    upper = text.upper()
    known_headers = (
        "ASSESSMENT",
        "KEY FINDINGS",
        "CROSS-DOCUMENT LINKS",
        "TIMELINE",
        "CONFIDENCE POSTURE",
        "EVIDENCE GAPS",
        "SUGGESTED NEXT ACTIONS",
    )
    present = [header for header in known_headers if header in upper]
    return 0 < len(present) < 4


def _render_assessment(report: InvestigationReport) -> str:
    if report.hypothesis:
        return report.hypothesis

    if not report.key_facts:
        if report.confidence == "LOW":
            return (
                "Insufficient evidence is available to form a stable analytical narrative. "
                "The current picture is circumstantial and does not yet support a firm assessment."
            )
        return "The available evidence is too thin to support a reliable investigative narrative."

    subjects = _unique_ordered(fact.subject for fact in report.key_facts)
    objects = _unique_ordered(fact.object for fact in report.key_facts)
    subject_text = ", ".join(subjects[:2]) or "The named subject"
    object_text = ", ".join(objects[:3]) or "the indexed evidence"
    connective_sentence = (
        "The strongest connective tissue comes from repeated co-mentions and relationship edges across multiple cited documents."
        if _spans_multiple_docs(report)
        else "The current picture is assembled from a constrained set of in-scope references rather than broad corpus coverage."
    )
    confidence_sentence = (
        "The picture remains circumstantial and should be treated as a working hypothesis."
        if report.confidence == "LOW"
        else (
            "The evidence is mixed but directionally consistent across the retrieved material."
            if report.confidence == "MEDIUM"
            else "Multiple cited facts line up into a coherent account."
        )
    )
    return (
        f"{subject_text} appear most consistently connected to {object_text}. "
        f"{connective_sentence} {confidence_sentence}"
    )


def _render_confidence_posture(report: InvestigationReport) -> str:
    fact_count = len(report.key_facts)
    path_count = len(report.paths)
    timeline_count = len(report.timeline)
    if report.confidence == "LOW":
        return (
            f"The reporting posture is LOW confidence: {fact_count} cited fact(s), "
            f"{path_count} relationship path(s), and {timeline_count} dated event(s) were gathered. "
            "The direction of travel is useful, but the evidence remains incomplete and could shift with broader retrieval."
        )
    if report.confidence == "HIGH":
        return (
            f"The reporting posture is HIGH confidence: {fact_count} cited fact(s) align with "
            f"{path_count} corroborating path(s) and {timeline_count} dated event(s). The main residual risk is omission of unseen material rather than contradiction inside the retrieved evidence."
        )
    return (
        f"The reporting posture is MEDIUM confidence: {fact_count} cited fact(s) support the core narrative, "
        f"while {path_count} relationship path(s) and {timeline_count} dated event(s) add context. "
        "The account is plausible, but it still depends on partial corpus coverage and extracted relationships."
    )


def _spans_multiple_docs(report: InvestigationReport) -> bool:
    docs: set[str] = set()
    for fact in report.key_facts:
        docs.add(fact.citation.doc_id)
    for path in report.paths:
        for cite in path.citations:
            docs.add(cite.doc_id)
    return len(docs) >= 2


def _unique_ordered(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
