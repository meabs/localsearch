const form = document.getElementById("query-form");
const input = document.getElementById("query-input");
const caseInput = document.getElementById("case-input");
const answer = document.getElementById("answer");
const claims = document.getElementById("claims");
const evidenceChunks = document.getElementById("evidence-chunks");
const cloudToggle = document.getElementById("cloud-toggle");
const templateSelect = document.getElementById("query-template-select");
const recallModeSelect = document.getElementById("recall-mode-select");
const chatThread = document.getElementById("query-chat-thread");
const chatResetButton = document.getElementById("chat-reset-btn");
const submitButton = form ? form.querySelector("button[type='submit']") : null;
let inFlightController = null;
const chatTurns = [];
const CHAT_HISTORY_LIMIT = 12;

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatAnswerText(rawAnswer) {
  const raw = String(rawAnswer || "").trim();
  if (!raw) {
    return '<div class="answer-empty">No answer generated.</div>';
  }
  const sectionRe = /\b(PEOPLE|PLACES|ASSETS|KEY FINDINGS|CONFIDENCE POSTURE|CONFIDENCE|EVIDENCE GAPS|GAPS)\b/g;
  const withBreaks = raw.replace(sectionRe, "\n$1\n");
  const lines = withBreaks
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);

  let html = "";
  let currentTitle = "";
  let buffer = [];

  function flush() {
    if (!currentTitle) return;
    const bulletLikeRe = /^(?:[-*]\s+|\d+\.\s+|#+\s+)/;
    const bulletItems = buffer
      .filter((line) => bulletLikeRe.test(line))
      .map((line) => line.replace(bulletLikeRe, "").trim())
      .filter(Boolean);
    const proseItems = buffer
      .filter((line) => !bulletLikeRe.test(line))
      .map((line) => line.trim())
      .filter(Boolean);

    let list = [];
    if (bulletItems.length) {
      list = [...bulletItems];
      if (proseItems.length) {
        list.push(...proseItems);
      }
    } else {
      const text = proseItems.join(" ").trim();
      list = text
        .split(/\.\s+/)
        .map((part) => part.trim())
        .filter(Boolean)
        .map((part) => (part.endsWith(".") ? part : `${part}.`));
    }

    const listHtml = list.length
      ? `<ul>${list.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : "<div class='answer-empty'>None identified.</div>";
    html += `<section class="answer-section"><h4>${escapeHtml(currentTitle)}</h4>${listHtml}</section>`;
  }

  for (const line of lines) {
    const normalizedLine = line.replace(/^#+\s*/, "").trim();
    if (/^(PEOPLE|PLACES|ASSETS|KEY FINDINGS|CONFIDENCE POSTURE|CONFIDENCE|EVIDENCE GAPS|GAPS)$/i.test(normalizedLine)) {
      flush();
      currentTitle = normalizedLine.toUpperCase();
      buffer = [];
      continue;
    }
    if (!currentTitle) {
      currentTitle = "KEY FINDINGS";
    }
    buffer.push(line);
  }
  flush();
  return html || `<div class="answer-text">${escapeHtml(raw)}</div>`;
}

function renderClaim(claim) {
  const status = claim.status || "UNSUPPORTED";
  const citations = claim.citations || [];
  const citationHtml = citations
    .map((c, idx) => {
      const page = c.page ?? "?";
      const doc = c.doc_name || c.doc_id || "unknown-doc";
      const span = c.span_text || "No span excerpt available.";
      const key = `${c.doc_id || doc}|${page}|${idx}`;
      return `
        <button type="button" class="citation citation-link" data-evidence-key="${escapeHtml(key)}">
          <div><strong>${doc}</strong> page ${page}</div>
          <div>${span}</div>
        </button>
      `;
    })
    .join("");
  return `
    <div class="claim">
      <div class="claim-head">
        <div class="claim-badge-row">${confidenceBadge(status)}</div>
        <div>confidence ${(claim.confidence ?? 0).toFixed(2)}</div>
      </div>
      <div>${claim.text || "No claim text"}</div>
      <div class="claim-citations">${citationHtml}</div>
    </div>
  `;
}

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildEvidenceItems(data) {
  const items = [];
  const topResults = data.top_results || [];
  const claimRows = data.claims || [];

  claimRows.forEach((claim) => {
    (claim.citations || []).forEach((citation, idx) => {
      items.push({
        key: `${citation.doc_id || "unknown-doc"}|${citation.page ?? "?"}|${idx}`,
        doc_id: citation.doc_id || "unknown-doc",
        doc_name: citation.doc_name || citation.doc_id || "unknown-doc",
        page: citation.page ?? "?",
        source: "claim-citation",
        text: citation.span_text || "",
      });
    });
  });

  topResults.forEach((row, idx) => {
    items.push({
      key: `${row.doc_id || "unknown-doc"}|${row.page ?? "?"}|top-${idx}`,
      doc_id: row.doc_id || "unknown-doc",
      doc_name: row.doc_name || row.doc_id || "unknown-doc",
      page: row.page ?? "?",
      source: row.source || "retrieval",
      text: row.text || "",
    });
  });

  const byKey = new Map();
  items.forEach((item) => {
    if (!item.text || !item.text.trim()) return;
    if (!byKey.has(item.key)) {
      byKey.set(item.key, item);
    }
  });
  return Array.from(byKey.values());
}

function renderEvidencePanel(data) {
  if (!evidenceChunks) return;
  const items = buildEvidenceItems(data);
  if (!items.length) {
    evidenceChunks.innerHTML = '<div class="timeline-empty">No evidence extracts available for this response.</div>';
    return;
  }
  evidenceChunks.innerHTML = items
    .map((item) => {
      const text = escapeHtml(item.text).slice(0, 900);
      return `
        <article class="evidence-card" data-evidence-key="${escapeHtml(item.key)}">
          <div class="evidence-card-header">
            <span>${escapeHtml(item.doc_name || item.doc_id)} p.${escapeHtml(item.page)}</span>
            <span>${escapeHtml(item.source)}</span>
          </div>
          <div class="evidence-card-body">${text}</div>
        </article>
      `;
    })
    .join("");
}

function truncateChatText(text) {
  const trimmed = String(text || "").trim();
  if (trimmed.length <= 420) return trimmed;
  return `${trimmed.slice(0, 417)}...`;
}

function renderChatThread() {
  if (!chatThread) return;
  if (!chatTurns.length) {
    chatThread.innerHTML = '<div class="timeline-empty">No prior turns yet. Ask your first question.</div>';
    return;
  }
  chatThread.innerHTML = chatTurns
    .map((turn) => {
      const role = turn.role === "assistant" ? "assistant" : "user";
      const roleLabel = role === "assistant" ? "Assistant" : "Investigator";
      const roleClass = role === "assistant" ? "chat-turn-assistant" : "chat-turn-user";
      return `
        <article class="chat-turn ${roleClass}">
          <div class="chat-turn-role">${roleLabel}</div>
          <div class="chat-turn-text">${escapeHtml(turn.content)}</div>
        </article>
      `;
    })
    .join("");
  chatThread.scrollTop = chatThread.scrollHeight;
}

function pushChatTurn(role, content) {
  if (!content || !String(content).trim()) return;
  chatTurns.push({ role, content: truncateChatText(content) });
  if (chatTurns.length > CHAT_HISTORY_LIMIT) {
    chatTurns.splice(0, chatTurns.length - CHAT_HISTORY_LIMIT);
  }
  renderChatThread();
}

function buildChatHistoryPayload() {
  return chatTurns.map((turn) => ({ role: turn.role, content: turn.content }));
}

function focusEvidenceCard(key, snippet) {
  if (!evidenceChunks || !key) return;
  const cards = evidenceChunks.querySelectorAll(".evidence-card");
  cards.forEach((card) => {
    card.style.outline = "";
    card.style.boxShadow = "";
  });

  let match = evidenceChunks.querySelector(`.evidence-card[data-evidence-key="${CSS.escape(key)}"]`);
  if (!match && snippet) {
    const snippetRe = new RegExp(escapeRegExp(snippet.trim().slice(0, 60)), "i");
    match = Array.from(cards).find((card) => snippetRe.test(card.textContent || ""));
  }
  if (!match) return;
  match.style.outline = "1px solid var(--amber-dim)";
  match.style.boxShadow = "0 0 0 2px rgba(232, 160, 48, 0.18)";
  match.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function loadQueryTemplates() {
  if (!templateSelect) return;
  try {
    const response = await fetch("/query/templates");
    if (!response.ok) return;
    const payload = await response.json();
    const templates = Array.isArray(payload.templates) ? payload.templates : [];
    if (!templates.length) return;
    templateSelect.innerHTML = [
      '<option value="">Choose saved template (optional)</option>',
      ...templates.map(
        (item) =>
          `<option value="${escapeHtml(item.query || "")}" data-template-id="${escapeHtml(
            item.template_id || "",
          )}">${escapeHtml(item.label || item.template_id || "Template")}</option>`,
      ),
    ].join("");
  } catch (_) {
    // Keep manual query flow even when templates are unavailable.
  }
}

if (templateSelect && input) {
  templateSelect.addEventListener("change", () => {
    const selected = templateSelect.value || "";
    if (!selected) return;
    input.value = selected;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });
}

if (chatResetButton) {
  chatResetButton.addEventListener("click", () => {
    chatTurns.length = 0;
    renderChatThread();
  });
}

window.addEventListener("lens:fill-query", (event) => {
  const query = String(event?.detail?.query || "").trim();
  if (!query || !input) return;
  input.value = query;
  const queryTabButton = document.querySelector('.nav-btn[data-target="tab-query"]');
  if (queryTabButton instanceof HTMLElement) {
    queryTabButton.click();
  }
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
});

if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = input.value.trim();
    if (!query) return;
    const caseRef = (caseInput?.value || "").trim();
    const priorChatHistory = buildChatHistoryPayload();
    pushChatTurn("user", query);
    if (inFlightController) {
      inFlightController.abort();
    }
    inFlightController = new AbortController();
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Running...";
    }
    answer.innerHTML = `
      <div class="answer-header">
        <div class="answer-title">Assessment in progress</div>
        <div class="answer-meta">
          <span class="answer-pill">Query active</span>
        </div>
      </div>
      <div class="answer-text">Running query...</div>
    `;
    claims.innerHTML = '<div class="timeline-empty">Waiting for claim validation.</div>';
    if (evidenceChunks) {
      evidenceChunks.innerHTML = '<div class="timeline-empty">Collecting evidence extracts.</div>';
    }

    async function runRequest() {
      const resp = await fetch("/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          case_ref: caseRef || null,
          use_cloud: Boolean(cloudToggle?.checked),
          chat_history: priorChatHistory,
          recall_mode: recallModeSelect?.value || "auto",
        }),
        signal: inFlightController.signal,
      });
      if (!resp.ok) {
        const body = await resp.text();
        throw new Error(`Query failed (${resp.status}): ${body.slice(0, 240)}`);
      }
      return resp.json();
    }

    try {
      let data;
      try {
        data = await runRequest();
      } catch (firstErr) {
        if (firstErr.name === "AbortError") throw firstErr;
        await new Promise((resolve) => setTimeout(resolve, 400));
        data = await runRequest();
      }

      answer.innerHTML = `
        <div class="answer-header">
          <div class="answer-title">Assessment brief</div>
          <div class="answer-meta">
            <span class="answer-pill">Intent ${escapeHtml(data.intent || "unknown")}</span>
            <span class="answer-pill">Scope ${escapeHtml(data.case_scope || "ALL_CASES")}</span>
            <span class="answer-pill">${escapeHtml(String(data.result_count || 0))} results</span>
            <span class="answer-pill">Backend ${escapeHtml(data.backend || "unknown")}</span>
            <span class="answer-pill">Recall ${escapeHtml(data.recall_mode || "auto")}</span>
          </div>
        </div>
        <div class="answer-structured">${formatAnswerText(data.answer)}</div>
      `;
      const claimItems = (data.claims || []).map((claim) => renderClaim(claim)).join("");
      claims.innerHTML = claimItems || '<div class="timeline-empty">No claims returned for this response.</div>';
      renderEvidencePanel(data);
      pushChatTurn("assistant", data.answer || "No answer generated.");
      claims.querySelectorAll(".citation-link").forEach((el) => {
        el.addEventListener("click", () => {
          const key = el.getAttribute("data-evidence-key") || "";
          const snippet = el.textContent || "";
          focusEvidenceCard(key, snippet);
        });
      });
      window.dispatchEvent(new CustomEvent("lens:query-result", { detail: data }));
    } catch (error) {
      if (error.name === "AbortError") return;
      pushChatTurn("assistant", `Request failed: ${String(error)}`);
      answer.innerHTML = `
        <div class="answer-header">
          <div class="answer-title">Assessment unavailable</div>
          <div class="answer-meta">
            <span class="answer-pill">Request failed</span>
          </div>
        </div>
        <div class="answer-text">The backend may have restarted or dropped the connection.</div>
        <div class="answer-text">${escapeHtml(String(error))}</div>
      `;
      claims.innerHTML = '<div class="timeline-empty">No claims due to request error.</div>';
      if (evidenceChunks) {
        evidenceChunks.innerHTML = '<div class="timeline-empty">No evidence due to request error.</div>';
      }
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = "Run briefing";
      }
      inFlightController = null;
    }
  });
}

renderChatThread();
loadQueryTemplates().catch(() => {});
