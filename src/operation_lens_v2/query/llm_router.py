from __future__ import annotations

import json
import logging
import re
from typing import Any

from operation_lens_v2.config import settings
from operation_lens_v2.query.prompts import (
    DOCUMENT_SUMMARY_SYSTEM_PROMPT,
    FREEFORM_SYSTEM_PROMPT,
    RELATIONSHIP_SYSTEM_PROMPT,
)
from operation_lens_v2.runtime import get_http_client

logger = logging.getLogger(__name__)

RELATION_TEXT = {
    "ASSOCIATED_WITH": "is associated with",
    "OBSERVED_AT": "was observed at",
    "LINKED_TO": "is linked to",
    "MENTIONED_WITH": "was mentioned with",
    "INFERRED_LINK": "is inferentially linked to",
}
UNKNOWN_SOURCE = "Unknown source"
UNKNOWN_TARGET = "Unknown target"
UNKNOWN_DOC = "unknown-doc"
OPENROUTER_CITATION_LIMIT = 3
LOCAL_FINDINGS_LIMIT = 8
OPENROUTER_FINDINGS_LIMIT = 10
EXACT_FINDINGS_LIMIT = 10
RELATIONSHIP_FINDINGS_LIMIT = 8
CHUNK_FINDINGS_LIMIT = 6
DOCUMENT_ANALYSIS_CHUNK_LIMIT = 8
DOCUMENT_ANALYSIS_TEXT_LIMIT = 1400
CHUNK_FALLBACK_GAP = (
    "This briefing was assembled from retrieved document excerpts because no high-confidence "
    "typed relationships were available for the requested scope."
)
RELATIONSHIP_MISSING_MESSAGE = (
    "No strongly supported relationships were found in the current evidence packet."
)
ENTITY_LINKAGE_POSTURE = "Identifier/entity linkage is direct; typed relationships may be absent."
NO_GRAPH_RELATIONSHIP_GAP = "No high-confidence graph relationships met threshold for this query."
DOCUMENT_SUMMARY_GAP = (
    "Document summaries depend on OCR/chunk quality; weak extraction or redactions can hide detail."
)
FALLBACK_POSTURE_LINES = [
    "",
    "Confidence Posture:",
    "- High: confidence >= 0.80",
    "- Medium: 0.60 to 0.79",
    "- Low: < 0.60",
    "",
    "Evidence Gaps:",
    "- Relationships with a single document source need corroboration.",
    "- Mentions without typed relationships are excluded from findings.",
]
DOCUMENT_BOILERPLATE_PHRASES = (
    "official-sensitive",
    "daily intelligence report",
    "analyst:",
    "grading (source)",
    "grading (info)",
    "handling:",
    "5x5x5",
)
DOCUMENT_FINDING_KEYWORDS = (
    "observed",
    "meeting",
    "property",
    "address",
    "vehicle",
    "van",
    "phone",
    "call",
    "contact",
    "crime",
    "report",
    "anonymous",
    "surveillance",
    "identified",
    "linked",
    "used",
    "visited",
    "delivery",
    "cash",
    "account",
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|;\s+")
WHITESPACE_RE = re.compile(r"\s+")


def _aggregate_exact_matches(exact_matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate and merge exact-match results by entity_id."""
    grouped: dict[str, dict[str, Any]] = {}
    for item in exact_matches:
        entity_id = str(item.get("entity_id") or "")
        if not entity_id:
            continue
        if entity_id not in grouped:
            grouped[entity_id] = {
                "entity_id": entity_id,
                "canonical_name": item.get("canonical_name") or item.get("alias_text") or "Unknown",
                "entity_type": item.get("entity_type") or "UNKNOWN",
                "mention_count": int(item.get("mention_count", 0) or 0),
                "aliases": set(),
                "citations": [],
                "doc_set": set(),
                "score": float(item.get("score", 0.0) or 0.0),
            }
        entry = grouped[entity_id]
        alias = item.get("alias_text")
        if alias:
            entry["aliases"].add(str(alias))
        citation = {
            "doc_id": item.get("doc_name") or item.get("doc_id") or UNKNOWN_DOC,
            "page": item.get("page") or "?",
            "span_text": item.get("span_text", ""),
        }
        entry["citations"].append(citation)
        entry["doc_set"].add(citation["doc_id"])
        entry["score"] = max(entry["score"], float(item.get("score", 0.0) or 0.0))

    exact_findings = []
    for value in grouped.values():
        value["aliases"] = sorted(value["aliases"])
        value["doc_count"] = len(value["doc_set"])
        value["evidence_count"] = len(value["citations"])
        value["doc_set"] = sorted(value["doc_set"])
        exact_findings.append(value)

    exact_findings.sort(
        key=lambda item: (
            item["score"],
            item["mention_count"],
            item["doc_count"],
            item["evidence_count"],
        ),
        reverse=True,
    )
    return exact_findings


def _aggregate_relationships(relationships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate and merge typed relationships by (source, target, type)."""
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for relationship in relationships:
        source = relationship.get("source_name") or relationship.get(
            "source_entity", UNKNOWN_SOURCE
        )
        target = relationship.get("target_name") or relationship.get(
            "target_entity", UNKNOWN_TARGET
        )
        relation_type = relationship.get("relation_type", "LINKED_TO")
        group_key = (str(source), str(target), str(relation_type))
        if group_key not in grouped:
            grouped[group_key] = {
                "source": source,
                "target": target,
                "source_type": relationship.get("source_type", "UNKNOWN"),
                "target_type": relationship.get("target_type", "UNKNOWN"),
                "relation_type": relation_type,
                "confidence": float(relationship.get("confidence", 0.0)),
                "citations": [],
                "doc_set": set(),
            }
        entry = grouped[group_key]
        entry["confidence"] = max(entry["confidence"], float(relationship.get("confidence", 0.0)))
        citation = {
            "doc_id": relationship.get("doc_name") or relationship.get("doc_id", UNKNOWN_DOC),
            "page": relationship.get("page", "?"),
            "span_text": relationship.get("span_text", ""),
        }
        entry["citations"].append(citation)
        entry["doc_set"].add(citation["doc_id"])

    findings = []
    for value in grouped.values():
        value["doc_count"] = len(value["doc_set"])
        value["evidence_count"] = len(value["citations"])
        value["doc_set"] = sorted(value["doc_set"])
        findings.append(value)

    findings.sort(
        key=lambda item: (item["confidence"], item["doc_count"], item["evidence_count"]),
        reverse=True,
    )
    return findings


def _redact_openrouter_entity(_name: Any, entity_type: Any) -> str:
    normalized_type = str(entity_type or "UNKNOWN").upper()
    # Cloud payloads must stay structured and redacted: never ship raw names.
    if normalized_type == "PERSON":
        return normalized_type
    return normalized_type


def _openrouter_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"doc_id": citation["doc_id"], "page": citation["page"]}
        for citation in citations[:OPENROUTER_CITATION_LIMIT]
    ]


def _deterministic_relationship_lines(
    evidence_packet: dict[str, Any], relationships: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, Any]]]:
    lines = [
        f"Case Scope: {evidence_packet.get('case_scope', 'ALL_CASES')}",
        "",
        "Key Findings:",
    ]
    claims: list[dict[str, Any]] = []

    for relationship in relationships[:RELATIONSHIP_FINDINGS_LIMIT]:
        source = relationship.get("source", UNKNOWN_SOURCE)
        target = relationship.get("target", UNKNOWN_TARGET)
        relation_type = relationship.get("relation_type", "LINKED_TO")
        relation_phrase = RELATION_TEXT.get(relation_type, f"has relation {relation_type} with")
        claim_text = f"{source} {relation_phrase} {target}"
        citations = relationship.get("citations", [])[:OPENROUTER_CITATION_LIMIT]
        citation_text = ", ".join(f"{c['doc_id']} p.{c['page']}" for c in citations)
        lines.append(
            f"- {claim_text} | confidence {relationship.get('confidence', 0.0):.2f} | "
            f"docs {relationship.get('doc_count', 0)} | "
            f"evidence {relationship.get('evidence_count', 0)} | "
            f"{citation_text}"
        )
        claims.append(
            {
                "text": claim_text,
                "citations": citations,
                "confidence": float(relationship.get("confidence", 0.0)),
            }
        )

    return lines + FALLBACK_POSTURE_LINES, claims


def _chunk_fallback_lines(chunks: list[dict[str, Any]]) -> list[str]:
    lines = ["KEY FINDINGS"]
    seen: set[tuple[str, Any, str]] = set()

    for chunk in chunks[:CHUNK_FINDINGS_LIMIT]:
        text = " ".join(str(chunk.get("text") or "").split())
        if not text:
            continue
        doc_label = str(chunk.get("doc_name") or chunk.get("doc_id") or UNKNOWN_DOC)
        page = chunk.get("page") or "?"
        snippet = text[:260].rstrip()
        if len(text) > 260:
            snippet = f"{snippet}..."
        key = (doc_label, page, snippet)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {snippet} [{doc_label}, p.{page}]")

    if len(lines) == 1:
        lines.append("No retrieved document excerpts were available for this scope.")

    lines.extend(
        [
            "CONFIDENCE POSTURE",
            CHUNK_FALLBACK_GAP,
            "EVIDENCE GAPS",
            "- Findings reflect excerpt-level retrieval and may omit context from unreturned pages.",
            "- Relationship extraction may still be absent even when the document contains useful narrative evidence.",
        ]
    )
    return lines


def _clean_document_text(text: Any) -> str:
    return WHITESPACE_RE.sub(" ", str(text or "")).strip()


def _looks_like_document_boilerplate(sentence: str) -> bool:
    lowered = sentence.lower()
    if any(phrase in lowered for phrase in DOCUMENT_BOILERPLATE_PHRASES):
        return True
    if sentence.count("|") >= 2:
        return True
    letters = [char for char in sentence if char.isalpha()]
    if letters:
        uppercase_ratio = sum(1 for char in letters if char.isupper()) / len(letters)
        if uppercase_ratio > 0.7 and len(sentence) < 220:
            return True
    return False


def _score_document_sentence(sentence: str) -> int:
    lowered = sentence.lower()
    score = 0
    score += sum(2 for keyword in DOCUMENT_FINDING_KEYWORDS if keyword in lowered)
    if re.search(r"\b\d{1,2}[:/.-]\d{1,2}(?:[:/.-]\d{2,4})?\b", sentence):
        score += 2
    if re.search(r"\b\d{1,2}:\d{2}\b", sentence):
        score += 2
    if re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", sentence):
        score += 1
    if re.search(r"\b(?:Road|Street|Rd|Lane|Avenue|Close|Court|Drive|Yard|Dock|Estate|House)\b", sentence):
        score += 1
    if 60 <= len(sentence) <= 220:
        score += 2
    elif len(sentence) > 240:
        score -= 2
    if "redacted" in lowered:
        score -= 1
    return score


def _document_summary_lines(
    chunks: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    seen_sentences: set[str] = set()

    for chunk in chunks:
        cleaned_text = _clean_document_text(chunk.get("text"))
        if not cleaned_text:
            continue
        doc_label = str(chunk.get("doc_name") or chunk.get("doc_id") or UNKNOWN_DOC)
        page = chunk.get("page") or "?"
        for sentence in SENTENCE_SPLIT_RE.split(cleaned_text):
            normalized = _clean_document_text(sentence)
            if len(normalized) < 45:
                continue
            lowered = normalized.lower()
            if lowered in seen_sentences:
                continue
            if _looks_like_document_boilerplate(normalized):
                continue
            score = _score_document_sentence(normalized)
            if score <= 0:
                continue
            seen_sentences.add(lowered)
            candidates.append(
                {
                    "text": normalized,
                    "doc_id": doc_label,
                    "page": page,
                    "score": score,
                    "span_text": cleaned_text,
                }
            )

    candidates.sort(
        key=lambda item: (int(item["score"]), -int(item["page"]) if str(item["page"]).isdigit() else 0),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    seen_pages: set[tuple[str, Any]] = set()
    for candidate in candidates:
        page_key = (str(candidate["doc_id"]), candidate["page"])
        if page_key in seen_pages and len(selected) >= 3:
            continue
        seen_pages.add(page_key)
        selected.append(candidate)
        if len(selected) >= CHUNK_FINDINGS_LIMIT:
            break

    if not selected:
        return _chunk_fallback_lines(chunks), []

    lines = ["KEY FINDINGS"]
    claims: list[dict[str, Any]] = []
    for finding in selected:
        lines.append(f"- {finding['text']} [{finding['doc_id']}, p.{finding['page']}]")
        claims.append(
            {
                "text": str(finding["text"]),
                "citations": [
                    {
                        "doc_id": finding["doc_id"],
                        "doc_name": finding["doc_id"],
                        "page": finding["page"],
                        "span_text": finding["span_text"],
                    }
                ],
                "confidence": 0.72,
            }
        )

    lines.extend(
        [
            "CONFIDENCE POSTURE",
            "Summary prioritised concrete operational statements over document headers and handling metadata.",
            "EVIDENCE GAPS",
            DOCUMENT_SUMMARY_GAP,
        ]
    )
    return lines, claims


async def _local_document_analysis(
    evidence_packet: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> str | None:
    excerpts = []
    for chunk in chunks[:DOCUMENT_ANALYSIS_CHUNK_LIMIT]:
        cleaned_text = _clean_document_text(chunk.get("text"))
        if not cleaned_text:
            continue
        excerpts.append(
            {
                "doc_id": chunk.get("doc_name") or chunk.get("doc_id") or UNKNOWN_DOC,
                "page": chunk.get("page") or "?",
                "text": cleaned_text[:DOCUMENT_ANALYSIS_TEXT_LIMIT],
            }
        )
    if not excerpts:
        return None

    prompt_obj = {
        "task": "Summarise one investigative document into operational findings",
        "query": evidence_packet.get("query_text"),
        "case_scope": evidence_packet.get("case_scope", "ALL_CASES"),
        "instructions": [
            "Ignore cover-sheet metadata, grading fields, handling codes, and repeated headers unless operationally relevant.",
            "Extract concrete findings from the content itself.",
            "Prefer events, locations, timings, assets, leads, and reported activity.",
            "Keep each finding concise and cite it as [DOC_ID, p.N].",
            "Return plain text only with sections: KEY FINDINGS, CONFIDENCE POSTURE, EVIDENCE GAPS.",
        ],
        "excerpts": excerpts,
    }
    try:
        client = get_http_client(base_url=settings.ollama_base_url, timeout=settings.ollama_timeout)
        response = await client.post(
            "/api/generate",
            json={
                "model": settings.local_reasoning_model,
                "prompt": (
                    f"{DOCUMENT_SUMMARY_SYSTEM_PROMPT}\n\n"
                    f"{json.dumps(prompt_obj)}"
                ),
                "stream": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
        text = _clean_document_text(payload.get("response", ""))
        if text and "[DOC" in text or (text and re.search(r"\[[^\]]+,\s*p\.\d+\]", text)):
            return str(payload.get("response", "")).strip()
        return str(payload.get("response", "")).strip() or None
    except Exception as exc:
        logger.warning("Local document analysis failed: %s", exc)
        return None


async def _local_freeform_analysis(
    evidence_packet: dict[str, Any],
    chunks: list[dict[str, Any]],
    exact_matches: list[dict[str, Any]],
) -> str | None:
    excerpts: list[dict[str, Any]] = []
    for chunk in chunks[:DOCUMENT_ANALYSIS_CHUNK_LIMIT]:
        cleaned_text = _clean_document_text(chunk.get("text"))
        if not cleaned_text:
            continue
        excerpts.append(
            {
                "doc_id": chunk.get("doc_name") or chunk.get("doc_id") or UNKNOWN_DOC,
                "page": chunk.get("page") or "?",
                "text": cleaned_text[:DOCUMENT_ANALYSIS_TEXT_LIMIT],
            }
        )
    if not excerpts:
        return None

    known_entities: list[dict[str, Any]] = []
    for item in exact_matches:
        citations = []
        for citation in item.get("citations", []):
            citations.append(
                {
                    "doc_id": citation.get("doc_id") or citation.get("doc_name") or UNKNOWN_DOC,
                    "page": citation.get("page") or "?",
                }
            )
        known_entities.append(
            {
                "canonical_name": item.get("canonical_name") or item.get("alias_text") or "Unknown",
                "entity_type": item.get("entity_type") or "UNKNOWN",
                "citations": citations,
            }
        )

    prompt_obj = {
        "task": "Analyse freeform evidence into an operational briefing",
        "query": evidence_packet.get("query_text"),
        "case_scope": evidence_packet.get("case_scope", "ALL_CASES"),
        "known_entities": known_entities,
        "instructions": [
            "Use only the supplied evidence excerpts and known entities.",
            "Return plain text only.",
            "Produce a narrative operational briefing with concise findings.",
        ],
        "excerpts": excerpts,
    }
    try:
        client = get_http_client(base_url=settings.ollama_base_url, timeout=settings.ollama_timeout)
        response = await client.post(
            "/api/generate",
            json={
                "model": settings.local_reasoning_model,
                "prompt": (
                    f"{FREEFORM_SYSTEM_PROMPT}\n\n"
                    f"{json.dumps(prompt_obj)}"
                ),
                "stream": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
        text = str(payload.get("response", "")).strip()
        return text if text else None
    except Exception as exc:
        logger.warning("Local freeform analysis failed: %s", exc)
        return None


async def _local_ollama_analysis(
    evidence_packet: dict[str, Any],
    findings: list[dict[str, Any]],
) -> str | None:
    """Call local Ollama reasoning model. Returns plain-text answer or None on failure."""
    if not findings:
        return None
    prompt_obj = {
        "task": "Produce concise operational analysis from evidence",
        "requirements": [
            "Do not repeat the same finding",
            "State only supported claims with citations in format [DOC_ID, p.N]",
            "Include confidence posture and evidence gaps",
        ],
        "query": evidence_packet.get("query_text"),
        "case_scope": evidence_packet.get("case_scope", "ALL_CASES"),
        "findings": [
            {
                "source": finding["source"],
                "source_type": finding.get("source_type"),
                "target": finding["target"],
                "target_type": finding.get("target_type"),
                "relation_type": finding["relation_type"],
                "confidence": finding["confidence"],
                "doc_count": finding["doc_count"],
                "evidence_count": finding["evidence_count"],
                "citations": finding["citations"][:OPENROUTER_CITATION_LIMIT],
            }
            for finding in findings[:LOCAL_FINDINGS_LIMIT]
        ],
    }
    try:
        client = get_http_client(base_url=settings.ollama_base_url, timeout=settings.ollama_timeout)
        response = await client.post(
            "/api/generate",
            json={
                "model": settings.local_reasoning_model,
                "prompt": (
                    f"{RELATIONSHIP_SYSTEM_PROMPT}\n\n"
                    f"{json.dumps(prompt_obj)}"
                ),
                "stream": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
        text = str(payload.get("response", "")).strip()
        return text if text else None
    except Exception as exc:
        logger.warning("Local Ollama analysis failed: %s", exc)
        return None


def _build_openrouter_payload(
    evidence_packet: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the OpenRouter payload. MUST NOT include span_text or raw chunk text."""
    payload = {
        "query": evidence_packet.get("query_text"),
        "case_scope": evidence_packet.get("case_scope", "ALL_CASES"),
        "findings": [
            {
                "source": _redact_openrouter_entity(finding["source"], finding.get("source_type")),
                "source_type": finding.get("source_type"),
                "target": _redact_openrouter_entity(finding["target"], finding.get("target_type")),
                "target_type": finding.get("target_type"),
                "relation_type": finding["relation_type"],
                "confidence": finding["confidence"],
                "doc_count": finding["doc_count"],
                "evidence_count": finding["evidence_count"],
                "citations": _openrouter_citations(finding["citations"]),
            }
            for finding in findings[:OPENROUTER_FINDINGS_LIMIT]
        ],
    }
    serialised = json.dumps(payload)
    assert "span_text" not in serialised, (
        "Data sovereignty violation: span_text found in OpenRouter payload"
    )
    assert "Arkwright Road" not in serialised, (
        "Data sovereignty violation: raw location text found in OpenRouter payload"
    )
    return payload


async def _openrouter_analysis(
    evidence_packet: dict[str, Any],
    findings: list[dict[str, Any]],
) -> str | None:
    """Call OpenRouter cloud API with redacted evidence (no raw text)."""
    if not settings.openrouter_api_key or not findings:
        return None
    payload = _build_openrouter_payload(evidence_packet, findings)
    system = (
        "You are an intelligence analyst. Produce concise operational output.\n"
        "Return plain text only, no markdown, no asterisks.\n"
        "Use exact sections: People, Places, Assets, Key Findings, Confidence Posture, "
        "Evidence Gaps, Next Actions.\n"
        "Cite every claim as [DOC_ID, p.N]."
    )
    try:
        client = get_http_client(
            base_url=settings.openrouter_base_url,
            timeout=settings.ollama_timeout,
        )
        response = await client.post(
            "/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.openrouter_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload)},
                ],
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return None
        content = choices[0].get("message", {}).get("content", "")
        text = str(content).strip()
        return text if text else None
    except Exception as exc:
        logger.warning("OpenRouter analysis failed: %s", exc)
        return None


async def generate_answer(
    evidence_packet: dict[str, Any], *, use_cloud: bool = False
) -> dict[str, Any]:
    """Generate an answer from the evidence packet, routing to local or cloud LLM."""
    query_intent = str(evidence_packet.get("query_intent") or "")
    relationships = evidence_packet.get("relationships", [])
    exact_matches = evidence_packet.get("exact_matches", [])
    chunks = evidence_packet.get("chunks", [])
    backend_used = "deterministic-fallback"

    if query_intent == "document_summary_query" and chunks:
        local_document_analysis = await _local_document_analysis(evidence_packet, chunks)
        fallback_lines, claims = _document_summary_lines(chunks)
        if local_document_analysis:
            answer = local_document_analysis
            claims = []
            backend_used = settings.local_reasoning_model
        else:
            answer = "\n".join(fallback_lines)
        return {"backend": backend_used, "answer": answer, "claims": claims}

    if not relationships:
        local_freeform_analysis = await _local_freeform_analysis(
            evidence_packet, chunks, exact_matches
        )
        if local_freeform_analysis:
            return {
                "backend": settings.local_reasoning_model,
                "answer": local_freeform_analysis,
                "claims": [],
            }

        exact_findings = _aggregate_exact_matches(exact_matches)
        if not exact_findings:
            if chunks:
                answer = "\n".join(_chunk_fallback_lines(chunks))
            else:
                answer = RELATIONSHIP_MISSING_MESSAGE
            claims = []
        else:
            people: list[str] = []
            places: list[str] = []
            assets: list[str] = []
            finding_lines: list[str] = []
            claims = []

            for item in exact_findings[:EXACT_FINDINGS_LIMIT]:
                entity_line = f"{item['canonical_name']} ({item['entity_type']})"
                entity_type = str(item.get("entity_type", "UNKNOWN")).upper()
                if entity_type == "PERSON":
                    people.append(entity_line)
                elif entity_type == "LOCATION":
                    places.append(entity_line)
                else:
                    assets.append(entity_line)

                citations = item.get("citations", [])[:OPENROUTER_CITATION_LIMIT]
                citation_text = ", ".join(f"{c['doc_id']} p.{c['page']}" for c in citations)
                alias_hint = (
                    f" (aliases: {', '.join(item['aliases'][:2])})" if item.get("aliases") else ""
                )
                finding_lines.append(
                    f"{item['canonical_name']} identified in {item['doc_count']} doc(s), "
                    f"{item['evidence_count']} evidence span(s){alias_hint}. {citation_text}"
                )
                claims.append(
                    {"text": item["canonical_name"], "citations": citations, "confidence": 0.65}
                )

            answer = "\n".join(
                [
                    "PEOPLE",
                    ", ".join(people) if people else "None identified",
                    "PLACES",
                    ", ".join(places) if places else "None identified",
                    "ASSETS",
                    ", ".join(assets) if assets else "None identified",
                    "KEY FINDINGS",
                    *finding_lines,
                    "CONFIDENCE POSTURE",
                    ENTITY_LINKAGE_POSTURE,
                    "EVIDENCE GAPS",
                    NO_GRAPH_RELATIONSHIP_GAP,
                ]
            )
    else:
        findings = _aggregate_relationships(relationships)
        top_findings = findings[:RELATIONSHIP_FINDINGS_LIMIT]

        local_analysis: str | None = None
        openrouter_analysis: str | None = None

        if use_cloud and settings.openrouter_api_key:
            openrouter_analysis = await _openrouter_analysis(evidence_packet, top_findings)
        local_analysis = await _local_ollama_analysis(evidence_packet, top_findings)

        fallback_lines, claims = _deterministic_relationship_lines(evidence_packet, top_findings)

        if settings.prefer_openrouter_output and openrouter_analysis:
            answer = openrouter_analysis
            backend_used = f"openrouter:{settings.openrouter_model}"
        elif local_analysis:
            answer = local_analysis
            backend_used = settings.local_reasoning_model
        elif openrouter_analysis:
            answer = openrouter_analysis
            backend_used = f"openrouter:{settings.openrouter_model}"
        else:
            answer = "\n".join(fallback_lines)

    return {"backend": backend_used, "answer": answer, "claims": claims}
