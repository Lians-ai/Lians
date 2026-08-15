(() => {
  "use strict";

  const ID = "lians-cloud-controls";
  const STATUS_PATH = "/v1/cloud/status";
  const POLL_MS = 30_000;
  let deleteTimer = null;
  let busy = false;
  let currentStatus = { state: "loading", sync_state: "not_started" };

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  async function request(path, method = "GET") {
    const options = {
      method,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    };
    if (method !== "GET") {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify({ confirmed: true });
    }
    const response = await fetch(path, options);
    const document = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error("cloud-action-failed");
    return document;
  }

  function viewModel(status) {
    const state = status.state || "unavailable";
    const syncState = status.sync_state || "not_started";
    const hasWorkspace = syncState === "ready" || Number(status.head_revision || 0) > 0;
    if (state === "connected" || state === "current" || state === "synced") {
      return {
        tone: "on",
        trigger: "CLOUD SYNC · ON",
        status: "ENCRYPTED SYNC ON",
        title: "Your memory follows you",
        description:
          "Lians checks for changes before an AI uses memory and saves corrections everywhere after you make them.",
        primary: "Sync now",
        primaryAction: "sync",
        secondary: "Turn off sync",
        danger: hasWorkspace,
      };
    }
    if (state === "refresh_required" || state === "needs_attention" || state === "pending") {
      return {
        tone: "attention",
        trigger: "SYNC · ACTION NEEDED",
        status: "LOCAL MEMORY IS SAFE",
        title: "Cloud sync needs attention",
        description:
          "Your memory is still available on this device. Reconnect to continue carrying changes between AI tools.",
        primary: "Reconnect",
        primaryAction: state === "refresh_required" ? "sync" : "signin",
        secondary: "Turn off sync",
        danger: hasWorkspace,
      };
    }
    if (state === "signed_out") {
      return {
        tone: "off",
        trigger: "CLOUD SYNC · OFF",
        status: "PRIVATE ON THIS DEVICE",
        title: "Take your memory to every AI",
        description:
          "Turn on encrypted sync with a normal browser sign-in. The Lians App never asks for or displays an API key.",
        primary: "Turn on encrypted sync",
        primaryAction: "signin",
        secondary: "",
        danger: false,
      };
    }
    if (state === "loading") {
      return {
        tone: "loading",
        trigger: "CHECKING MEMORY",
        status: "CHECKING",
        title: "Checking encrypted memory",
        description: "Lians is checking whether cloud continuity is available in this build.",
        primary: "",
        primaryAction: "",
        secondary: "",
        danger: false,
      };
    }
    return {
      tone: "local",
      trigger: "MEMORY · THIS DEVICE",
      status: "ENCRYPTED LOCAL",
      title: "Your memory stays on this device",
      description:
        "Local memory is ready and works without an account. Encrypted cloud continuity is not configured in this build.",
      primary: "",
      primaryAction: "",
      secondary: "",
      danger: false,
    };
  }

  function build() {
    const root = element("section", "lians-cloud");
    root.id = ID;

    const trigger = element("button", "lians-cloud__trigger");
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-controls", "lians-cloud-dialog");
    const triggerDot = element("span", "lians-cloud__dot");
    triggerDot.setAttribute("aria-hidden", "true");
    const triggerText = element("span", "lians-cloud__trigger-text", "CHECKING MEMORY");
    trigger.append(triggerDot, triggerText);

    const backdrop = element("div", "lians-cloud__backdrop");
    backdrop.hidden = true;
    const dialog = element("div", "lians-cloud__dialog");
    dialog.id = "lians-cloud-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "lians-cloud-title");
    dialog.tabIndex = -1;

    const top = element("div", "lians-cloud__top");
    const eyebrow = element("p", "lians-cloud__eyebrow", "LIANS CLOUD");
    const close = element("button", "lians-cloud__close", "Close");
    close.type = "button";
    close.setAttribute("aria-label", "Close cloud memory controls");
    top.append(eyebrow, close);

    const status = element("p", "lians-cloud__status", "CHECKING");
    const title = element("h2", "lians-cloud__title", "Checking encrypted memory");
    title.id = "lians-cloud-title";
    const description = element("p", "lians-cloud__description");

    const facts = element("div", "lians-cloud__facts");
    [
      ["01", "Encrypted before upload", "Cloud storage receives ciphertext, not your memory."],
      ["02", "Lians cannot read it", "The workspace key stays with devices you approve."],
      ["03", "Corrections travel", "Update or forget once and connected AI tools use the change."],
    ].forEach(([number, heading, copy]) => {
      const fact = element("div", "lians-cloud__fact");
      fact.append(
        element("span", "lians-cloud__fact-number", number),
        element("strong", "lians-cloud__fact-title", heading),
        element("span", "lians-cloud__fact-copy", copy),
      );
      facts.append(fact);
    });

    const recovery = element("div", "lians-cloud__recovery");
    recovery.append(
      element("strong", "", "Keep a recovery path"),
      element(
        "span",
        "",
        "Until Add device and recovery are enabled in this preview, keep this computer or an encrypted Lians backup.",
      ),
    );

    const metrics = element("p", "lians-cloud__metrics");
    const feedback = element("p", "lians-cloud__feedback");
    feedback.setAttribute("role", "status");
    feedback.setAttribute("aria-live", "polite");

    const actions = element("div", "lians-cloud__actions");
    const primary = element("button", "lians-cloud__button lians-cloud__button--primary");
    primary.type = "button";
    const secondary = element("button", "lians-cloud__button lians-cloud__button--secondary");
    secondary.type = "button";
    const danger = element("button", "lians-cloud__button lians-cloud__button--danger", "Delete cloud copy");
    danger.type = "button";
    actions.append(primary, secondary, danger);

    dialog.append(top, status, title, description, facts, recovery, metrics, feedback, actions);
    backdrop.append(dialog);
    root.append(trigger, backdrop);
    document.body.append(root);

    return {
      root,
      trigger,
      triggerText,
      backdrop,
      dialog,
      close,
      status,
      title,
      description,
      metrics,
      feedback,
      primary,
      secondary,
      danger,
    };
  }

  const ui = build();

  function setOpen(open) {
    ui.backdrop.hidden = !open;
    document.documentElement.classList.toggle("lians-cloud-open", open);
    ui.trigger.setAttribute("aria-expanded", String(open));
    if (open) {
      ui.dialog.focus();
      refresh();
    } else {
      if (window.location.hash === "#cloud-sync") {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      }
      ui.trigger.focus();
    }
  }

  function render(status, feedback = "") {
    currentStatus = status;
    const model = viewModel(status);
    ui.root.dataset.tone = model.tone;
    ui.triggerText.textContent = model.trigger;
    ui.status.textContent = model.status;
    ui.title.textContent = model.title;
    ui.description.textContent = model.description;
    ui.feedback.textContent = feedback;
    ui.primary.hidden = !model.primary;
    ui.primary.textContent = model.primary;
    ui.primary.dataset.action = model.primaryAction;
    ui.secondary.hidden = !model.secondary;
    ui.secondary.textContent = model.secondary;
    ui.danger.hidden = !model.danger;
    ui.danger.dataset.armed = "false";
    ui.danger.textContent = "Delete cloud copy";
    const revision = Number(status.head_revision || 0);
    const devices = Math.max(1, Number(status.device_count || 1));
    ui.metrics.textContent =
      status.sync_state === "ready"
        ? `${devices} ${devices === 1 ? "device" : "devices"} · encrypted revision ${revision}`
        : "Local memory stays available whether cloud sync is on or off.";
    [ui.primary, ui.secondary, ui.danger].forEach((button) => {
      button.disabled = busy;
    });
  }

  async function refresh(feedback = "") {
    try {
      const status = await request(STATUS_PATH);
      render(status, feedback);
    } catch {
      render(
        { state: "needs_attention", sync_state: currentStatus.sync_state },
        "Lians could not check cloud sync. Your local memory is still available.",
      );
    }
  }

  async function act(action) {
    if (busy) return;
    busy = true;
    render(currentStatus, action === "signin" ? "Finish signing in with your browser…" : "Working…");
    try {
      if (action === "signin") {
        await request("/v1/cloud/sign-in", "POST");
        await request("/v1/cloud/sync", "POST");
        await refresh("Encrypted sync is on. New memory and corrections can travel between tools.");
      } else if (action === "sync") {
        await request("/v1/cloud/sync", "POST");
        await refresh("Encrypted memory is up to date.");
      } else if (action === "signout") {
        await request("/v1/cloud/sign-out", "POST");
        await refresh("Sync is off. Local memory was preserved.");
      } else if (action === "delete") {
        await request("/v1/cloud/delete", "POST");
        await refresh("The cloud copy was deleted and sync was turned off. Local memory remains here.");
      }
    } catch {
      await refresh("Lians could not complete that action. Your local memory was not changed.");
    } finally {
      busy = false;
      render(currentStatus, ui.feedback.textContent);
    }
  }

  ui.trigger.addEventListener("click", () => {
    window.history.replaceState(null, "", "#cloud-sync");
    setOpen(true);
  });
  ui.close.addEventListener("click", () => setOpen(false));
  ui.backdrop.addEventListener("click", (event) => {
    if (event.target === ui.backdrop) setOpen(false);
  });
  ui.primary.addEventListener("click", () => act(ui.primary.dataset.action));
  ui.secondary.addEventListener("click", () => act("signout"));
  ui.danger.addEventListener("click", () => {
    if (ui.danger.dataset.armed === "true") {
      clearTimeout(deleteTimer);
      act("delete");
      return;
    }
    ui.danger.dataset.armed = "true";
    ui.danger.textContent = "Click again to delete cloud copy";
    ui.feedback.textContent = "This deletes encrypted cloud data and turns sync off. Local memory stays here.";
    deleteTimer = setTimeout(() => {
      ui.danger.dataset.armed = "false";
      ui.danger.textContent = "Delete cloud copy";
    }, 8_000);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !ui.backdrop.hidden) setOpen(false);
  });
  window.addEventListener("focus", () => refresh());
  window.addEventListener("hashchange", () => {
    if (window.location.hash === "#cloud-sync") setOpen(true);
  });
  window.setInterval(() => {
    if (document.visibilityState === "visible") refresh();
  }, POLL_MS);
  if (window.location.hash === "#cloud-sync") setOpen(true);
  refresh();
})();
