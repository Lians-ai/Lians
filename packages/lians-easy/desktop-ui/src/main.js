import {
  createTimeline,
  stagger as animeStagger,
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
const introCanvas = document.querySelector("#intro-particles");
const ambientCanvas = document.querySelector("#ambient-canvas");
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
let ambientParticles = [];
let ambientFrame = 0;
let ambientLastDraw = 0;
const pointer = { x: -1000, y: -1000 };

function seededRandom(seed = 0x4c49414e) {
  let value = seed >>> 0;
  return () => {
    value += 0x6d2b79f5;
    let result = value;
    result = Math.imul(result ^ (result >>> 15), result | 1);
    result ^= result + Math.imul(result ^ (result >>> 7), result | 61);
    return ((result ^ (result >>> 14)) >>> 0) / 4294967296;
  };
}

function sizeCanvas(canvas) {
  const bounds = canvas.getBoundingClientRect();
  const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
  const width = Math.max(1, Math.round(bounds.width * ratio));
  const height = Math.max(1, Math.round(bounds.height * ratio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { context, width: bounds.width, height: bounds.height, ratio };
}

function loadImage(source) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = source;
  });
}

function wordmarkTargetPoints(image, count = 4200) {
  const sample = document.createElement("canvas");
  sample.width = 650;
  sample.height = 240;
  const context = sample.getContext("2d", { willReadFrequently: true });
  context.drawImage(image, 0, 0, sample.width, sample.height);
  const pixels = context.getImageData(0, 0, sample.width, sample.height).data;
  const edgeCandidates = [];
  const fillCandidates = [];
  const colorAt = (x, y) => {
    if (x < 0 || y < 0 || x >= sample.width || y >= sample.height) return null;
    const offset = (y * sample.width + x) * 4;
    return { red: pixels[offset], green: pixels[offset + 1], blue: pixels[offset + 2] };
  };
  const isWordmark = (color) => color
    && color.blue >= 5
    && color.blue > color.red * 1.45
    && color.blue > color.green * 1.35;
  const isWordmarkAt = (x, y) => isWordmark(colorAt(x, y));
  const opacityFor = (color) => {
    if (!color) return 0;
    return Math.min(255, Math.max(96, color.blue * 1.7));
  };
  for (let y = 0; y < sample.height; y += 2) {
    for (let x = 0; x < sample.width; x += 2) {
      const color = colorAt(x, y);
      if (!isWordmark(color)) continue;
      const point = {
        x: x / (sample.width - 1),
        y: y / (sample.height - 1),
        alpha: opacityFor(color),
      };
      const edge = !isWordmarkAt(x - 4, y)
        || !isWordmarkAt(x + 4, y)
        || !isWordmarkAt(x, y - 4)
        || !isWordmarkAt(x, y + 4);
      (edge ? edgeCandidates : fillCandidates).push(point);
    }
  }
  const random = seededRandom(0x4c4f5455);
  const shuffle = (points) => {
    for (let index = points.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(random() * (index + 1));
      [points[index], points[swap]] = [points[swap], points[index]];
    }
  };
  shuffle(edgeCandidates);
  shuffle(fillCandidates);
  const edgeCount = Math.min(edgeCandidates.length, Math.round(count * 0.38));
  const points = edgeCandidates.slice(0, edgeCount);
  points.push(...fillCandidates.slice(0, Math.max(0, count - points.length)));
  return points;
}

function finishIntro() {
  const timeline = createTimeline({
    defaults: { ease: "out(3)" },
    onComplete: () => {
      content.classList.add("ready");
      motionAnimate(intro, { opacity: [1, 0] }, { duration: 0.2 }).finished.then(() => {
        intro.hidden = true;
      });
    },
  });
  timeline.add(
    ".reveal",
    {
      opacity: [0, 1],
      y: [14, 0],
      delay: animeStagger(28),
      duration: 360,
    },
    0,
  );
}

async function runIntro() {
  if (reduceMotion || window.location.hash === "#intro-complete") {
    intro.hidden = true;
    content.classList.add("ready");
    return;
  }
  try {
    const image = await loadImage("lians-wordmark.png");
    const targets = wordmarkTargetPoints(image);
    const { context, width, height } = sizeCanvas(introCanvas);
    const random = seededRandom();
    const aspect = image.naturalWidth / image.naturalHeight;
    const targetWidth = Math.min(width * 0.64, height * 0.39 * aspect);
    const targetHeight = targetWidth / aspect;
    const left = (width - targetWidth) / 2;
    const top = (height - targetHeight) / 2;
    const particles = targets.map((target) => ({
      startX: random() * width,
      startY: random() * height,
      targetX: left + target.x * targetWidth,
      targetY: top + target.y * targetHeight,
      size: 0.65 + random() * 1,
      phase: random() * Math.PI * 2,
      speed: 0.7 + random() * 1.8,
      driftX: 24 + random() * 82,
      driftY: 18 + random() * 64,
      bendX: (random() - 0.5) * width * 0.34,
      bendY: (random() - 0.5) * height * 0.38,
      opacity: 0.5 + (target.alpha / 255) * 0.5,
    }));
    const started = performance.now();
    const chaosDuration = 220;
    const gatherDuration = 820;
    const wordmarkHold = 170;
    const duration = chaosDuration + gatherDuration + wordmarkHold;
    const render = (now) => {
      const elapsed = now - started;
      const gatherProgress = Math.min(
        1,
        Math.max(0, (elapsed - chaosDuration) / gatherDuration),
      );
      const eased = gatherProgress < 0.5
        ? 4 * gatherProgress * gatherProgress * gatherProgress
        : 1 - Math.pow(-2 * gatherProgress + 2, 3) / 2;
      const seconds = elapsed * 0.001;
      context.clearRect(0, 0, width, height);
      for (const particle of particles) {
        let x;
        let y;
        if (elapsed < chaosDuration) {
          x = particle.startX
            + Math.sin(particle.phase + seconds * particle.speed * 4.2) * particle.driftX;
          y = particle.startY
            + Math.cos(particle.phase * 0.7 + seconds * particle.speed * 3.6) * particle.driftY;
        } else {
          const curve = Math.sin(gatherProgress * Math.PI) * (1 - gatherProgress);
          const flutter = (1 - eased) * 28;
          x = particle.startX + (particle.targetX - particle.startX) * eased
            + curve * particle.bendX
            + Math.sin(particle.phase + seconds * particle.speed * 5) * flutter;
          y = particle.startY + (particle.targetY - particle.startY) * eased
            + curve * particle.bendY
            + Math.cos(particle.phase + seconds * particle.speed * 4.4) * flutter;
        }
        context.beginPath();
        const red = Math.round(60 + eased * 44);
        const green = Math.round(98 + eased * 55);
        const blue = Math.round(218 + eased * 37);
        const alpha = particle.opacity * (0.58 + eased * 0.42);
        context.fillStyle = `rgba(${red}, ${green}, ${blue}, ${alpha})`;
        const displaySize = particle.size * (0.18 + eased * 1.12);
        context.arc(x, y, displaySize, 0, Math.PI * 2);
        context.fill();
      }
      if (elapsed < duration) {
        requestAnimationFrame(render);
      } else {
        finishIntro();
      }
    };
    requestAnimationFrame(render);
  } catch {
    finishIntro();
  }
}

function resetAmbientParticles(width, height) {
  const random = seededRandom(0x414d4249);
  const count = Math.max(24, Math.min(42, Math.round((width * height) / 46000)));
  ambientParticles = Array.from({ length: count }, () => ({
    x: random() * width,
    y: random() * height,
    vx: (random() - 0.5) * 0.12,
    vy: (random() - 0.5) * 0.08,
    size: 0.8 + random() * 1.5,
    phase: random() * Math.PI * 2,
  }));
}

function drawAmbient(now) {
  if (!reduceMotion) ambientFrame = requestAnimationFrame(drawAmbient);
  if (document.hidden || (!reduceMotion && now - ambientLastDraw < 32)) return;
  ambientLastDraw = now;
  const { context, width, height } = sizeCanvas(ambientCanvas);
  if (!ambientParticles.length) resetAmbientParticles(width, height);
  context.clearRect(0, 0, width, height);
  const dark = root.dataset.theme !== "light";
  const time = now * 0.00016;

  for (let band = 0; band < 4; band += 1) {
    context.beginPath();
    for (let x = -20; x <= width + 20; x += 24) {
      const y = height * (0.62 + band * 0.075)
        + Math.sin(x * 0.008 + time * (1 + band * 0.08)) * (9 + band * 2);
      if (x === -20) context.moveTo(x, y);
      else context.lineTo(x, y);
    }
    context.strokeStyle = dark ? "rgba(57, 96, 205, 0.055)" : "rgba(37, 74, 168, 0.065)";
    context.lineWidth = 1;
    context.stroke();
  }

  for (const particle of ambientParticles) {
    if (!reduceMotion) {
      particle.x += particle.vx + Math.sin(time + particle.phase) * 0.025;
      particle.y += particle.vy + Math.cos(time + particle.phase) * 0.018;
      const dx = particle.x - pointer.x;
      const dy = particle.y - pointer.y;
      const distance = Math.hypot(dx, dy);
      if (distance < 120 && distance > 0) {
        particle.x += (dx / distance) * 0.22;
        particle.y += (dy / distance) * 0.22;
      }
      if (particle.x < -12) particle.x = width + 12;
      if (particle.x > width + 12) particle.x = -12;
      if (particle.y < -12) particle.y = height + 12;
      if (particle.y > height + 12) particle.y = -12;
    }
  }

  for (let first = 0; first < ambientParticles.length; first += 1) {
    const a = ambientParticles[first];
    for (let second = first + 1; second < ambientParticles.length; second += 1) {
      const b = ambientParticles[second];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      if (distance < 135) {
        context.beginPath();
        context.moveTo(a.x, a.y);
        context.lineTo(b.x, b.y);
        const opacity = (1 - distance / 135) * (dark ? 0.075 : 0.09);
        context.strokeStyle = `rgba(64, 103, 215, ${opacity})`;
        context.stroke();
      }
    }
    context.beginPath();
    context.fillStyle = dark ? "rgba(82, 122, 232, 0.24)" : "rgba(42, 78, 177, 0.22)";
    context.arc(a.x, a.y, a.size, 0, Math.PI * 2);
    context.fill();
  }
}

function startAmbientBackground() {
  cancelAnimationFrame(ambientFrame);
  resetAmbientParticles(ambientCanvas.clientWidth, ambientCanvas.clientHeight);
  ambientFrame = requestAnimationFrame(drawAmbient);
  window.addEventListener("pointermove", (event) => {
    pointer.x = event.clientX;
    pointer.y = event.clientY;
  }, { passive: true });
  window.addEventListener("pointerleave", () => {
    pointer.x = -1000;
    pointer.y = -1000;
  });
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
      resetAmbientParticles(width, height);
    });
  });

  titlebar.addEventListener("mousedown", (event) => {
    if (event.button === 0 && !event.target.closest("button")) {
      const request = window.pywebview?.api.drag_window();
      request?.catch(() => {});
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
startAmbientBackground();
runIntro();
