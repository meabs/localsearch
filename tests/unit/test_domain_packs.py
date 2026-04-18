from __future__ import annotations

import json

from operation_lens_v2.ingestion.entity_schema import get_schema, list_domain_packs, reload_schema, resolve_schema_config


def test_domain_pack_merge_extends_base(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    packs_dir = config_dir / "domain_packs"
    packs_dir.mkdir(parents=True)
    (config_dir / "entity_schema.json").write_text(
        json.dumps(
            {
                "entity_types": {
                    "PERSON": {
                        "gliner_prompts": ["person"],
                        "llm_aliases": [],
                        "color": "#111111",
                        "normalise": {"strategy": "trim"},
                    }
                },
                "relation_hints": ["LINKED_TO"],
                "ui": {"default_query_templates": [{"template_id": "base", "label": "Base", "query": "base"}]},
            }
        ),
        encoding="utf-8",
    )
    (packs_dir / "finance.json").write_text(
        json.dumps(
            {
                "name": "finance",
                "extends": "base",
                "entity_types": {
                    "ACCOUNT": {
                        "gliner_prompts": ["account"],
                        "llm_aliases": [],
                        "color": "#22aa22",
                        "normalise": {"strategy": "trim"},
                    }
                },
                "relation_hints": ["TRANSFERRED_TO"],
            }
        ),
        encoding="utf-8",
    )

    reload_schema(path=config_dir / "entity_schema.json", domain_packs_path=packs_dir)
    resolved = resolve_schema_config(domain_pack="finance")
    schema = get_schema(domain_pack="finance")

    assert "PERSON" in resolved["entity_types"]
    assert "ACCOUNT" in resolved["entity_types"]
    assert "TRANSFERRED_TO" in resolved["relation_hints"]
    assert "PERSON" in schema.entity_types
    assert "ACCOUNT" in schema.entity_types


def test_list_domain_packs_reads_metadata(monkeypatch) -> None:
    monkeypatch.delenv("DOMAIN_PACKS_PATH", raising=False)
    monkeypatch.delenv("ENTITY_SCHEMA_PATH", raising=False)
    reload_schema()
    packs = list_domain_packs()

    assert any(pack["name"] == "investigations" for pack in packs)
    assert any(pack["name"] == "fraud_finance" for pack in packs)
