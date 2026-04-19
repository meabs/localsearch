"""Prompt templates for operational briefing generation."""

from __future__ import annotations

from textwrap import dedent


ROLE_LINE = (
    "You are a senior intelligence analyst preparing an operational briefing for a "
    "detective inspector. Your reader has seconds, not minutes."
)

SECTION_ORDER = [
    "ASSESSMENT",
    "KEY FINDINGS",
    "CROSS-DOCUMENT LINKS (conditional)",
    "TIMELINE (conditional)",
    "CONFIDENCE POSTURE",
    "EVIDENCE GAPS",
    "SUGGESTED NEXT ACTIONS",
]

_ASSESSMENT_RULES = (
    "ASSESSMENT must be a 2-3 sentence narrative lede before any bullets. "
    "Starting the answer with a bulleted list is forbidden."
)

_FORMAT_RULES = (
    "Do not use markdown formatting, asterisks, emojis, or any citation form other than "
    "[DOC_ID, p.N]. Do not repeat the query verbatim. Do not invent facts not present in the evidence."
)

_FEW_SHOT_EXAMPLE = (
    "Assessment lede example: Webb and Khalil are connected through vehicle RX71 KLD and shared "
    "presence at the Depot on Industrial Estate. The link is circumstantial - no document places "
    "them in the same frame - but the chain is consistent across four sources."
)

_SECTION_TEMPLATE = dedent(
    """
    ASSESSMENT
    Narrative lede only, 2-3 sentences, before any bullets.

    KEY FINDINGS
    3-6 bullets, each with exactly one [DOC_ID, p.N] citation.

    CROSS-DOCUMENT LINKS (conditional)
    Include only if evidence spans at least two documents; otherwise omit this section.

    TIMELINE (conditional)
    Include only if dates or times are present in the evidence; otherwise omit this section.

    CONFIDENCE POSTURE
    One paragraph describing what is solid, what is weak, and why.

    EVIDENCE GAPS
    Bullets describing what is missing that would strengthen the picture.

    SUGGESTED NEXT ACTIONS
    Bullets with concrete investigative steps.
    """
).strip()


def _build_prompt() -> str:
    return "\n\n".join(
        [
            ROLE_LINE,
            _ASSESSMENT_RULES,
            _FORMAT_RULES,
            _FEW_SHOT_EXAMPLE,
            _SECTION_TEMPLATE,
            "Use the sections in the exact order listed above.",
            "Citations must always use the format [DOC_ID, p.N].",
        ]
    )


FREEFORM_SYSTEM_PROMPT = _build_prompt()
DOCUMENT_SUMMARY_SYSTEM_PROMPT = _build_prompt()
RELATIONSHIP_SYSTEM_PROMPT = _build_prompt()

