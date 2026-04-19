const caseCreateForm = document.getElementById("case-create-form");
const caseCreateRefInput = document.getElementById("case-create-ref");
const caseCreateNameInput = document.getElementById("case-create-name");
const caseCreateStatus = document.getElementById("case-create-status");
const caseUploadForm = document.getElementById("case-upload-form");
const caseUploadSelect = document.getElementById("case-upload-select");
const caseUploadFileInput = document.getElementById("case-upload-file");
const caseUploadStatus = document.getElementById("case-upload-status");
const caseList = document.getElementById("case-list");
const caseDocuments = document.getElementById("case-documents");
const queryCaseInput = document.getElementById("case-input");

let knownCases = [];
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

function setSelectedCase(caseRef, options = {}) {
  selectedCaseRef = caseRef || "";
  if (caseUploadSelect) {
    caseUploadSelect.value = selectedCaseRef;
  }
  if (!options.skipQueryInput && queryCaseInput && selectedCaseRef) {
    queryCaseInput.value = selectedCaseRef;
  }
  renderCaseList();
  if (!options.skipDocuments && selectedCaseRef) {
    loadDocuments(selectedCaseRef);
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
    if (!resp.ok) {
      throw new Error(`Unable to load cases (${resp.status})`);
    }
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
    if (caseDocuments) {
      caseDocuments.innerHTML = '<div class="timeline-empty">Documents are unavailable because the case register could not be loaded.</div>';
    }
  }
}

async function loadDocuments(caseRef) {
  if (!caseDocuments || !caseRef) return;
  caseDocuments.innerHTML = '<div class="timeline-empty">Loading documents for the selected case.</div>';
  try {
    const resp = await fetch(`/cases/${encodeURIComponent(caseRef)}/documents`);
    if (!resp.ok) {
      throw new Error(`Unable to load documents (${resp.status})`);
    }
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
              <div class="case-card-metrics">${escapeCaseHtml(doc.page_count || "0")} pages</div>
            </div>
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

if (caseUploadSelect) {
  caseUploadSelect.addEventListener("change", () => {
    setSelectedCase(caseUploadSelect.value);
  });
}

if (caseCreateForm) {
  caseCreateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const case_ref = (caseCreateRefInput?.value || "").trim();
    const case_name = (caseCreateNameInput?.value || "").trim();
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
        body: JSON.stringify({ case_ref, case_name }),
      });
      if (!resp.ok) {
        const body = await resp.text();
        throw new Error(body || `Case creation failed (${resp.status})`);
      }
      await loadCases(case_ref);
      caseCreateForm.reset();
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
      setInlineStatus(caseUploadStatus, "error", "Choose a case before uploading a document.");
      return;
    }
    if (!file) {
      setInlineStatus(caseUploadStatus, "error", "Select a PDF or CSV document to upload.");
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
    if (knownCase?.case_name) {
      formData.append("case_name", knownCase.case_name);
    }

    try {
      const resp = await fetch("/ingest/upload", {
        method: "POST",
        body: formData,
      });
      if (!resp.ok) {
        const body = await resp.text();
        throw new Error(body || `Upload failed (${resp.status})`);
      }
      const data = await resp.json();
      await loadCases(caseRef);
      await loadDocuments(caseRef);
      caseUploadForm.reset();
      renderCaseOptions();
      caseUploadSelect.value = caseRef;
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

loadCases();
