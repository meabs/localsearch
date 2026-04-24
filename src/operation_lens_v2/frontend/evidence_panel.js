const form = document.getElementById("query-form");
const input = document.getElementById("query-input");
const caseInput = document.getElementById("case-input");
const docIdInput = document.getElementById("doc-id-input");
const answer = document.getElementById("answer");
const claims = document.getElementById("claims");
const evidenceChunks = document.getElementById("evidence-chunks");
const reportPack = document.getElementById("report-pack");
const cloudToggle = document.getElementById("cloud-toggle");
const templateSelect = document.getElementById("query-template-select");
const recallModeSelect = document.getElementById("recall-mode-select");
const scopeInputs = Array.from(document.querySelectorAll('input[name="query-scope"]'));
const chatThread = document.getElementById("query-chat-thread");
const chatResetButton = document.getElementById("chat-reset-btn");
const runButtons = form ? Array.from(form.querySelectorAll("button[data-run-mode]")) : [];
const submitButton = runButtons[0] || null;
const agentTracePanel = document.getElementById("agent-trace-panel");
const agentTrace = document.getElementById("agent-trace");
const agentTraceStatus = document.getElementById("agent-trace-status");
const viewState = window.LensStateStore?.createStore?.("lens:v1");
let inFlightController = null;
const chatTurns = [];
const CHAT_HISTORY_LIMIT = 12;

function setAgentTraceStatus(label) {
  if (agentTraceStatus) {
    agentTraceStatus.textContent = label;
  }
}

function resetAgentTrace(message) {
  if (!agentTrace) return;
  agentTrace.innerHTML = `<div class="timeline-empty">${escapeHtml(message)}</div>`;
  setAgentTraceStatus("idle");
}

function appendTraceStep(event) {
  if (!agentTrace) return;
  if (agentTrace.querySelector(".timeline-empty")) {
    agentTrace.innerHTML = "";
  }
  const step = document.createElement("div");
  step.className = `agent-trace-step trace-${event.kind}`;

  const head = document.createElement("div");
  head.className = "agent-trace-step-head";
  let label;
  switch (event.kind) {
    case "tool_call":
      label = `→ ${event.tool || "tool"}`;
      break;
    case "tool_result":
      label = `← ${event.tool || "tool"}`;
      break;
    case "thought":
      label = "thought";
      break;
    case "thinking":
      label = "thinking";
      break;
    case "error":
      label = `error · ${event.error_type || "unknown"}`;
      break;
    case "start":
      label = `start · scope ${event.scope || "?"}`;
      break;
    case "composing":
      label = "composing briefing";
      break;
    default:
      label = event.kind || "event";
  }
  head.textContent = label;
  step.appendChild(head);

  let body = "";
  if (event.kind === "tool_call") {
    body = event.args_preview || "";
  } else if (event.kind === "tool_result") {
    body = event.result_preview || "";
  } else if (event.kind === "thought" || event.kind === "thinking") {
    body = event.text || "";
  } else if (event.kind === "error") {
    body = event.message || "";
  } else if (event.kind === "start") {
    body = event.scope_label || "";
  }
  if (body) {
    const bodyEl = document.createElement("pre");
    bodyEl.className = "agent-trace-step-body";
    bodyEl.textContent = body;
    step.appendChild(bodyEl);
  }

  agentTrace.appendChild(step);
  agentTrace.scrollTop = agentTrace.scrollHeight;
}

async function streamInvestigator(body, signal) {
  const resp = await fetch("/query/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    throw new Error(`stream failed (${resp.status}): ${text.slice(0, 240)}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalPayload = null;
  let terminalError = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const dataLine = rawEvent.split("\n").find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      const json = dataLine.slice(5).trim();
      if (!json) continue;
      let event;
      try {
        event = JSON.parse(json);
      } catch (_) {
        continue;
      }
      if (event.kind === "final") {
        finalPayload = event.payload;
      } else if (event.kind === "end") {
        // terminal sentinel — nothing to render
      } else if (event.kind === "error" && !event.recoverable) {
        terminalError = event;
        appendTraceStep(event);
      } else {
        appendTraceStep(event);
      }
    }
  }
  if (finalPayload) return finalPayload;
  if (terminalError) throw new Error(`${terminalError.error_type}: ${terminalError.message}`);
  throw new Error("stream ended without final payload");
}

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
  const sectionNames = [
    "ASSESSMENT",
    "PEOPLE",
    "PLACES",
    "ASSETS",
    "KEY FINDINGS",
    "CROSS-DOCUMENT LINKS",
    "TIMELINE",
    "CONFIDENCE POSTURE",
    "CONFIDENCE",
    "EVIDENCE GAPS",
    "GAPS",
    "SUGGESTED NEXT ACTIONS",
  ];
  const sectionRe = new RegExp(`\\b(${sectionNames.join("|")})\\b`, "g");
  const withBreaks = raw.replace(sectionRe, "\n$1\n");
  const lines = withBreaks
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);

  let html = "";
  let currentTitle = "";
  let buffer = [];
  const proseSections = new Set(["ASSESSMENT", "CONFIDENCE POSTURE"]);

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

    if (proseSections.has(currentTitle)) {
      const proseHtml = proseItems.length
        ? proseItems.map((item) => `<p>${escapeHtml(item)}</p>`).join("")
        : "<div class='answer-empty'>None identified.</div>";
      html += `<section class="answer-section"><h4>${escapeHtml(currentTitle)}</h4>${proseHtml}</section>`;
      return;
    }

    let list = [];
    if (bulletItems.length) {
      list = [...bulletItems];
      if (proseItems.length) {
        list.push(...proseItems);
      }
    } else {
      list = proseItems;
    }
    const listHtml = list.length
      ? `<ul>${list.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : "<div class='answer-empty'>None identified.</div>";
    html += `<section class="answer-section"><h4>${escapeHtml(currentTitle)}</h4>${listHtml}</section>`;
  }

  for (const line of lines) {
    const normalizedLine = line.replace(/^#+\s*/, "").trim();
    if (sectionNames.includes(normalizedLine.toUpperCase())) {
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

function renderClaim(claim, claimId) {
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
    <div class="claim" data-claim-id="${escapeHtml(claimId)}">
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

  claimRows.forEach((claim, claimIndex) => {
    const claimId = `c-${claimIndex}`;
    (claim.citations || []).forEach((citation, idx) => {
      items.push({
        key: `${citation.doc_id || "unknown-doc"}|${citation.page ?? "?"}|${idx}`,
        doc_id: citation.doc_id || "unknown-doc",
        doc_name: citation.doc_name || citation.doc_id || "unknown-doc",
        page: citation.page ?? "?",
        source: "claim-citation",
        text: citation.span_text || "",
        claim_id: claimId,
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
      byKey.set(item.key, { ...item, claim_ids: item.claim_id ? [item.claim_id] : [] });
      return;
    }
    const existing = byKey.get(item.key);
    if (item.claim_id && !existing.claim_ids.includes(item.claim_id)) existing.claim_ids.push(item.claim_id);
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
        <article class="evidence-card" data-evidence-key="${escapeHtml(item.key)}" data-claim-id="${escapeHtml((item.claim_ids || []).join(" "))}">
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

function clearReportPack() {
  if (!reportPack) return;
  reportPack.classList.remove("is-visible");
  reportPack.innerHTML = "";
}

function downloadTextFile(filename, content) {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function renderReportPack(data) {
  if (!reportPack) return;
  const pack = data?.report_pack;
  if (!pack) {
    clearReportPack();
    return;
  }
  const metrics = pack.metrics || {};
  const timeline = Array.isArray(pack.timeline) ? pack.timeline : [];
  const centrality = Array.isArray(pack.centrality) ? pack.centrality : [];
  const communities = Array.isArray(pack.communities) ? pack.communities : [];
  const documents = Array.isArray(pack.documents) ? pack.documents : [];

  reportPack.classList.add("is-visible");
  reportPack.innerHTML = `
    <div class="report-pack-header">
      <div>
        <div class="report-pack-title">Case intelligence report</div>
        <div class="report-pack-copy">
          Full-case summary for ${escapeHtml(pack.case_ref || data.case_scope || "current case")}
          ${pack.case_name ? `&middot; ${escapeHtml(pack.case_name)}` : ""}
          ${pack.generated_at ? `&middot; generated ${escapeHtml(pack.generated_at)}` : ""}
        </div>
      </div>
      <div class="report-pack-actions">
        <button type="button" class="btn btn-secondary" id="report-download-btn">Download markdown</button>
      </div>
    </div>
    <div class="report-pack-grid">
      <div class="report-pack-card">
        <div class="report-pack-label">Documents</div>
        <div class="report-pack-value">${escapeHtml(String(metrics.documents || 0))}</div>
      </div>
      <div class="report-pack-card">
        <div class="report-pack-label">Entities</div>
        <div class="report-pack-value">${escapeHtml(String(metrics.entities || 0))}</div>
      </div>
      <div class="report-pack-card">
        <div class="report-pack-label">Relationships</div>
        <div class="report-pack-value">${escapeHtml(String(metrics.relationships || 0))}</div>
      </div>
      <div class="report-pack-card">
        <div class="report-pack-label">Locations</div>
        <div class="report-pack-value">${escapeHtml(String(metrics.locations || 0))}</div>
      </div>
    </div>
    <section class="report-pack-section">
      <h4>Network highlights</h4>
      <div class="report-pack-meta">
        ${escapeHtml(String(pack.graph_metrics?.node_count || 0))} nodes &middot;
        ${escapeHtml(String(pack.graph_metrics?.edge_count || 0))} edges &middot;
        modularity ${escapeHtml(String(pack.modularity ?? 0))}
      </div>
      <ul>
        ${
          centrality.length
            ? centrality
                .slice(0, 8)
                .map(
                  (entity) =>
                    `<li>${escapeHtml(entity.canonical_name || entity.entity_id)} (${escapeHtml(
                      entity.entity_type || "?",
                    )}) &middot; score ${escapeHtml(String(entity.score ?? 0))}</li>`,
                )
                .join("")
            : "<li>No central actors available for this case yet.</li>"
        }
      </ul>
    </section>
    <section class="report-pack-section">
      <h4>Communities</h4>
      <ul>
        ${
          communities.length
            ? communities
                .slice(0, 6)
                .map((community) => {
                  const members = (community.members || [])
                    .slice(0, 5)
                    .map((member) => member.canonical_name || member.entity_id)
                    .filter(Boolean)
                    .join(", ");
                  return `<li>Cluster ${escapeHtml(String((community.community_id || 0) + 1))}: ${escapeHtml(
                    String(community.size || 0),
                  )} members${members ? ` &middot; ${escapeHtml(members)}` : ""}</li>`;
                })
                .join("")
            : "<li>No community clusters available for this case.</li>"
        }
      </ul>
    </section>
    <section class="report-pack-section">
      <h4>Chronology</h4>
      <ul>
        ${
          timeline.length
            ? timeline
                .slice(0, 12)
                .map(
                  (event) =>
                    `<li><strong>${escapeHtml(event.event_time || "Undated")}</strong>: ${escapeHtml(
                      event.text || "",
                    )}${event.doc_name || event.doc_id ? ` <span class="report-pack-meta">[${escapeHtml(
                      event.doc_name || event.doc_id,
                    )} p.${escapeHtml(String(event.page ?? "?"))}]</span>` : ""}</li>`,
                )
                .join("")
            : "<li>No dated events were extracted into this report.</li>"
        }
      </ul>
    </section>
    <section class="report-pack-section">
      <h4>Document register</h4>
      <ul>
        ${
          documents.length
            ? documents
                .slice(0, 16)
                .map(
                  (document) =>
                    `<li>${escapeHtml(document.filename || document.doc_id)} &middot; ${escapeHtml(
                      String(document.page_count || 0),
                    )} pages &middot; ${escapeHtml(String(document.entity_count || 0))} entities</li>`,
                )
                .join("")
            : "<li>No indexed documents found for this case.</li>"
        }
      </ul>
    </section>
  `;

  const downloadBtn = document.getElementById("report-download-btn");
  if (downloadBtn) {
    downloadBtn.addEventListener("click", () => {
      const safeCase = String(pack.case_ref || "case-report").replace(/[^a-z0-9_-]+/gi, "-");
      downloadTextFile(`${safeCase}-intelligence-report.md`, pack.markdown || data.answer || "");
    });
  }
}

function clearClaimEvidenceHighlights() {
  claims.querySelectorAll(".claim").forEach((el) => el.classList.remove("claim-active"));
  evidenceChunks.querySelectorAll(".evidence-card").forEach((el) => {
    el.classList.remove("evidence-active");
    el.classList.remove("evidence-dimmed");
  });
}

function activateClaimEvidence(claimId) {
  clearClaimEvidenceHighlights();
  if (!claimId) return;
  const claimEl = claims.querySelector(`.claim[data-claim-id="${CSS.escape(claimId)}"]`);
  if (claimEl) claimEl.classList.add("claim-active");
  const evidenceEls = Array.from(evidenceChunks.querySelectorAll(".evidence-card"));
  let firstMatch = null;
  evidenceEls.forEach((el) => {
    const ids = String(el.getAttribute("data-claim-id") || "")
      .split(/\s+/)
      .filter(Boolean);
    if (ids.includes(claimId)) {
      el.classList.add("evidence-active");
      if (!firstMatch) firstMatch = el;
    } else {
      el.classList.add("evidence-dimmed");
    }
  });
  if (firstMatch) firstMatch.scrollIntoView({ behavior: "smooth", block: "center" });
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

function getSelectedScope() {
  const checked = scopeInputs.find((inputEl) => inputEl.checked);
  return checked?.value || "corpus";
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
    viewState?.set("brief.lastQuery", query);
    if (!query) return;
    const caseRef = (caseInput?.value || "").trim();
    const docId = (docIdInput?.value || "").trim();
    const selectedScope = getSelectedScope();
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

    const requestBody = {
      query,
      case_ref: caseRef || null,
      case_scope: caseRef || null,
      doc_id: docId || null,
      scope: selectedScope,
      use_cloud: Boolean(cloudToggle?.checked),
      chat_history: priorChatHistory,
      recall_mode: recallModeSelect?.value || "auto",
    };

    resetAgentTrace("Investigator starting…");
    setAgentTraceStatus("running");

    async function runStreamRequest() {
      return streamInvestigator(requestBody, inFlightController.signal);
    }

    async function runRequest() {
      const resp = await fetch("/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
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
      const canStream = Boolean(selectedScope) && !requestBody.use_cloud;
      try {
        data = canStream ? await runStreamRequest() : await runRequest();
      } catch (firstErr) {
        if (firstErr.name === "AbortError") throw firstErr;
        await new Promise((resolve) => setTimeout(resolve, 400));
        data = await runRequest();
      }
      setAgentTraceStatus("done");

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
      const claimItems = (data.claims || []).map((claim, idx) => renderClaim(claim, `c-${idx}`)).join("");
      claims.innerHTML = claimItems || '<div class="timeline-empty">No claims returned for this response.</div>';
      renderEvidencePanel(data);
      claims.querySelectorAll(".claim").forEach((claimEl) => {
        claimEl.addEventListener("click", () => {
          activateClaimEvidence(claimEl.getAttribute("data-claim-id") || "");
        });
      });
      evidenceChunks.querySelectorAll(".evidence-card").forEach((cardEl) => {
        cardEl.addEventListener("click", () => {
          const first = String(cardEl.getAttribute("data-claim-id") || "").split(/\s+/).filter(Boolean)[0];
          if (first) activateClaimEvidence(first);
        });
      });
      pushChatTurn("assistant", data.answer || "No answer generated.");
      claims.querySelectorAll(".citation-link").forEach((el) => {
        el.addEventListener("click", () => {
          const key = el.getAttribute("data-evidence-key") || "";
          const snippet = el.textContent || "";
          focusEvidenceCard(key, snippet);
          const card = key.split("|");
          const docId = card[0] || "";
          const page = Number(card[1] || "1");
          if (docId) {
            window.dispatchEvent(
              new CustomEvent("lens:open-source", {
                detail: { doc_id: docId, page: Number.isFinite(page) ? page : 1 },
              }),
            );
          }
        });
      });
      window.addEventListener("keydown", (evt) => {
        if (evt.key === "Escape") clearClaimEvidenceHighlights();
      });
      window.dispatchEvent(new CustomEvent("lens:query-result", { detail: data }));
    } catch (error) {
      if (error.name === "AbortError") {
        setAgentTraceStatus("cancelled");
        return;
      }
      setAgentTraceStatus("failed");
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

window.LensQueryUI = {
  activateClaimEvidence,
  appendTraceStep,
  clearClaimEvidenceHighlights,
  clearReportPack,
  escapeHtml,
  focusEvidenceCard,
  formatAnswerText,
  pushChatTurn,
  renderClaim,
  renderEvidencePanel,
  renderReportPack,
  resetAgentTrace,
  setAgentTraceStatus,
};

renderChatThread();
loadQueryTemplates().catch(() => {});
const lastQuery = viewState?.get("brief.lastQuery", "");
if (lastQuery && input && !input.value.trim()) input.value = lastQuery;
