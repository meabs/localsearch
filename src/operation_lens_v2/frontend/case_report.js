(function () {
  const form = document.getElementById("query-form");
  const input = document.getElementById("query-input");
  const caseInput = document.getElementById("case-input");
  const reportCaseSelect = document.getElementById("report-case-select");
  const reportPromptInput = document.getElementById("report-prompt-input");
  const reportShortcutButton = document.getElementById("report-shortcut-btn");
  const answer = document.getElementById("answer");
  const claims = document.getElementById("claims");
  const evidenceChunks = document.getElementById("evidence-chunks");
  const reportButton = document.getElementById("case-report-btn");
  const runButtons = form ? Array.from(form.querySelectorAll("button[data-run-mode]")) : [];
  const queryUI = window.LensQueryUI;

  if (!form || !reportButton || !queryUI) return;

  function setButtonsBusy(activeButton, busyLabel) {
    runButtons.forEach((button) => {
      button.disabled = true;
      button.dataset.originalLabel = button.textContent || "";
      if (button === activeButton) {
        button.textContent = busyLabel;
      }
    });
  }

  function restoreButtons() {
    runButtons.forEach((button) => {
      button.disabled = false;
      if (button.dataset.originalLabel) {
        button.textContent = button.dataset.originalLabel;
      }
    });
  }

  function attachClaimInteractions() {
    claims.querySelectorAll(".claim").forEach((claimEl) => {
      claimEl.addEventListener("click", () => {
        queryUI.activateClaimEvidence(claimEl.getAttribute("data-claim-id") || "");
      });
    });
    evidenceChunks.querySelectorAll(".evidence-card").forEach((cardEl) => {
      cardEl.addEventListener("click", () => {
        const first = String(cardEl.getAttribute("data-claim-id") || "")
          .split(/\s+/)
          .filter(Boolean)[0];
        if (first) queryUI.activateClaimEvidence(first);
      });
    });
    claims.querySelectorAll(".citation-link").forEach((el) => {
      el.addEventListener("click", () => {
        const key = el.getAttribute("data-evidence-key") || "";
        const snippet = el.textContent || "";
        queryUI.focusEvidenceCard(key, snippet);
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
  }

  async function generateCaseReport(caseRef, prompt) {
    const response = await fetch("/query/case-report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        case_ref: caseRef,
        prompt: prompt || null,
      }),
    });
    if (!response.ok) {
      const body = await response.text().catch(() => "");
      throw new Error(`Case report failed (${response.status}): ${body.slice(0, 240)}`);
    }
    return response.json();
  }

  function renderMissingCaseMessage() {
    answer.innerHTML = `
      <div class="answer-header">
        <div class="answer-title">Case selection required</div>
        <div class="answer-meta">
          <span class="answer-pill">Case report</span>
        </div>
      </div>
      <div class="answer-text">Choose a registered case from the dropdown before generating a case intelligence report.</div>
    `;
  }

  async function runCaseReport(activeButton, caseRef, prompt) {
    if (!caseRef) {
      renderMissingCaseMessage();
      return;
    }

    const cleanPrompt = String(prompt || "").trim();
    const userTurn = cleanPrompt
      ? `Generate a full case intelligence report for ${caseRef}. Focus: ${cleanPrompt}`
      : `Generate a full case intelligence report for ${caseRef}.`;

    queryUI.pushChatTurn("user", userTurn);
    queryUI.resetAgentTrace("Generating case-wide report...");
    queryUI.setAgentTraceStatus("running");
    setButtonsBusy(activeButton, "Generating report...");
    if (reportShortcutButton) {
      reportShortcutButton.disabled = true;
      reportShortcutButton.dataset.originalLabel = reportShortcutButton.textContent || "";
      if (activeButton === reportShortcutButton) {
        reportShortcutButton.textContent = "Generating report...";
      }
    }

    answer.innerHTML = `
      <div class="answer-header">
        <div class="answer-title">Case intelligence report in progress</div>
        <div class="answer-meta">
          <span class="answer-pill">Case report</span>
          <span class="answer-pill">${queryUI.escapeHtml(caseRef)}</span>
        </div>
      </div>
      <div class="answer-text">Gathering case-wide findings, graph analytics, and chronology.</div>
    `;
    claims.innerHTML = '<div class="timeline-empty">Waiting for claim validation.</div>';
    evidenceChunks.innerHTML =
      '<div class="timeline-empty">Collecting case-wide evidence and graph context.</div>';
    queryUI.clearReportPack();

    try {
      const data = await generateCaseReport(caseRef, cleanPrompt);
      queryUI.setAgentTraceStatus("done");
      answer.innerHTML = `
        <div class="answer-header">
          <div class="answer-title">Case intelligence report</div>
          <div class="answer-meta">
            <span class="answer-pill">Intent ${queryUI.escapeHtml(data.intent || "unknown")}</span>
            <span class="answer-pill">Scope ${queryUI.escapeHtml(data.case_scope || caseRef)}</span>
            <span class="answer-pill">${queryUI.escapeHtml(String(data.result_count || 0))} results</span>
            <span class="answer-pill">Backend ${queryUI.escapeHtml(data.backend || "unknown")}</span>
            <span class="answer-pill">Recall ${queryUI.escapeHtml(data.recall_mode || "case-report")}</span>
          </div>
        </div>
        <div class="answer-structured">${queryUI.formatAnswerText(data.answer)}</div>
      `;

      const claimItems = (data.claims || [])
        .map((claim, idx) => queryUI.renderClaim(claim, `c-${idx}`))
        .join("");
      claims.innerHTML =
        claimItems || '<div class="timeline-empty">No claims returned for this report.</div>';
      queryUI.renderEvidencePanel(data);
      queryUI.renderReportPack(data);
      attachClaimInteractions();
      queryUI.pushChatTurn("assistant", data.answer || "Case intelligence report generated.");
      window.dispatchEvent(new CustomEvent("lens:query-result", { detail: data }));
    } catch (error) {
      queryUI.setAgentTraceStatus("failed");
      queryUI.pushChatTurn("assistant", `Request failed: ${String(error)}`);
      answer.innerHTML = `
        <div class="answer-header">
          <div class="answer-title">Case report unavailable</div>
          <div class="answer-meta">
            <span class="answer-pill">Request failed</span>
          </div>
        </div>
        <div class="answer-text">${queryUI.escapeHtml(String(error))}</div>
      `;
      claims.innerHTML = '<div class="timeline-empty">No claims due to request error.</div>';
      evidenceChunks.innerHTML = '<div class="timeline-empty">No evidence due to request error.</div>';
      queryUI.clearReportPack();
    } finally {
      restoreButtons();
      if (reportShortcutButton) {
        reportShortcutButton.disabled = false;
        if (reportShortcutButton.dataset.originalLabel) {
          reportShortcutButton.textContent = reportShortcutButton.dataset.originalLabel;
        }
      }
    }
  }

  form.addEventListener(
    "submit",
    async (event) => {
      const submitter = event.submitter instanceof HTMLElement ? event.submitter : null;
      if (!submitter || submitter.dataset.runMode !== "case-report") {
        return;
      }

      event.preventDefault();
      event.stopImmediatePropagation();
      await runCaseReport(submitter, String(caseInput?.value || "").trim(), String(input?.value || "").trim());
    },
    true,
  );

  if (reportShortcutButton) {
    reportShortcutButton.addEventListener("click", async () => {
      await runCaseReport(
        reportShortcutButton,
        String(reportCaseSelect?.value || caseInput?.value || "").trim(),
        String(reportPromptInput?.value || "").trim(),
      );
    });
  }
})();
