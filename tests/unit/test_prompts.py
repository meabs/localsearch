"""Tests for the prompts module — structure and scope rendering."""

from __future__ import annotations

import re

from operation_lens_v2.query.prompts import (
    CRITIC_SYSTEM_PROMPT,
    FREEFORM_FALLBACK_PROMPT,
    INVESTIGATOR_SYSTEM_PROMPT,
    WRITER_SYSTEM_PROMPT,
    render_investigator_prompt,
)
from operation_lens_v2.query.scope import build_scope


def test_render_investigator_corpus_mentions_all():
    prompt = render_investigator_prompt(build_scope("corpus"))
    assert "CORPUS" in prompt
    assert "{" not in prompt


def test_render_investigator_document_lists_ids():
    prompt = render_investigator_prompt(build_scope("document", doc_ids=["NF-INT-001", "NF-INT-002"]))
    assert "DOCUMENT" in prompt
    assert "NF-INT-001" in prompt
    assert "NF-INT-002" in prompt


def test_render_investigator_case_surfaces_case_ref():
    prompt = render_investigator_prompt(build_scope("case", case_scope="OP_NIGHTFALL"))
    assert "CASE" in prompt
    assert "OP_NIGHTFALL" in prompt


def test_render_investigator_leaves_no_unresolved_format_placeholders():
    prompt = render_investigator_prompt(build_scope("corpus"))
    assert not re.search(r"\{\w+\}", prompt)


def test_investigator_prompt_has_role_and_tool_guidance():
    assert "senior intelligence analyst" in INVESTIGATOR_SYSTEM_PROMPT
    assert "get_cooccurrence" in INVESTIGATOR_SYSTEM_PROMPT
    assert "walk_graph" in INVESTIGATOR_SYSTEM_PROMPT
    assert "fetch_chunk" in INVESTIGATOR_SYSTEM_PROMPT
    assert re.search(r"10\s+tool\s+calls", INVESTIGATOR_SYSTEM_PROMPT)
    assert "InvestigationReport" in INVESTIGATOR_SYSTEM_PROMPT


def test_investigator_prompt_requires_disconfirmation_step():
    assert "disprove" in INVESTIGATOR_SYSTEM_PROMPT or "disconfirm" in INVESTIGATOR_SYSTEM_PROMPT


def test_writer_prompt_lists_all_required_sections():
    for section in (
        "ASSESSMENT",
        "KEY FINDINGS",
        "CROSS-DOCUMENT LINKS",
        "TIMELINE",
        "CONFIDENCE POSTURE",
        "EVIDENCE GAPS",
        "SUGGESTED NEXT ACTIONS",
    ):
        assert section in WRITER_SYSTEM_PROMPT


def test_writer_prompt_forbids_markdown_and_preamble():
    assert "No markdown" in WRITER_SYSTEM_PROMPT
    assert "preamble" in WRITER_SYSTEM_PROMPT
    assert "[doc_name, p.N]" in WRITER_SYSTEM_PROMPT


def test_writer_prompt_requires_narrative_assessment():
    assert "narrative" in WRITER_SYSTEM_PROMPT.lower()
    assert "Not bullets" in WRITER_SYSTEM_PROMPT


def test_critic_prompt_requests_structured_review():
    assert "JSON" in CRITIC_SYSTEM_PROMPT
    assert "sentence_reviews" in CRITIC_SYSTEM_PROMPT
    assert "missed_links" in CRITIC_SYSTEM_PROMPT


def test_freeform_fallback_prompt_contract():
    for section in ("ASSESSMENT", "KEY FINDINGS", "CONFIDENCE POSTURE", "EVIDENCE GAPS"):
        assert section in FREEFORM_FALLBACK_PROMPT
    assert "[doc_name, p.N]" in FREEFORM_FALLBACK_PROMPT
