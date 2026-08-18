(() => {
  "use strict";

  const card = document.querySelector("#continuity-card");
  const ready = document.querySelector("#continuity-ready");
  const empty = document.querySelector("#continuity-empty");
  const choices = document.querySelector("#continuity-choices");
  const status = document.querySelector("#continuity-status");
  const copyButton = document.querySelector("#continuity-copy");
  let currentBrief = "";

  function escapeHtml(value) {
    const node = document.createElement("span");
    node.textContent = String(value || "");
    return node.innerHTML;
  }

  function showReady(result) {
    const contract = result.contract || {};
    const state = result.state || {};
    const assessment = result.assessment || {};
    const criteria = assessment.criteria || [];
    const satisfied = criteria.filter((item) => item.satisfied).length;
    document.querySelector("#continuity-goal").textContent = contract.goal || contract.title || "Active work";
    document.querySelector("#continuity-checkpoint").textContent = state.summary || "Goal saved. No checkpoint yet.";
    document.querySelector("#continuity-next").textContent = state.current_action
      || (assessment.blockers || [])[0]
      || "Continue from the latest verified work";
    document.querySelector("#continuity-progress").textContent = `${satisfied} of ${criteria.length} outcomes verified`;
    status.textContent = assessment.status === "blocked" ? "Blocked" : "Ready to continue";
    status.dataset.state = assessment.status || "active";
    currentBrief = result.context || "";
    ready.hidden = false;
    empty.hidden = true;
    choices.hidden = true;
  }

  function showChoices(result) {
    ready.hidden = true;
    empty.hidden = true;
    choices.hidden = false;
    status.textContent = "Choose your work";
    status.dataset.state = "ambiguous";
    choices.innerHTML = `
      <p>${escapeHtml(result.message)}</p>
      <div class="continuity-choice-list">
        ${(result.tasks || []).map((task) => `
          <button type="button" data-task-id="${escapeHtml(task.task_id)}">
            <span>${escapeHtml(task.status)}</span>
            <strong>${escapeHtml(task.title)}</strong>
            <small>${escapeHtml(task.next_action || task.checkpoint || task.goal)}</small>
          </button>`).join("")}
      </div>`;
    choices.querySelectorAll("button[data-task-id]").forEach((button) => {
      button.addEventListener("click", () => load(button.dataset.taskId));
    });
  }

  function showEmpty(result) {
    ready.hidden = true;
    choices.hidden = true;
    empty.hidden = false;
    status.textContent = "Ready when you are";
    status.dataset.state = "empty";
    const message = empty.querySelector("p");
    if (result?.message) message.textContent = result.message;
  }

  async function load(taskId = null) {
    if (!window.pywebview?.api) return;
    card.setAttribute("aria-busy", "true");
    status.textContent = "Finding your place";
    status.dataset.state = "loading";
    try {
      const result = await window.pywebview.api.continuity(taskId);
      if (result.status === "ready") showReady(result);
      else if (result.status === "ambiguous") showChoices(result);
      else showEmpty(result);
    } catch {
      showEmpty({ message: "Continuity is unavailable. Your saved memory remains on this device." });
    } finally {
      card.removeAttribute("aria-busy");
    }
  }

  async function copyBrief() {
    if (!currentBrief) return;
    try {
      await navigator.clipboard.writeText(currentBrief);
    } catch {
      const field = document.createElement("textarea");
      field.value = currentBrief;
      field.setAttribute("readonly", "");
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.appendChild(field);
      field.select();
      document.execCommand("copy");
      field.remove();
    }
    copyButton.textContent = "Brief copied";
    window.setTimeout(() => { copyButton.textContent = "Copy brief"; }, 1400);
  }

  copyButton.addEventListener("click", copyBrief);
  document.querySelector("#refresh").addEventListener("click", () => load());
  window.addEventListener("pywebviewready", () => load());
})();
