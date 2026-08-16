const statusDot = document.querySelector("#status-dot");
const statusText = document.querySelector("#status-text");
const runButton = document.querySelector("#run-button");
const runHelper = document.querySelector("#run-helper");
const progressCard = document.querySelector("#progress-card");
const progressProvider = document.querySelector("#progress-provider");
const resultsCard = document.querySelector("#results-card");
const reduction = document.querySelector("#reduction");
const resultCopy = document.querySelector("#result-copy");
const fullTokens = document.querySelector("#full-tokens");
const liansTokens = document.querySelector("#lians-tokens");
const answers = document.querySelector("#answers");
const gateResult = document.querySelector("#gate-result");
const closeButton = document.querySelector("#close-button");
const providerOptions = [...document.querySelectorAll(".provider-option")];

const providerNames = { claude: "Claude", codex: "Codex", cursor: "Cursor" };
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
let selectedProvider = "claude";
let statusRequest = 0;

async function request(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "The local test could not continue.");
  }
  return payload;
}

function showError(message) {
  statusDot.className = "status-dot error";
  statusText.textContent = message;
  runButton.disabled = true;
  runHelper.textContent = `Fix the ${providerNames[selectedProvider]} sign in, then check again.`;
  progressCard.hidden = true;
}

async function checkProvider() {
  const provider = selectedProvider;
  const requestId = ++statusRequest;
  statusDot.className = "status-dot";
  statusText.textContent = `Checking ${providerNames[provider]}`;
  runButton.disabled = true;
  runHelper.textContent = "Checking the local CLI sign in. No account details leave this device.";
  try {
    const result = await request(`api/status?provider=${encodeURIComponent(provider)}`);
    if (requestId !== statusRequest || provider !== selectedProvider) return;
    if (!result.ready) {
      showError(result.message);
      return;
    }
    statusDot.className = "status-dot ready";
    statusText.textContent = `${result.provider_name} is ready`;
    runHelper.textContent = "The test runs four calls and may use a small amount of subscription usage.";
    runButton.disabled = false;
  } catch (error) {
    if (requestId === statusRequest && provider === selectedProvider) showError(error.message);
  }
}

providerOptions.forEach((option) => {
  option.addEventListener("click", () => {
    if (runButton.dataset.running === "true") return;
    selectedProvider = option.dataset.provider;
    providerOptions.forEach((item) => {
      const selected = item === option;
      item.classList.toggle("selected", selected);
      item.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    resultsCard.hidden = true;
    checkProvider();
  });
});

runButton.addEventListener("click", async () => {
  const provider = selectedProvider;
  runButton.disabled = true;
  runButton.dataset.running = "true";
  providerOptions.forEach((option) => { option.disabled = true; });
  progressProvider.textContent = providerNames[provider];
  progressCard.hidden = false;
  resultsCard.hidden = true;
  statusText.textContent = `${providerNames[provider]} is running the matched research tasks`;
  try {
    const result = await request("api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    reduction.textContent = number.format(result.reduction_percent);
    resultCopy.textContent = `fewer ${result.measurement_label.toLowerCase()}`;
    fullTokens.textContent = number.format(result.full_input_tokens);
    liansTokens.textContent = number.format(result.lians_input_tokens);
    answers.textContent = `${result.exact_answers}/${result.total_answers}`;
    gateResult.textContent = result.gate_met
      ? "The 50% test target passed"
      : "The 50% test target was not reached";
    statusDot.className = result.exact_answers === result.total_answers
      ? "status-dot ready"
      : "status-dot error";
    statusText.textContent = `${result.provider_name} test complete`;
    progressCard.hidden = true;
    resultsCard.hidden = false;
    resultsCard.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showError(error.message);
  } finally {
    runButton.dataset.running = "false";
    providerOptions.forEach((option) => { option.disabled = false; });
  }
});

closeButton.addEventListener("click", async () => {
  closeButton.disabled = true;
  try {
    await request("api/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    statusText.textContent = "The local test is closed. You can close this tab.";
  } catch (error) {
    statusText.textContent = "You can close this tab.";
  }
});

checkProvider();
