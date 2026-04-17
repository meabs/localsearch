// Phase 8 panels: path finder, centrality leaderboard, community detection.
// Kept as a standalone module so graph.js stays focused on the Cytoscape
// network view — these helpers render into their own <slot> elements and
// dispatch `lens:highlight-path` so the network view can draw the overlay.
(function () {
  const pathForm = document.getElementById("path-finder-form");
  const pathResults = document.getElementById("path-finder-results");
  const centralityResults = document.getElementById("centrality-results");
  const centralityMetric = document.getElementById("centrality-metric");
  const centralityRefresh = document.getElementById("centrality-refresh");
  const communityResults = document.getElementById("community-results");
  const communityRefresh = document.getElementById("community-refresh");
  const minConfidenceInput = document.getElementById("insights-min-confidence");
  const excludeTypesInput = document.getElementById("insights-exclude-types");

  if (!pathForm && !centralityResults && !communityResults) return;

  function apiUrl(path) {
    const explicit = window.LENS_API_BASE;
    if (explicit && typeof explicit === "string") {
      return `${explicit.replace(/\/+$/, "")}${path}`;
    }
    if (window.location && window.location.port === "8002") {
      return `${window.location.protocol}//${window.location.hostname || "127.0.0.1"}:8000${path}`;
    }
    return path;
  }

  function escHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function currentFilters() {
    const minConf = Number.parseFloat(minConfidenceInput?.value || "0") || 0;
    const exclude = (excludeTypesInput?.value || "").trim();
    const params = new URLSearchParams();
    if (minConf > 0) params.set("min_confidence", String(minConf));
    if (exclude) params.set("exclude_relation_types", exclude);
    return params;
  }

  async function runPathFinder(source, target) {
    const params = currentFilters();
    params.set("source", source);
    params.set("target", target);
    params.set("k", "3");
    const resp = await fetch(apiUrl(`/graph/path?${params.toString()}`));
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const body = await resp.json();
        if (body?.detail) detail = body.detail;
      } catch (_) {}
      throw new Error(detail);
    }
    return resp.json();
  }

  function renderPath(path, idx) {
    const nodes = path.nodes || [];
    const edges = path.edges || [];
    const ribbon = nodes
      .map((n) => `<span class="path-node">${escHtml(n.canonical_name || n.entity_id)}</span>`)
      .join('<span class="path-arrow">&rarr;</span>');
    const hopDetails = edges
      .map((edge) => {
        const citation = (edge.citations || [])[0];
        const citationLine = citation
          ? `${citation.doc_name || citation.doc_id} p.${citation.page ?? "?"}`
          : "No direct span indexed";
        const snippet = citation?.span_text
          ? `<div class="path-snippet">${escHtml(citation.span_text.slice(0, 240))}</div>`
          : "";
        return `
          <div class="path-hop">
            <div class="path-hop-kind">${escHtml(edge.relation_type || "UNKNOWN")}</div>
            <div class="path-hop-meta">conf ${(edge.confidence || 0).toFixed(2)} &middot; ${edge.evidence_count || 0} span(s) &middot; ${escHtml(citationLine)}</div>
            ${snippet}
          </div>`;
      })
      .join("");
    return `
      <div class="path-result">
        <div class="path-result-head">
          <span class="path-result-index">Path ${idx + 1}</span>
          <span class="path-result-stats">${path.hops} hop(s) &middot; weakest conf ${(path.min_confidence || 0).toFixed(2)}</span>
          <button type="button" class="btn btn-secondary path-highlight-btn" data-path-index="${idx}">Highlight</button>
        </div>
        <div class="path-ribbon">${ribbon}</div>
        <div class="path-hops">${hopDetails}</div>
      </div>`;
  }

  if (pathForm) {
    pathForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const source = document.getElementById("path-source")?.value?.trim();
      const target = document.getElementById("path-target")?.value?.trim();
      if (!source || !target) {
        pathResults.innerHTML = '<div class="node-empty">Enter both a source and target entity.</div>';
        return;
      }
      pathResults.innerHTML = '<div class="node-empty">Finding paths...</div>';
      try {
        const data = await runPathFinder(source, target);
        if (!data.paths?.length) {
          pathResults.innerHTML = '<div class="node-empty">No path found. Try relaxing the minimum confidence or excluding fewer relation types.</div>';
          return;
        }
        pathResults.innerHTML = data.paths.map((p, idx) => renderPath(p, idx)).join("");
        pathResults.querySelectorAll(".path-highlight-btn").forEach((btn) => {
          btn.addEventListener("click", () => {
            const idx = Number(btn.getAttribute("data-path-index") || 0);
            const path = data.paths[idx];
            if (!path) return;
            window.dispatchEvent(
              new CustomEvent("lens:highlight-path", { detail: { path } }),
            );
          });
        });
      } catch (err) {
        pathResults.innerHTML = `<div class="node-empty">Path search failed: ${escHtml(String(err))}</div>`;
      }
    });
  }

  async function loadCentrality() {
    if (!centralityResults) return;
    const metric = centralityMetric?.value || "degree";
    const params = currentFilters();
    params.set("metric", metric);
    params.set("top_n", "15");
    centralityResults.innerHTML = '<div class="node-empty">Scoring entities...</div>';
    try {
      const resp = await fetch(apiUrl(`/graph/centrality?${params.toString()}`));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (!data.entities?.length) {
        centralityResults.innerHTML = '<div class="node-empty">No central entities found at current filters.</div>';
        return;
      }
      centralityResults.innerHTML = data.entities
        .map(
          (ent, idx) => `
            <button type="button" class="centrality-row lens-open-path-btn" data-entity="${escHtml(ent.canonical_name || ent.entity_id)}">
              <span class="centrality-rank">${idx + 1}</span>
              <span class="centrality-name">${escHtml(ent.canonical_name || ent.entity_id)}</span>
              <span class="centrality-sub">${escHtml(ent.entity_type || "?")} &middot; deg ${ent.degree}</span>
              <span class="centrality-score">${Number(ent.score || 0).toFixed(4)}</span>
            </button>`,
        )
        .join("");
      centralityResults.querySelectorAll(".lens-open-path-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          const name = btn.getAttribute("data-entity") || "";
          const sourceInput = document.getElementById("path-source");
          if (sourceInput) sourceInput.value = name;
        });
      });
    } catch (err) {
      centralityResults.innerHTML = `<div class="node-empty">Centrality load failed: ${escHtml(String(err))}</div>`;
    }
  }

  async function loadCommunities() {
    if (!communityResults) return;
    const params = currentFilters();
    communityResults.innerHTML = '<div class="node-empty">Detecting communities...</div>';
    try {
      const resp = await fetch(apiUrl(`/graph/communities?${params.toString()}`));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (!data.communities?.length) {
        communityResults.innerHTML = '<div class="node-empty">No communities found (need at least one edge cluster).</div>';
        return;
      }
      communityResults.innerHTML = `
        <div class="community-summary">Modularity ${Number(data.modularity || 0).toFixed(3)} &middot; ${data.group_count} group(s) detected</div>
        ${data.communities
          .map(
            (c) => `
              <div class="community-row">
                <div class="community-head">
                  <span class="community-id">Cluster ${c.community_id + 1}</span>
                  <span class="community-size">${c.size} member(s)</span>
                </div>
                <div class="community-members">${(c.members || [])
                  .map((m) => `<span class="answer-pill">${escHtml(m.canonical_name || m.entity_id)}</span>`)
                  .join("")}</div>
              </div>`,
          )
          .join("")}
      `;
    } catch (err) {
      communityResults.innerHTML = `<div class="node-empty">Community detection failed: ${escHtml(String(err))}</div>`;
    }
  }

  centralityMetric?.addEventListener("change", loadCentrality);
  centralityRefresh?.addEventListener("click", loadCentrality);
  communityRefresh?.addEventListener("click", loadCommunities);

  // Kick off initial loads if the insights drawer is visible on first paint.
  if (centralityResults) loadCentrality().catch(() => {});
  if (communityResults) loadCommunities().catch(() => {});
})();
