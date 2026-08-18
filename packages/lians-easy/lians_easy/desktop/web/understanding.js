(() => {
  const button = document.querySelector("#understanding");
  if (!button || button.dataset.bound === "true") return;
  button.dataset.bound = "true";

  const panel = document.querySelector("#understanding-panel");
  const closeButton = document.querySelector("#understanding-close");
  const input = document.querySelector("#understanding-input");
  const runButton = document.querySelector("#understanding-run");
  const status = document.querySelector("#understanding-status");
  const result = document.querySelector("#understanding-result");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function escapeHtml(value) {
    const node = document.createElement("span");
    node.textContent = String(value || "");
    return node.innerHTML;
  }

  function formatNumber(value) {
    return new Intl.NumberFormat().format(Math.max(0, Number(value || 0)));
  }

  function reveal(element) {
    if (!reduceMotion && typeof element.animate === "function") {
      element.animate(
        [{ opacity: 0, transform: "translateY(12px)" }, { opacity: 1, transform: "none" }],
        { duration: 280, easing: "cubic-bezier(.22,1,.36,1)" },
      );
    }
  }

  function render(brief, health) {
    const questions = brief?.questions || [];
    document.querySelector("#understanding-intent").textContent = brief?.intent || "Ready";
    document.querySelector("#understanding-headline").textContent = brief?.needs_clarification
      ? questions[0]?.question || "One detail is missing."
      : "Your request is ready to move.";
    const memoryCount = brief?.privacy?.memory_items_considered || 0;
    document.querySelector("#understanding-detail").textContent = brief?.needs_clarification
      ? questions[0]?.why || "This answer changes the next useful action."
      : `${memoryCount} relevant memories checked. Lians will avoid asking for what it already knows.`;
    document.querySelector("#understanding-questions").innerHTML = questions
      .slice(brief?.needs_clarification ? 1 : 0, 3)
      .map((item) => `
        <article class="understanding-question">
          <strong>${escapeHtml(item.question)}</strong>
          <p>${escapeHtml(item.why)}</p>
        </article>`)
      .join("");
    document.querySelector("#memory-health-score").textContent = health
      ? `${formatNumber(health.score)} / 100`
      : "Unavailable";
    result.hidden = false;
    reveal(result);
  }

  async function run() {
    const request = input.value.trim();
    if (!window.pywebview || !request || runButton.getAttribute("aria-busy") === "true") {
      status.textContent = request ? "Lians is still starting." : "Describe the outcome first.";
      return;
    }
    runButton.setAttribute("aria-busy", "true");
    status.textContent = "Finding what matters...";
    try {
      const [brief, health] = await Promise.all([
        window.pywebview.api.understand_request(request),
        window.pywebview.api.memory_health(),
      ]);
      render(brief, health);
      status.textContent = brief.needs_clarification
        ? "One answer will sharpen the work."
        : "Ready to use with your agent.";
    } catch {
      status.textContent = "Lians could not read this request.";
    } finally {
      runButton.removeAttribute("aria-busy");
    }
  }

  button.addEventListener("click", () => {
    panel.hidden = false;
    input.focus();
    reveal(panel);
  });
  closeButton.addEventListener("click", () => {
    panel.hidden = true;
  });
  runButton.addEventListener("click", run);
  input.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") run();
  });
})();
