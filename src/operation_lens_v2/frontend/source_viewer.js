(function () {
  let modal = null;

  function ensureModal() {
    if (modal) return modal;
    modal = document.createElement("div");
    modal.id = "source-viewer-modal";
    modal.innerHTML = `
      <div class="source-viewer-backdrop"></div>
      <div class="source-viewer-panel">
        <button type="button" class="source-viewer-close">Close</button>
        <div class="source-viewer-head"></div>
        <pre class="source-viewer-body"></pre>
      </div>
    `;
    document.body.appendChild(modal);
    modal.querySelector(".source-viewer-backdrop").addEventListener("click", closeModal);
    modal.querySelector(".source-viewer-close").addEventListener("click", closeModal);
    return modal;
  }

  function closeModal() {
    if (modal) modal.style.display = "none";
  }

  async function openSource(docId, page) {
    const root = ensureModal();
    root.style.display = "grid";
    const head = root.querySelector(".source-viewer-head");
    const body = root.querySelector(".source-viewer-body");
    head.textContent = "Loading source page...";
    body.textContent = "";
    try {
      const resp = await fetch(`/documents/${encodeURIComponent(docId)}/page/${encodeURIComponent(page)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      head.textContent = `${data.filename || data.doc_id} // p.${data.page} // ${data.format}`;
      body.textContent = data.text || "(No extracted text for this page)";
    } catch (error) {
      head.textContent = "Source load failed";
      body.textContent = String(error);
    }
  }

  window.addEventListener("lens:open-source", (event) => {
    const docId = String(event?.detail?.doc_id || "");
    const page = Number(event?.detail?.page || 1);
    if (!docId) return;
    openSource(docId, page).catch(() => {});
  });
})();
