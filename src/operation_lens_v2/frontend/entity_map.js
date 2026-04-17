(function () {
  if (!window.L) {
    window.LensEntityMap = {
      renderInto() {},
    };
    return;
  }

  const OSM_TILE = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
  const OSM_ATTRIB =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
  const ESRI_TILE =
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
  const ESRI_ATTRIB =
    'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community';

  function ensureStyles() {
    if (document.getElementById("lens-map-styles")) return;
    const style = document.createElement("style");
    style.id = "lens-map-styles";
    style.textContent = `
      .lens-map-shell { margin-top: 14px; border:1px solid var(--border-0); background:rgba(0,0,0,0.45); border-radius:10px; overflow:hidden; }
      .lens-map-shell .lens-map-canvas { width:100%; height:220px; background:#0a131c; }
      .lens-map-controls { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:6px 10px; font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-muted); border-bottom:1px solid var(--border-0); background:rgba(255,255,255,0.02); }
      .lens-map-toggle { display:flex; gap:4px; }
      .lens-map-toggle button { font:inherit; padding:3px 8px; background:transparent; color:var(--ink-soft); border:1px solid var(--border-0); cursor:pointer; letter-spacing:.08em; text-transform:uppercase; font-size:10px; }
      .lens-map-toggle button.active { background:var(--amber-100); color:#0a131c; border-color:var(--amber-100); }
      .lens-map-action { font:inherit; padding:3px 10px; background:transparent; color:var(--ink-soft); border:1px solid var(--border-0); cursor:pointer; letter-spacing:.08em; text-transform:uppercase; font-size:10px; }
      .lens-map-action:hover { border-color: var(--amber-100); color: var(--amber-100); }
      .lens-map-caption { padding:6px 10px; font-size:11px; color:var(--ink-muted); line-height:1.4; border-top:1px solid var(--border-0); background:rgba(255,255,255,0.02); }
      .lens-map-caption.err { color: #d58a8a; }
      .leaflet-container { background: #0a131c !important; }
    `;
    document.head.appendChild(style);
  }

  const instances = new WeakMap();

  function renderMap(container, { lat, lng, label, displayName }) {
    if (instances.has(container)) {
      try {
        instances.get(container).remove();
      } catch (_) {}
      instances.delete(container);
    }
    const street = L.tileLayer(OSM_TILE, { attribution: OSM_ATTRIB, maxZoom: 19 });
    const satellite = L.tileLayer(ESRI_TILE, { attribution: ESRI_ATTRIB, maxZoom: 19 });
    const map = L.map(container, {
      center: [lat, lng],
      zoom: 15,
      layers: [street],
      zoomControl: true,
      attributionControl: true,
    });
    const marker = L.marker([lat, lng]).addTo(map);
    marker.bindPopup(`<strong>${label || "Location"}</strong><br/>${displayName || ""}`);
    instances.set(container, map);
    setTimeout(() => map.invalidateSize(), 60);
    return { map, street, satellite };
  }

  function setActive(buttons, activeKey) {
    buttons.forEach((btn) => {
      if (btn.dataset.layer === activeKey) btn.classList.add("active");
      else btn.classList.remove("active");
    });
  }

  async function fetchGeocode(entityId, force = false) {
    const resp = await fetch(
      `/graph/entity/${encodeURIComponent(entityId)}/geocode${force ? "?force=true" : ""}`,
    );
    if (resp.status === 503) {
      return { disabled: true };
    }
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    return resp.json();
  }

  async function renderInto(container, node) {
    ensureStyles();
    if (!container || !node) return;
    container.innerHTML = "";
    const hasCoords =
      Number.isFinite(Number(node.latitude)) && Number.isFinite(Number(node.longitude));

    const shell = document.createElement("div");
    shell.className = "lens-map-shell";
    shell.innerHTML = `
      <div class="lens-map-controls">
        <span>Geospatial // ${node.entity_type || "LOCATION"}</span>
        <div class="lens-map-toggle">
          <button type="button" data-layer="street" class="active">Street</button>
          <button type="button" data-layer="satellite">Satellite</button>
        </div>
        <button type="button" class="lens-map-action" data-action="locate">${hasCoords ? "Re-locate" : "Locate"}</button>
      </div>
      <div class="lens-map-canvas"></div>
      <div class="lens-map-caption">${hasCoords ? "Geocoded via Nominatim &mdash; coords cached locally." : "No coordinates yet. Click LOCATE to resolve this location."}</div>
    `;
    container.appendChild(shell);

    const canvas = shell.querySelector(".lens-map-canvas");
    const toggleButtons = Array.from(shell.querySelectorAll(".lens-map-toggle button"));
    const caption = shell.querySelector(".lens-map-caption");
    const locateBtn = shell.querySelector('[data-action="locate"]');

    let layers = null;
    function paintMap(lat, lng, displayName) {
      layers = renderMap(canvas, {
        lat,
        lng,
        label: node.label,
        displayName,
      });
      setActive(toggleButtons, "street");
    }

    if (hasCoords) {
      paintMap(Number(node.latitude), Number(node.longitude), node.geocode_display_name || "");
    } else {
      canvas.innerHTML =
        '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--ink-muted);font-size:12px;letter-spacing:.1em;text-transform:uppercase;">No coordinates</div>';
    }

    toggleButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        if (!layers) return;
        const layerKey = btn.dataset.layer;
        if (layerKey === "street") {
          layers.map.removeLayer(layers.satellite);
          layers.map.addLayer(layers.street);
        } else {
          layers.map.removeLayer(layers.street);
          layers.map.addLayer(layers.satellite);
        }
        setActive(toggleButtons, layerKey);
      });
    });

    locateBtn.addEventListener("click", async () => {
      locateBtn.disabled = true;
      locateBtn.textContent = "Locating...";
      caption.classList.remove("err");
      caption.textContent = "Querying Nominatim...";
      try {
        const data = await fetchGeocode(node.id, hasCoords);
        if (data.disabled) {
          caption.classList.add("err");
          caption.textContent = "Geocoding disabled in settings (GEOCODING_ENABLED=0).";
        } else if (data.status === "ok" && data.geocode) {
          const g = data.geocode;
          node.latitude = g.latitude;
          node.longitude = g.longitude;
          node.geocode_display_name = g.display_name;
          paintMap(g.latitude, g.longitude, g.display_name);
          caption.textContent = g.display_name || "Location resolved.";
        } else {
          caption.classList.add("err");
          caption.textContent = "No geocode match for this location string.";
        }
      } catch (err) {
        caption.classList.add("err");
        caption.textContent = `Geocode failed: ${String(err)}`;
      } finally {
        locateBtn.disabled = false;
        locateBtn.textContent = "Re-locate";
      }
    });
  }

  window.LensEntityMap = { renderInto };
})();
