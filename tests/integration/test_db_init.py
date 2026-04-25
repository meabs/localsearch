from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from operation_lens_v2.ingestion.duck_store import db_health, init_db


def test_db_init_creates_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence.duckdb"
    con = init_db(str(db_path))
    health = db_health(con)
    assert health["status"] == "ok"


def test_db_init_serializes_concurrent_schema_bootstrap(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent.duckdb"

    def bootstrap() -> str:
        con = init_db(str(db_path))
        try:
            return str(db_health(con)["status"])
        finally:
            con.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: bootstrap(), range(4)))

    assert statuses == ["ok", "ok", "ok", "ok"]
