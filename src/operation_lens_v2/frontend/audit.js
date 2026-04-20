// Ingest Audit tab: list ingestion events, drill into one to see metadata +
// the entities that were extracted. Low-confidence candidates can be promoted
// with a single click so they join the graph as reviewed entries.

(function () {
  const auditFilterForm = document.getElementById("audit-filter-form");
  const auditCaseSelect = document.getElementById("audit-case-select");
  const auditStatusSelect = document.getElementById("audit-status-select");
  const auditFilterStatus = document.getElementById("audit-filter-status");
  const auditList = document.getElementById("audit-list");
  const auditDetail = document.getElementById("audit-detail");
  const auditEntities = document.getElementById("audit-entities");
  const auditEntitiesSummary = document.getElementById("audit-entities-summary");
  const auditEventCount = document.getElementById("audit-event-count");
  const auditLowCount = document.getElementById("audit-low-count");
  const auditThreshold = document.getElementById("audit-threshold");

  if (!auditList) return;

  let selectedEventId = "";
  let lowThreshold = 0.5;
  const selectedEntities = new Set();

  function esc(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function setStatus(tone, message) {
    if (!auditFilterStatus) return;
    auditFilterStatus.className = `inline-status inline-status-${tone}`;
    auditFilterStatus.textContent = message;
  }

  function formatDuration(ms) {
    if (ms === null || ms === undefined) return "-";
    if (ms < 1000) return `${ms} ms`;
    const seconds = ms / 1000;
    if (seconds < 60) return `${seconds.toFixed(1)} s`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes}m ${Math.floor(seconds - minutes * 60)}s`;
  }

  function formatConfidence(value) {
    if (value === null || value === undefined) return "-";
    return Number(value).toFixed(2);
  }

  function statusClass(status) {
    if (status === "success") return "audit-status-success";
    if (status === "failed") return "audit-status-failed";
    return "audit-status-skipped";
  }

  async function loadCaseOptions() {
    if (!auditCaseSelect) return;
    try {
      const resp = await fetch("/cases");
      if (!resp.ok) return;
      const data = await resp.json();
      const cases = Array.isArray(data.cases) ? data.cases : [];
      const existing = auditCaseSelect.value;
      auditCaseSelect.innerHTML = '<option value="">All cases</option>';
      for (const item of cases) {
        const option = document.createElement("option");
        option.value = item.case_ref;
        option.textContent = `${item.case_ref} - ${item.case_name || ""}`.trim();
        auditCaseSelect.appendChild(option);
      }
      if (existing) auditCaseSelect.value = existing;
    } catch (error) {
      // Non-fatal — the filter still works without case options.
    }
  }

  async function loadIngestions() {
    setStatus("muted", "Loading ingestion history...");
    const caseRef = auditCaseSelect?.value || "";
    const status = auditStatusSelect?.value || "";
    const params = new URLSearchParams();
    if (caseRef) params.set("case_ref", caseRef);
    if (status) params.set("status", status);
    params.set("limit", "100");

    try {
      const resp = await fetch(`/audit/ingestions?${params.toString()}`);
      if (!resp.ok) throw new Error(`Unable to load ingestions (${resp.status})`);
      const data = await resp.json();
      const events = Array.isArray(data.ingestions) ? data.ingestions : [];
      lowThreshold = Number(data.low_confidence_threshold ?? 0.5);
      if (auditThreshold) auditThreshold.textContent = lowThreshold.toFixed(2);
      if (auditEventCount) auditEventCount.textContent = String(events.length);
      renderList(events);
      setStatus(
        events.length ? "success" : "muted",
        events.length
          ? `${events.length} ingestion event${events.length === 1 ? "" : "s"} loaded.`
          : "No ingestion events match the current filter.",
      );
      if (events.length) {
        const defaultSelection = events.find((evt) => evt.event_id === selectedEventId) || events[0];
        selectEvent(defaultSelection.event_id);
      } else {
        selectedEventId = "";
        renderDetail(null);
        renderEntities([]);
      }
    } catch (error) {
      setStatus("error", String(error));
    }
  }

  function renderList(events) {
    if (!auditList) return;
    if (!events.length) {
      auditList.innerHTML = '<div class="timeline-empty">No ingestion events match the current filter.</div>';
      return;
    }
    auditList.innerHTML = events
      .map((event) => {
        const active = event.event_id === selectedEventId ? " active" : "";
        const label = event.filename || event.source_path || event.event_id;
        const started = event.started_at || "";
        const durationLabel = formatDuration(event.duration_ms);
        const statusLabel = event.status || "unknown";
        const entityCount = event.entities_new ?? "-";
        return `
          <button type="button" class="case-card audit-card${active}" data-event-id="${esc(event.event_id)}">
            <div class="case-card-head">
              <div>
                <div class="case-card-ref">${esc(label)}</div>
                <div class="case-card-name">${esc(event.case_ref || "unassigned")} &middot; ${esc(event.source_type || "")}</div>
              </div>
              <div class="case-card-metrics audit-status ${statusClass(event.status)}">${esc(statusLabel)}</div>
            </div>
            <div class="doc-card-meta">${esc(started)} &middot; ${esc(durationLabel)} &middot; ${esc(entityCount)} new entities</div>
          </button>
        `;
      })
      .join("");

    auditList.querySelectorAll(".audit-card").forEach((button) => {
      button.addEventListener("click", () => {
        const eventId = button.getAttribute("data-event-id") || "";
        selectEvent(eventId);
      });
    });
  }

  async function selectEvent(eventId) {
    if (!eventId) return;
    selectedEventId = eventId;
    auditList?.querySelectorAll(".audit-card").forEach((button) => {
      button.classList.toggle("active", button.getAttribute("data-event-id") === eventId);
    });
    renderDetail(null, { loading: true });
    renderEntities([], { loading: true });
    try {
      const resp = await fetch(`/audit/ingestions/${encodeURIComponent(eventId)}`);
      if (!resp.ok) throw new Error(`Unable to load ingestion (${resp.status})`);
      const data = await resp.json();
      lowThreshold = Number(data.low_confidence_threshold ?? lowThreshold);
      renderDetail(data.ingestion || null);
      renderEntities(Array.isArray(data.entities) ? data.entities : [], {
        totalLowConfidence: data.low_confidence_count ?? 0,
      });
      if (auditLowCount) {
        auditLowCount.textContent = String(data.low_confidence_count ?? 0);
      }
    } catch (error) {
      renderDetail(null, { error: String(error) });
      renderEntities([], { error: String(error) });
    }
  }

  function renderDetail(event, options = {}) {
    if (!auditDetail) return;
    if (options.loading) {
      auditDetail.innerHTML = '<div class="timeline-empty">Loading ingestion metadata...</div>';
      return;
    }
    if (options.error) {
      auditDetail.innerHTML = `<div class="timeline-empty">${esc(options.error)}</div>`;
      return;
    }
    if (!event) {
      auditDetail.innerHTML = '<div class="timeline-empty">Select an ingestion on the left to inspect its metadata.</div>';
      return;
    }
    const notesField = event.notes
      ? `<div class="audit-field"><span class="audit-field-label">Notes</span><pre class="audit-notes">${esc(event.notes)}</pre></div>`
      : "";
    const errorField = event.error_message
      ? `<div class="audit-field"><span class="audit-field-label">Error</span><div class="audit-field-value audit-error">${esc(event.error_message)}</div></div>`
      : "";
    auditDetail.innerHTML = `
      <article class="doc-card audit-detail-card">
        <div class="doc-card-head">
          <div class="doc-card-title">${esc(event.filename || event.source_path || event.event_id)}</div>
          <div class="case-card-metrics audit-status ${statusClass(event.status)}">${esc(event.status || "unknown")}</div>
        </div>
        <div class="audit-grid">
          <div class="audit-field"><span class="audit-field-label">Case</span><div class="audit-field-value">${esc(event.case_ref || "-")} <span class="audit-dim">${esc(event.case_name || "")}</span></div></div>
          <div class="audit-field"><span class="audit-field-label">Source type</span><div class="audit-field-value">${esc(event.source_type || "-")}</div></div>
          <div class="audit-field"><span class="audit-field-label">Started</span><div class="audit-field-value">${esc(event.started_at || "-")}</div></div>
          <div class="audit-field"><span class="audit-field-label">Completed</span><div class="audit-field-value">${esc(event.completed_at || "-")}</div></div>
          <div class="audit-field"><span class="audit-field-label">Duration</span><div class="audit-field-value">${esc(formatDuration(event.duration_ms))}</div></div>
          <div class="audit-field"><span class="audit-field-label">Pages</span><div class="audit-field-value">${esc(event.pages ?? "-")}</div></div>
          <div class="audit-field"><span class="audit-field-label">Chunks</span><div class="audit-field-value">${esc(event.chunks ?? "-")}</div></div>
          <div class="audit-field"><span class="audit-field-label">Entities new</span><div class="audit-field-value">${esc(event.entities_new ?? "-")}</div></div>
          <div class="audit-field"><span class="audit-field-label">Relationships new</span><div class="audit-field-value">${esc(event.relationships_new ?? "-")}</div></div>
          <div class="audit-field"><span class="audit-field-label">OCR used</span><div class="audit-field-value">${esc(event.ocr_used === null || event.ocr_used === undefined ? "-" : String(event.ocr_used))}</div></div>
          <div class="audit-field audit-field-wide"><span class="audit-field-label">Document ID</span><div class="audit-field-value audit-mono">${esc(event.doc_id || "-")}</div></div>
          <div class="audit-field audit-field-wide"><span class="audit-field-label">Source path</span><div class="audit-field-value audit-mono">${esc(event.source_path || "-")}</div></div>
        </div>
        ${notesField}
        ${errorField}
      </article>
    `;
  }

  function renderEntities(entities, options = {}) {
    if (!auditEntities) return;
    if (auditEntitiesSummary) {
      if (options.loading) {
        auditEntitiesSummary.textContent = "loading...";
      } else if (!entities.length) {
        auditEntitiesSummary.textContent = "";
      } else {
        const low = entities.filter((item) => item.low_confidence).length;
        auditEntitiesSummary.textContent = `${entities.length} total · ${low} low-confidence · threshold ${lowThreshold.toFixed(2)}`;
      }
    }
    if (options.loading) {
      auditEntities.innerHTML = '<div class="timeline-empty">Loading extracted entities...</div>';
      return;
    }
    if (options.error) {
      auditEntities.innerHTML = `<div class="timeline-empty">${esc(options.error)}</div>`;
      return;
    }
    if (!entities.length) {
      auditEntities.innerHTML = '<div class="timeline-empty">No entities are linked to this ingestion yet.</div>';
      selectedEntities.clear();
      return;
    }

    const actionBar = `
      <div class="audit-bulk-bar">
        <span id="audit-bulk-count">${selectedEntities.size} selected</span>
        <button type="button" class="btn btn-secondary audit-bulk-confirm">Confirm all</button>
        <button type="button" class="btn btn-ghost audit-bulk-reject">Reject all</button>
        <button type="button" class="btn btn-ghost audit-bulk-clear">Clear</button>
      </div>
    `;

    auditEntities.innerHTML = actionBar + entities
      .map((entity) => {
        const low = entity.low_confidence;
        const reviewed = entity.reviewed_at;
        const badgeClass = reviewed
          ? "audit-entity-badge audit-entity-reviewed"
          : low
            ? "audit-entity-badge audit-entity-low"
            : "audit-entity-badge audit-entity-ok";
        const badgeLabel = reviewed ? "Confirmed" : low ? "Low confidence" : "Accepted";
        const actionButton = reviewed
          ? `<span class="audit-entity-reviewed-label">Reviewed ${esc(entity.reviewed_at)}${entity.reviewed_by ? ` by ${esc(entity.reviewed_by)}` : ""}</span>`
          : `<div class="audit-entity-action-group">
              <button type="button" class="btn btn-secondary audit-confirm-btn" data-entity-id="${esc(entity.entity_id)}">
                ${low ? "Add to graph" : "Confirm"}
              </button>
              <button type="button" class="btn btn-ghost audit-remove-btn" data-entity-id="${esc(entity.entity_id)}" data-entity-name="${esc(entity.canonical_name || "")}" title="Remove this entity and its aliases/relationships from the graph">
                Remove
              </button>
            </div>`;
        return `
          <article class="doc-card audit-entity-card${low && !reviewed ? " audit-entity-card-low" : ""}">
            <div class="doc-card-head">
              <div>
                <div class="doc-card-title">${esc(entity.canonical_name || "(unnamed)")}</div>
                <div class="case-card-name">${esc(entity.entity_type || "UNKNOWN")} · confidence ${esc(formatConfidence(entity.confidence))} · ${esc(entity.mentions_in_doc ?? 0)} mentions in this doc</div>
              </div>
              <div class="${badgeClass}">${esc(badgeLabel)}</div>
            </div>
            <div class="doc-card-meta audit-entity-actions">
              <label><input type="checkbox" class="audit-entity-check" data-entity-id="${esc(entity.entity_id)}" ${selectedEntities.has(entity.entity_id) ? "checked" : ""} /> Select</label>
              <span class="audit-mono">${esc(entity.entity_id)}</span>
              ${actionButton}
            </div>
          </article>
        `;
      })
      .join("");

    auditEntities.querySelectorAll(".audit-entity-check").forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        const entityId = checkbox.getAttribute("data-entity-id") || "";
        if (!entityId) return;
        if (checkbox.checked) selectedEntities.add(entityId);
        else selectedEntities.delete(entityId);
        const countEl = auditEntities.querySelector("#audit-bulk-count");
        if (countEl) countEl.textContent = `${selectedEntities.size} selected`;
      });
    });

    async function runBulk(action) {
      if (!selectedEntities.size) return;
      const resp = await fetch("/audit/entities/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, entity_ids: Array.from(selectedEntities) }),
      });
      if (!resp.ok) {
        const body = await resp.text();
        throw new Error(body || `Bulk action failed (${resp.status})`);
      }
      selectedEntities.clear();
      if (selectedEventId) await selectEvent(selectedEventId);
    }

    auditEntities.querySelector(".audit-bulk-confirm")?.addEventListener("click", async () => {
      try {
        await runBulk("confirm");
      } catch (error) {
        setStatus("error", String(error));
      }
    });
    auditEntities.querySelector(".audit-bulk-reject")?.addEventListener("click", async () => {
      try {
        await runBulk("reject");
      } catch (error) {
        setStatus("error", String(error));
      }
    });
    auditEntities.querySelector(".audit-bulk-clear")?.addEventListener("click", () => {
      selectedEntities.clear();
      renderEntities(entities);
    });

    auditEntities.querySelectorAll(".audit-confirm-btn").forEach((button) => {
      button.addEventListener("click", async () => {
        const entityId = button.getAttribute("data-entity-id") || "";
        if (!entityId) return;
        button.disabled = true;
        button.textContent = "Confirming...";
        try {
          const resp = await fetch(`/audit/entities/${encodeURIComponent(entityId)}/confirm`, {
            method: "POST",
          });
          if (!resp.ok) {
            const body = await resp.text();
            throw new Error(body || `Confirm failed (${resp.status})`);
          }
          if (selectedEventId) {
            await selectEvent(selectedEventId);
          }
        } catch (error) {
          button.disabled = false;
          button.textContent = "Retry confirm";
          setStatus("error", String(error));
        }
      });
    });

    auditEntities.querySelectorAll(".audit-remove-btn").forEach((button) => {
      button.addEventListener("click", async () => {
        const entityId = button.getAttribute("data-entity-id") || "";
        if (!entityId) return;
        const entityName = button.getAttribute("data-entity-name") || "(unnamed)";
        const ok = window.confirm(
          `Remove "${entityName}" from the graph?\n\nThis also deletes its aliases, relationships, and evidence. This cannot be undone.`,
        );
        if (!ok) return;
        button.disabled = true;
        button.textContent = "Removing...";
        try {
          const resp = await fetch(`/audit/entities/${encodeURIComponent(entityId)}`, {
            method: "DELETE",
          });
          if (!resp.ok) {
            const body = await resp.text();
            throw new Error(body || `Remove failed (${resp.status})`);
          }
          if (selectedEventId) {
            await selectEvent(selectedEventId);
          }
        } catch (error) {
          button.disabled = false;
          button.textContent = "Retry remove";
          setStatus("error", String(error));
        }
      });
    });
  }

  if (auditFilterForm) {
    auditFilterForm.addEventListener("submit", (event) => {
      event.preventDefault();
      loadIngestions();
    });
  }

  window.addEventListener("lens:case-selected", (event) => {
    const caseRef = event?.detail?.caseRef || "";
    if (auditCaseSelect && caseRef) {
      auditCaseSelect.value = caseRef;
    }
  });

  (async function init() {
    await loadCaseOptions();
    await loadIngestions();
  })();
})();
