# Operation Lens v2 — Build Spec v1

Target audience: Claude Code working autonomously. Ordered by dependency, not impact. Each workstream lists: goal, files touched, interface, acceptance test.

Working directory: `C:/Users/meaburn/code/datagraph`. Source root: `src/operation_lens_v2/`.

---

## WS-0 — Preconditions

Before starting any workstream, verify:

```bash
python scripts/check_env.py   # must PASS
pytest tests/ -v              # baseline green
```

Stash the current uncommitted state on `main` first. Create branch `feature/build-spec-v1`.

---

## WS-1 — CSV ingestion

**Goal.** Accept `.csv` / `.tsv` as a first-class corpus input. Each row is a pseudo-chunk that flows through the existing NER → normaliser → relationship → embed pipeline.

**Files.**

- NEW `src/operation_lens_v2/ingestion/csv_ingest.py`
- EDIT `src/operation_lens_v2/ingestion/pipeline.py` (router)
- EDIT `src/operation_lens_v2/api/routes/ingest.py` (accept `.csv`, `.tsv`)
- EDIT `src/operation_lens_v2/frontend/case_manager.js` (accept new extensions)
- NEW `tests/unit/test_csv_ingest.py`

**Interface.**

```python
@dataclass
class CsvIngestConfig:
    text_columns: list[str] | None = None     # None = auto-detect string columns
    id_column: str | None = None              # used as doc-level row id
    max_rows: int | None = None
    delimiter: str | None = None              # None = sniff

async def ingest_csv(
    csv_path: Path,
    case_ref: str,
    duck_con: duckdb.DuckDBPyConnection,
    lance_table,
    gliner_model,
    ollama_client: httpx.AsyncClient,
    config: CsvIngestConfig,
) -> IngestionResult: ...
```

**Behaviour.**

1. `pandas.read_csv` with `dtype=str, keep_default_na=False`; sniff delimiter if not provided.
2. For each row, synthesise a chunk:
  - `text = " | ".join(f"{col}: {val}" for col, val in row.items() if val)`
  - `chunk_id = uuid5(doc_id, row_index)`
  - Store row index as `page` (1-indexed).
3. Treat the whole CSV as one `documents` row; each row becomes a `pages` row so the page pivot still works.
4. Run existing `ner_rules` + `ner_gliner` + `ner_llm` on the synthesised text. CSVs typically produce clean regex hits (phones, plates, case refs) — weight `ner_rules` highest.
5. Embed each row as one chunk; no sub-chunking (rows are already short).
6. Relationship extraction runs per row; co-occurrence fallback is valuable here because rows often list entities without verbs.

**Acceptance test.**

- Ingest a 50-row synthetic CSV with columns `subject, phone, plate, location`. Assert: 50 chunks, ≥50 entities, `documents.format = 'csv'`, each entity reachable from the graph endpoint.
- `/ingest/upload` accepts `.csv` via multipart with `content_type` in `{text/csv, application/vnd.ms-excel}`.

---

## WS-2 — Standalone image + OCR ingestion

**Goal.** Accept `.jpg`, `.jpeg`, `.png`, `.tiff`, `.bmp`. Run Tesseract, produce a one-page "document", feed through the normal pipeline.

**Files.**

- NEW `src/operation_lens_v2/ingestion/image_ingest.py`
- EDIT `src/operation_lens_v2/ingestion/pipeline.py`
- EDIT `src/operation_lens_v2/api/routes/ingest.py`
- EDIT `src/operation_lens_v2/frontend/case_manager.js`
- NEW `tests/unit/test_image_ingest.py`

**Interface.**

```python
async def ingest_image(
    image_path: Path,
    case_ref: str,
    duck_con, lance_table, gliner_model, ollama_client,
    config: ImageIngestConfig,
) -> IngestionResult: ...

@dataclass
class ImageIngestConfig:
    ocr_lang: str = "eng"
    min_chars: int = 20        # below this, mark ocr_failed=True
    thumbnail_size: tuple[int, int] = (320, 320)
```

**Behaviour.**

1. Reuse `_ocr_page_image` helper already in `extractor.py` (refactor out if currently private).
2. Store the thumbnail bytes on the `documents` row (new nullable `thumbnail_blob BLOB` column — add via ALTER). Served at `/documents/{doc_id}/thumbnail`.
3. If OCR yields `< min_chars` usable chars, persist the document but flag `ocr_failed = TRUE` and emit one ingestion_event with status `ocr_failed`. Do not silently skip.
4. Images embed in the graph as a `doc_id` node just like PDFs.

**Acceptance test.**

- Render a synthetic PIL image with text "Plate RX71 KLD at 14 Arkwright Road". Ingest. Assert entities VEHICLE `RX71 KLD` and LOCATION `14 Arkwright Road` exist and `documents.format = 'image'`.

---

## WS-3 — `.eml` / `.msg` email parser

**Goal.** Supplement the Parquet email-thread path with real email files.

**Files.**

- NEW `src/operation_lens_v2/ingestion/email_eml.py`
- NEW `src/operation_lens_v2/ingestion/email_msg.py` (uses `extract-msg`)
- EDIT `src/operation_lens_v2/ingestion/email_threads.py` — expose a shared `ThreadDocument` dataclass and reuse its chunking + EMAILED edge logic.
- EDIT `pyproject.toml` — add `extract-msg>=0.48`, `mail-parser>=3.15` (or equivalent).
- NEW `tests/unit/test_email_eml.py`, `tests/unit/test_email_msg.py`

**Interface.**

```python
def parse_eml(path: Path) -> ThreadDocument: ...
def parse_msg(path: Path) -> ThreadDocument: ...

@dataclass
class ThreadDocument:
    thread_id: str
    subject: str
    messages: list[EmailMessage]
    attachments: list[AttachmentRef]
```

**Behaviour.**

1. Parse MIME → extract `from`, `to`, `cc`, `bcc`, `date`, `subject`, plain-text body (fall back to HTML via `html2text`).
2. Group single emails into a synthetic one-message thread; multiple `.eml` sharing `In-Reply-To` / `References` become a thread.
3. Emit PERSON entities for each address, EMAIL entity for each address literal, ORGANISATION inferred from domain (only if the domain is not a public provider — maintain a stoplist).
4. Emit `EMAILED` relationship edges with direction.
5. Attachments: store as child documents, ingest through format router (PDF → WS default path; image → WS-2; CSV → WS-1). Link via `relationships` with type `ATTACHED_TO`.

**Acceptance test.**

- Ingest a hand-crafted `.eml` with 2 recipients + 1 PDF attachment. Assert 1 thread, 3 PERSON entities, 2 EMAILED edges, 1 ATTACHED_TO edge to the child PDF doc.

---

## WS-4 — Graph layout overhaul (the "squashed graph" fix)

**Goal.** The current `cose` layout with `randomize: true` at [graph.js:626-641](src/operation_lens_v2/frontend/graph.js:626) produces an unreadable blob for anything >50 nodes. Replace with a layout toolkit the analyst controls.

**Files.**

- EDIT `src/operation_lens_v2/frontend/index.html` — add layout toolbar in Atlas mode + CDN scripts for `cytoscape-fcose`, `cytoscape-cose-bilkent`, `cytoscape-dagre`, `cytoscape-navigator` (minimap).
- EDIT `src/operation_lens_v2/frontend/graph.js`
- EDIT `src/operation_lens_v2/frontend/styles.css` — minimap container, layout toolbar, confidence slider.
- NEW `src/operation_lens_v2/frontend/graph_layout.js` — layout registry + persistence.

**What "better" means concretely.**

1. **Swap default from `cose` to `fcose`.** `fcose` is the same algorithm family but with proper node separation, edge-crossing minimisation, and component handling. Single change fixes 80% of squash complaints.
2. **Layout picker (dropdown in toolbar):**
  - `fcose` (default, organic) — quality: true, nodeSeparation: 120, idealEdgeLength: 140.
  - `cose-bilkent` — for medium graphs where fcose is too spacious.
  - `dagre` — directed, hierarchical. Good for email chains (`EMAILED` flows top-down).
  - `concentric` — centrality-based, pivot entity in the middle.
  - `circle` — for small dense clusters.
  - `breadthfirst` — when the user seeds on one entity.
3. **Clustering wrappers.** Add `compoundNodes` grouping by `case_ref` or `entity_type` (toggle in toolbar). `fcose` respects compounds and produces visibly grouped sub-graphs. Changes perceived density dramatically.
4. **Confidence slider** (0.0–1.0, default 0.35). Edges below the threshold are hidden (not deleted — `display: none`). Slider re-runs the layout with the visible subgraph. Removes the co-occurrence noise that creates the blob.
5. **Progressive disclosure.** On seeded views (`/graph/entity/{name}`), show seed + 1-hop only. Double-click a node to request `/graph/expand?id=…&hops=1` and merge new elements without re-laying out existing positions (use `cy.layout({name: 'preset', ...})` for the new nodes only, or run `fcose` with `randomize: false`).
6. **Minimap** via `cytoscape-navigator`. Docked bottom-right, 180×120. Critical when the graph is bigger than the viewport.
7. **Position persistence.** Store `cy.nodes().map(n => ({id, position}))` in `sessionStorage` keyed by `case_ref + seed_entity`. On reload, layout `name: 'preset'` and apply saved positions; unknown new nodes go through `fcose` on top.
8. **Re-layout / Fit / Centre-on-selection buttons.** Three separate buttons, not one "refresh".
9. **Zoom-aware labels.** Edge labels hide when `cy.zoom() < 0.75`; node labels truncate below `< 0.5`. Already-implemented `short_label` stays; add a zoom listener.
10. **Edge bundling for parallel edges.** When two entities have 3+ relationships, collapse into one styled edge with a "×N" badge; expand on click.

**Cross-doc styling (ties to WS-5).** The layout toolbar includes a "Highlight cross-document" toggle that adds `.cross-doc-edge` to edges whose citations span >1 `doc_id`.

**Interface sketch (toolbar).**

```
[Layout: fcose ▼] [Group by: entity_type ▼] [Confidence: ━●━━ 0.35] [▢ Cross-doc] [⟲ Re-layout] [⛶ Fit] [◎ Centre]
```

**Acceptance test.**

- Load a seed entity with ≥80 connected entities. Screenshot check: no two node labels overlap at zoom 1.0 with `fcose`.
- Slide confidence to 0.7 → edge count drops, layout re-runs, no orphan islands remain.
- Reload page with same seed → node positions identical (preset restore works).
- Toggle `Group by: case_ref` → visually separable clusters with a gutter ≥40px.

---

## WS-5 — Cross-document edge styling

**Goal.** Make `.cross-doc-edge` real, not just legend text.

**Files.**

- EDIT `src/operation_lens_v2/frontend/graph.js`
- EDIT `src/operation_lens_v2/api/routes/graph.py` — ensure edge payload includes `distinct_doc_count`.

**Behaviour.**

Backend: in the graph route, for each edge aggregate `COUNT(DISTINCT re.doc_id)` from `relationship_evidence`. Add `distinct_doc_count` to edge data.

Frontend style rule:

```js
{
  selector: "edge.cross-doc-edge",
  style: {
    "line-style": "dashed",
    "line-color": "#f0b840",
    "target-arrow-color": "#f0b840",
    width: (ele) => 1.2 + Math.min(2.4, (ele.data("confidence") || 0) * 2),
    opacity: 0.9,
  },
}
```

On graph build, for every edge: `if (data.distinct_doc_count > 1) edge.addClass("cross-doc-edge")`. Toolbar toggle (WS-4) hides the class when off.

**Acceptance test.** Seed `Marcus Webb` in the demo corpus → the Webb↔Khalil edge is dashed amber (evidence spans NF-SURV-004 + NF-FIN-005 + NF-INT-001).

---

## WS-6 — Node → source-span pivot

**Goal.** Clicking a citation in the entity profile opens the source document at the correct page, with the span highlighted.

**Files.**

- NEW `src/operation_lens_v2/api/routes/documents.py` — `GET /documents/{doc_id}/page/{page}` returns `{text, span_offsets[], image_url?}`.
- NEW `src/operation_lens_v2/frontend/source_viewer.js` — modal component.
- EDIT `src/operation_lens_v2/frontend/graph.js` — replace text-only citations with buttons.
- EDIT `src/operation_lens_v2/frontend/evidence_panel.js` — same treatment.
- EDIT `src/operation_lens_v2/frontend/styles.css` — modal styles.

**Interface.**

```
GET /documents/{doc_id}/page/{page}
→ {
    doc_id, page, format, text,
    spans: [{start, end, entity_id, entity_type}],
    thumbnail_url: "/documents/{doc_id}/thumbnail",
    pdf_url: "/documents/{doc_id}/pdf"   # null for non-PDF
  }
```

Modal shows:

- Header: doc filename, page N of M, format badge.
- Body: monospace text with `<mark>` around span offsets. Embedded image thumbnail for image docs. PDF.js iframe (page-param'd) for PDF.
- Footer: "Open next page", "Open in new tab", "Copy citation".

Every `[doc_id, p.N]` in the app becomes a clickable pill. Opens modal, does not navigate away — officer keeps the graph context.

**Acceptance test.** Click the `NF-SURV-004, p.1` citation under a Khalil entity → modal opens, "Rania Khalil" is `<mark>`-highlighted in rendered text.

---

## WS-7 — Claim ↔ evidence cross-highlight

**Files.**

- EDIT `src/operation_lens_v2/frontend/evidence_panel.js`
- EDIT `src/operation_lens_v2/frontend/styles.css`

**Behaviour.**

1. When rendering claims, assign `data-claim-id="c-{index}"`. When rendering evidence cards, tag each with all matching `data-claim-id`s based on the citation list the claim references.
2. On claim click: add `.claim-active` (amber left border), remove from siblings. Then `document.querySelectorAll('[data-claim-id~="c-{index}"].evidence-card')` → add `.evidence-active`, scroll first into view with `behavior: 'smooth', block: 'center'`.
3. Reverse direction: clicking an evidence card highlights its owning claim(s) and scrolls the claims panel.
4. Pressing Esc clears all highlights.

**Acceptance test.** Ask a 3-claim query. Click claim 2 → claim 2 has amber border, panel scrolls, matching evidence cards show amber border, non-matching cards dim to 40% opacity.

---

## WS-8 — Batch confirm/reject in Audit

**Files.**

- EDIT `src/operation_lens_v2/api/routes/audit.py`
- EDIT `src/operation_lens_v2/frontend/audit.js`
- EDIT `src/operation_lens_v2/frontend/styles.css`

**New endpoint.**

```
POST /audit/entities/bulk
Body: { action: "confirm" | "reject", entity_ids: [str] }
Response: { updated: N, failed: [{entity_id, reason}] }
```

Transaction scope: one `BEGIN ... COMMIT`. If any fail, commit the successes and return the failures array — do not rollback the whole batch.

**UI.**

- Each entity row gets a checkbox (left side, before the current "Confirm"/"Reject" buttons).
- A sticky action bar appears when any checkbox is on: `[3 selected] [Confirm all] [Reject all] [Clear]`.
- Header-level "Select all in this event" checkbox.
- After bulk action: local list re-fetches that event's entities. No full-page reload.

**Acceptance test.** Seed 10 low-conf entities under one ingestion event. Select all, click Confirm all → one network call, all 10 flip to `confirmed`, UI updates.

---

## WS-9 — Persist filters & layout state

**Files.**

- NEW `src/operation_lens_v2/frontend/state_store.js` — thin sessionStorage wrapper with namespacing.
- EDIT `timeline.js`, `case_map.js`, `graph.js`, `evidence_panel.js`, `audit.js`.

**Interface.**

```js
const state = createStore("lens:v1");
state.set("timeline.filters", { entity_ids, doc_ids });
state.get("timeline.filters", defaultValue);
state.subscribe("case_ref", (newCase) => { /* clear case-scoped keys */ });
```

Keys to persist:

- `graph.layout` — `{ name, groupBy, confidenceThreshold, crossDocHighlight }`
- `graph.positions.{seed}` — `[{id, x, y}]`
- `timeline.filters.{case_ref}` — entity + doc filters
- `map.filters.{case_ref}` — basemap choice, entity filter
- `brief.lastQuery` — last question string (do NOT persist the answer — it's tied to a server query_id)

When the active case changes (`lens:case-selected` event), clear all `.{case_ref}`-namespaced keys for the prior case.

**Acceptance test.** Set timeline filter → switch to Brief → switch back. Filter is still applied, pill visible.

---

## WS-10 — End-to-end integration test

**Files.**

- NEW `tests/integration/test_pipeline_e2e.py`
- NEW `tests/integration/fixtures/` — one tiny synthetic PDF, one tiny CSV, one tiny PNG, one tiny `.eml`.
- EDIT `pyproject.toml` — `pytest` marker `integration`, slower-test config.

**Scope.**

```python
@pytest.mark.integration
async def test_full_pipeline_pdf_csv_eml_image(tmp_path):
    # 1. init DuckDB + LanceDB in tmp_path
    # 2. Ingest one of each format (4 calls)
    # 3. Assert documents, chunks, entities, relationships populated
    # 4. Run a known-answer query via run_query()
    # 5. Assert claim_validator returns ≥1 SUPPORTED claim
    # 6. Assert graph route returns the expected cross-doc edge
```

Mock Ollama calls via a stub HTTP server (`pytest-httpx` or a minimal FastAPI test app). Do NOT require a live Ollama during CI.

**Acceptance test.** `pytest -m integration` passes in < 60s without a running Ollama.

---

## WS-11 — Ingestion UX polish

**Files.**

- EDIT `src/operation_lens_v2/frontend/case_manager.js`
- EDIT `src/operation_lens_v2/frontend/index.html` — Cases + Ingest panels.
- EDIT `src/operation_lens_v2/frontend/styles.css`.

**Changes.**

1. **Drag-and-drop zone** over the existing file input. Accept multiple files. Show filename + size + format-detected badge before upload.
2. **Per-file progress** (upload bytes, then pipeline stages: extract → NER → graph → embed). Poll `/ingest/status/{doc_id}` at 2s intervals; stop polling on terminal state.
3. **Format hint** on the dropzone: "PDF, image (PNG/JPG/TIFF), CSV, email (.eml/.msg), email-thread Parquet."
4. **Actionable error surfacing.** If the backend returns `ocr_failed`, show: *"OCR produced <20 chars — the document is probably a scan of a blank form or a non-Latin script. Try Tesseract with `--lang` in settings."* Generic errors still fall back to current behaviour.

**Acceptance test.** Drop 3 mixed-format files → 3 progress rows appear → one fails with a specific reason → other two complete and show up in the case register without a page reload.

---

## Execution order

Dependencies:

```
WS-1 ─┐
WS-2 ─┼─► WS-10 (integration tests)
WS-3 ─┘

WS-5 depends on graph backend fields; do alongside WS-4.
WS-4 is mostly standalone frontend.
WS-6, WS-7 both depend on WS-4 toolbar idioms but can start in parallel.
WS-8, WS-9, WS-11 are independent leaves.
```

Suggested sprints:

- **Sprint 1 (core data):** WS-1, WS-2, WS-3. Unblocks the "CSV + images + real email" promise.
- **Sprint 2 (graph usability):** WS-4, WS-5. This is the "unsquash" sprint.
- **Sprint 3 (evidence UX):** WS-6, WS-7, WS-11.
- **Sprint 4 (polish + hardening):** WS-8, WS-9, WS-10.

---

## Non-goals for v1

Explicitly out of scope for this spec — re-raise if needed:

- Rewriting the frontend in React/Svelte. Vanilla + Cytoscape is correct for this scale.
- Multi-user auth. Single-operator offline remains the threat model.
- Cloud-only deployment. Data-sovereignty constraint stands.
- OpenRouter/cloud LLM hardening beyond the existing span-redaction guard.
- Document annotation / case notes. Can be WS-12+ after officer feedback.

---

## Definition of done (whole spec)

- All unit + integration tests green.
- `scripts/check_env.py` passes.
- Generate demo corpus → ingest → the canonical query "What connects Marcus Webb to Rania Khalil?" returns at least 2 SUPPORTED claims with dashed amber cross-doc edges visible in Atlas mode.
- Drop one each of a PDF, PNG, CSV, and `.eml` in the Cases dropzone → all four appear in the graph within 60s (on reference hardware: M3 Max or equivalent).
- Graph with 150 nodes renders readably at zoom 1.0 — no overlapping node labels, layout stable across reloads.

