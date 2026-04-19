"""Coverage for `normalise()` — one test per strategy plus edge cases."""

from __future__ import annotations

import logging

from operation_lens_v2.ingestion.normaliser import normalise


def test_person_strategy_strips_titles_and_reorders() -> None:
    assert normalise("dc webb, marcus", "PERSON") == "Marcus Webb"


def test_person_strategy_no_comma_title_case() -> None:
    assert normalise("Dr rania khalil", "PERSON") == "Rania Khalil"


def test_plate_strategy_reformats() -> None:
    assert normalise("rx71kld", "VEHICLE") == "RX71 KLD"


def test_plate_strategy_strips_punctuation() -> None:
    assert normalise("RX71-KLD", "VEHICLE") == "RX71 KLD"


def test_phone_e164_uk_mobile() -> None:
    assert normalise("07712 345 678", "PHONE") == "+447712345678"


def test_phone_e164_keeps_already_international() -> None:
    # Leading 00 → +, then digits; 00 44 → + 44.
    assert normalise("0044 7712 345678", "PHONE") == "+447712345678"


def test_phone_e164_empty_surface_returns_empty() -> None:
    # Blank surface short-circuits in normalise() before strategy dispatch.
    assert normalise("   ", "PHONE") == ""


def test_upper_snake_strategy_case_ref() -> None:
    assert normalise("op nightfall", "CASE_REF") == "OP_NIGHTFALL"


def test_title_expand_strategy_location() -> None:
    # Relies on expansions configured for LOCATION in entity_schema.json.
    result = normalise("14 arkwright rd", "LOCATION")
    assert "Arkwright" in result
    # "rd" should expand to "Road" if the schema defines that expansion.
    assert result == result.strip()


def test_strip_suffix_title_organisation() -> None:
    # Should strip a legal suffix if configured (Ltd/Limited/plc).
    result = normalise("arkwright holdings ltd", "ORGANISATION")
    assert "Arkwright" in result
    assert "Ltd" not in result and "ltd" not in result.lower().split()


def test_unknown_entity_type_warns_and_trims(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        result = normalise("  some value  ", "NOT_A_REAL_TYPE")
    assert result == "some value"
    assert any(
        "unknown entity_type" in record.getMessage().lower() for record in caplog.records
    ), "Expected a warning for unknown entity_type"


def test_empty_surface_returns_empty_string() -> None:
    assert normalise("", "PERSON") == ""
    assert normalise(None, "PERSON") == ""  # type: ignore[arg-type]
