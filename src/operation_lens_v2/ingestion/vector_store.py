from __future__ import annotations

from pathlib import Path
from typing import Any

import lancedb


class VectorStore:
    def __init__(self, db_path: str, table_name: str = "chunks") -> None:
        Path(db_path).mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(db_path)
        self.table_name = table_name
        self._table = None
        self._table_exists: bool | None = None
        self._vector_dim: int | None = None

    def _has_table(self) -> bool:
        if self._table_exists is None:
            self._table_exists = self.table_name in self.db.table_names()
        return self._table_exists

    def _open_table(self):
        if self._table is None and self._has_table():
            self._table = self.db.open_table(self.table_name)
        return self._table

    @staticmethod
    def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep the last row for each chunk_id so repeated ingests replace older data."""
        deduped: dict[Any, dict[str, Any]] = {}
        passthrough: list[dict[str, Any]] = []
        for row in rows:
            chunk_id = row.get("chunk_id")
            if chunk_id is None:
                passthrough.append(row)
                continue
            deduped[chunk_id] = row
        return [*passthrough, *deduped.values()]

    @staticmethod
    def _chunk_id_predicate(chunk_ids: list[Any]) -> str:
        escaped = [str(chunk_id).replace("'", "''") for chunk_id in chunk_ids]
        joined = " OR ".join(f"chunk_id = '{chunk_id}'" for chunk_id in escaped)
        return f"({joined})"

    def _delete_chunk_ids(self, table, chunk_ids: list[Any]) -> None:
        if not chunk_ids:
            return
        predicate = self._chunk_id_predicate(chunk_ids)
        try:
            table.delete(predicate)
        except TypeError:
            table.delete(where=predicate)

    def upsert(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        rows = self._dedupe_rows(rows)
        if not self._has_table():
            self._table = self.db.create_table(self.table_name, data=rows, mode="overwrite")
            self._table_exists = True
            sample_vector = rows[0].get("vector")
            if isinstance(sample_vector, list):
                self._vector_dim = len(sample_vector)
            return
        table = self._open_table()
        self._delete_chunk_ids(
            table,
            [row["chunk_id"] for row in rows if row.get("chunk_id") is not None],
        )
        table.add(rows)
        if self._vector_dim is None:
            sample_vector = rows[0].get("vector")
            if isinstance(sample_vector, list):
                self._vector_dim = len(sample_vector)

    def search(self, vector: list[float], top_k: int = 10) -> list[dict[str, Any]]:
        if not self._has_table():
            return []
        table = self._open_table()
        return table.search(vector).limit(top_k).to_list()

    def vector_dim(self) -> int | None:
        """Best-effort vector dimension for the current table."""
        if self._vector_dim is not None:
            return self._vector_dim
        if not self._has_table():
            return None
        table = self._open_table()
        sample = table.head(1).to_pylist()
        if not sample:
            return None
        vector = sample[0].get("vector")
        if isinstance(vector, list):
            self._vector_dim = len(vector)
            return self._vector_dim
        return None
