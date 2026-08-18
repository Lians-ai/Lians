(() => {
  "use strict";

  const openButton = document.querySelector("#control-center");
  const panel = document.querySelector("#control-panel");
  const closeButton = document.querySelector("#control-close");
  const saveButton = document.querySelector("#control-save");
  const status = document.querySelector("#control-status");
  const budget = document.querySelector("#context-budget");
  const autoTask = document.querySelector("#auto-task-context");
  const inferred = document.querySelector("#show-inferred-links");
  const approvals = document.querySelector("#approval-actions");
  const enforcement = document.querySelector("#control-enforcement");
  const boundary = document.querySelector("#control-boundary-text");
  const modeButtons = [...document.querySelectorAll(".mode-card")];
  let selectedMode = "guide";

  function selectMode(mode) {
    selectedMode = ["observe", "guide", "protect"].includes(mode) ? mode : "guide";
    modeButtons.forEach((button) => {
      button.setAttribute("aria-checked", String(button.dataset.mode === selectedMode));
    });
    approvals.disabled = selectedMode !== "protect";
    enforcement.textContent = {
      observe: "Observe mode stores no prompt content.",
      guide: "Guide mode supplies bounded user-owned context.",
      protect: "Protect mode requests approval for selected actions.",
    }[selectedMode];
  }

  function render(result) {
    const policy = result?.policy || {};
    selectMode(policy.mode || "guide");
    openButton.textContent = `${selectedMode[0].toUpperCase()}${selectedMode.slice(1)} mode`;
    budget.value = String(policy.context_budget_tokens || 512);
    autoTask.checked = policy.auto_task_context !== false;
    inferred.checked = policy.show_inferred_links === true;
    const selectedActions = new Set(policy.approval_actions || []);
    approvals.querySelectorAll("input[type='checkbox']").forEach((input) => {
      input.checked = selectedActions.has(input.value);
    });
    boundary.textContent = result?.enforcement?.boundary
      || "Lians uses supported native integrations and reports where a host cannot enforce an action.";
  }

  async function open() {
    if (!window.pywebview?.api) return;
    openButton.setAttribute("aria-busy", "true");
    status.textContent = "Loading local policy...";
    try {
      render(await window.pywebview.api.control_status());
      document.querySelector("#map-panel").hidden = true;
      panel.hidden = false;
      status.textContent = "";
    } catch {
      status.textContent = "Control policy is unavailable.";
      panel.hidden = false;
    } finally {
      openButton.removeAttribute("aria-busy");
    }
  }

  function close() {
    panel.hidden = true;
    status.textContent = "";
  }

  async function save() {
    if (!window.pywebview?.api || saveButton.getAttribute("aria-busy") === "true") return;
    const approvalActions = [...approvals.querySelectorAll("input:checked")]
      .map((input) => input.value);
    saveButton.setAttribute("aria-busy", "true");
    status.textContent = "Saving encrypted policy...";
    try {
      const result = await window.pywebview.api.update_control_policy({
        mode: selectedMode,
        context_budget_tokens: Number(budget.value),
        auto_task_context: autoTask.checked,
        show_inferred_links: inferred.checked,
        approval_actions: approvalActions,
      });
      render(result);
      status.textContent = "Saved locally.";
      window.dispatchEvent(new CustomEvent("lians-control-changed", { detail: result }));
    } catch {
      status.textContent = "Policy could not be saved.";
    } finally {
      saveButton.removeAttribute("aria-busy");
    }
  }

  modeButtons.forEach((button) => {
    button.addEventListener("click", () => selectMode(button.dataset.mode));
  });
  openButton.addEventListener("click", open);
  closeButton.addEventListener("click", close);
  saveButton.addEventListener("click", save);
  window.addEventListener("pywebviewready", () => {
    window.pywebview.api.control_status().then(render).catch(() => {});
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) close();
  });
  document.documentElement.dataset.controlCenterReady = "true";
})();
