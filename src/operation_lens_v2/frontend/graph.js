(function () {
  if (window.cytoscape) {
    try {
      if (window.cytoscapeFcose) window.cytoscape.use(window.cytoscapeFcose);
      if (window.cytoscapeCoseBilkent) window.cytoscape.use(window.cytoscapeCoseBilkent);
      if (window.cytoscapeDagre) window.cytoscape.use(window.cytoscapeDagre);
      // navigator plugin is optional and not always exposed as a plain extension factory.
      // Registering an incompatible bundle can cause core init errors (isHeadless on null).
    } catch (_) {
      // Best effort plugin registration - graph still renders with built-ins.
    }
  }

  const cyRoot = document.getElementById("cy");
  const detail = document.getElementById("node-detail");
  const form = document.getElementById("graph-form");
  const graphModeSelect = document.getElementById("graph-mode-select");
  const input = document.getElementById("graph-entity-input");
  const caseSelect = document.getElementById("graph-case-select");
  const layoutSelect = document.getElementById("graph-layout-select");
  const confidenceSlider = document.getElementById("graph-confidence");
  const crossDocToggle = document.getElementById("graph-cross-doc-toggle");
  const relayoutBtn = document.getElementById("graph-relayout-btn");
  const fitBtn = document.getElementById("graph-fit-btn");
  const graphPanelOverline = document.getElementById("graph-panel-overline");
  const graphPanelTitle = document.getElementById("graph-panel-title");
  const graphPanelCopy = document.getElementById("graph-panel-copy");
  const graphPanelChip = document.getElementById("graph-panel-chip");
  const graphSubmitBtn = document.getElementById("graph-submit-btn");
  const graphCanvasStatus = document.getElementById("graph-canvas-status");
  if (!cyRoot || !detail) return;

  let cy = null;
  let detailRequestSeq = 0;
  // The Atlas tracks its own case selection so operators can switch between
  // ingested operations (or pick "All operations") without leaving the graph
  // view. `null` means "follow whatever case_manager.js picked globally" so
  // the first render after boot still lines up with the Cases desk.
  let atlasCaseOverride = null;
  let knownAtlasCases = [];
  const layoutStore = window.LensGraphLayout;
  const savedLayout = layoutStore?.load?.() || {};
  const graphView = {
    mode: savedLayout.mode || "entity",
    layout: savedLayout.layout || "fcose",
    confidence: Number(savedLayout.confidence ?? 0.35),
    crossDoc: savedLayout.crossDoc ?? true,
  };
  // Colours come from the backend schema (first-party) with a deterministic
  // HSL hash fallback - any new entity type gets a stable colour automatically.
  const FALLBACK_COLOR = "#5b6b7d";
  const colorCache = Object.create(null);
  let schemaColors = Object.create(null);

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

  function hashString(value) {
    let h = 0;
    for (let i = 0; i < value.length; i += 1) {
      h = (h * 31 + value.charCodeAt(i)) | 0;
    }
    return Math.abs(h);
  }

  function colorFor(entityType) {
    const key = String(entityType || "UNKNOWN").toUpperCase();
    if (key === "MEDIA_ASSET") return "#d58b24";
    if (key === "MEDIA_FRAME") return "#8394a7";
    if (key === "MEDIA_OBJECT") return "#4db6c8";
    if (schemaColors[key]) return schemaColors[key];
    if (colorCache[key]) return colorCache[key];
    if (key === "UNKNOWN") return FALLBACK_COLOR;
    const h = hashString(key);
    const hue = h % 360;
    const sat = 42 + (h % 18);
    const light = 44 + ((h >> 3) % 10);
    const color = `hsl(${hue}, ${sat}%, ${light}%)`;
    colorCache[key] = color;
    return color;
  }

  async function loadSchemaColors() {
    try {
      const resp = await fetch(apiUrl("/graph/schema"));
      if (!resp.ok) return;
      const data = await resp.json();
      const next = Object.create(null);
      (data.entity_types || []).forEach((item) => {
        if (item?.name && item?.color) next[item.name.toUpperCase()] = item.color;
      });
      schemaColors = next;
    } catch (_) {
      // Offline or schema route missing - fallback hash colours still work.
    }
  }

  function renderCitationList(citations) {
    if (!citations || !citations.length) {
      return '<div style="margin-top:10px;color:var(--ink-muted);font-size:13px;line-height:1.45;">No exact supporting citations available for this selection.</div>';
    }
    return citations
      .map((citation) => `
        <div style="margin-top:10px;padding:10px 12px;border:1px solid var(--border-0);background:rgba(255,255,255,0.03);border-radius:10px;">
          <div style="font-size:11px;letter-spacing:.08em;color:var(--amber-100);text-transform:uppercase;">
            ${(citation.doc_name || citation.doc_id || "unknown-doc")} p.${citation.page ?? "?"}
            ${citation.extraction_method ? ` // ${citation.extraction_method}` : ""}
          </div>
          <div style="margin-top:6px;font-size:13px;color:var(--ink-soft);line-height:1.55;">
            ${citation.span_text || "No span excerpt available."}
          </div>
          <button type="button" class="lens-open-source" data-doc-id="${escHtml(citation.doc_id || "")}" data-page="${escHtml(citation.page ?? 1)}" style="margin-top:8px;padding:4px 8px;border:1px solid var(--border-1);border-radius:8px;background:rgba(255,255,255,0.03);color:var(--ink-soft);font-size:11px;cursor:pointer;">Open source</button>
        </div>
      `)
      .join("");
  }

  function escHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function fetchAttachments(entityId) {
    const resp = await fetch(apiUrl(`/graph/entity/${encodeURIComponent(entityId)}/attachments`));
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  async function uploadAttachment(entityId, file) {
    const payload = new FormData();
    payload.append("file", file, file.name);
    const resp = await fetch(apiUrl(`/graph/entity/${encodeURIComponent(entityId)}/attachments`), {
      method: "POST",
      body: payload,
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const data = await resp.json();
        if (data?.detail) detail = data.detail;
      } catch (_) {}
      throw new Error(detail);
    }
    return resp.json();
  }

  async function fetchVehicleTrack(entityId) {
    const resp = await fetch(apiUrl(`/graph/entity/${encodeURIComponent(entityId)}/vehicle-track`));
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  async function fetchEntityProfile(entityId) {
    const resp = await fetch(apiUrl(`/graph/entity/${encodeURIComponent(entityId)}/profile`));
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  function renderProfileSummary(profile) {
    const aliases = (profile.aliases || []).slice(0, 10);
    const docs = (profile.documents || []).slice(0, 8);
    const links = (profile.links || []).slice(0, 8);
    const timeline = (profile.timeline || []).slice(0, 6);

    const aliasHtml = aliases.length
      ? aliases.map((alias) => `<span class="answer-pill" style="margin-right:6px;margin-bottom:6px;">${escHtml(alias)}</span>`).join("")
      : '<div class="node-empty" style="margin-top:8px;">No aliases recorded.</div>';

    const docsHtml = docs.length
      ? docs
          .map(
            (doc) => `<div style="margin-top:8px;padding:8px 10px;border:1px solid var(--border-0);border-radius:10px;background:rgba(255,255,255,0.02);">
              <div style="font-size:12px;color:var(--ink-soft);">${escHtml(doc.filename || doc.doc_id)}</div>
              <div style="margin-top:4px;font-size:11px;color:var(--ink-faint);letter-spacing:.08em;text-transform:uppercase;">mentions ${Number(doc.mention_count || 0)} // first page ${escHtml(doc.first_page ?? "?")}</div>
              <button type="button" class="lens-open-query-btn" data-query="${escHtml(
                `Summarise key findings from ${doc.filename || doc.doc_id}`,
              )}" style="margin-top:8px;padding:6px 10px;border:1px solid var(--border-1);border-radius:10px;background:rgba(255,255,255,0.03);color:var(--amber-100);font-size:11px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;">Open in Query</button>
            </div>`,
          )
          .join("")
      : '<div class="node-empty" style="margin-top:8px;">No source documents recorded.</div>';

    const linksHtml = links.length
      ? links
          .map((link) => {
            const connected = link.linked_entity || {};
            const firstCitation = (link.citations || [])[0];
            const citationLine = firstCitation
              ? `${firstCitation.doc_name || firstCitation.doc_id} p.${firstCitation.page ?? "?"}`
              : "No citation";
            return `<div style="margin-top:8px;padding:8px 10px;border:1px solid var(--border-0);border-radius:10px;background:rgba(255,255,255,0.02);">
              <div style="font-size:12px;color:var(--ink-soft);">${escHtml(link.relation_type)} -> ${escHtml(connected.canonical_name || connected.entity_id || "unknown")}</div>
              <div style="margin-top:4px;font-size:11px;color:var(--ink-faint);letter-spacing:.08em;text-transform:uppercase;">conf ${(Number(link.confidence || 0)).toFixed(2)} // ${escHtml(citationLine)}</div>
              <button type="button" class="lens-open-query-btn" data-query="${escHtml(
                `What connects ${profile.entity?.canonical_name || "this entity"} to ${connected.canonical_name || connected.entity_id || "this linked entity"}?`,
              )}" style="margin-top:8px;padding:6px 10px;border:1px solid var(--border-1);border-radius:10px;background:rgba(255,255,255,0.03);color:var(--amber-100);font-size:11px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;">Open in Query</button>
            </div>`;
          })
          .join("")
      : '<div class="node-empty" style="margin-top:8px;">No linked entities found.</div>';

    const timelineHtml = timeline.length
      ? timeline
          .map(
            (event) => `<div style="margin-top:8px;padding:8px 10px;border:1px solid var(--border-0);border-radius:10px;background:rgba(255,255,255,0.02);">
              <div style="font-size:12px;color:var(--amber-100);">${escHtml(event.seen_at || "unknown time")}</div>
              <div style="margin-top:4px;font-size:11px;color:var(--ink-soft);">${escHtml(event.filename || event.doc_id)} p.${escHtml(event.page ?? "?")}</div>
              <button type="button" class="lens-open-query-btn" data-query="${escHtml(
                `Build a timeline movement summary for ${profile.entity?.canonical_name || "this entity"}`,
              )}" style="margin-top:8px;padding:6px 10px;border:1px solid var(--border-1);border-radius:10px;background:rgba(255,255,255,0.03);color:var(--amber-100);font-size:11px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;">Open in Query</button>
            </div>`,
          )
          .join("")
      : '<div class="node-empty" style="margin-top:8px;">No dated events found for this entity.</div>';

    return `
      <div style="margin-top:12px;color:var(--ink-faint);font-size:11px;letter-spacing:.1em;text-transform:uppercase;">Aliases</div>
      <div style="margin-top:8px;display:flex;flex-wrap:wrap;">${aliasHtml}</div>
      <div style="margin-top:12px;color:var(--ink-faint);font-size:11px;letter-spacing:.1em;text-transform:uppercase;">Documents</div>
      ${docsHtml}
      <div style="margin-top:12px;color:var(--ink-faint);font-size:11px;letter-spacing:.1em;text-transform:uppercase;">Linked entities</div>
      ${linksHtml}
      <div style="margin-top:12px;color:var(--ink-faint);font-size:11px;letter-spacing:.1em;text-transform:uppercase;">Timeline</div>
      ${timelineHtml}
    `;
  }

  function renderAttachments(container, node) {
    if (!container || !node?.id) return;
    const dropzone = container.querySelector(".entity-attach-dropzone");
    const list = container.querySelector(".entity-attach-list");
    const status = container.querySelector(".entity-attach-status");
    const pickerBtn = container.querySelector(".entity-attach-picker-btn");
    if (!dropzone || !list || !status) return;

    async function refresh() {
      try {
        const data = await fetchAttachments(node.id);
        const items = data.attachments || [];
        if (!items.length) {
          list.innerHTML = '<div class="node-empty" style="margin-top:8px;">No photos attached.</div>';
          return;
        }
        list.innerHTML = items
          .map(
            (a) => `
              <a class="entity-attach-item" href="${apiUrl(`/graph/attachments/${encodeURIComponent(a.attachment_id)}`)}" target="_blank" rel="noopener">
                <img src="${apiUrl(`/graph/attachments/${encodeURIComponent(a.attachment_id)}`)}" alt="${escHtml(a.filename)}" />
                <div class="entity-attach-meta">
                  <div class="entity-attach-name">${escHtml(a.filename)}</div>
                  <div class="entity-attach-sub">${escHtml(a.mime_type)} // ${Math.max(1, Math.round((a.file_size || 0) / 1024))} KB</div>
                </div>
              </a>
            `,
          )
          .join("");
      } catch (err) {
        list.innerHTML = `<div class="node-empty" style="margin-top:8px;">Attachment load failed: ${escHtml(String(err))}</div>`;
      }
    }

    async function handleFiles(files) {
      if (!files?.length) return;
      const photoFiles = Array.from(files).filter((f) => (f.type || "").startsWith("image/"));
      if (!photoFiles.length) {
        status.textContent = "Only image files are accepted.";
        return;
      }
      status.textContent = `Uploading ${photoFiles.length} photo(s)...`;
      for (const photo of photoFiles) {
        try {
          await uploadAttachment(node.id, photo);
        } catch (err) {
          status.textContent = `Upload failed: ${String(err)}`;
          return;
        }
      }
      status.textContent = "Upload complete.";
      await refresh();
    }

    dropzone.addEventListener("dragover", (evt) => {
      evt.preventDefault();
      dropzone.classList.add("drag-over");
    });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag-over"));
    dropzone.addEventListener("drop", (evt) => {
      evt.preventDefault();
      dropzone.classList.remove("drag-over");
      handleFiles(evt.dataTransfer?.files);
    });
    const fileInput = dropzone.querySelector('input[type="file"]');
    dropzone.addEventListener("click", () => {
      fileInput?.click();
    });
    pickerBtn?.addEventListener("click", (evt) => {
      evt.preventDefault();
      fileInput?.click();
    });
    fileInput?.addEventListener("change", () => handleFiles(fileInput.files));
    refresh().catch(() => {});
  }

  function renderVehicleTrack(container, node) {
    if (!container || !node?.id || !window.L) return;
    fetchVehicleTrack(node.id)
      .then((data) => {
        const points = data.points || [];
        if (!points.length) {
          container.innerHTML =
            '<div class="node-empty" style="margin-top:8px;">No geocoded sightings found for this vehicle.</div>';
          return;
        }
        const mapEl = document.createElement("div");
        mapEl.className = "vehicle-track-map";
        container.innerHTML = "";
        container.appendChild(mapEl);
        const map = L.map(mapEl, { zoomControl: true, attributionControl: true });
        const street = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        });
        street.addTo(map);
        const latLngs = points.map((p) => [Number(p.latitude), Number(p.longitude)]);
        L.polyline(latLngs, { color: "#ffd17d", weight: 2, opacity: 0.9 }).addTo(map);
        points.forEach((p, idx) => {
          const marker = L.circleMarker([Number(p.latitude), Number(p.longitude)], {
            radius: 4,
            color: "#f1a73f",
            weight: 1,
            fillColor: "#ffd17d",
            fillOpacity: 0.92,
          }).addTo(map);
          marker.bindPopup(
            `<strong>${escHtml(p.location_name || "Location")}</strong><br/>` +
              `T+${idx + 1} // ${escHtml(p.seen_at || "time unknown")}<br/>` +
              `${escHtml(p.filename || p.doc_id)} p.${escHtml(p.page)}`,
          );
        });
        if (latLngs.length === 1) map.setView(latLngs[0], 13);
        else map.fitBounds(latLngs, { padding: [18, 18] });
        setTimeout(() => map.invalidateSize(), 80);

        const timeline = document.createElement("div");
        timeline.className = "vehicle-track-points";
        timeline.innerHTML = points
          .slice(0, 10)
          .map(
            (p, idx) => `<div class="vehicle-track-point">
              <span>T+${idx + 1}</span>
              <span>${escHtml(p.seen_at || "time unknown")}</span>
              <span>${escHtml(p.location_name || "-")}</span>
            </div>`,
          )
          .join("");
        container.appendChild(timeline);
      })
      .catch((err) => {
        container.innerHTML = `<div class="node-empty" style="margin-top:8px;">Vehicle track failed: ${escHtml(String(err))}</div>`;
      });
  }

  function frameIdForNode(node) {
    if (!node) return "";
    if (node.frame_id) return String(node.frame_id);
    const id = String(node.id || "");
    return id.startsWith("frame:") ? id.slice("frame:".length) : "";
  }

  function mediaFrameUrl(frameId) {
    return apiUrl(`/graph/media-frames/${encodeURIComponent(frameId)}`);
  }

  function mediaPreviewHtml(node, { compact = false } = {}) {
    const frameId = frameIdForNode(node);
    if (!frameId) return "";
    const bbox = Array.isArray(node?.bbox) ? escHtml(JSON.stringify(node.bbox)) : "";
    return `
      <div class="media-frame-image-wrap ${compact ? "compact" : ""}">
        <img class="media-frame-img" src="${mediaFrameUrl(frameId)}" alt="${escHtml(node.label || "media frame")}" loading="lazy" />
        ${
          bbox
            ? `<span class="media-bbox active" data-bbox="${bbox}" title="${escHtml(node.label || "detection")}"></span>`
            : ""
        }
      </div>
    `;
  }

  function positionMediaBoxes(root) {
    (root || document).querySelectorAll(".media-frame-image-wrap").forEach((wrap) => {
      const img = wrap.querySelector(".media-frame-img");
      if (!img) return;
      img.addEventListener("error", () => {
        wrap.classList.add("frame-error");
      });
      const update = () => {
        const width = img.naturalWidth || 1;
        const height = img.naturalHeight || 1;
        wrap.querySelectorAll(".media-bbox[data-bbox]").forEach((box) => {
          let bbox = [];
          try {
            bbox = JSON.parse(box.getAttribute("data-bbox") || "[]");
          } catch (_) {
            bbox = [];
          }
          if (!Array.isArray(bbox) || bbox.length !== 4) return;
          const [x1, y1, x2, y2] = bbox.map(Number);
          box.style.left = `${Math.max(0, Math.min(100, (x1 / width) * 100))}%`;
          box.style.top = `${Math.max(0, Math.min(100, (y1 / height) * 100))}%`;
          box.style.width = `${Math.max(1, Math.min(100, ((x2 - x1) / width) * 100))}%`;
          box.style.height = `${Math.max(1, Math.min(100, ((y2 - y1) / height) * 100))}%`;
        });
      };
      if (img.complete) update();
      else img.addEventListener("load", update, { once: true });
    });
  }

  function frameSortValue(node) {
    return Number(node.timestamp_seconds ?? node.frame_index ?? 0);
  }

  function renderMediaReview(data, nodes, edges) {
    if (cy) {
      cy.destroy();
      cy = null;
    }
    cyRoot.classList.add("media-review-root");
    const assets = nodes.filter((node) => node.entity_type === "MEDIA_ASSET");
    const frames = nodes
      .filter((node) => node.entity_type === "MEDIA_FRAME")
      .sort((a, b) => frameSortValue(a) - frameSortValue(b));
    const objects = nodes.filter((node) => node.entity_type === "MEDIA_OBJECT");
    const detectionsByFrame = new Map();
    objects.forEach((node) => {
      const key = String(node.frame_id || "");
      if (!detectionsByFrame.has(key)) detectionsByFrame.set(key, []);
      detectionsByFrame.get(key).push(node);
    });
    detectionsByFrame.forEach((items) =>
      items.sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0)),
    );
    const frameCards = frames
      .map((frame) => {
        const frameId = frameIdForNode(frame);
        const detections = detectionsByFrame.get(frameId) || [];
        const boxes = detections
          .filter((item) => Array.isArray(item.bbox))
          .map(
            (item) =>
              `<span class="media-bbox" data-node-id="${escHtml(item.id)}" data-bbox="${escHtml(
                JSON.stringify(item.bbox),
              )}" title="${escHtml(item.label)}"></span>`,
          )
          .join("");
        const chips = detections.length
          ? detections
              .map(
                (item) => `<button type="button" class="media-object-chip" data-node-id="${escHtml(item.id)}">
                  ${escHtml(item.object_label || "object")} ${(Number(item.confidence || 0)).toFixed(2)}
                </button>`,
              )
              .join("")
          : '<span class="media-no-detections">No objects</span>';
        return `
          <article class="media-frame-card" data-node-id="${escHtml(frame.id)}">
            <div class="media-frame-image-wrap">
              <img class="media-frame-img" src="${mediaFrameUrl(frameId)}" alt="${escHtml(frame.label)}" loading="lazy" />
              ${boxes}
            </div>
            <div class="media-frame-card-meta">
              <span>${escHtml(frame.label)}</span>
              <strong>${detections.length} detection${detections.length === 1 ? "" : "s"}</strong>
            </div>
            <div class="media-detection-list">${chips}</div>
          </article>
        `;
      })
      .join("");
    const labelCounts = objects.reduce((acc, item) => {
      const label = String(item.object_label || "object");
      acc.set(label, (acc.get(label) || 0) + 1);
      return acc;
    }, new Map());
    const labelSummary = Array.from(labelCounts.entries())
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([label, count]) => `<span class="media-summary-pill">${escHtml(label)} ${count}</span>`)
      .join("");
    cyRoot.innerHTML = `
      <div class="media-review-board">
        <div class="media-review-summary">
          <div>
            <div class="media-review-kicker">Visual Evidence</div>
            <div class="media-review-title">${escHtml(assets[0]?.label || data.case_ref || "Media review")}</div>
          </div>
          <div class="media-review-metrics">
            <span>${Number(data.meta?.frame_count || frames.length)} frames</span>
            <span>${Number(data.meta?.detection_count || objects.length)} detections</span>
            <span>${Number(data.meta?.asset_count || assets.length)} assets</span>
          </div>
        </div>
        <div class="media-summary-pills">${labelSummary || '<span class="media-summary-pill">No object detections</span>'}</div>
        <div class="media-frame-grid">${frameCards || '<div class="timeline-empty">No frames were extracted for this media.</div>'}</div>
      </div>
    `;
    positionMediaBoxes(cyRoot);
    cyRoot.querySelectorAll(".media-frame-card").forEach((card) => {
      card.addEventListener("click", () => {
        const node = nodes.find((item) => item.id === card.getAttribute("data-node-id"));
        setMediaDetail(node, edges, nodes, null);
      });
    });
    cyRoot.querySelectorAll(".media-object-chip,.media-bbox").forEach((control) => {
      control.addEventListener("click", (event) => {
        event.stopPropagation();
        const node = nodes.find((item) => item.id === control.getAttribute("data-node-id"));
        setMediaDetail(node, edges, nodes, null);
      });
    });
    setMediaDetail(objects[0] || frames[0] || assets[0] || null, edges, nodes, null);
  }

  function setMediaDetail(node, edges, nodes, edge = null) {
    if (!node && !edge) {
      detail.innerHTML =
        '<div class="node-empty">Select a media asset, frame, object, or relationship to inspect the visual evidence graph.</div>';
      return;
    }
    if (edge) {
      const source = nodes.find((n) => n.id === edge.source);
      const target = nodes.find((n) => n.id === edge.target);
      detail.innerHTML = `
        <div class="node-detail-header">Media Relationship</div>
        <div class="detail-field"><span class="detail-key">SOURCE</span><span class="detail-val">${escHtml(source?.label || edge.source)}</span></div>
        <div class="detail-field"><span class="detail-key">TARGET</span><span class="detail-val">${escHtml(target?.label || edge.target)}</span></div>
        <div class="detail-field"><span class="detail-key">TYPE</span><span class="detail-val">${escHtml(edge.type || "UNKNOWN")}</span></div>
        <div class="detail-field"><span class="detail-key">CONFIDENCE</span><span class="detail-val">${(edge.confidence || 0).toFixed(2)}</span></div>
      `;
      return;
    }
    const linked = edges.filter((e) => e.source === node.id || e.target === node.id);
    const bbox = Array.isArray(node.bbox) ? node.bbox.map((v) => Number(v).toFixed(1)).join(", ") : "";
    detail.innerHTML = `
      <div class="node-detail-header">${escHtml(node.label)}</div>
      ${mediaPreviewHtml(node, { compact: true })}
      <div class="detail-field"><span class="detail-key">TYPE</span><span class="detail-val">${escHtml(node.entity_type || "UNKNOWN")}</span></div>
      ${node.media_type ? `<div class="detail-field"><span class="detail-key">MEDIA</span><span class="detail-val">${escHtml(node.media_type)}</span></div>` : ""}
      ${node.object_label ? `<div class="detail-field"><span class="detail-key">OBJECT</span><span class="detail-val">${escHtml(node.object_label)}</span></div>` : ""}
      ${node.confidence !== undefined ? `<div class="detail-field"><span class="detail-key">CONFIDENCE</span><span class="detail-val">${Number(node.confidence || 0).toFixed(2)}</span></div>` : ""}
      ${node.timestamp_seconds !== undefined ? `<div class="detail-field"><span class="detail-key">TIME</span><span class="detail-val">${Number(node.timestamp_seconds || 0).toFixed(1)}s</span></div>` : ""}
      ${bbox ? `<div class="detail-field"><span class="detail-key">BBOX</span><span class="detail-val">${escHtml(bbox)}</span></div>` : ""}
      ${node.filepath ? `<div class="detail-field"><span class="detail-key">FILE</span><span class="detail-val">${escHtml(node.filepath)}</span></div>` : ""}
      ${node.image_path ? `<div class="detail-field"><span class="detail-key">FRAME</span><span class="detail-val">${escHtml(node.image_path)}</span></div>` : ""}
      <div class="detail-field"><span class="detail-key">LINKS</span><span class="detail-val">${linked.length}</span></div>
    `;
    positionMediaBoxes(detail);
  }

  function setDetail(node, edges, nodes, edge = null) {
    if (graphView.mode === "media") {
      setMediaDetail(node, edges, nodes, edge);
      return;
    }
    if (!node && !edge) {
      detail.innerHTML =
        '<div class="node-empty">Select a node or edge to view entity details and exact supporting evidence.</div>';
      return;
    }
    if (edge) {
      const source = nodes.find((n) => n.id === edge.source);
      const target = nodes.find((n) => n.id === edge.target);
      detail.innerHTML = `
        <div class="node-detail-header">Relationship Evidence</div>
        <div class="detail-field"><span class="detail-key">SOURCE</span><span class="detail-val">${source?.label || edge.source}</span></div>
        <div class="detail-field"><span class="detail-key">TARGET</span><span class="detail-val">${target?.label || edge.target}</span></div>
        <div class="detail-field"><span class="detail-key">TYPE</span><span class="detail-val">${edge.type || "UNKNOWN"}</span></div>
        <div class="detail-field"><span class="detail-key">CONFIDENCE</span><span class="detail-val">${(edge.confidence || 0).toFixed(2)}</span></div>
        <div class="detail-field"><span class="detail-key">EVIDENCE</span><span class="detail-val">${edge.evidence_count || 0} supporting span(s)</span></div>
        <div style="margin-top:12px;color:var(--ink-faint);font-size:11px;letter-spacing:.1em;text-transform:uppercase;">Top supporting citations</div>
        ${renderCitationList(edge.citations || [])}
      `;
      detail.querySelectorAll(".lens-open-source").forEach((button) => {
        button.addEventListener("click", () => {
          const docId = button.getAttribute("data-doc-id") || "";
          const page = Number(button.getAttribute("data-page") || "1");
          if (!docId) return;
          window.dispatchEvent(
            new CustomEvent("lens:open-source", {
              detail: { doc_id: docId, page: Number.isFinite(page) ? page : 1 },
            }),
          );
        });
      });
      return;
    }
    const linked = edges.filter((e) => e.source === node.id || e.target === node.id);
    const isLocation = String(node.entity_type || "").toUpperCase() === "LOCATION";
    const isVehicle = String(node.entity_type || "").toUpperCase() === "VEHICLE";
    detail.innerHTML = `
      <div class="node-detail-header">${node.label}</div>
      <div class="detail-field"><span class="detail-key">TYPE</span><span class="detail-val">${node.entity_type || "UNKNOWN"}</span></div>
      <div class="detail-field"><span class="detail-key">MENTIONS</span><span class="detail-val">${node.mention_count || 0}</span></div>
      <div class="detail-field"><span class="detail-key">LINKS</span><span class="detail-val">${linked.length}</span></div>
      <div id="lens-entity-profile-slot">
        <div class="node-empty" style="margin-top:12px;">Loading entity dossier...</div>
      </div>
      ${isLocation ? '<div id="lens-node-map-slot"></div>' : ""}
      ${isVehicle ? '<div id="lens-vehicle-track-slot"></div>' : ""}
      <div class="entity-attach-shell">
        <div style="margin-top:12px;color:var(--ink-faint);font-size:11px;letter-spacing:.1em;text-transform:uppercase;">Entity photos (local)</div>
        <div class="entity-attach-picker-row">
          <button type="button" class="entity-attach-picker-btn">Select file(s)</button>
        </div>
        <label class="entity-attach-dropzone" role="button" tabindex="0">
          <input type="file" accept="image/*" multiple style="display:none;" />
          <span>Drop images here or click to select</span>
        </label>
        <div class="entity-attach-status">Stored locally under data/attachments/${escHtml(node.id)}/</div>
        <div class="entity-attach-list"></div>
      </div>
    `;
    const requestId = ++detailRequestSeq;
    const profileSlot = detail.querySelector("#lens-entity-profile-slot");
    fetchEntityProfile(node.id)
      .then((profile) => {
        if (requestId !== detailRequestSeq || !profileSlot) return;
        profileSlot.innerHTML = renderProfileSummary(profile);
        detail.querySelectorAll(".lens-open-query-btn").forEach((button) => {
          button.addEventListener("click", () => {
            const queryText = button.getAttribute("data-query") || "";
            if (!queryText.trim()) return;
            window.dispatchEvent(
              new CustomEvent("lens:fill-query", {
                detail: { query: queryText, source: "graph-profile" },
              }),
            );
          });
        });
      })
      .catch((err) => {
        if (requestId !== detailRequestSeq || !profileSlot) return;
        profileSlot.innerHTML = `<div class="node-empty" style="margin-top:12px;">Entity dossier load failed: ${escHtml(String(err))}</div>`;
      });
    if (isLocation && window.LensEntityMap) {
      const slot = detail.querySelector("#lens-node-map-slot");
      if (slot) window.LensEntityMap.renderInto(slot, node);
    }
    if (isVehicle) {
      const slot = detail.querySelector("#lens-vehicle-track-slot");
      if (slot) renderVehicleTrack(slot, node);
    }
    renderAttachments(detail, node);
  }

  function globalCaseRef() {
    const value = window.__lensSelectedCaseRef;
    return typeof value === "string" ? value.trim() : "";
  }

  function activeCaseRef() {
    // An explicit picker choice (including the empty "All operations"
    // option) always wins over the global case. Before the user touches the
    // picker we mirror whatever Cases desk is pointing at so the two views
    // start out aligned.
    if (atlasCaseOverride !== null) return atlasCaseOverride;
    return globalCaseRef();
  }

  function renderCaseSelect() {
    if (!caseSelect) return;
    const current = activeCaseRef();
    const options = ['<option value="">All operations</option>'];
    knownAtlasCases.forEach((item) => {
      const ref = String(item.case_ref || "");
      const label = `${ref}${item.case_name ? ` - ${item.case_name}` : ""}`;
      options.push(
        `<option value="${escHtml(ref)}">${escHtml(label)}</option>`,
      );
    });
    caseSelect.innerHTML = options.join("");
    caseSelect.value = current;
  }

  function updateGraphModeUi() {
    const isMedia = graphView.mode === "media";
    if (graphModeSelect) graphModeSelect.value = graphView.mode;
    if (input) {
      input.disabled = isMedia;
      input.placeholder = isMedia ? "Media graph uses the selected operation" : "e.g. Marcus Webb";
      if (isMedia) input.value = "";
    }
    if (graphPanelOverline) graphPanelOverline.textContent = isMedia ? "Media objects" : "Entity seed";
    if (graphPanelTitle) graphPanelTitle.textContent = isMedia ? "Inspect media graph" : "Expand a network";
    if (graphPanelCopy) {
      graphPanelCopy.textContent = isMedia
        ? "Load visual objects, frames, and spatial links from uploaded media."
        : "Use a person, alias, location, or asset to build an operational map around it.";
    }
    if (graphPanelChip) graphPanelChip.textContent = isMedia ? "Object graph" : "Atlas query";
    if (graphSubmitBtn) graphSubmitBtn.textContent = isMedia ? "Load media graph" : "Expand network";
    if (graphCanvasStatus) {
      graphCanvasStatus.textContent = isMedia
        ? "Frame thumbnails, detections and spatial links"
        : "Weighted by mentions and confidence";
    }
    if (crossDocToggle) crossDocToggle.disabled = isMedia;
    if (layoutSelect) layoutSelect.disabled = isMedia;
    if (relayoutBtn) relayoutBtn.disabled = isMedia;
    if (fitBtn) fitBtn.disabled = isMedia;
  }

  function persistGraphView() {
    layoutStore?.save?.({
      mode: graphView.mode,
      layout: graphView.layout,
      confidence: graphView.confidence,
      crossDoc: graphView.crossDoc,
    });
  }

  function currentLayoutConfig() {
    return window.LensGraphLayout?.registry?.[graphView.layout] || { name: "fcose", animate: false, fit: true };
  }

  function applyLayout() {
    if (!cy) return;
    cy.layout(currentLayoutConfig()).run();
  }

  async function refreshAtlasCases() {
    if (!caseSelect) return;
    try {
      const resp = await fetch(apiUrl("/cases"));
      if (!resp.ok) return;
      const data = await resp.json();
      knownAtlasCases = Array.isArray(data.cases) ? data.cases : [];
      renderCaseSelect();
    } catch (_) {
      // Network hiccup is not fatal - the picker just keeps its last list.
    }
  }

  async function loadNetwork(entityName = "") {
    const params = new URLSearchParams();
    params.set("limit", "140");
    if (graphView.mode !== "media" && entityName) {
      params.set("entity", entityName);
      params.set("hops", "2");
    }
    const caseRef = activeCaseRef();
    if (caseRef) params.set("case_ref", caseRef);
    const endpoint = graphView.mode === "media" ? "/graph/media-network" : "/graph/network";
    const resp = await fetch(apiUrl(`${endpoint}?${params.toString()}`));
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (graphView.mode !== "media" && entityName && data.meta && data.meta.entity_found === false) {
      if (cy) cy.destroy();
      cyRoot.innerHTML =
        '<div class="timeline-empty">No exact graph match was found for that entity.</div>';
      setDetail(null, [], []);
      return;
    }
    const nodes = data.nodes || [];
    if (graphView.mode === "media") {
      const edges = data.edges || [];
      if (!nodes.length) {
        if (cy) {
          cy.destroy();
          cy = null;
        }
        cyRoot.classList.add("media-review-root");
        cyRoot.innerHTML =
          '<div class="timeline-empty">No media-object graph exists for this operation yet. Upload an image/video and ingest it first.</div>';
        setDetail(null, [], []);
        return;
      }
      renderMediaReview(data, nodes, edges);
      return;
    }
    cyRoot.classList.remove("media-review-root");
    const rawEdges = (data.edges || []).filter((edge) => {
      const conf = Number(edge.confidence || 0);
      const isCrossDoc = Number(edge.distinct_doc_count || 0) > 1;
      if (conf < graphView.confidence) return false;
      if (graphView.mode !== "media" && !graphView.crossDoc && isCrossDoc) return false;
      return true;
    });
    // Collapse duplicate edges between the same pair/type to reduce visual mesh.
    const collapsedByKey = new Map();
    rawEdges.forEach((edge) => {
      if (!edge?.source || !edge?.target) return;
      if (edge.source === edge.target) return;
      const a = String(edge.source);
      const b = String(edge.target);
      const key = a < b ? `${a}|${b}|${edge.type}` : `${b}|${a}|${edge.type}`;
      const prev = collapsedByKey.get(key);
      if (!prev) {
        collapsedByKey.set(key, {
          ...edge,
          evidence_count: Number(edge.evidence_count || 0),
          confidence: Number(edge.confidence || 0),
          citations: Array.isArray(edge.citations) ? [...edge.citations] : [],
        });
        return;
      }
      prev.evidence_count += Number(edge.evidence_count || 0);
      prev.confidence = Math.max(prev.confidence, Number(edge.confidence || 0));
      const merged = [...(prev.citations || []), ...(Array.isArray(edge.citations) ? edge.citations : [])];
      const seen = new Set();
      prev.citations = merged.filter((citation) => {
        const key = `${citation.doc_id || citation.doc_name || "unknown"}|${citation.page ?? "?"}|${citation.span_text || ""}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      }).slice(0, 4);
    });
    const edges = Array.from(collapsedByKey.values());
    if (cy) cy.destroy();
    if (!nodes.length && graphView.mode === "media") {
      cyRoot.innerHTML =
        '<div class="timeline-empty">No media-object graph exists for this operation yet. Upload an image/video and ingest it first.</div>';
      setDetail(null, [], []);
      return;
    }
    if (!window.cytoscape) {
      cyRoot.innerHTML =
        '<div class="timeline-empty">Graph library failed to load (Cytoscape CDN unavailable).</div>';
      return;
    }
    cy = window.cytoscape({
      container: cyRoot,
      elements: [
        ...nodes.map((n) => ({
          data: {
            ...n,
            id: n.id,
            label: n.label,
            short_label: String(n.label || "").slice(0, 28),
            entity_type: n.entity_type || "UNKNOWN",
            color_type:
              graphView.mode === "media" && n.entity_type === "MEDIA_OBJECT"
                ? `OBJECT_${n.object_label || "unknown"}`
                : n.entity_type || "UNKNOWN",
            mention_count: Number(n.mention_count || 0),
            attachment_id: n.attachment_id || "",
            attachment_url: n.attachment_id
              ? apiUrl(`/graph/attachments/${encodeURIComponent(n.attachment_id)}`)
              : "",
          },
        })),
        ...edges.map((e) => ({
          data: {
            id: e.id,
            source: e.source,
            target: e.target,
            type: e.type,
            type_label: String(e.type || "").replaceAll("_", " "),
            confidence: Number(e.confidence || 0),
            evidence_count: Number(e.evidence_count || 0),
            distinct_doc_count: Number(e.distinct_doc_count || 0),
            citations: e.citations || [],
          },
          classes: Number(e.distinct_doc_count || 0) > 1 ? "cross-doc-edge" : "",
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            label: "data(short_label)",
            "font-size": 9,
            "font-weight": 600,
            color: "#b8cdd8",
            "text-wrap": "wrap",
            "text-max-width": 100,
            "text-valign": "bottom",
            "text-margin-y": 10,
            width: (ele) => Math.max(16, Math.min(34, 14 + Math.log2((ele.data("mention_count") || 1) + 1) * 4)),
            height: (ele) => Math.max(16, Math.min(34, 14 + Math.log2((ele.data("mention_count") || 1) + 1) * 4)),
            "background-color": (ele) => colorFor(ele.data("color_type") || ele.data("entity_type")),
            "background-image": (ele) => (ele.data("attachment_url") ? ele.data("attachment_url") : "none"),
            "background-fit": "cover",
            "background-width": "100%",
            "background-height": "100%",
            "background-opacity": 1,
            "border-width": 1.5,
            "border-color": "#0e141d",
          },
        },
        {
          selector: "edge",
          style: {
            width: (ele) => 0.8 + Math.min(2.2, (ele.data("confidence") || 0) * 1.8),
            "line-color": "#8a9bab",
            "target-arrow-color": "#8a9bab",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.7,
            "curve-style": "bezier",
            label: "data(type_label)",
            "font-size": 8,
            color: "#d6e3ee",
            "text-background-color": "#06080a",
            "text-background-opacity": 0.95,
            "text-background-padding": 2,
            "text-rotation": "autorotate",
            "text-margin-y": -2,
            opacity: (ele) => 0.28 + Math.min(0.55, (ele.data("confidence") || 0) * 0.6),
          },
        },
        {
          selector: "edge.cross-doc-edge",
          style: {
            "line-style": "dashed",
            "line-color": "#f0b840",
            "target-arrow-color": "#f0b840",
            width: (ele) => 1.2 + Math.min(2.4, (ele.data("confidence") || 0) * 2),
            opacity: 0.9,
          },
        },
        {
          selector: ".selected",
          style: {
            "border-width": 3,
            "border-color": "#f0b840",
            opacity: 1,
          },
        },
        {
          selector: ".edge-selected",
          style: {
            width: 2.8,
            label: "data(type_label)",
            "font-size": 8,
            opacity: 0.95,
            "line-color": "#f0b840",
            "target-arrow-color": "#f0b840",
          },
        },
        {
          selector: ".dimmed",
          style: {
            opacity: 0.12,
          },
        },
        {
          selector: ".path-highlight",
          style: {
            "border-width": 3,
            "border-color": "#ffd17d",
            "background-color": "#f0b840",
            opacity: 1,
            "z-index": 99,
          },
        },
      ],
      layout: {
        ...currentLayoutConfig(),
        animate: false,
        fit: true,
        padding: 36,
        randomize: false,
        componentSpacing: 220,
        nodeRepulsion: () => 14000,
        idealEdgeLength: () => 120,
        edgeElasticity: () => 80,
        gravity: 0.35,
        numIter: 2800,
        initialTemp: 220,
        coolingFactor: 0.95,
        minTemp: 1.0,
      },
    });
    setDetail(null, edges, nodes, null);
    cy.on("tap", "node", (evt) => {
      const id = evt.target.id();
      const node = nodes.find((n) => n.id === id);
      cy.elements().removeClass("selected edge-selected dimmed");
      const selected = evt.target;
      selected.addClass("selected");
      const neighborhood = selected.closedNeighborhood();
      selected.connectedEdges().addClass("edge-selected");
      cy.elements().not(neighborhood).addClass("dimmed");
      setDetail(node, edges, nodes, null);
      const queryInput = document.getElementById("query-input");
      if (graphView.mode !== "media" && queryInput && node?.label) queryInput.value = node.label;
    });
    cy.on("tap", "edge", (evt) => {
      const id = evt.target.id();
      const edge = edges.find((item) => item.id === id);
      cy.elements().removeClass("selected edge-selected dimmed");
      evt.target.addClass("edge-selected");
      evt.target.connectedNodes().addClass("selected");
      const neighborhood = evt.target.connectedNodes().closedNeighborhood().union(evt.target);
      cy.elements().not(neighborhood).addClass("dimmed");
      setDetail(null, edges, nodes, edge || null);
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        cy.elements().removeClass("selected edge-selected dimmed");
        setDetail(null, edges, nodes, null);
      }
    });
  }

  if (layoutSelect) {
    layoutSelect.value = graphView.layout;
    layoutSelect.addEventListener("change", () => {
      graphView.layout = layoutSelect.value || "fcose";
      persistGraphView();
      applyLayout();
    });
  }
  if (graphModeSelect) {
    graphModeSelect.value = graphView.mode;
    graphModeSelect.addEventListener("change", () => {
      graphView.mode = graphModeSelect.value === "media" ? "media" : "entity";
      updateGraphModeUi();
      persistGraphView();
      loadNetwork((input?.value || "").trim()).catch((error) => {
        cyRoot.innerHTML = `<div class="timeline-empty">Graph load failed: ${String(error)}</div>`;
      });
    });
  }
  if (confidenceSlider) {
    confidenceSlider.value = String(graphView.confidence);
    confidenceSlider.addEventListener("input", () => {
      graphView.confidence = Number(confidenceSlider.value || 0.35);
      persistGraphView();
      loadNetwork((input?.value || "").trim()).catch(() => {});
    });
  }
  if (crossDocToggle) {
    crossDocToggle.checked = Boolean(graphView.crossDoc);
    crossDocToggle.addEventListener("change", () => {
      graphView.crossDoc = crossDocToggle.checked;
      persistGraphView();
      loadNetwork((input?.value || "").trim()).catch(() => {});
    });
  }
  if (relayoutBtn) {
    relayoutBtn.addEventListener("click", () => {
      applyLayout();
    });
  }
  if (fitBtn) {
    fitBtn.addEventListener("click", () => {
      if (cy) cy.fit(undefined, 60);
    });
  }

  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadNetwork(input?.value?.trim() || "");
      } catch (error) {
        cyRoot.innerHTML = `<div class="timeline-empty">Graph load failed: ${String(error)}</div>`;
      }
    });
  }

  window.addEventListener("lens:query-result", (event) => {
    const data = event.detail || {};
    const resolved = data.entities_resolved || [];
    const focusEntity = resolved[0]?.canonical_name || (data.entities || [])[0] || "";
    loadNetwork(focusEntity).catch(() => {});
  });

  // Re-render the atlas whenever the operator switches the active case
  // (fired by case_manager.js). Without this, the graph stays pinned to
  // whatever case was active on first boot and looks like data is missing
  // after new cases are ingested/selected. If the operator has explicitly
  // locked the Atlas to a case via the picker, we leave it alone so the
  // Cases desk and the Atlas can be browsed independently.
  window.addEventListener("lens:case-selected", () => {
    refreshAtlasCases().catch(() => {});
    if (atlasCaseOverride !== null) {
      renderCaseSelect();
      return;
    }
    renderCaseSelect();
    const focus = (input?.value || "").trim();
    loadNetwork(focus).catch((error) => {
      cyRoot.innerHTML = `<div class="timeline-empty">Graph load failed: ${String(error)}</div>`;
    });
  });

  if (caseSelect) {
    caseSelect.addEventListener("change", () => {
      atlasCaseOverride = caseSelect.value || "";
      const focus = (input?.value || "").trim();
      loadNetwork(focus).catch((error) => {
        cyRoot.innerHTML = `<div class="timeline-empty">Graph load failed: ${String(error)}</div>`;
      });
    });
  }

  // Phase 8: light up a specific path returned by the path finder. If the
  // path includes nodes outside the current canvas, we re-seed from the
  // source entity first so every hop is represented.
  window.addEventListener("lens:highlight-path", async (event) => {
    const detail = event.detail || {};
    const path = detail.path;
    if (!cy || !path?.nodes?.length) return;
    const nodeIds = path.nodes.map((n) => n.entity_id);
    const missing = nodeIds.filter((id) => !cy.getElementById(id).length);
    if (missing.length) {
      const firstName = path.nodes[0]?.canonical_name || path.nodes[0]?.entity_id;
      if (firstName) {
        await loadNetwork(firstName);
      }
    }
    cy.elements().removeClass("selected edge-selected dimmed path-highlight");
    const matched = nodeIds
      .map((id) => cy.getElementById(id))
      .filter((ele) => ele && ele.length);
    if (!matched.length) return;
    matched.forEach((node) => node.addClass("path-highlight"));
    for (let i = 0; i < nodeIds.length - 1; i += 1) {
      const a = nodeIds[i];
      const b = nodeIds[i + 1];
      cy.edges().forEach((edge) => {
        const src = edge.data("source");
        const tgt = edge.data("target");
        if ((src === a && tgt === b) || (src === b && tgt === a)) {
          edge.addClass("edge-selected");
        }
      });
    }
    const hull = cy.collection(matched).union(cy.collection(matched).connectedEdges());
    cy.elements().not(hull).addClass("dimmed");
    cy.fit(hull, 80);
  });

  loadSchemaColors().finally(() => {
    updateGraphModeUi();
    // Kick off the case picker population in parallel with the first render
    // so the operator sees the full list of ingested operations as soon as
    // possible. The initial loadNetwork() call uses whatever case is
    // currently active (global or picker override, whichever resolves first).
    refreshAtlasCases().catch(() => {});
    loadNetwork().catch((error) => {
      cyRoot.innerHTML = `<div class="timeline-empty">Graph load failed: ${String(error)}</div>`;
    });
  });
})();
