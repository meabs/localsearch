from __future__ import annotations

from operation_lens_v2.ingestion.vector_store import VectorStore


class FakeSearch:
    def __init__(self, rows):
        self._rows = rows

    def limit(self, top_k: int):
        self._rows = self._rows[:top_k]
        return self

    def to_list(self):
        return self._rows


class FakeTable:
    def __init__(self):
        self.rows: list[dict] = []
        self.deleted_predicates: list[str] = []
        self.add_calls = 0

    def delete(self, predicate=None, where=None):
        clause = predicate if predicate is not None else where
        assert isinstance(clause, str)
        self.deleted_predicates.append(clause)
        chunk_ids = []
        for part in clause.strip("()").split(" OR "):
            if "chunk_id = '" in part:
                raw_value = part.split("chunk_id = '", 1)[1].rsplit("'", 1)[0]
                chunk_ids.append(raw_value.replace("''", "'"))
        self.rows = [row for row in self.rows if row.get("chunk_id") not in chunk_ids]

    def add(self, rows):
        self.add_calls += 1
        self.rows.extend(rows)

    def search(self, vector):
        return FakeSearch([{"_distance": 0.0, **row} for row in self.rows])

    def head(self, _: int):
        return self

    def to_pylist(self):
        return self.rows[:1]


class FakeDb:
    def __init__(self):
        self.tables: dict[str, FakeTable] = {}

    def table_names(self):
        return list(self.tables)

    def create_table(self, name: str, data, mode: str):
        table = FakeTable()
        table.rows.extend(data)
        self.tables[name] = table
        return table

    def open_table(self, name: str):
        return self.tables[name]


def test_upsert_replaces_existing_chunk_rows(monkeypatch, tmp_path):
    db = FakeDb()

    def fake_connect(_: str):
        return db

    monkeypatch.setattr("operation_lens_v2.ingestion.vector_store.lancedb.connect", fake_connect)

    store = VectorStore(str(tmp_path / "lancedb"))

    store.upsert(
        [
            {
                "chunk_id": "chunk-1",
                "doc_id": "doc-1",
                "page": 1,
                "text": "first version",
                "vector": [0.1, 0.2],
            }
        ]
    )

    assert store.search([0.1, 0.2], top_k=10)[0]["text"] == "first version"

    store.upsert(
        [
            {
                "chunk_id": "chunk-1",
                "doc_id": "doc-1",
                "page": 1,
                "text": "second version",
                "vector": [0.3, 0.4],
            }
        ]
    )

    results = store.search([0.3, 0.4], top_k=10)
    assert len(results) == 1
    assert results[0]["text"] == "second version"
    assert len(db.tables["chunks"].rows) == 1
    assert db.tables["chunks"].rows[0]["text"] == "second version"
    assert db.tables["chunks"].add_calls == 1
    assert db.tables["chunks"].deleted_predicates == ["(chunk_id = 'chunk-1')"]
