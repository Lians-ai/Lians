import {
  createTimeline,
  stagger as animeStagger,
  spring as animeSpring,
} from "animejs";
import {
  animate as motionAnimate,
  frame,
  hover,
  press,
  resize,
} from "motion";

const root = document.documentElement;
const intro = document.querySelector("#intro");
const introLotus = document.querySelector("#intro-lotus");
const content = document.querySelector("#content");
const themeButton = document.querySelector("#theme-toggle");
const refreshButton = document.querySelector("#refresh");
const titlebar = document.querySelector("#titlebar");
const minimizeButton = document.querySelector("#window-minimize");
const maximizeButton = document.querySelector("#window-maximize");
const closeButton = document.querySelector("#window-close");
const pet = document.querySelector("#lotus-pet");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

let bridgeReady = false;
let refreshing = false;

function runIntro() {
  if (reduceMotion) {
    intro.hidden = true;
    content.classList.add("ready");
    return;
  }

  const begin = () => requestAnimationFrame(() => requestAnimationFrame(() => window.setTimeout(() => {
    const timeline = createTimeline({
      defaults: { ease: "out(3)" },
      onComplete: () => {
        content.classList.add("ready");
        motionAnimate(intro, { opacity: [1, 0] }, { duration: 0.16 }).finished.then(() => {
          intro.hidden = true;
        });
      },
    });

    timeline
      .add(introLotus, { scale: [1.08, 1], duration: 140 }, 0)
      .add(
        introLotus,
        {
          scale: 0.075,
          ease: animeSpring({ duration: 620, bounce: 0.06 }),
        },
        115,
      )
      .add(
        ".intro-ring",
        {
          opacity: [0.28, 0],
          scale: [0.7, 1.7],
          duration: 520,
        },
        160,
      )
      .add(
        ".reveal",
        {
          opacity: [0, 1],
          y: [18, 0],
          delay: animeStagger(38),
          duration: 420,
        },
        470,
      );
  }, 220)));

  if (introLotus.decode) {
    introLotus.decode().then(begin, begin);
  } else {
    begin();
  }
}

function setTheme(theme, animateChange = true) {
  root.dataset.theme = theme;
  localStorage.setItem("lians-theme", theme);
  themeButton.dataset.icon = theme === "dark" ? "sun" : "moon";
  themeButton.setAttribute(
    "aria-label",
    theme === "dark" ? "Use light mode" : "Use dark mode",
  );
  if (animateChange && !reduceMotion) {
    motionAnimate(themeButton, { scale: [0.82, 1] }, { type: "spring", bounce: 0.18, duration: 0.28 });
    motionAnimate(document.body, { opacity: [0.94, 1] }, { duration: 0.12 });
  }
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(Math.max(0, Number(value || 0)));
}

function renderActivity(items) {
  const container = document.querySelector("#activity-list");
  if (!items || items.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <span class="waterline" aria-hidden="true"></span>
        <p>Waiting for the first handoff.</p>
      </div>`;
    return;
  }
  container.innerHTML = items
    .map(
      (item) => `
        <article class="activity-row">
          <div>
            <h3>${escapeHtml(item.title)}</h3>
            <p>${escapeHtml(item.detail)}</p>
          </div>
          <time>${escapeHtml(item.time)}</time>
        </article>`,
    )
    .join("");
  if (!reduceMotion) {
    motionAnimate(
      ".activity-row",
      { opacity: [0, 1], y: [10, 0] },
      { duration: 0.26, delay: (index) => index * 0.04 },
    );
  }
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value || "");
  return node.innerHTML;
}

function render(snapshot) {
  const metrics = snapshot?.metrics || {};
  document.querySelector("#tokens-value").textContent = `~${formatNumber(
    metrics.repeated_tokens_avoided_estimate,
  )}`;
  document.querySelector("#handoffs-value").textContent = formatNumber(metrics.context_events);
  document.querySelector("#memory-value").textContent = formatNumber(metrics.memories_reused);
  document.querySelector("#saved-memory-count").textContent = `${formatNumber(
    metrics.saved_memories,
  )} saved memories`;

  const connection = document.querySelector("#connection");
  const connectionIcon = document.querySelector("#connection-icon");
  const connectionText = document.querySelector("#connection-text");
  if (snapshot?.agent) {
    connection.dataset.state = "connected";
    connectionIcon.src = `agents/${snapshot.agent.key}.png`;
    connectionIcon.alt = "";
    connectionIcon.hidden = false;
    connectionText.textContent = `${snapshot.agent.label} connected`;
  } else {
    connection.dataset.state = "offline";
    connectionIcon.hidden = true;
    connectionText.textContent = "No connection detected";
  }
  renderActivity(metrics.activity || []);
}

async function refresh() {
  if (!bridgeReady || refreshing) return;
  refreshing = true;
  refreshButton.setAttribute("aria-busy", "true");
  try {
    render(await window.pywebview.api.snapshot());
  } finally {
    refreshing = false;
    refreshButton.removeAttribute("aria-busy");
  }
}

function installInteractions() {
  hover(".metric", (element) => {
    motionAnimate(element, { y: -4 }, { type: "spring", bounce: 0.08, duration: 0.24 });
    return () => motionAnimate(element, { y: 0 }, { duration: 0.16 });
  });
  hover(".control", (element) => {
    motionAnimate(element, { y: -2 }, { duration: 0.14 });
    return () => motionAnimate(element, { y: 0 }, { duration: 0.12 });
  });
  press(".control", (element) => {
    motionAnimate(element, { scale: 0.96 }, { duration: 0.08 });
    return () => motionAnimate(element, { scale: 1 }, { type: "spring", bounce: 0.14, duration: 0.22 });
  });
  press(pet, (element) => {
    motionAnimate(
      element,
      { y: [0, -18, 0], rotate: [0, -4, 4, 0], scale: [1, 1.08, 1] },
      { type: "spring", bounce: 0.3, duration: 0.52 },
    );
  });
  resize(({ width, height }) => {
    frame.render(() => {
      root.style.setProperty("--viewport-width", `${width}px`);
      root.style.setProperty("--viewport-height", `${height}px`);
    });
  });

  titlebar.addEventListener("mousedown", (event) => {
    if (event.button === 0 && !event.target.closest("button")) {
      window.pywebview?.api.drag_window();
    }
  });
  titlebar.addEventListener("dblclick", (event) => {
    if (!event.target.closest("button")) {
      window.pywebview?.api.toggle_maximize();
    }
  });
  document.querySelectorAll(".resize-handle").forEach((handle) => {
    handle.addEventListener("mousedown", (event) => {
      if (event.button === 0) {
        window.pywebview?.api.resize_window(Number(handle.dataset.edge));
      }
    });
  });
}

themeButton.addEventListener("click", () => {
  setTheme(root.dataset.theme === "dark" ? "light" : "dark");
});
refreshButton.addEventListener("click", refresh);
minimizeButton.addEventListener("click", () => window.pywebview?.api.minimize());
maximizeButton.addEventListener("click", () => window.pywebview?.api.toggle_maximize());
closeButton.addEventListener("click", () => window.pywebview?.api.close());

window.addEventListener("pywebviewready", () => {
  bridgeReady = true;
  refresh();
  window.setInterval(refresh, 3000);
});

setTheme(localStorage.getItem("lians-theme") === "light" ? "light" : "dark", false);
installInteractions();
runIntro();
