const caseCreateForm = document.getElementById("case-create-form");
const caseCreateRefInput = document.getElementById("case-create-ref");
const caseCreateNameInput = document.getElementById("case-create-name");
const caseCreateDomainPackInput = document.getElementById("case-create-domain-pack");
const caseCreateStatus = document.getElementById("case-create-status");
const caseUploadForm = document.getElementById("case-upload-form");
const caseUploadSelect = document.getElementById("case-upload-select");
const caseUploadFileInput = document.getElementById("case-upload-file");
const caseUploadStatus = document.getElementById("case-upload-status");
const caseDomainPackSelect = document.getElementById("case-domain-pack-select");
const caseList = document.getElementById("case-list");
const caseDocuments = document.getElementById("case-documents");
const caseDashboard = document.getElementById("case-dashboard");
const queryCaseInput = document.getElementById("case-input");
const queryDomainPackLabel = document.getElementById("query-domain-pack-label");
const openDemoCaseButton = document.getElementById("open-demo-case-btn");
const exportMdButton = document.getElementById("export-md-btn");
const exportHtmlButton = document.getElementById("export-html-btn");
const exportPdfButton = document.getElementById("export-pdf-btn");
const caseExportStatus = document.getElementById("case-export-status");

const DEMO_CASE_REF = "OP_DEMO_SIGNAL";
const DEMO_QUERY =
  "What connects Lena Hart to South Quay Locker and what evidence supports that connection?";

let knownCases = [];
let availableDomainPacks = [];
let selectedCaseRef = "";

function escapeCaseHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setInlineStatus(element, tone, message) {
  if (!element) return;
  element.className = `inline-status inline-status-${tone}`;
  element.textContent = message;
}

function selectedCaseRecord() {
  return knownCases.find((item) => item.case_ref === selectedCaseRef) || null;
}

function renderDomainPackOptions() {
  const options = availableDomainPacks.length
    ? availableDomainPacks
    : [{ name: "base", description: "Base schema" }];
  const html = options
    .map((pack) => `<option value="${escapeCaseHtml(pack.name)}">${escapeCaseHtml(pack.name)}</option>`)
    .join("");
  if (caseCreateDomainPackInput) caseCreateDomainPackInput.innerHTML = html;
  if (caseDomainPackSelect) caseDomainPackSelect.innerHTML = html;
}

async function loadDomainPacks() {
  try {
    const resp = await fetch("/config/domain-packs");
    if (!resp.ok) return;
    const data = await resp.json();
    availableDomainPacks = Array.isArray(data.domain_packs) ? data.domain_packs : [];
  } catch (_) {
    availableDomainPacks = [];
  }
  renderDomainPackOptions();
}

function setSelectedCase(caseRef, options = {}) {
  selectedCaseRef = caseRef || "";
  if (caseUploadSelect) caseUploadSelect.value = selectedCaseRef;
  const current = selectedCaseRecord();
  if (caseDomainPackSelect && current) {
    caseDomainPackSelect.value = current.domain_pack || "base";
  }
  if (queryDomainPackLabel) {
    queryDomainPackLabel.value = current?.domain_pack || "base";
  }
  if (!options.skipQueryInput && queryCaseInput && selectedCaseRef) {
    queryCaseInput.value = selectedCaseRef;
    window.dispatchEvent(
      new CustomEvent("lens:case-selected", {
        detail: {
          case_ref: selectedCaseRef,
          domain_pack: current?.domain_pack || "base",
        },
      }),
    );
  }
  renderCaseList();
  if (!options.skipDocuments && selectedCaseRef) {
    loadDocuments(selectedCaseRef);
    loadDashboard(selectedCaseRef);
  }
}

function renderCaseList() {
  if (!caseList) return;
  if (!knownCases.length) {
    caseList.innerHTML = '<div class="timeline-empty">No cases registered yet. Create one to start uploading evidence.</div>';
    return;
  }

  caseList.innerHTML = knownCases
    .map((item) => {
      const active = item.case_ref === selectedCaseRef ? " active" : "";
      return `
        <button type="button" class="case-card${active}" data-case-ref="${escapeCaseHtml(item.case_ref)}">
          <div class="case-card-head">
            <div>
              <div class="case-card-ref">${escapeCaseHtml(item.case_ref)}</div>
              <div class="case-card-name">${escapeCaseHtml(item.case_name || "Unnamed case")}</div>
            </div>
            <div class="case-card-metrics">${escapeCaseHtml(item.doc_count || "0")} docs</div>
          </div>
          <div class="doc-card-meta">pack ${escapeCaseHtml(item.domain_pack || "base")}</div>
        </button>
      `;
    })
    .join("");

  caseList.querySelectorAll(".case-card").forEach((button) => {
    button.addEventListener("click", () => {
      const caseRef = button.getAttribute("data-case-ref") || "";
      setSelectedCase(caseRef);
    });
  });
}

function renderCaseOptions() {
  if (!caseUploadSelect) return;
  if (!knownCases.length) {
    caseUploadSelect.innerHTML = '<option value="">No cases available</option>';
    caseUploadSelect.disabled = true;
    return;
  }
  caseUploadSelect.disabled = false;
  caseUploadSelect.innerHTML = knownCases
    .map(
      (item) =>
        `<option value="${escapeCaseHtml(item.case_ref)}">${escapeCaseHtml(item.case_ref)} - ${escapeCaseHtml(item.case_name)}</option>`,
    )
    .join("");
  if (selectedCaseRef) {
    caseUploadSelect.value = selectedCaseRef;
  } else {
    selectedCaseRef = knownCases[0].case_ref;
    caseUploadSelect.value = selectedCaseRef;
  }
}

async function loadCases(preferredCaseRef = selectedCaseRef) {
  if (!caseList) return;
  try {
    const resp = await fetch("/cases");
    if (!resp.ok) throw new Error(`Unable to load cases (${resp.status})`);
    const data = await resp.json();
    knownCases = Array.isArray(data.cases) ? data.cases : [];
    renderCaseOptions();
    renderCaseList();
    const nextCaseRef =
      preferredCaseRef && knownCases.some((item) => item.case_ref === preferredCaseRef)
        ? preferredCaseRef
        : knownCases[0]?.case_ref || "";
    if (nextCaseRef) {
      setSelectedCase(nextCaseRef, { skipQueryInput: true });
    } else if (caseDocuments) {
      caseDocuments.innerHTML = '<div class="timeline-empty">Select a case to review its uploaded evidence.</div>';
    }
  } catch (error) {
    caseList.innerHTML = `<div class="timeline-empty">${escapeCaseHtml(String(error))}</div>`;
  }
}

async function loadDocuments(caseRef) {
  if (!caseDocuments || !caseRef) return;
  caseDocuments.innerHTML = '<div class="timeline-empty">Loading documents for the selected case.</div>';
  try {
    const resp = await fetch(`/cases/${encodeURIComponent(caseRef)}/documents`);
    if (!resp.ok) throw new Error(`Unable to load documents (${resp.status})`);
    const data = await resp.json();
    const docs = Array.isArray(data.documents) ? data.documents : [];
    if (!docs.length) {
      caseDocuments.innerHTML = '<div class="timeline-empty">No documents have been uploaded for this case yet.</div>';
      return;
    }
    caseDocuments.innerHTML = docs
      .map(
        (doc) => `
          <article class="doc-card">
            <div class="doc-card-head">
              <div class="doc-card-title">${escapeCaseHtml(doc.filename || doc.doc_id)}</div>
              <div class="case-card-metrics">${escapeCaseHtml(doc.source_type || "pdf")}</div>
            </div>
            <div class="doc-card-meta">${escapeCaseHtml(doc.page_count || "0")} segments</div>
            <div class="doc-card-meta">${escapeCaseHtml(doc.ingested_at || "unknown time")}</div>
            <div class="doc-card-path">${escapeCaseHtml(doc.filepath || "")}</div>
          </article>
        `,
      )
      .join("");
  } catch (error) {
    caseDocuments.innerHTML = `<div class="timeline-empty">${escapeCaseHtml(String(error))}</div>`;
  }
}

async function loadDashboard(caseRef) {
  if (!caseDashboard || !caseRef) return;
  caseDashboard.innerHTML = '<div class="timeline-empty">Loading case dashboard...</div>';
  try {
    const resp = await fetch(`/cases/${encodeURIComponent(caseRef)}/dashboard-config`);
    if (!resp.ok) throw new Error(`Unable to load dashboard (${resp.status})`);
    const data = await resp.json();
    const widgets = Array.isArray(data.widgets) ? data.widgets : [];
    if (!widgets.length) {
      caseDashboard.innerHTML = '<div class="timeline-empty">No domain widgets configured for this case.</div>';
      return;
    }
    caseDashboard.innerHTML = widgets
      .map(
        (widget) => `
          <article class="doc-card">
            <div class="doc-card-head">
              <div class="doc-card-title">${escapeCaseHtml(widget.label || widget.widget_id)}</div>
              <div class="case-card-metrics">${escapeCaseHtml(data.domain_pack || "base")}</div>
            </div>
            <div class="doc-card-meta">${escapeCaseHtml(widget.description || "")}</div>
            ${
              widget.query
                ? `<button type="button" class="btn btn-secondary case-dashboard-query" data-query="${escapeCaseHtml(widget.query)}">Open query</button>`
                : ""
            }
          </article>
        `,
      )
      .join("");
    caseDashboard.querySelectorAll(".case-dashboard-query").forEach((button) => {
      button.addEventListener("click", () => {
        const query = button.getAttribute("data-query") || "";
        window.dispatchEvent(new CustomEvent("lens:fill-query", { detail: { query } }));
      });
    });
  } catch (error) {
    caseDashboard.innerHTML = `<div class="timeline-empty">${escapeCaseHtml(String(error))}</div>`;
  }
}

async function updateCaseDomainPack(caseRef, domainPack) {
  if (!caseRef || !domainPack) return;
  try {
    const resp = await fetch(`/cases/${encodeURIComponent(caseRef)}/domain-pack`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain_pack: domainPack }),
    });
    if (!resp.ok) {
      const body = await resp.text();
      throw new Error(body || `Domain pack update failed (${resp.status})`);
    }
    await loadCases(caseRef);
    setInlineStatus(caseUploadStatus, "success", `Domain pack updated to ${domainPack}.`);
  } catch (error) {
    setInlineStatus(caseUploadStatus, "error", String(error));
  }
}

async function exportBriefing(format) {
  if (!selectedCaseRef) {
    setInlineStatus(caseExportStatus, "error", "Select a case before exporting a briefing.");
    return;
  }
  setInlineStatus(caseExportStatus, "muted", `Exporting ${format.toUpperCase()} briefing...`);
  try {
    const resp = await fetch(`/cases/${encodeURIComponent(selectedCaseRef)}/export?format=${encodeURIComponent(format)}`, {
      method: "POST",
    });
    if (!resp.ok) {
      const body = await resp.text();
      throw new Error(body || `Export failed (${resp.status})`);
    }
    const data = await resp.json();
    setInlineStatus(
      caseExportStatus,
      "success",
      `Briefing exported to ${data.file_path}.`,
    );
  } catch (error) {
    setInlineStatus(caseExportStatus, "error", String(error));
  }
}

if (caseUploadSelect) {
  caseUploadSelect.addEventListener("change", () => setSelectedCase(caseUploadSelect.value));
}

if (caseDomainPackSelect) {
  caseDomainPackSelect.addEventListener("change", () => {
    if (!selectedCaseRef) return;
    updateCaseDomainPack(selectedCaseRef, caseDomainPackSelect.value);
  });
}

if (openDemoCaseButton) {
  openDemoCaseButton.addEventListener("click", () => {
    const hasDemo = knownCases.some((item) => item.case_ref === DEMO_CASE_REF);
    if (!hasDemo) {
      setInlineStatus(caseCreateStatus, "error", "Demo case not found yet. Run `python scripts/load_demo_case.py` first.");
      return;
    }
    setSelectedCase(DEMO_CASE_REF);
    window.dispatchEvent(new CustomEvent("lens:fill-query", { detail: { query: DEMO_QUERY } }));
  });
}

if (exportMdButton) exportMdButton.addEventListener("click", () => exportBriefing("md"));
if (exportHtmlButton) exportHtmlButton.addEventListener("click", () => exportBriefing("html"));
if (exportPdfButton) exportPdfButton.addEventListener("click", () => exportBriefing("pdf"));

if (caseCreateForm) {
  caseCreateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const case_ref = (caseCreateRefInput?.value || "").trim();
    const case_name = (caseCreateNameInput?.value || "").trim();
    const domain_pack = (caseCreateDomainPackInput?.value || "base").trim() || "base";
    if (!case_ref || !case_name) {
      setInlineStatus(caseCreateStatus, "error", "Case reference and name are required.");
      return;
    }
    const submitButton = caseCreateForm.querySelector("button[type='submit']");
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Creating...";
    }
    setInlineStatus(caseCreateStatus, "muted", "Registering case record...");
    try {
      const resp = await fetch("/cases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_ref, case_name, domain_pack }),
      });
      if (!resp.ok) {
        const body = await resp.text();
        throw new Error(body || `Case creation failed (${resp.status})`);
      }
      await loadCases(case_ref);
      caseCreateForm.reset();
      renderDomainPackOptions();
      setInlineStatus(caseCreateStatus, "success", `Case ${case_ref} is ready for uploads.`);
    } catch (error) {
      setInlineStatus(caseCreateStatus, "error", String(error));
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = "Create case";
      }
    }
  });
}

if (caseUploadForm) {
  caseUploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const caseRef = (caseUploadSelect?.value || "").trim();
    const file = caseUploadFileInput?.files?.[0];
    if (!caseRef) {
      setInlineStatus(caseUploadStatus, "error", "Choose a case before uploading evidence.");
      return;
    }
    if (!file) {
      setInlineStatus(caseUploadStatus, "error", "Select an evidence file to upload.");
      return;
    }
    const submitButton = caseUploadForm.querySelector("button[type='submit']");
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Uploading...";
    }
    setInlineStatus(caseUploadStatus, "muted", `Uploading ${file.name} to ${caseRef}...`);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("case_ref", caseRef);
    const knownCase = knownCases.find((item) => item.case_ref === caseRef);
    if (knownCase?.case_name) formData.append("case_name", knownCase.case_name);
    try {
      const resp = await fetch("/ingest/upload", { method: "POST", body: formData });
      if (!resp.ok) {
        const body = await resp.text();
        throw new Error(body || `Upload failed (${resp.status})`);
      }
      const data = await resp.json();
      await loadCases(caseRef);
      await loadDocuments(caseRef);
      caseUploadForm.reset();
      renderCaseOptions();
      if (caseUploadSelect) caseUploadSelect.value = caseRef;
      setInlineStatus(
        caseUploadStatus,
        "success",
        `${data.filename || file.name} stored and ingested for ${caseRef}.`,
      );
    } catch (error) {
      setInlineStatus(caseUploadStatus, "error", String(error));
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = "Upload and ingest";
      }
    }
  });
}

loadDomainPacks().finally(() => {
  loadCases();
});
