from pathlib import Path

from operation_lens_v2.ingestion.duck_store import db_health, init_db


def test_db_init_creates_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence.duckdb"
    con = init_db(str(db_path))
    health = db_health(con)
    assert health["status"] == "ok"
