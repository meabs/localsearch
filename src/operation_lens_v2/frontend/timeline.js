// timeline.js - Mode 3: chronological event timeline
// Fetches /timeline and renders a vertical timeline with filterable events.

(function () {
  const submitBtn = document.getElementById("timeline-submit");
  const entityInput = document.getElementById("timeline-entity");
  const docIdsInput = document.getElementById("timeline-doc-ids");
  const container = document.getElementById("timeline-container");
  const statusEl = document.getElementById("timeline-status");
  const state = window.LensStateStore?.createStore?.("lens:v1");

  if (!submitBtn || !container || !statusEl) return;

  const initial = state?.get("timeline.filters", null);
  if (initial) {
    if (entityInput) entityInput.value = initial.entity || "";
    if (docIdsInput) docIdsInput.value = initial.doc_ids || "";
  }

  submitBtn.addEventListener("click", async () => {
    const entity = entityInput ? entityInput.value.trim() : "";
    const docIds = docIdsInput ? docIdsInput.value.trim() : "";
    state?.set("timeline.filters", { entity, doc_ids: docIds });

    const params = new URLSearchParams();
    if (entity) params.set("entity", entity);
    if (docIds) params.set("doc_ids", docIds);
    params.set("limit", "100");

    statusEl.textContent = "Loading timeline...";
    container.innerHTML = '<div class="timeline-empty">Building dated sequence from the current filter.</div>';

    try {
      const resp = await fetch(`/timeline?${params.toString()}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      renderTimeline(data);
    } catch (err) {
      statusEl.textContent = `Error: ${err.message}`;
      container.innerHTML = '<div class="timeline-empty">Timeline request failed. Please retry once the backend is ready.</div>';
    }
  });

  function renderTimeline(data) {
    const events = data.events || [];
    statusEl.textContent =
      `${events.length} event${events.length !== 1 ? "s" : ""}` +
      (data.entity_filter ? ` mentioning "${data.entity_filter}"` : "");

    if (!events.length) {
      container.innerHTML = '<div class="timeline-empty">No dated events found for the current filter.</div>';
      return;
    }

    const ul = document.createElement("ul");
    ul.className = "timeline-list";

    events.forEach((ev) => {
      const li = document.createElement("li");
      li.className = "timeline-item";

      const dot = document.createElement("div");
      dot.className = "timeline-dot";

      const content = document.createElement("div");
      content.className = "timeline-content";

      const header = document.createElement("div");
      header.className = "timeline-header";
      header.innerHTML =
        `<span class="timeline-date">${escHtml(ev.date)}</span>` +
        `<span class="timeline-source">${escHtml(ev.filename)} p.${ev.page}</span>`;

      const excerpt = document.createElement("p");
      excerpt.className = "timeline-excerpt";
      excerpt.textContent = ev.excerpt;

      content.appendChild(header);
      content.appendChild(excerpt);
      li.appendChild(dot);
      li.appendChild(content);
      ul.appendChild(li);
    });

    container.innerHTML = "";
    container.appendChild(ul);
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
