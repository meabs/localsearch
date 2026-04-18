from __future__ import annotations

from scripts.load_demo_case import CASE_REF, DEMO_DIR, create_demo_seed


def test_create_demo_seed_writes_expected_files() -> None:
    files = create_demo_seed()

    names = {path.name for path in files}
    assert "01_field_report.txt" in names
    assert "02_contact_bridges.csv" in names
    assert "03_event_log.json" in names
    assert "04_control_update.eml" in names
    assert "05_site_note.html" in names
    assert DEMO_DIR.exists()
    assert CASE_REF == "OP_DEMO_SIGNAL"
