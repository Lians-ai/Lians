(() => {
  "use strict";

  const ID = "lians-trust-review";
  const REVIEW_PATH = "/v1/reviews";
  const DIAGNOSTICS_PATH = "/v1/diagnostics";
  const POLL_MS = 30_000;
  let busy = false;
  let diagnosticsBusy = false;
  let diagnosticsReport = null;
  let forgetTimer = null;
  let current = { reviews: [], total: 0, project: { name: "this project" } };

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  async function request(path, method = "GET", payload = {}) {
    const options = {
      method,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    };
    if (method !== "GET") {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify({ confirmed: true, ...payload });
    }
    const response = await fetch(path, options);
    const document = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(document.error || "review-action-failed");
    return document;
  }

  function sourceLine(memory) {
    const client = memory.source_client || "Lians App";
    const source = memory.source || "Saved memory";
    const reference = memory.source_ref ? ` · ${memory.source_ref}` : "";
    return `${client} · ${source}${reference}`;
  }

  function timeLine(memory) {
    const value = new Date(memory.updated_at || memory.created_at || "");
    const timestamp = Number.isNaN(value.getTime())
      ? "Unknown time"
      : value.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    const scope = memory.scope === "global" ? "All projects" : "This project";
    return `${timestamp} · ${scope}`;
  }

  function build() {
    const root = element("section", "lians-review");
    root.id = ID;
    root.dataset.tone = "clear";

    const trigger = element("button", "lians-review__trigger");
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-controls", "lians-review-dialog");
    const dot = element("span", "lians-review__dot");
    dot.setAttribute("aria-hidden", "true");
    const triggerText = element("span", "lians-review__trigger-text", "REVIEW · CLEAR");
    trigger.append(dot, triggerText);

    const backdrop = element("div", "lians-review__backdrop");
    backdrop.hidden = true;
    const dialog = element("div", "lians-review__dialog");
    dialog.id = "lians-review-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "lians-review-title");
    dialog.tabIndex = -1;

    const top = element("div", "lians-review__top");
    const heading = element("div", "lians-review__heading");
    heading.append(
      element("p", "lians-review__eyebrow", "TRUST REVIEW"),
      element("h2", "lians-review__title", "Review what Lians should trust"),
    );
    heading.lastChild.id = "lians-review-title";
    const close = element("button", "lians-review__close", "Close");
    close.type = "button";
    close.setAttribute("aria-label", "Close memory review");
    top.append(heading, close);

    const description = element(
      "p",
      "lians-review__description",
      "Lians holds device edit collisions, possible contradictions, and stale work out of AI context until you decide. Nothing here is silently deleted.",
    );
    const boundary = element("div", "lians-review__boundary");
    boundary.append(
      element("strong", "", "Why this is here"),
      element(
        "span",
        "",
        "Each item shows its exact source, time, scope, and why it was excluded. Your correction applies to every connected AI after encrypted sync.",
      ),
    );
    const summary = element("p", "lians-review__summary", "Checking memory…");
    summary.setAttribute("aria-live", "polite");
    const list = element("div", "lians-review__list");
    const feedback = element("p", "lians-review__feedback");
    feedback.setAttribute("role", "status");
    feedback.setAttribute("aria-live", "polite");
    dialog.append(top, description, boundary, summary, list, feedback);
    backdrop.append(dialog);
    root.append(trigger, backdrop);
    document.body.append(root);
    return { root, trigger, triggerText, backdrop, dialog, close, summary, list, feedback };
  }

  const ui = build();

  function buildDiagnostics() {
    const root = element("section", "lians-review lians-diagnostics");
    root.id = "lians-system-check";
    root.dataset.tone = "unchecked";

    const trigger = element(
      "button",
      "lians-review__trigger lians-diagnostics__trigger",
      "SYSTEM CHECK",
    );
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-controls", "lians-diagnostics-dialog");

    const backdrop = element("div", "lians-review__backdrop");
    backdrop.hidden = true;
    const dialog = element("div", "lians-review__dialog lians-diagnostics__dialog");
    dialog.id = "lians-diagnostics-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "lians-diagnostics-title");
    dialog.tabIndex = -1;

    const top = element("div", "lians-review__top");
    const heading = element("div", "lians-review__heading");
    heading.append(
      element("p", "lians-review__eyebrow", "PRIVATE SYSTEM CHECK"),
      element("h2", "lians-review__title", "Is Lians ready for my next chat?"),
    );
    heading.lastChild.id = "lians-diagnostics-title";
    const close = element("button", "lians-review__close", "Close");
    close.type = "button";
    close.setAttribute("aria-label", "Close system check");
    top.append(heading, close);

    const description = element(
      "p",
      "lians-review__description",
      "Check the Bridge, encrypted memory, connected AI tools, cloud continuity, and Trust Review in one place.",
    );
    const privacy = element("div", "lians-review__boundary lians-diagnostics__privacy");
    privacy.append(
      element("strong", "", "Safe to share with support"),
      element(
        "span",
        "",
        "The report excludes prompts, memory content, credentials, account identifiers, encryption keys, and local file paths.",
      ),
    );
    const overall = element("div", "lians-diagnostics__overall");
    overall.append(
      element("span", "lians-diagnostics__overall-state", "NOT CHECKED"),
      element("strong", "lians-diagnostics__overall-summary", "Run a private check when you need help."),
    );
    const list = element("div", "lians-diagnostics__checks");
    const feedback = element("p", "lians-review__feedback");
    feedback.setAttribute("role", "status");
    feedback.setAttribute("aria-live", "polite");
    const actions = element("div", "lians-diagnostics__actions");
    const run = element("button", "lians-review__button lians-review__button--primary", "Run system check");
    const download = element("button", "lians-review__button", "Download safe help report");
    run.type = "button";
    download.type = "button";
    download.disabled = true;
    actions.append(run, download);
    dialog.append(top, description, privacy, overall, list, actions, feedback);
    backdrop.append(dialog);
    root.append(trigger, backdrop);
    document.body.append(root);
    return {
      root,
      trigger,
      backdrop,
      dialog,
      close,
      overall,
      list,
      run,
      download,
      feedback,
    };
  }

  const diagnosticsUi = buildDiagnostics();

  function renderDiagnostics(report) {
    diagnosticsReport = report;
    const overall = ["ready", "attention", "problem"].includes(report.overall)
      ? report.overall
      : "problem";
    diagnosticsUi.root.dataset.tone = overall;
    diagnosticsUi.overall.dataset.tone = overall;
    diagnosticsUi.overall.querySelector("span").textContent = overall.toUpperCase();
    diagnosticsUi.overall.querySelector("strong").textContent =
      report.summary || "Lians could not complete the system check.";
    diagnosticsUi.list.replaceChildren();
    const checks = Array.isArray(report.checks) ? report.checks : [];
    checks.forEach((check) => {
      const status = ["ready", "attention", "problem"].includes(check.status)
        ? check.status
        : "problem";
      const card = element("article", "lians-diagnostics__check");
      card.dataset.tone = status;
      const heading = element("div", "lians-diagnostics__check-top");
      heading.append(
        element("strong", "", check.title || "Lians check"),
        element("span", "", status.toUpperCase()),
      );
      card.append(heading, element("p", "", check.message || "No result was returned."));
      diagnosticsUi.list.append(card);
    });
    diagnosticsUi.download.disabled = !checks.length;
    diagnosticsUi.trigger.textContent = overall === "ready"
      ? "SYSTEM · READY"
      : overall === "attention"
        ? "SYSTEM · CHECK"
        : "SYSTEM · HELP";
  }

  async function runDiagnostics() {
    if (diagnosticsBusy) return;
    diagnosticsBusy = true;
    diagnosticsUi.run.disabled = true;
    diagnosticsUi.download.disabled = true;
    diagnosticsUi.run.textContent = "Checking…";
    diagnosticsUi.feedback.textContent = "Checking only local operational state. No memory content is added to this report.";
    try {
      const report = await request(DIAGNOSTICS_PATH);
      renderDiagnostics(report);
      diagnosticsUi.feedback.textContent = report.overall === "ready"
        ? "Check complete. Lians is ready."
        : "Check complete. Follow the plain-language action shown above.";
    } catch (error) {
      diagnosticsUi.root.dataset.tone = "problem";
      diagnosticsUi.feedback.textContent = "The system check could not run. Keep Lians open and try again.";
    } finally {
      diagnosticsBusy = false;
      diagnosticsUi.run.disabled = false;
      diagnosticsUi.run.textContent = "Run system check again";
      diagnosticsUi.download.disabled = !diagnosticsReport;
    }
  }

  async function downloadDiagnostics() {
    if (diagnosticsBusy) return;
    diagnosticsBusy = true;
    diagnosticsUi.run.disabled = true;
    diagnosticsUi.download.disabled = true;
    diagnosticsUi.feedback.textContent = "Preparing a privacy-safe help report…";
    try {
      const response = await fetch("/v1/diagnostics/export", {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: true }),
      });
      if (!response.ok) throw new Error("diagnostics-export-failed");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "Lians-help-report.json";
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      diagnosticsUi.feedback.textContent = "Safe help report downloaded. You can inspect it before sharing.";
    } catch (error) {
      diagnosticsUi.feedback.textContent = "Lians could not download the report. No information was shared.";
    } finally {
      diagnosticsBusy = false;
      diagnosticsUi.run.disabled = false;
      diagnosticsUi.download.disabled = !diagnosticsReport;
    }
  }

  function setDiagnosticsOpen(open) {
    if (open && !ui.backdrop.hidden) {
      ui.backdrop.hidden = true;
      ui.trigger.setAttribute("aria-expanded", "false");
    }
    diagnosticsUi.backdrop.hidden = !open;
    document.documentElement.classList.toggle("lians-review-open", open);
    diagnosticsUi.trigger.setAttribute("aria-expanded", String(open));
    if (open) {
      diagnosticsUi.dialog.focus();
      runDiagnostics();
    } else {
      if (window.location.hash === "#system-check") {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      }
      diagnosticsUi.trigger.focus();
    }
  }

  function memoryPanel(memory, label, held) {
    const panel = element("section", "lians-review__memory");
    if (held) panel.dataset.held = "true";
    const top = element("div", "lians-review__memory-top");
    top.append(
      element("span", "lians-review__memory-label", label),
      element(
        "span",
        held ? "lians-review__held" : "lians-review__active",
        held ? "HELD FROM AI" : "CURRENTLY ACTIVE",
      ),
    );
    panel.append(
      top,
      element("p", "lians-review__content", memory.content || "This memory was forgotten."),
      element("p", "lians-review__source", sourceLine(memory)),
      element("p", "lians-review__time", timeLine(memory)),
    );
    return panel;
  }

  function actionButton(label, resolution, review, className = "", candidateId = null) {
    const button = element(
      "button",
      `lians-review__button ${className}`.trim(),
      label,
    );
    button.type = "button";
    button.disabled = busy;
    button.addEventListener("click", () => {
      resolve(review, resolution, button, candidateId);
    });
    return button;
  }

  function conflictCard(review) {
    const card = element("article", "lians-review__card");
    const label = element("div", "lians-review__card-label");
    label.append(
      element("span", "lians-review__type", "POSSIBLE CONFLICT"),
      element("span", "lians-review__review-id", review.id.slice(-8).toUpperCase()),
    );
    const comparison = element("div", "lians-review__comparison");
    comparison.append(
      memoryPanel(review.memory_a, "EXISTING MEMORY", false),
      memoryPanel(review.memory_b, "NEWER MEMORY", true),
    );
    const actions = element("div", "lians-review__actions");
    actions.append(
      actionButton("Keep existing", "keep_existing", review),
      actionButton("Use newer", "use_newer", review, "lians-review__button--primary"),
      actionButton("Both are valid", "keep_both", review),
    );
    card.append(label, element("p", "lians-review__reason", review.reason), comparison, actions);
    return card;
  }

  function staleCard(review) {
    const card = element("article", "lians-review__card");
    const label = element("div", "lians-review__card-label");
    label.append(
      element("span", "lians-review__type", "STALE MEMORY"),
      element("span", "lians-review__age", `${Number(review.age_days || 0)} DAYS OLD`),
    );
    const actions = element("div", "lians-review__actions");
    actions.append(
      actionButton("Still current", "keep_active", review, "lians-review__button--primary"),
      actionButton("Pause it", "pause", review),
      actionButton("Forget permanently", "forget", review, "lians-review__button--danger"),
    );
    card.append(
      label,
      element("p", "lians-review__reason", review.reason),
      memoryPanel(review.memory, "MEMORY WAITING FOR REVIEW", true),
      actions,
    );
    return card;
  }

  function divergentEditCard(review) {
    const card = element("article", "lians-review__card");
    const label = element("div", "lians-review__card-label");
    label.append(
      element("span", "lians-review__type", "DEVICE EDIT COLLISION"),
      element("span", "lians-review__review-id", review.id.slice(-8).toUpperCase()),
    );
    const original = memoryPanel(review.original_memory, "ORIGINAL MEMORY", true);
    const comparison = element("div", "lians-review__comparison");
    const candidates = Array.isArray(review.candidates) ? review.candidates : [];
    candidates.forEach((candidate, index) => {
      const panel = memoryPanel(candidate, `EDIT ${index + 1}`, true);
      panel.append(
        actionButton(
          "Use this edit",
          "use_candidate",
          review,
          "lians-review__button--primary lians-review__candidate-action",
          candidate.id,
        ),
      );
      comparison.append(panel);
    });
    const actions = element("div", "lians-review__actions");
    actions.append(actionButton("Keep every edit", "keep_both", review));
    card.append(
      label,
      element("p", "lians-review__reason", review.reason),
      original,
      comparison,
      actions,
    );
    return card;
  }

  function render(document, feedback = "") {
    current = document;
    const reviews = Array.isArray(document.reviews) ? document.reviews : [];
    const count = reviews.length;
    ui.root.dataset.tone = count ? "attention" : "clear";
    ui.triggerText.textContent = count ? `REVIEW · ${count}` : "REVIEW · CLEAR";
    ui.summary.textContent = count
      ? `${count} ${count === 1 ? "memory decision needs" : "memory decisions need"} you before reaching another AI.`
      : `Everything is clear for ${document.project && document.project.name ? document.project.name : "this project"}.`;
    ui.list.replaceChildren();
    if (!count) {
      const empty = element("div", "lians-review__empty");
      empty.append(
        element("strong", "", "Nothing needs review"),
        element(
          "p",
          "",
          "Active memories have a clear scope and no unresolved contradiction or stale handoff was found.",
        ),
      );
      ui.list.append(empty);
    } else {
      reviews.forEach((review) => {
        const card = review.type === "possible_conflict"
          ? conflictCard(review)
          : review.type === "divergent_edit"
            ? divergentEditCard(review)
            : staleCard(review);
        ui.list.append(card);
      });
    }
    ui.feedback.textContent = feedback;
  }

  async function refresh(feedback = "") {
    try {
      const document = await request(REVIEW_PATH);
      render(document, feedback);
      return document;
    } catch (error) {
      render(current, "Lians could not refresh Review. No memory was changed.");
      return current;
    }
  }

  async function resolve(review, resolution, button, candidateId = null) {
    if (busy) return;
    if (resolution === "forget" && button.dataset.armed !== "true") {
      button.dataset.armed = "true";
      button.textContent = "Click again to forget permanently";
      ui.feedback.textContent = "Permanent forgetting removes this memory and its content everywhere after sync.";
      clearTimeout(forgetTimer);
      forgetTimer = window.setTimeout(() => {
        button.dataset.armed = "false";
        button.textContent = "Forget permanently";
      }, 8_000);
      return;
    }
    busy = true;
    ui.list.querySelectorAll("button").forEach((item) => {
      item.disabled = true;
    });
    ui.feedback.textContent = "Saving your decision…";
    try {
      const payload = { resolution };
      if (candidateId) payload.candidate_id = candidateId;
      const result = await request(
        `/v1/reviews/${encodeURIComponent(review.id)}/resolve`,
        "POST",
        payload,
      );
      const cloud = result.cloud_sync || {};
      const message = cloud.memory_scope === "everywhere"
        ? "Decision saved everywhere. Connected AI tools will use it immediately."
        : "Decision saved on this device. Encrypted sync will carry it everywhere when available.";
      await refresh(message);
      window.setTimeout(() => window.location.reload(), 600);
    } catch (error) {
      await refresh(
        error instanceof Error
          ? error.message
          : "Lians could not save that decision. No memory was changed.",
      );
    } finally {
      busy = false;
      ui.list.querySelectorAll("button").forEach((item) => {
        item.disabled = false;
      });
    }
  }

  function setOpen(open) {
    if (open && !diagnosticsUi.backdrop.hidden) {
      diagnosticsUi.backdrop.hidden = true;
      diagnosticsUi.trigger.setAttribute("aria-expanded", "false");
    }
    ui.backdrop.hidden = !open;
    document.documentElement.classList.toggle("lians-review-open", open);
    ui.trigger.setAttribute("aria-expanded", String(open));
    if (open) {
      ui.dialog.focus();
      refresh();
    } else {
      clearTimeout(forgetTimer);
      if (["#review", "#review-queue"].includes(window.location.hash)) {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      }
      ui.trigger.focus();
    }
  }

  ui.trigger.addEventListener("click", () => {
    window.history.replaceState(null, "", "#review-queue");
    setOpen(true);
  });
  diagnosticsUi.trigger.addEventListener("click", () => {
    window.history.replaceState(null, "", "#system-check");
    setDiagnosticsOpen(true);
  });
  diagnosticsUi.close.addEventListener("click", () => setDiagnosticsOpen(false));
  diagnosticsUi.backdrop.addEventListener("click", (event) => {
    if (event.target === diagnosticsUi.backdrop) setDiagnosticsOpen(false);
  });
  diagnosticsUi.run.addEventListener("click", () => runDiagnostics());
  diagnosticsUi.download.addEventListener("click", () => downloadDiagnostics());
  ui.close.addEventListener("click", () => setOpen(false));
  ui.backdrop.addEventListener("click", (event) => {
    if (event.target === ui.backdrop) setOpen(false);
  });
  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element ? event.target.closest('a[href="#review"]') : null;
    if (!link) return;
    event.preventDefault();
    window.history.replaceState(null, "", "#review-queue");
    setOpen(true);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !ui.backdrop.hidden) setOpen(false);
    if (event.key === "Escape" && !diagnosticsUi.backdrop.hidden) setDiagnosticsOpen(false);
  });
  window.addEventListener("hashchange", () => {
    if (["#review", "#review-queue"].includes(window.location.hash)) setOpen(true);
    if (window.location.hash === "#system-check") setDiagnosticsOpen(true);
  });
  window.addEventListener("focus", () => refresh());
  window.setInterval(() => {
    if (document.visibilityState === "visible") refresh();
  }, POLL_MS);
  if (["#review", "#review-queue"].includes(window.location.hash)) setOpen(true);
  if (window.location.hash === "#system-check") setDiagnosticsOpen(true);
  refresh();
})();
