const statusDot = document.querySelector("#status-dot");
const statusText = document.querySelector("#status-text");
const runButton = document.querySelector("#run-button");
const runHelper = document.querySelector("#run-helper");
const progressCard = document.querySelector("#progress-card");
const resultsCard = document.querySelector("#results-card");
const reduction = document.querySelector("#reduction");
const fullTokens = document.querySelector("#full-tokens");
const liansTokens = document.querySelector("#lians-tokens");
const answers = document.querySelector("#answers");
const gateResult = document.querySelector("#gate-result");
const closeButton = document.querySelector("#close-button");

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

async function request(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
  });
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
  runHelper.textContent = "Fix the Claude sign in, then reopen this app.";
  progressCard.hidden = true;
}

async function checkClaude() {
  try {
    const result = await request("api/status");
    if (!result.ready) {
      showError(result.message);
      return;
    }
    statusDot.className = "status-dot ready";
    statusText.textContent = "Claude Pro is ready";
    runHelper.textContent = "The test runs four calls and usually finishes in less than one minute.";
    runButton.disabled = false;
  } catch (error) {
    showError(error.message);
  }
}

runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  progressCard.hidden = false;
  resultsCard.hidden = true;
  statusText.textContent = "Claude is running the matched research tasks";
  try {
    const result = await request("api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    reduction.textContent = number.format(result.reduction_percent);
    fullTokens.textContent = number.format(result.full_input_tokens);
    liansTokens.textContent = number.format(result.lians_input_tokens);
    answers.textContent = `${result.exact_answers}/${result.total_answers}`;
    gateResult.textContent = result.gate_met
      ? "The 50% test target passed"
      : "The 50% test target was not reached";
    statusDot.className = result.exact_answers === result.total_answers
      ? "status-dot ready"
      : "status-dot error";
    statusText.textContent = "The test is complete";
    progressCard.hidden = true;
    resultsCard.hidden = false;
    resultsCard.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showError(error.message);
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

checkClaude();
