from __future__ import annotations

from operation_lens_v2.ingestion.ner_rules import extract_rule_entities


def _texts(entities, entity_type: str) -> list[str]:
    return [e.text for e in entities if e.entity_type == entity_type]


# VEHICLE


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


# PHONE


def test_phone_number_detected():
    entities = extract_rule_entities("Contact number: +447712345678 was registered.")
    phone_ents = [e for e in entities if e.entity_type == "PHONE"]
    assert len(phone_ents) >= 1


def test_phone_in_prose():
    entities = extract_rule_entities("Webb's phone 07798 123456 made 47 calls.")
    phone_ents = [e for e in entities if e.entity_type == "PHONE"]
    assert len(phone_ents) >= 1


# CASE_REF


def test_operation_name_detected():
    entities = extract_rule_entities("This relates to Operation Redfox OP_REDFOX.")
    case_ents = [e for e in entities if e.entity_type == "CASE_REF"]
    assert len(case_ents) >= 1


def test_urn_pattern_detected():
    extract_rule_entities("URN: 24/MPS/99871 was assigned to this case.")
    _ = extract_rule_entities("URN:AB/123456 is the case reference.")


# LOCATION / DATE / BANK / SERIAL


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


# CRYPTO_WALLET


def test_crypto_wallet_detects_btc_and_eth_forms():
    entities = extract_rule_entities(
        "Wallets: 1BoatSLRHtKNngkdXEeobR76b53LETtpyT, "
        "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080, "
        "and 0x52908400098527886E0F7030069857D2E4169EE7 were recovered."
    )
    wallet_texts = _texts(entities, "CRYPTO_WALLET")
    assert "1BoatSLRHtKNngkdXEeobR76b53LETtpyT" in wallet_texts
    assert any(t.lower() == "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080" for t in wallet_texts)
    assert "0x52908400098527886E0F7030069857D2E4169EE7" in wallet_texts


def test_crypto_wallet_rejects_near_miss():
    entities = extract_rule_entities("Fake address bc1qnotlongenough was pasted into the note.")
    assert not any(e.entity_type == "CRYPTO_WALLET" for e in entities)


# MAC_ADDRESS


def test_mac_address_detected():
    entities = extract_rule_entities("Adapter MAC 00:1A:2B:3C:4D:5E was recorded.")
    mac_texts = _texts(entities, "MAC_ADDRESS")
    assert mac_texts == ["00:1A:2B:3C:4D:5E"]


def test_mac_address_rejects_short_candidate():
    entities = extract_rule_entities("Adapter MAC 00:1A:2B:3C:4D was recorded.")
    assert not any(e.entity_type == "MAC_ADDRESS" for e in entities)


# CREDIT_CARD


def test_credit_card_detected_with_luhn_validation():
    entities = extract_rule_entities("Card 4111 1111 1111 1111 cleared authorization.")
    card_texts = _texts(entities, "CREDIT_CARD")
    assert card_texts == ["4111 1111 1111 1111"]


def test_credit_card_rejects_invalid_luhn_number():
    entities = extract_rule_entities("Card 4111 1111 1111 1112 cleared authorization.")
    assert not any(e.entity_type == "CREDIT_CARD" for e in entities)


# SOCIAL_HANDLE


def test_social_handle_detects_username_and_links():
    entities = extract_rule_entities(
        "Reach @casewatch, t.me/casewatch, or instagram.com/casewatch for updates."
    )
    handle_texts = _texts(entities, "SOCIAL_HANDLE")
    assert "@casewatch" in handle_texts
    assert any(t.lower() == "t.me/casewatch" for t in handle_texts)
    assert any(t.lower() == "instagram.com/casewatch" for t in handle_texts)


def test_social_handle_rejects_email_like_text():
    entities = extract_rule_entities("Send it to user@example.com or example.com/contact.")
    assert not any(e.entity_type == "SOCIAL_HANDLE" for e in entities)


# NATIONAL_ID


def test_national_id_detects_uk_nino_and_us_ssn():
    entities = extract_rule_entities("Identifiers AB123456C and 123-45-6789 were referenced.")
    national_texts = _texts(entities, "NATIONAL_ID")
    assert "AB123456C" in national_texts
    assert "123-45-6789" in national_texts


def test_national_id_rejects_invalid_examples():
    entities = extract_rule_entities("Identifiers AB123456E and 000-12-3456 were rejected.")
    assert not any(e.entity_type == "NATIONAL_ID" for e in entities)


# Character offsets


def test_entity_offsets_are_valid():
    text = "The vehicle RX71 KLD departed at 14 Arkwright Road."
    entities = extract_rule_entities(text)
    for e in entities:
        assert e.start >= 0
        assert e.end > e.start
        assert e.end <= len(text)
        assert text[e.start : e.end].strip()
