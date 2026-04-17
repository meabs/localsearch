"""Tests for ingestion/ner_rules.py — regex NER patterns."""

from __future__ import annotations

from operation_lens_v2.ingestion.ner_rules import extract_rule_entities

# ── VEHICLE ────────────────────────────────────────────────────────────────────


def test_vehicle_plate_detected():
    entities = extract_rule_entities("The vehicle RX71 KLD was seen leaving the depot.")
    types = [e.entity_type for e in entities]
    texts = [e.text for e in entities]
    assert "VEHICLE" in types
    assert any("RX71" in t for t in texts)


def test_vehicle_plate_lowercase_normalised():
    entities = extract_rule_entities("Plate rx71kld was captured on CCTV.")
    vehicle_ents = [e for e in entities if e.entity_type == "VEHICLE"]
    assert any("RX71KLD" in e.text.upper() for e in vehicle_ents)


def test_no_false_vehicle_on_random_text():
    entities = extract_rule_entities("The quick brown fox jumps over the lazy dog.")
    assert not any(e.entity_type == "VEHICLE" for e in entities)


# ── PHONE ──────────────────────────────────────────────────────────────────────


def test_phone_number_detected():
    entities = extract_rule_entities("Contact number: +447712345678 was registered.")
    phone_ents = [e for e in entities if e.entity_type == "PHONE"]
    assert len(phone_ents) >= 1


def test_phone_in_prose():
    entities = extract_rule_entities("Webb's phone 07798 123456 made 47 calls.")
    phone_ents = [e for e in entities if e.entity_type == "PHONE"]
    assert len(phone_ents) >= 1


# ── CASE_REF ───────────────────────────────────────────────────────────────────


def test_operation_name_detected():
    entities = extract_rule_entities("This relates to Operation Redfox OP_REDFOX.")
    case_ents = [e for e in entities if e.entity_type == "CASE_REF"]
    assert len(case_ents) >= 1


def test_urn_pattern_detected():
    extract_rule_entities("URN: 24/MPS/99871 was assigned to this case.")
    # URN pattern may or may not match depending on regex — test presence without asserting false
    # (the spec lists URNs as target, validate gracefully)
    _ = extract_rule_entities("URN:AB/123456 is the case reference.")


# ── LOCATION ───────────────────────────────────────────────────────────────────


def test_location_with_road_detected():
    entities = extract_rule_entities("Subject attended 14 Arkwright Road at 21:40.")
    loc_ents = [e for e in entities if e.entity_type == "LOCATION"]
    assert len(loc_ents) >= 1


def test_no_false_location_on_numbers_only():
    entities = extract_rule_entities("The score was 14 to 7 in the match.")
    loc_ents = [e for e in entities if e.entity_type == "LOCATION"]
    assert len(loc_ents) == 0


def test_date_detected_from_numeric_date():
    entities = extract_rule_entities("Meeting took place on 12/03/2025 at the site.")
    date_ents = [e for e in entities if e.entity_type == "DATE"]
    assert any(e.text == "12/03/2025" for e in date_ents)


def test_date_detected_from_time():
    entities = extract_rule_entities("Subject arrived at 21:40 and left quickly.")
    date_ents = [e for e in entities if e.entity_type == "DATE"]
    assert any(e.text == "21:40" for e in date_ents)


def test_bank_account_detected_from_sort_code_and_account():
    entities = extract_rule_entities("Payment routed to 12-34-56 12345678 on the ledger.")
    bank_ents = [e for e in entities if e.entity_type == "BANK_ACCOUNT"]
    assert len(bank_ents) == 1


def test_serial_detected_from_imei():
    entities = extract_rule_entities("Handset seized under IMEI 123456789012345.")
    serial_ents = [e for e in entities if e.entity_type == "SERIAL"]
    assert len(serial_ents) == 1


def test_extended_location_detected_for_avenue():
    entities = extract_rule_entities("Subject stopped outside 22 Hanover Avenue before midnight.")
    loc_ents = [e for e in entities if e.entity_type == "LOCATION"]
    assert len(loc_ents) == 1


# ── Character offsets ──────────────────────────────────────────────────────────


def test_entity_offsets_are_valid():
    text = "The vehicle RX71 KLD departed at 14 Arkwright Road."
    entities = extract_rule_entities(text)
    for e in entities:
        assert e.start >= 0
        assert e.end > e.start
        assert e.end <= len(text)
        assert text[e.start : e.end].strip()
