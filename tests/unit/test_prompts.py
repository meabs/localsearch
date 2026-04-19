from __future__ import annotations

import pytest

from operation_lens_v2.query.prompts import (
    DOCUMENT_SUMMARY_SYSTEM_PROMPT,
    FREEFORM_SYSTEM_PROMPT,
    RELATIONSHIP_SYSTEM_PROMPT,
    ROLE_LINE,
    SECTION_ORDER,
)


PROMPTS = {
    "freeform": FREEFORM_SYSTEM_PROMPT,
    "document_summary": DOCUMENT_SUMMARY_SYSTEM_PROMPT,
    "relationship": RELATIONSHIP_SYSTEM_PROMPT,
}


@pytest.mark.parametrize("prompt_name,prompt_text", PROMPTS.items())
def test_prompt_contains_required_role_and_sections(prompt_name: str, prompt_text: str) -> None:
    assert ROLE_LINE in prompt_text, prompt_name
    for label in SECTION_ORDER:
        assert label in prompt_text, prompt_name
    assert "Citations must always use the format [DOC_ID, p.N]." in prompt_text, prompt_name


@pytest.mark.parametrize("prompt_name,prompt_text", PROMPTS.items())
def test_prompt_contains_assessment_and_format_rules(prompt_name: str, prompt_text: str) -> None:
    assert "ASSESSMENT must be a 2-3 sentence narrative lede before any bullets." in prompt_text, prompt_name
    assert "Starting the answer with a bulleted list is forbidden." in prompt_text, prompt_name
    assert "Do not use markdown formatting, asterisks, emojis, or any citation form other than [DOC_ID, p.N]." in prompt_text, prompt_name
    assert "Do not repeat the query verbatim." in prompt_text, prompt_name
    assert "Do not invent facts not present in the evidence." in prompt_text, prompt_name


@pytest.mark.parametrize("prompt_name,prompt_text", PROMPTS.items())
def test_prompt_contains_few_shot_assessment_lede_example(prompt_name: str, prompt_text: str) -> None:
    assert "Webb and Khalil are connected through vehicle RX71 KLD and shared presence at the Depot on Industrial Estate." in prompt_text, prompt_name
    assert "The link is circumstantial - no document places them in the same frame - but the chain is consistent across four sources." in prompt_text, prompt_name

