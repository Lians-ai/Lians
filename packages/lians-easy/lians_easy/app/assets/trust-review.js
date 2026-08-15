(() => {
  "use strict";

  const ID = "lians-trust-review";
  const REVIEW_PATH = "/v1/reviews";
  const POLL_MS = 30_000;
  let busy = false;
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
      "Lians holds possible contradictions and stale work out of AI context until you decide. Nothing here is silently deleted.",
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

  function actionButton(label, resolution, review, className = "") {
    const button = element(
      "button",
      `lians-review__button ${className}`.trim(),
      label,
    );
    button.type = "button";
    button.disabled = busy;
    button.addEventListener("click", () => resolve(review, resolution, button));
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
        ui.list.append(
          review.type === "possible_conflict" ? conflictCard(review) : staleCard(review),
        );
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

  async function resolve(review, resolution, button) {
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
      const result = await request(
        `/v1/reviews/${encodeURIComponent(review.id)}/resolve`,
        "POST",
        { resolution },
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
  });
  window.addEventListener("hashchange", () => {
    if (["#review", "#review-queue"].includes(window.location.hash)) setOpen(true);
  });
  window.addEventListener("focus", () => refresh());
  window.setInterval(() => {
    if (document.visibilityState === "visible") refresh();
  }, POLL_MS);
  if (["#review", "#review-queue"].includes(window.location.hash)) setOpen(true);
  refresh();
})();
