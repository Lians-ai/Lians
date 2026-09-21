const token = document.querySelector('meta[name="lians-token"]').content;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let activeJob = null;
let pollTimer = null;
let runtimeRoutable = false;
let busyState = false;

const modeDetails = {
  protect: { label: "Protect", budget: 1 },
  balanced: { label: "Balanced", budget: 2 },
  maximum: { label: "Maximum", budget: 4 },
};

async function api(path, options = {}) {
  const headers = { "X-Lians-Token": token, ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`);
  return value;
}

function message(text = "", kind = "error") {
  const target = $("#form-message");
  target.textContent = text;
  target.style.color = kind === "ok" ? "var(--success)" : "var(--danger)";
}

function selectedMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

function payload(includeVerification = false) {
  const validUntil = $("#valid-until").value;
  const value = {
    repository: $("#repository").value.trim(),
    task: $("#task").value.trim(),
    constraints: $("#constraints").value,
    definition_of_done: $("#definition").value.trim(),
    valid_until: validUntil ? new Date(validUntil).toISOString() : null,
    mode: selectedMode(),
  };
  if (includeVerification) value.verification_command = $("#verification").value.trim();
  return value;
}

function renderPolicy(mode) {
  const detail = modeDetails[mode] || modeDetails.protect;
  $("#rail-mode").textContent = detail.label;
  $("#rail-budget").textContent = `${detail.budget} premium max`;
  $("#inspector-mode").textContent = detail.label;
  $("#inspector-budget").textContent = `${detail.budget} max`;
}

function renderWorkspace(repository) {
  const parts = repository.split(/[\\/]/).filter(Boolean);
  $("#workspace-name").textContent = parts.at(-1) || "Workspace";
}

function renderAgentStack(agents) {
  const list = $("#agent-stack");
  list.replaceChildren();
  let detected = 0;
  let routable = 0;
  agents.forEach((agent) => {
    detected += agent.installed ? 1 : 0;
    routable += agent.routable ? 1 : 0;
    const row = document.createElement("div");
    row.className = `agent-row ${agent.status}`;
    const name = document.createElement("span");
    name.append(document.createElement("i"), document.createTextNode(agent.name));
    const state = document.createElement("em");
    state.textContent = agent.status === "ready" ? "Routable" : agent.status === "detected" ? "Detected" : "Not found";
    row.append(name, state);
    list.append(row);
  });
  runtimeRoutable = routable > 0;
  $("#stack-summary").textContent = `${detected} detected · ${routable} routable`;
  $("#engine-state").textContent = runtimeRoutable ? "Available" : "Setup needed";
  $("#engine-status").classList.toggle("degraded", !runtimeRoutable);
  syncRunAvailability();
}

function renderUsage(usage) {
  const observed = usage || {};
  $("#usage-verified").textContent = observed.verified_tasks || 0;
  $("#usage-premium").textContent = observed.premium_calls || 0;
  $("#usage-summary").textContent = observed.observed_runs
    ? `${observed.observed_runs} observed · ${observed.premium_calls || 0} premium calls`
    : "No runs yet";
}

function renderBridge(bridge) {
  const selected = bridge?.selected;
  const label = selected === "app-server" ? "Persistent · beta" : selected === "exec" ? "One-shot fallback" : "Unavailable";
  const target = $("#codex-bridge");
  target.textContent = label;
  target.title = bridge?.claim_boundary || "Codex execution bridge";
}

function renderProof(suggestion, filled = true) {
  if (!suggestion) {
    $("#proof-brief-state").textContent = "not found";
    $("#proof-summary").textContent = "Choose a proof command";
    $("#proof-source").textContent = "No standard test command was found. Add one before starting.";
    return;
  }
  if (filled && !$("#verification").value.trim()) $("#verification").value = suggestion.command;
  $("#proof-brief-state").textContent = "ready";
  $("#proof-summary").textContent = suggestion.label;
  $("#proof-source").textContent = `${suggestion.reason} Review before starting.`;
}

async function inspectWorkspace(announce = false) {
  const repository = $("#repository").value.trim();
  if (!repository) return;
  renderWorkspace(repository);
  if (announce) message("Inspecting the workspace locally…", "ok");
  try {
    const inspection = await api("/api/inspect", {
      method: "POST",
      body: JSON.stringify({ repository }),
    });
    renderAgentStack(inspection.agent_stack || []);
    renderUsage(inspection.usage || {});
    renderProof((inspection.proof_suggestions || [])[0]);
    if (announce) message("Workspace inspected. Nothing was executed.", "ok");
  } catch (error) {
    if (announce) message(error.message);
  }
}

function resetComposer() {
  ["#task", "#constraints", "#definition", "#valid-until"].forEach((selector) => {
    $(selector).value = "";
  });
  $("#task-count").textContent = "0 / 8,000";
  $("#goal-state").textContent = "draft";
  $("#ledger-summary").textContent = "Ready";
  $("#route-panel").classList.add("hidden");
  $("#run-panel").classList.add("hidden");
  $("#activity").classList.add("hidden");
  $("#mission-controls").open = false;
  $("#mission").classList.remove("has-context");
  $("#task").focus();
  message();
}

function showContext() {
  $("#mission").classList.add("has-context");
  $("#activity").classList.remove("hidden");
}

function toggleSidebar() {
  const collapsed = document.body.classList.toggle("sidebar-collapsed");
  const button = $("#toggle-sidebar");
  button.setAttribute("aria-pressed", String(collapsed));
  button.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
  button.title = `${collapsed ? "Expand" : "Collapse"} sidebar (Ctrl+Alt+B)`;
}

function toggleControls() {
  const controls = $("#mission-controls");
  controls.open = !controls.open;
}

function stageModel(stage) {
  if (!stage.model) return "Local proof";
  return stage.model.replace("gpt-", "GPT ").replaceAll("-", " · ");
}

function renderMission(mission) {
  if (!mission) return;
  const shortHash = mission.mission_sha256.slice(0, 10);
  $("#mission-revision").textContent = `R${mission.revision}`;
  $("#mission-status").textContent = mission.status === "expired" ? "Expired" : "Active";
  $("#mission-hash").textContent = `${shortHash} · ${new Date(mission.as_of).toLocaleString()}`;
  $("#goal-state").textContent = `R${mission.revision} locked`;
  $("#ledger-summary").textContent = `Revision ${mission.revision} · ${mission.status}`;
}

function renderRoute(route, mission = null) {
  showContext();
  renderPolicy(route.mode);
  renderMission(mission);
  $("#route-panel").classList.remove("hidden");
  $("#route-title").textContent = `${modeDetails[route.mode].label} route`;
  $("#risk-badge").textContent = `${route.risk} risk`;
  $("#premium-budget").textContent = `Premium budget: ${route.max_premium_calls}`;
  const stages = $("#route-stages");
  stages.replaceChildren();
  route.stages.forEach((stage, index) => {
    const item = document.createElement("div");
    item.className = `route-stage${stage.premium ? " premium" : ""}`;
    const number = document.createElement("span");
    number.className = "stage-number";
    number.textContent = `0${index + 1}`;
    const title = document.createElement("strong");
    title.textContent = stage.id;
    const model = document.createElement("span");
    model.className = "model";
    model.textContent = stageModel(stage);
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = stage.run_when.replaceAll("_", " ");
    item.append(number, title, model, when);
    stages.append(item);
  });
  $("#route-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function setBusy(busy) {
  busyState = busy;
  ["#preview", "#run", "#browse", "#detect-proof", "#import-agent", "#export-agent", "#export-beta"].forEach((selector) => {
    $(selector).disabled = busy;
  });
  syncRunAvailability();
}

function syncRunAvailability() {
  const button = $("#run");
  button.disabled = busyState || !runtimeRoutable;
  button.title = runtimeRoutable
    ? "Start the governed route"
    : "Install the Codex CLI to execute. Preview and portable agents still work.";
}

function downloadJson(filename, value, mediaType = "application/json") {
  const blob = new Blob([`${JSON.stringify(value, null, 2)}\n`], {
    type: mediaType,
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function exportAgent() {
  message();
  setBusy(true);
  try {
    const result = await api("/api/agent/export", {
      method: "POST",
      body: JSON.stringify(payload(true)),
    });
    downloadJson(result.filename, result.bundle, "application/vnd.lians.agent+json");
    $(".system-drawer").open = false;
    message(`Exported ${result.filename}. Nothing was executed.`, "ok");
  } catch (error) {
    message(error.message);
    $("#mission-controls").open = true;
  } finally {
    setBusy(false);
  }
}

async function exportBetaReport() {
  message();
  setBusy(true);
  try {
    const result = await api("/api/beta/report");
    downloadJson(result.filename, result.report);
    const count = result.report.aggregate?.eligible_runs || 0;
    $(".system-drawer").open = false;
    message(`Exported a privacy-bounded beta report with ${count} eligible run${count === 1 ? "" : "s"}.`, "ok");
  } catch (error) {
    message(error.message);
  } finally {
    setBusy(false);
  }
}

function localDateTimeValue(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

async function importAgentFile(file) {
  if (!file) return;
  message();
  setBusy(true);
  try {
    if (file.size > 60 * 1024) throw new Error("Agent bundle must be 60 KB or smaller.");
    const result = await api("/api/agent/import", {
      method: "POST",
      body: JSON.stringify({ bundle_text: await file.text() }),
    });
    $("#task").value = result.task;
    $("#task-count").textContent = `${result.task.length.toLocaleString()} / 8,000`;
    $("#repository").value = result.repository;
    $("#constraints").value = result.constraints;
    $("#definition").value = result.definition_of_done;
    $("#valid-until").value = localDateTimeValue(result.valid_until);
    $("#verification").value = result.verification_command;
    const mode = document.querySelector(`input[name="mode"][value="${result.mode}"]`);
    mode.checked = true;
    $$(".mode-switch label").forEach((label) => label.classList.toggle("selected", label.contains(mode)));
    renderPolicy(result.mode);
    renderRoute(result.route);
    $("#goal-state").textContent = "imported";
    $("#ledger-summary").textContent = "Imported · not locked";
    $("#mission-controls").open = true;
    $(".system-drawer").open = false;
    const workspaceNote = `Choose the workspace for ${result.workspace_name}.`;
    const routeNote = result.route_stale ? " The route was refreshed for this Lians version." : "";
    message(`Agent verified and imported. ${workspaceNote}${routeNote} Nothing was executed.`, "ok");
  } catch (error) {
    message(error.message);
  } finally {
    $("#agent-file").value = "";
    setBusy(false);
  }
}

async function preview() {
  message();
  setBusy(true);
  try {
    const plan = await api("/api/plan", { method: "POST", body: JSON.stringify(payload()) });
    renderRoute(plan.route, plan.mission);
    message("Route and mission revision locked locally. No model was invoked.", "ok");
  } catch (error) {
    message(error.message);
  } finally {
    setBusy(false);
  }
}

function renderEvents(events) {
  const list = $("#event-list");
  list.replaceChildren();
  events.forEach((event) => {
    const item = document.createElement("div");
    item.className = "event";
    const time = document.createElement("time");
    time.textContent = new Date(event.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    item.append(time, document.createTextNode(event.message));
    list.append(item);
  });
}

function metric(label, value) {
  const item = document.createElement("div");
  item.className = "metric";
  const name = document.createElement("span");
  name.textContent = label;
  const amount = document.createElement("strong");
  amount.textContent = value;
  item.append(name, amount);
  return item;
}

function renderJob(job) {
  $("#run-panel").classList.remove("hidden");
  renderRoute(job.route, job.mission);
  renderEvents(job.events || []);
  const status = job.status.toLowerCase();
  const terminal = ["pass", "block", "error", "interrupted"].includes(status);
  const icon = $("#status-icon");
  icon.className = `status-icon ${terminal ? status : "running"}`;
  $("#status-chip").textContent = terminal ? job.status : "Running";
  $("#status-kicker").textContent = status === "pass" ? "VERIFIED RECEIPT" : status === "running" ? "RUNNING LOCALLY" : status === "queued" ? "QUEUED LOCALLY" : "RUN STOPPED";
  $("#status-title").textContent = status === "pass" ? "Mission verified" : status === "block" ? "Completion blocked" : status === "interrupted" ? "Run was interrupted" : status === "error" ? "Run needs attention" : "Lians is working";
  $("#status-copy").textContent = job.error || (status === "pass" ? "The configured proof passed." : status === "block" ? "The proof still failed after the allowed route." : "The route and limit were locked before the first call.");
  const metrics = $("#receipt-metrics");
  const boundary = $("#claim-boundary");
  if (job.receipt) {
    metrics.replaceChildren(
      metric("Model calls", job.receipt.model_calls),
      metric("Premium calls", job.receipt.premium_calls),
      metric("Premium ceiling", job.receipt.max_premium_calls),
      metric("Duration", `${job.duration_seconds}s`),
    );
    metrics.classList.remove("hidden");
    boundary.textContent = job.receipt.claim_boundary;
    boundary.classList.remove("hidden");
  } else {
    metrics.classList.add("hidden");
    boundary.classList.add("hidden");
  }
  if (terminal) {
    setBusy(false);
    activeJob = null;
    clearTimeout(pollTimer);
    loadRuns();
    inspectWorkspace(false);
  }
}

async function pollJob() {
  if (!activeJob) return;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(activeJob)}`);
    renderJob(job);
    if (activeJob) pollTimer = setTimeout(pollJob, 1200);
  } catch (error) {
    message(error.message);
    setBusy(false);
  }
}

async function run() {
  message();
  setBusy(true);
  try {
    const job = await api("/api/run", { method: "POST", body: JSON.stringify(payload(true)) });
    activeJob = job.id;
    renderJob(job);
    $("#run-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
    pollTimer = setTimeout(pollJob, 450);
  } catch (error) {
    message(error.message);
    setBusy(false);
    $("#mission-controls").open = true;
  }
}

async function browse() {
  message("Opening the folder picker…", "ok");
  try {
    const result = await api("/api/pick-directory", { method: "POST", body: "{}" });
    if (result.repository) {
      $("#repository").value = result.repository;
      await inspectWorkspace(false);
    }
    message(result.repository ? "Workspace selected." : "No folder selected.", "ok");
  } catch (error) {
    message(error.message);
  }
}

async function openRun(id) {
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
    renderJob(job);
    $("#run-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    message(error.message);
  }
}

async function loadRuns() {
  try {
    const response = await api("/api/runs");
    const list = $("#run-history");
    list.replaceChildren();
    if (!response.runs.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No local runs yet.";
      list.append(empty);
      return;
    }
    response.runs.forEach((runItem) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "history-item";
      const title = document.createElement("strong");
      title.textContent = runItem.task;
      const meta = document.createElement("span");
      meta.textContent = `${runItem.status} · ${runItem.mode || "route"}`;
      button.append(title, meta);
      button.addEventListener("click", () => openRun(runItem.id));
      list.append(button);
    });
  } catch (_) {
    // The mission composer remains usable when history is unavailable.
  }
}

async function init() {
  try {
    const config = await api("/api/config");
    $("#repository").value = config.default_repository;
    renderWorkspace(config.default_repository);
    renderAgentStack(config.agent_stack || []);
    renderBridge(config.codex_bridge || {});
    renderUsage(config.usage || {});
    renderPolicy(selectedMode());
    await inspectWorkspace(false);
    if (!runtimeRoutable) {
      message("Codex is not available on this device. Preview and portable agents still work; install and sign in to Codex before Start.", "ok");
    }
  } catch (error) {
    message(error.message);
  }
  loadRuns();
}

$("#preview").addEventListener("click", preview);
$("#run").addEventListener("click", run);
$("#browse").addEventListener("click", browse);
$("#detect-proof").addEventListener("click", () => inspectWorkspace(true));
$("#export-agent").addEventListener("click", exportAgent);
$("#export-beta").addEventListener("click", exportBetaReport);
$("#import-agent").addEventListener("click", () => $("#agent-file").click());
$("#agent-file").addEventListener("change", (event) => importAgentFile(event.target.files[0]));
$("#new-run").addEventListener("click", resetComposer);
$("#toggle-sidebar").addEventListener("click", toggleSidebar);
$("#toggle-controls").addEventListener("click", toggleControls);
$("#mission-controls").addEventListener("toggle", (event) => {
  const open = event.target.open;
  $("#toggle-controls").setAttribute("aria-expanded", String(open));
  $("#toggle-controls").classList.toggle("active", open);
});
$("#task").addEventListener("input", (event) => {
  $("#task-count").textContent = `${event.target.value.length.toLocaleString()} / 8,000`;
  $("#goal-state").textContent = event.target.value.trim() ? "captured" : "draft";
});
$("#verification").addEventListener("input", (event) => {
  const ready = Boolean(event.target.value.trim());
  $("#proof-brief-state").textContent = ready ? "ready" : "missing";
  $("#proof-summary").textContent = ready ? "Command selected" : "Not selected";
});
$("#repository").addEventListener("change", (event) => renderWorkspace(event.target.value));
document.addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.altKey && event.key.toLowerCase() === "b") {
    event.preventDefault();
    toggleSidebar();
    return;
  }
  if (event.ctrlKey && event.key.toLowerCase() === "n") {
    event.preventDefault();
    resetComposer();
  }
});
$$('input[name="mode"]').forEach((input) => input.addEventListener("change", () => {
  $$(".mode-switch label").forEach((label) => label.classList.toggle("selected", label.contains(input)));
  renderPolicy(input.value);
  $("#route-panel").classList.add("hidden");
}));

init();
