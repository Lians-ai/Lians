const $ = (selector) => document.querySelector(selector);
const providerOptions = [...document.querySelectorAll(".provider-option")];
const kindOptions = [...document.querySelectorAll(".kind-option")];
const providerNames = { claude: "Claude", codex: "Codex", cursor: "Cursor" };
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

let selectedProvider = "claude";
let selectedKind = "research";
let providerReady = false;
let briefReady = false;
let workInput = "";
let busy = false;
let statusRequest = 0;

async function request(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Lians could not continue.");
  return payload;
}

function choose(options, selected) {
  options.forEach((item) => {
    const active = item === selected;
    item.classList.toggle("selected", active);
    item.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function setStatus(message, state = "") {
  $("#status-dot").className = `status-dot ${state}`.trim();
  $("#status-text").textContent = message;
}

function refreshActions() {
  $("#ask-button").textContent = `Run with ${providerNames[selectedProvider]}`;
  $("#ask-button").disabled = busy || !providerReady || !briefReady || !$("#task-input").value.trim();
  $("#compile-button").disabled = busy || !workInput;
  $("#benchmark-button").disabled = busy || !providerReady;
  $("#check-button").disabled = busy;
  providerOptions.forEach((item) => { item.disabled = busy; });
  kindOptions.forEach((item) => { item.disabled = busy; });
  if (!providerReady) {
    $("#task-helper").textContent = `Sign in to ${providerNames[selectedProvider]} and check the connection.`;
  } else if (!briefReady) {
    $("#task-helper").textContent = "Add work and create a smaller context brief first.";
  } else {
    $("#task-helper").textContent = `Ready. ${providerNames[selectedProvider]} receives only this task and the Lians brief.`;
  }
}

async function checkProvider() {
  const provider = selectedProvider;
  const requestId = ++statusRequest;
  providerReady = false;
  setStatus(`Checking ${providerNames[provider]}`);
  refreshActions();
  try {
    const result = await request(`api/status?provider=${encodeURIComponent(provider)}`);
    if (requestId !== statusRequest || provider !== selectedProvider) return;
    providerReady = Boolean(result.ready);
    setStatus(result.message, result.ready ? "ready" : "error");
  } catch (error) {
    if (requestId === statusRequest && provider === selectedProvider) setStatus(error.message, "error");
  }
  refreshActions();
}

function sampleResearch() {
  const statements = [
    ["memory", "Claude forgot the positioning decision after a long research session."],
    ["context", "Cursor needed the same project rules pasted into another task."],
    ["usage", "The team reached its usage limit while replaying old notes."],
    ["trust", "The researcher wants a receipt showing which evidence informed the answer."],
    ["privacy", "Local processing is required before anything is sent to an AI."],
    ["workflow", "The next report should preserve decisions without replaying every post."],
  ];
  return Array.from({ length: 240 }, (_, index) => {
    const [topic, text] = statements[index % statements.length];
    return {
      id: `sample-${index + 1}`,
      text,
      topic,
      sentiment: index % 3 === 0 ? "negative" : "neutral",
      tool: ["Claude", "Codex", "Cursor"][index % 3],
      engagement: 240 - index,
    };
  });
}

function sampleBrowser() {
  const states = ["new", "candidate", "reviewed", "published"];
  return Array.from({ length: 300 }, (_, index) => ({
    surface_id: `source-${(index % 30) + 1}`,
    state: states[Math.floor(index / 30) % states.length],
    priority: (index % 10) + 1,
    note: "Continue only when this is the latest eligible state.",
  }));
}

providerOptions.forEach((option) => {
  option.addEventListener("click", () => {
    if (busy) return;
    selectedProvider = option.dataset.provider;
    choose(providerOptions, option);
    $("#answer-card").hidden = true;
    checkProvider();
  });
});

kindOptions.forEach((option) => {
  option.addEventListener("click", () => {
    if (busy) return;
    selectedKind = option.dataset.kind;
    choose(kindOptions, option);
    workInput = "";
    briefReady = false;
    $("#work-file").value = "";
    $("#file-status").textContent = "Choose an export or load the built-in sample.";
    $("#brief-result").hidden = true;
    refreshActions();
  });
});

$("#check-button").addEventListener("click", checkProvider);
$("#file-button").addEventListener("click", () => $("#work-file").click());

$("#work-file").addEventListener("change", async (event) => {
  const [file] = event.target.files;
  if (!file) return;
  if (file.size > 64 * 1024 * 1024) {
    $("#file-status").textContent = "That file is larger than the 64 MiB local limit.";
    return;
  }
  workInput = await file.text();
  briefReady = false;
  $("#brief-result").hidden = true;
  $("#file-status").textContent = `${file.name} is ready. It stays on this device.`;
  refreshActions();
});

$("#sample-button").addEventListener("click", () => {
  workInput = JSON.stringify(selectedKind === "research" ? sampleResearch() : sampleBrowser());
  briefReady = false;
  $("#brief-result").hidden = true;
  $("#file-status").textContent = `Built-in ${selectedKind} sample loaded.`;
  refreshActions();
});

$("#compile-button").addEventListener("click", async () => {
  busy = true;
  briefReady = false;
  $("#compile-helper").textContent = "Reading and reducing the work locally.";
  refreshActions();
  try {
    const result = await request("api/compile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: selectedKind, input: workInput, evidence_limit: 12 }),
    });
    briefReady = true;
    $("#brief-reduction").textContent = `${number.format(result.estimated_reduction_percent)}%`;
    $("#record-count").textContent = number.format(result.raw_records);
    $("#raw-tokens").textContent = `${number.format(result.raw_token_estimate)} tokens`;
    $("#brief-tokens").textContent = `${number.format(result.brief_token_estimate)} tokens`;
    $("#brief-result").hidden = false;
    workInput = "";
    $("#work-file").value = "";
    $("#compile-helper").textContent = "The smaller context is ready. The original work is not retained.";
    $("#brief-result").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    $("#compile-helper").textContent = error.message;
  } finally {
    busy = false;
    refreshActions();
  }
});

$("#task-input").addEventListener("input", refreshActions);

$("#ask-button").addEventListener("click", async () => {
  busy = true;
  $("#answer-card").hidden = true;
  $("#task-helper").textContent = `${providerNames[selectedProvider]} is working from the smaller context.`;
  refreshActions();
  try {
    const result = await request("api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: selectedProvider, task: $("#task-input").value }),
    });
    $("#answer").textContent = result.answer;
    $("#provider-input").textContent = `${number.format(result.usage.provider_reported_total_input_tokens)} tokens`;
    $("#duration").textContent = `${number.format(result.duration_seconds)} seconds`;
    $("#answer-provider").textContent = result.provider_name;
    $("#answer-card").hidden = false;
    $("#answer-card").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    $("#task-helper").textContent = error.message;
  } finally {
    busy = false;
    refreshActions();
  }
});

$("#copy-button").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("#answer").textContent);
  $("#copy-button").textContent = "Copied";
  setTimeout(() => { $("#copy-button").textContent = "Copy answer"; }, 1400);
});

$("#benchmark-button").addEventListener("click", async () => {
  busy = true;
  $("#proof-result").hidden = false;
  $("#proof-result").textContent = `Running four matched ${providerNames[selectedProvider]} calls. Keep Lians open.`;
  refreshActions();
  try {
    const result = await request("api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: selectedProvider }),
    });
    const quality = `${result.exact_answers}/${result.total_answers} exact answers`;
    $("#proof-result").textContent = `${number.format(result.reduction_percent)}% fewer ${result.measurement_label.toLowerCase()} with ${quality}.`;
  } catch (error) {
    $("#proof-result").textContent = error.message;
  } finally {
    busy = false;
    refreshActions();
  }
});

$("#close-button").addEventListener("click", async () => {
  try {
    await request("api/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } finally {
    document.body.innerHTML = '<main class="closed"><img src="wordmark.png" alt="Lians"><h1>Lians is closed.</h1><p>You can close this tab.</p></main>';
  }
});

checkProvider();
refreshActions();
