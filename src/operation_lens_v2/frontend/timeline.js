// timeline.js - Mode 3: chronological event timeline
// Fetches /timeline and renders a vertical timeline with filterable events.

(function () {
  const submitBtn = document.getElementById("timeline-submit");
  const entityInput = document.getElementById("timeline-entity");
  const docIdsInput = document.getElementById("timeline-doc-ids");
  const container = document.getElementById("timeline-container");
  const statusEl = document.getElementById("timeline-status");

  if (!submitBtn || !container || !statusEl) return;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const timelineState = {
    events: [],
    bucketMode: "day",
    selectedBucketKey: "",
    entityFilter: "",
  };

  submitBtn.addEventListener("click", async () => {
    const entity = entityInput ? entityInput.value.trim() : "";
    const docIds = docIdsInput ? docIdsInput.value.trim() : "";

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
    timelineState.events = Array.isArray(data.events) ? data.events.slice() : [];
    timelineState.entityFilter = data.entity_filter || "";
    timelineState.selectedBucketKey = "";
    timelineState.bucketMode = "day";
    renderTimelineView();
  }

  function renderTimelineView() {
    const events = timelineState.events;
    const bucketMode = timelineState.bucketMode;
    const buckets = buildBuckets(events, bucketMode);
    const selectedBucket =
      timelineState.selectedBucketKey && buckets.find((bucket) => bucket.key === timelineState.selectedBucketKey);
    const visibleEvents = selectedBucket
      ? events.filter((event) => getBucketInfo(event, bucketMode).key === selectedBucket.key)
      : events;

    const baseStatus = `${visibleEvents.length} event${visibleEvents.length !== 1 ? "s" : ""}`;
    const scopeStatus = timelineState.entityFilter ? ` mentioning "${timelineState.entityFilter}"` : "";
    const bucketStatus = selectedBucket
      ? ` // ${bucketMode === "week" ? "week" : "day"} bucket "${selectedBucket.label}"`
      : ` // grouped by ${bucketMode}`;
    statusEl.textContent = `${baseStatus}${scopeStatus}${bucketStatus}${events.length ? ` // ${events.length} total` : ""}`;

    if (!events.length) {
      container.innerHTML = '<div class="timeline-empty">No dated events found for the current filter.</div>';
      return;
    }

    const stack = document.createElement("div");
    stack.className = "timeline-stack";
    stack.appendChild(buildChartCard(buckets));
    stack.appendChild(buildTimelineList(visibleEvents));

    container.innerHTML = "";
    container.appendChild(stack);
  }

  function buildChartCard(buckets) {
    const shell = document.createElement("section");
    shell.className = "timeline-chart-shell";

    const header = document.createElement("div");
    header.className = "timeline-chart-head";

    const titleWrap = document.createElement("div");
    const kicker = document.createElement("div");
    kicker.className = "timeline-chart-kicker";
    kicker.textContent = "Event density";

    const title = document.createElement("div");
    title.className = "timeline-chart-title";
    title.textContent =
      timelineState.bucketMode === "week" ? "Event count per week" : "Event count per day";

    const subtitle = document.createElement("div");
    subtitle.className = "timeline-chart-copy";
    subtitle.textContent = "Click a bar to filter the timeline below. Click the active bar again to clear it.";

    titleWrap.appendChild(kicker);
    titleWrap.appendChild(title);
    titleWrap.appendChild(subtitle);

    const actions = document.createElement("div");
    actions.className = "timeline-chart-actions";

    const dayBtn = document.createElement("button");
    dayBtn.type = "button";
    dayBtn.className = `btn btn-secondary timeline-mode-btn${timelineState.bucketMode === "day" ? " active" : ""}`;
    dayBtn.textContent = "Day";
    dayBtn.addEventListener("click", () => setBucketMode("day"));

    const weekBtn = document.createElement("button");
    weekBtn.type = "button";
    weekBtn.className = `btn btn-secondary timeline-mode-btn${timelineState.bucketMode === "week" ? " active" : ""}`;
    weekBtn.textContent = "Week";
    weekBtn.addEventListener("click", () => setBucketMode("week"));

    const clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.className = "btn btn-secondary timeline-mode-btn";
    clearBtn.textContent = "Clear";
    clearBtn.disabled = !timelineState.selectedBucketKey;
    clearBtn.addEventListener("click", () => {
      if (!timelineState.selectedBucketKey) return;
      timelineState.selectedBucketKey = "";
      renderTimelineView();
    });

    actions.appendChild(dayBtn);
    actions.appendChild(weekBtn);
    actions.appendChild(clearBtn);

    header.appendChild(titleWrap);
    header.appendChild(actions);

    const summary = document.createElement("div");
    summary.className = "timeline-chart-summary";
    if (timelineState.selectedBucketKey) {
      const selectedBucket = buckets.find((bucket) => bucket.key === timelineState.selectedBucketKey);
      summary.textContent = selectedBucket
        ? `Focused on ${selectedBucket.label} (${selectedBucket.count} event${selectedBucket.count !== 1 ? "s" : ""}).`
        : "Focused bucket selected.";
    } else {
      summary.textContent = `${buckets.length} bucket${buckets.length !== 1 ? "s" : ""} available.`;
    }

    const viewport = document.createElement("div");
    viewport.className = "timeline-chart-viewport";

    if (!buckets.length) {
      viewport.innerHTML = '<div class="timeline-empty">No buckets could be built from these events.</div>';
    } else {
      viewport.appendChild(buildTimelineSvg(buckets));
    }

    shell.appendChild(header);
    shell.appendChild(summary);
    shell.appendChild(viewport);
    return shell;
  }

  function buildTimelineList(events) {
    if (!events.length) {
      const empty = document.createElement("div");
      empty.className = "timeline-empty";
      empty.textContent = "No dated events match the selected bucket.";
      return empty;
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

    return ul;
  }

  function buildTimelineSvg(buckets) {
    const width = 760;
    const labelWidth = 200;
    const countWidth = 64;
    const innerPadding = 20;
    const rowHeight = 30;
    const rowGap = 10;
    const topPad = 10;
    const bottomPad = 12;
    const barWidth = Math.max(120, width - labelWidth - countWidth - innerPadding * 2);
    const height = topPad + bottomPad + buckets.length * rowHeight + Math.max(0, buckets.length - 1) * rowGap;
    const maxCount = Math.max(...buckets.map((bucket) => bucket.count), 1);

    const svg = document.createElementNS(SVG_NS, "svg");
    svg.classList.add("timeline-chart-svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", `${height}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `Timeline event count grouped by ${timelineState.bucketMode}`);

    buckets.forEach((bucket, index) => {
      const rowTop = topPad + index * (rowHeight + rowGap);
      const rowCenter = rowTop + rowHeight / 2;
      const rowWidth = Math.max(8, Math.round((bucket.count / maxCount) * barWidth));

      const group = document.createElementNS(SVG_NS, "g");
      group.classList.add("timeline-chart-row");
      if (timelineState.selectedBucketKey === bucket.key) group.classList.add("active");
      group.setAttribute("tabindex", "0");
      group.setAttribute("role", "button");
      group.setAttribute("aria-pressed", timelineState.selectedBucketKey === bucket.key ? "true" : "false");
      group.setAttribute("aria-label", `${bucket.label}, ${bucket.count} event${bucket.count !== 1 ? "s" : ""}`);
      group.setAttribute("data-bucket-key", bucket.key);

      const hitArea = document.createElementNS(SVG_NS, "rect");
      hitArea.setAttribute("x", "0");
      hitArea.setAttribute("y", String(rowTop - 2));
      hitArea.setAttribute("width", String(width));
      hitArea.setAttribute("height", String(rowHeight + 4));
      hitArea.setAttribute("fill", "transparent");

      const label = document.createElementNS(SVG_NS, "text");
      label.classList.add("timeline-chart-label");
      label.setAttribute("x", "12");
      label.setAttribute("y", String(rowCenter + 4));
      label.textContent = bucket.label;

      const track = document.createElementNS(SVG_NS, "rect");
      track.classList.add("timeline-chart-track");
      track.setAttribute("x", String(labelWidth));
      track.setAttribute("y", String(rowTop + 6));
      track.setAttribute("width", String(barWidth));
      track.setAttribute("height", "18");
      track.setAttribute("rx", "9");

      const fill = document.createElementNS(SVG_NS, "rect");
      fill.classList.add("timeline-chart-fill");
      if (timelineState.selectedBucketKey === bucket.key) fill.classList.add("active");
      fill.setAttribute("x", String(labelWidth));
      fill.setAttribute("y", String(rowTop + 6));
      fill.setAttribute("width", String(rowWidth));
      fill.setAttribute("height", "18");
      fill.setAttribute("rx", "9");

      const count = document.createElementNS(SVG_NS, "text");
      count.classList.add("timeline-chart-count");
      count.setAttribute("x", String(width - 12));
      count.setAttribute("y", String(rowCenter + 4));
      count.setAttribute("text-anchor", "end");
      count.textContent = String(bucket.count);

      group.appendChild(hitArea);
      group.appendChild(label);
      group.appendChild(track);
      group.appendChild(fill);
      group.appendChild(count);

      const activate = () => {
        if (timelineState.selectedBucketKey === bucket.key) {
          timelineState.selectedBucketKey = "";
        } else {
          timelineState.selectedBucketKey = bucket.key;
        }
        renderTimelineView();
      };
      group.addEventListener("click", activate);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate();
        }
      });

      svg.appendChild(group);
    });

    return svg;
  }

  function setBucketMode(mode) {
    if (timelineState.bucketMode === mode) return;
    timelineState.bucketMode = mode;
    timelineState.selectedBucketKey = "";
    renderTimelineView();
  }

  function buildBuckets(events, mode) {
    const buckets = new Map();
    events.forEach((event) => {
      const info = getBucketInfo(event, mode);
      const existing = buckets.get(info.key);
      if (existing) {
        existing.count += 1;
        return;
      }
      buckets.set(info.key, {
        key: info.key,
        label: info.label,
        count: 1,
        sortKey: info.sortKey,
      });
    });

    return Array.from(buckets.values()).sort((a, b) => {
      if (a.sortKey !== b.sortKey) return a.sortKey - b.sortKey;
      return a.label.localeCompare(b.label);
    });
  }

  function getBucketInfo(event, mode) {
    const rawDate = String(event?.date || "").trim();
    const parsed = parseFlexibleDate(rawDate);
    if (!parsed) {
      const fallback = rawDate || "Unknown date";
      return {
        key: `raw:${fallback.toLowerCase()}`,
        label: fallback,
        sortKey: Number.MAX_SAFE_INTEGER,
      };
    }

    if (mode === "week") {
      const weekStart = startOfWeek(parsed);
      const weekEnd = new Date(weekStart.getTime());
      weekEnd.setUTCDate(weekEnd.getUTCDate() + 6);
      return {
        key: `week:${formatIsoDate(weekStart)}`,
        label: `${formatShortDate(weekStart)} - ${formatShortDate(weekEnd)}`,
        sortKey: weekStart.getTime(),
      };
    }

    return {
      key: `day:${formatIsoDate(parsed)}`,
      label: formatFriendlyDate(parsed),
      sortKey: parsed.getTime(),
    };
  }

  function parseFlexibleDate(value) {
    const normalized = normalizeDateText(value);
    if (!normalized) return null;
    const candidates = [normalized, normalized.replace("T", " "), normalized.replace(/\s+/g, " ")];
    for (const candidate of candidates) {
      const parsed = new Date(candidate);
      if (!Number.isNaN(parsed.getTime())) return parsed;
    }
    return null;
  }

  function normalizeDateText(value) {
    return String(value || "")
      .trim()
      .replace(/^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?[,\s]+/i, "")
      .replace(/\bat\b/i, " ")
      .replace(/(\d{1,2})(?:st|nd|rd|th)\b/gi, "$1")
      .replace(/Z$/i, "+00:00")
      .replace(/\s+/g, " ")
      .replace(/[,\s]+$/g, "");
  }

  function startOfWeek(date) {
    const weekStart = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
    const day = weekStart.getUTCDay();
    const offset = day === 0 ? -6 : 1 - day;
    weekStart.setUTCDate(weekStart.getUTCDate() + offset);
    return weekStart;
  }

  function formatIsoDate(date) {
    const year = date.getUTCFullYear();
    const month = String(date.getUTCMonth() + 1).padStart(2, "0");
    const day = String(date.getUTCDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function formatFriendlyDate(date) {
    return new Intl.DateTimeFormat("en-GB", {
      month: "short",
      day: "numeric",
      year: "numeric",
      timeZone: "UTC",
    }).format(date);
  }

  function formatShortDate(date) {
    return new Intl.DateTimeFormat("en-GB", {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    }).format(date);
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
