"""End-to-end test for the Operation Nightfall cross-document chain.

Validates the core claim behind the system: no single document contains the
full Webb → RX71 KLD → Depot → Khalil connection, but the ingestion pipeline
stitches it together. We drive three generator functions from
``scripts/generate_demo_corpus.py`` into a temp dir, run the real PDF + chunk
+ rule-NER + normaliser path, and assert the vehicle entity resolves to one
canonical row with aliases drawn from multiple source documents.

GLiNER, local LLM extraction, embeddings, and the vector store are stubbed —
rule-based extraction of VEHICLE plates is deterministic and sufficient to
prove cross-document resolution.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from operation_lens_v2.ingestion import (
    duck_store,
    embedder,
    ner_gliner,
    ner_llm,
    pipeline,
    relationship_extractor,
    vector_store,
)


def _load_corpus_module() -> object:
    """Load ``scripts/generate_demo_corpus.py`` as a module without installing it."""
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "generate_demo_corpus.py"
    spec = importlib.util.spec_from_file_location("generate_demo_corpus", script_path)
    assert spec and spec.loader, f"Cannot load {script_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_demo_corpus"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.integration
@pytest.mark.asyncio
async def test_webb_khalil_chain_discoverable_across_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("fpdf", reason="fpdf2 required to render demo corpus")

    corpus_module = _load_corpus_module()
    corpus_dir = tmp_path / "pdfs"
    corpus_dir.mkdir()
    monkeypatch.setattr(corpus_module, "OUTPUT_DIR", corpus_dir)

    # Only the three docs carrying the vehicle-chain link. Skipping the rest
    # keeps the test fast and focuses the assertion on the cross-document
    # resolution claim, not on volume.
    corpus_module.gen_nf_int_001()
    corpus_module.gen_nf_surv_004()
    corpus_module.gen_nf_surv_007()

    pdf_paths = sorted(corpus_dir.glob("NF-*.pdf"))
    assert len(pdf_paths) == 3, f"Expected 3 demo PDFs, got {[p.name for p in pdf_paths]}"

    # Heavy dependencies we don't need for a rule-only chain-resolution test.
    async def _stub_embed(_text: str) -> list[float]:
        return [0.0] * 768

    async def _stub_no_entities(*_args, **_kwargs):
        return []

    async def _stub_no_rels(*_args, **_kwargs):
        return []

    monkeypatch.setattr(ner_gliner, "extract_general_entities", lambda _text: [])
    monkeypatch.setattr(ner_llm, "extract_llm_entities", _stub_no_entities)
    monkeypatch.setattr(relationship_extractor, "extract_relationships", _stub_no_rels)
    monkeypatch.setattr(embedder, "embed_text", _stub_embed)

    class _NullVectorStore:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def upsert(self, _rows: list[dict]) -> None:
            return None

    monkeypatch.setattr(vector_store, "VectorStore", _NullVectorStore)

    db_path = tmp_path / "evidence.duckdb"
    monkeypatch.setattr("operation_lens_v2.config.settings.duckdb_path", str(db_path))
    monkeypatch.setattr(
        "operation_lens_v2.config.settings.lancedb_path", str(tmp_path / "lance")
    )

    for pdf_path in pdf_paths:
        result = await pipeline.ingest_pdf(
            pdf_path,
            db_path=str(db_path),
            case_ref="OP_NIGHTFALL",
            case_name="Operation Nightfall",
        )
        assert result.get("pages"), f"No pages extracted from {pdf_path.name}"

    con = duck_store.init_db(str(db_path))

    # ── The core claim: RX71 KLD resolves to a single canonical VEHICLE. ──
    vehicles = con.execute(
        """
        SELECT entity_id, canonical_name
        FROM entities
        WHERE entity_type = 'VEHICLE' AND canonical_name = 'RX71 KLD'
        """
    ).fetchall()
    assert len(vehicles) == 1, (
        f"Expected one canonical 'RX71 KLD' entity, got {len(vehicles)}: {vehicles}"
    )
    vehicle_id, _name = vehicles[0]

    # ── Cross-document discovery: the vehicle has aliases from ≥ 2 docs. ──
    alias_source_docs = con.execute(
        """
        SELECT DISTINCT ea.source_doc
        FROM entity_aliases ea
        WHERE ea.entity_id = ?
        """,
        [vehicle_id],
    ).fetchall()
    source_doc_ids = {row[0] for row in alias_source_docs}
    assert len(source_doc_ids) >= 2, (
        "RX71 KLD should be attested in at least two documents for the chain to hold; "
        f"found only {source_doc_ids}"
    )

    # ── The attested documents must be the ones placing the vehicle at the chain
    # endpoints: Webb's interview (ownership) and the Depot surveillance (Khalil
    # driving). Without those two specifically the cross-doc claim is vacuous. ──
    doc_filenames = con.execute(
        f"""
        SELECT DISTINCT d.filename
        FROM documents d
        WHERE d.doc_id IN ({",".join(["?"] * len(source_doc_ids))})
        """,
        list(source_doc_ids),
    ).fetchall()
    filenames = {row[0] for row in doc_filenames}
    assert "NF-INT-001.pdf" in filenames, (
        f"RX71 KLD must be attested in Webb's interview (NF-INT-001); got {filenames}"
    )
    assert "NF-SURV-004.pdf" in filenames, (
        "RX71 KLD must be attested in the Depot surveillance log (NF-SURV-004); "
        f"got {filenames}"
    )

    # ── The vehicle mention count should reflect at least one alias per
    # attesting document — so the audit trail shows cross-doc provenance, not
    # a single noisy match in one chunk. ──
    mention_count = con.execute(
        "SELECT mention_count FROM entities WHERE entity_id = ?",
        [vehicle_id],
    ).fetchone()[0]
    assert mention_count >= 2, f"Expected ≥2 mentions of RX71 KLD, got {mention_count}"
