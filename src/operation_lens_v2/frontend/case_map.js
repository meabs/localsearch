(function () {
  const canvas = document.getElementById("map-canvas");
  if (!canvas || !window.L) return;

  const OSM_TILE = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
  const OSM_ATTRIB =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
  const ESRI_TILE =
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
  const ESRI_ATTRIB =
    'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community';

  const pinCountEl = document.getElementById("map-pin-count");
  const caseLabelEl = document.getElementById("map-case-label");
  const caseSelect = document.getElementById("map-case-input");
  const refreshBtn = document.getElementById("map-refresh");
  const selectionEl = document.getElementById("map-selection");
  const layerToggle = document.getElementById("map-layer-toggle");

  let map = null;
  let street = null;
  let satellite = null;
  let markerLayer = null;
  let initialised = false;

  function ensureMap() {
    if (map) return;
    street = L.tileLayer(OSM_TILE, { attribution: OSM_ATTRIB, maxZoom: 19 });
    satellite = L.tileLayer(ESRI_TILE, { attribution: ESRI_ATTRIB, maxZoom: 19 });
    map = L.map(canvas, {
      center: [53.4, -2.5],
      zoom: 7,
      layers: [street],
      zoomControl: true,
    });
    markerLayer = L.layerGroup().addTo(map);
    setTimeout(() => map.invalidateSize(), 100);
  }

  function setActiveLayer(layer) {
    if (!map) return;
    if (layer === "street") {
      map.removeLayer(satellite);
      map.addLayer(street);
    } else {
      map.removeLayer(street);
      map.addLayer(satellite);
    }
    layerToggle.querySelectorAll("button").forEach((btn) => {
      if (btn.dataset.layer === layer) btn.classList.add("active");
      else btn.classList.remove("active");
    });
  }

  function renderSelection(pin) {
    if (!pin) {
      selectionEl.innerHTML = '<div class="node-empty">No pin selected.</div>';
      return;
    }
    const docs = (pin.doc_names || []).filter(Boolean);
    selectionEl.innerHTML = `
      <div class="node-detail-header">${pin.canonical_name}</div>
      <div class="detail-field"><span class="detail-key">DISPLAY</span><span class="detail-val">${pin.display_name || "-"}</span></div>
      <div class="detail-field"><span class="detail-key">MENTIONS</span><span class="detail-val">${pin.mention_count}</span></div>
      <div class="detail-field"><span class="detail-key">COORDS</span><span class="detail-val">${pin.latitude.toFixed(4)}, ${pin.longitude.toFixed(4)}</span></div>
      <div style="margin-top:12px;color:var(--ink-faint);font-size:11px;letter-spacing:.1em;text-transform:uppercase;">Documents (${docs.length})</div>
      ${docs.length
        ? docs
            .map(
              (d) =>
                `<div style="margin-top:6px;font-size:13px;color:var(--ink-soft);">${d}</div>`,
            )
            .join("")
        : '<div class="node-empty" style="margin-top:8px;">None indexed.</div>'}
    `;
  }

  async function loadPins() {
    ensureMap();
    const caseRef = caseSelect?.value || "";
    const params = new URLSearchParams();
    if (caseRef) params.set("case_ref", caseRef);
    params.set("limit", "500");
    const resp = await fetch(`/graph/locations?${params.toString()}`);
    if (!resp.ok) {
      pinCountEl.textContent = "err";
      return;
    }
    const data = await resp.json();
    markerLayer.clearLayers();
    const pins = data.pins || [];
    pinCountEl.textContent = String(pins.length);
    caseLabelEl.textContent = caseRef || "All";
    if (!pins.length) {
      renderSelection(null);
      return;
    }
    const bounds = [];
    pins.forEach((pin) => {
      const m = L.marker([pin.latitude, pin.longitude]);
      const docs = (pin.doc_names || []).filter(Boolean);
      const popup = `
        <strong>${pin.canonical_name}</strong><br/>
        <span style="color:#555;font-size:11px;">${pin.display_name || ""}</span><br/>
        <span style="font-size:11px;">Mentions: ${pin.mention_count}${docs.length ? ` &middot; ${docs.length} doc(s)` : ""}</span>
      `;
      m.bindPopup(popup);
      m.on("click", () => renderSelection(pin));
      m.addTo(markerLayer);
      bounds.push([pin.latitude, pin.longitude]);
    });
    if (bounds.length === 1) {
      map.setView(bounds[0], 13);
    } else {
      map.fitBounds(bounds, { padding: [30, 30] });
    }
  }

  async function loadCases() {
    try {
      const resp = await fetch("/cases");
      if (!resp.ok) return;
      const data = await resp.json();
      const cases = data.cases || [];
      cases.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.case_ref;
        opt.textContent = `${c.case_ref} (${c.doc_count})`;
        caseSelect.appendChild(opt);
      });
    } catch (_) {
      // Non-fatal — the map still renders with "All".
    }
  }

  refreshBtn?.addEventListener("click", () => loadPins().catch((err) => {
    pinCountEl.textContent = "err";
    selectionEl.innerHTML = `<div class="node-empty">Failed to load: ${String(err)}</div>`;
  }));

  caseSelect?.addEventListener("change", () => loadPins().catch(() => {}));

  layerToggle?.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => setActiveLayer(btn.dataset.layer));
  });

  // Lazy-init: only build the map when the tab becomes active.
  function maybeInit() {
    const pane = document.getElementById("tab-map");
    if (pane && pane.classList.contains("active") && !initialised) {
      initialised = true;
      loadCases();
      loadPins().catch(() => {});
    } else if (map) {
      setTimeout(() => map.invalidateSize(), 120);
    }
  }

  document.querySelectorAll('.nav-btn[data-target="tab-map"]').forEach((btn) => {
    btn.addEventListener("click", () => setTimeout(maybeInit, 120));
  });

  if (document.getElementById("tab-map")?.classList.contains("active")) {
    maybeInit();
  }
})();
