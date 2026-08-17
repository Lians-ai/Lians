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
const neuralField = document.querySelector("#neural-field");
const brainOrbit = document.querySelector("#brain-orbit");
const brainStatus = document.querySelector("#brain-status");
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
const brainStates = {
  compressed: { scaleX: 0.76, scaleY: 0.84, opacity: 0.62 },
  expanded: { scaleX: 1.16, scaleY: 1.08, opacity: 0.86 },
};

function applyWindowState(state) {
  const maximized = state === "maximized";
  maximizeButton.dataset.state = maximized ? "maximized" : "windowed";
  maximizeButton.setAttribute("aria-label", maximized ? "Restore window" : "Maximize window");
}

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

function lotusTargetPoints(image, count = 2600) {
  const sample = document.createElement("canvas");
  sample.width = 256;
  sample.height = 256;
  const context = sample.getContext("2d", { willReadFrequently: true });
  context.drawImage(image, 0, 0, sample.width, sample.height);
  const pixels = context.getImageData(0, 0, sample.width, sample.height).data;
  const edgeCandidates = [];
  const fillCandidates = [];
  const colorAt = (x, y) => {
    if (x < 0 || y < 0 || x >= sample.width || y >= sample.height) return null;
    const offset = (y * sample.width + x) * 4;
    return {
      red: pixels[offset],
      green: pixels[offset + 1],
      blue: pixels[offset + 2],
      alpha: pixels[offset + 3],
    };
  };
  const isLotus = (color) => color
    && color.alpha > 16
    && color.blue >= 5
    && color.blue > color.red * 1.35
    && color.blue > color.green * 1.18;
  const isLotusAt = (x, y) => isLotus(colorAt(x, y));
  const opacityFor = (color) => {
    if (!color) return 0;
    return Math.min(255, Math.max(96, color.blue * 1.7));
  };
  for (let y = 0; y < sample.height; y += 2) {
    for (let x = 0; x < sample.width; x += 2) {
      const color = colorAt(x, y);
      if (!isLotus(color)) continue;
      const point = {
        x: x / (sample.width - 1),
        y: y / (sample.height - 1),
        alpha: opacityFor(color),
      };
      const edge = !isLotusAt(x - 4, y)
        || !isLotusAt(x + 4, y)
        || !isLotusAt(x, y - 4)
        || !isLotusAt(x, y + 4);
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
    const image = await loadImage("lotus.png");
    const targets = lotusTargetPoints(image);
    const { context, width, height } = sizeCanvas(introCanvas);
    const random = seededRandom();
    const targetWidth = Math.min(width * 0.48, height * 0.72);
    const targetHeight = targetWidth;
    const left = (width - targetWidth) / 2;
    const top = (height - targetHeight) / 2;
    const lotusParticles = targets.map((target) => ({
      startX: random() * width,
      startY: random() * height,
      targetX: left + (0.24 + target.x * 0.52) * targetWidth,
      targetY: top + (0.24 + target.y * 0.52) * targetHeight,
      size: 0.42 + random() * 0.78,
      phase: random() * Math.PI * 2,
      speed: 0.7 + random() * 1.8,
      driftX: 24 + random() * 82,
      driftY: 18 + random() * 64,
      bendX: (random() - 0.5) * width * 0.34,
      bendY: (random() - 0.5) * height * 0.38,
      opacity: 0.5 + (target.alpha / 255) * 0.5,
      portal: false,
    }));
    const ringParticles = Array.from({ length: 980 }, (_, index) => {
      const angle = Math.PI * 2 * index / 980 + (random() - 0.5) * 0.014;
      const radius = 0.474 + (random() - 0.5) * 0.022;
      return {
        startX: random() * width,
        startY: random() * height,
        targetX: left + (0.5 + Math.cos(angle) * radius) * targetWidth,
        targetY: top + (0.5 + Math.sin(angle) * radius) * targetHeight,
        size: 0.48 + random() * 0.92,
        phase: random() * Math.PI * 2,
        speed: 0.8 + random() * 1.4,
        driftX: 32 + random() * 96,
        driftY: 26 + random() * 82,
        bendX: (random() - 0.5) * width * 0.46,
        bendY: (random() - 0.5) * height * 0.46,
        opacity: 0.56 + random() * 0.42,
        portal: true,
      };
    });
    const particles = [...lotusParticles, ...ringParticles];
    const started = performance.now();
    const chaosDuration = 90;
    const gatherDuration = 670;
    const portalHold = 260;
    const duration = chaosDuration + gatherDuration + portalHold;
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
      if (eased > 0.01) {
        const radius = targetWidth / 2;
        const centerX = left + radius;
        const centerY = top + radius;
        context.save();
        context.shadowColor = `rgba(49, 95, 233, ${eased * 0.7})`;
        context.shadowBlur = 34;
        context.strokeStyle = `rgba(106, 148, 255, ${eased * 0.82})`;
        context.lineWidth = 1.4;
        context.beginPath();
        context.arc(centerX, centerY, radius, 0, Math.PI * 2);
        context.stroke();
        context.shadowBlur = 0;
        context.strokeStyle = `rgba(74, 113, 238, ${eased * 0.55})`;
        context.lineWidth = 2.2;
        context.beginPath();
        context.arc(centerX, centerY, radius, seconds * 0.65, seconds * 0.65 + 1.3);
        context.stroke();
        context.restore();
      }
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
        const red = particle.portal ? 88 : 62;
        const green = particle.portal ? 132 : 102;
        const blue = particle.portal ? 255 : 232;
        const alpha = particle.opacity * (0.58 + eased * 0.42);
        context.fillStyle = `rgba(${red}, ${green}, ${blue}, ${alpha})`;
        const displaySize = particle.size * (0.16 + eased * 0.96);
        context.arc(x, y, displaySize, 0, Math.PI * 2);
        context.fill();
      }
      const revealRaw = Math.min(1, Math.max(0, (elapsed - 360) / 270));
      const reveal = revealRaw * revealRaw * (3 - 2 * revealRaw);
      if (reveal > 0.01) {
        const pulse = 1 + Math.sin(elapsed * 0.008) * 0.012 * eased;
        const logoSize = targetWidth * (0.46 + reveal * 0.08) * pulse;
        context.save();
        context.globalAlpha = reveal;
        context.drawImage(
          image,
          left + (targetWidth - logoSize) / 2,
          top + (targetHeight - logoSize) / 2,
          logoSize,
          logoSize,
        );
        context.restore();
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
    if (!reduceMotion) {
      const horizontal = ((event.clientX / Math.max(1, window.innerWidth)) - 0.5) * 12;
      const vertical = ((event.clientY / Math.max(1, window.innerHeight)) - 0.5) * 8;
      neuralField.style.setProperty("--brain-shift-x", `${horizontal.toFixed(2)}px`);
      neuralField.style.setProperty("--brain-shift-y", `${vertical.toFixed(2)}px`);
    }
  }, { passive: true });
  window.addEventListener("pointerleave", () => {
    pointer.x = -1000;
    pointer.y = -1000;
    neuralField.style.setProperty("--brain-shift-x", "0px");
    neuralField.style.setProperty("--brain-shift-y", "0px");
  });
}

function setBrainState(state, animateChange = true) {
  const next = brainStates[state] ? state : "compressed";
  const values = brainStates[next];
  neuralField.dataset.state = next;
  localStorage.setItem("lians-brain-state", next);
  if (animateChange && !reduceMotion) {
    motionAnimate(
      brainOrbit,
      {
        scaleX: values.scaleX,
        scaleY: values.scaleY,
        opacity: values.opacity,
      },
      { type: "spring", bounce: 0.08, duration: 0.72 },
    );
  } else {
    brainOrbit.style.transform = `scale(${values.scaleX}, ${values.scaleY})`;
    brainOrbit.style.opacity = String(values.opacity);
  }
  brainStatus.textContent = next === "expanded" ? "Background expanded" : "Background compressed";
}

function toggleBrain(event) {
  if (event.target.closest("button, a, input, textarea, select, [contenteditable='true']")) return;
  event.preventDefault();
  setBrainState(neuralField.dataset.state === "expanded" ? "compressed" : "expanded");
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
  const tokenMetric = metrics.token_metric || {};
  const tokenValue = tokenMetric.value ?? metrics.repeated_tokens_avoided_estimate;
  const tokenPrefix = tokenMetric.approximate === false ? "" : "~";
  document.querySelector("#tokens-label").textContent = tokenMetric.label || "Tokens saved";
  document.querySelector("#tokens-value").textContent = `${tokenPrefix}${formatNumber(tokenValue)}`;
  document.querySelector("#tokens-detail").textContent = tokenMetric.detail || "Repeated context removed";
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

  document.querySelector(".window-actions").addEventListener("mousedown", (event) => {
    event.stopPropagation();
  });
  titlebar.addEventListener("mousedown", (event) => {
    if (event.button === 0 && event.detail === 1 && !event.target.closest("button")) {
      window.pywebview?.api.start_drag().catch(() => {});
      window.addEventListener("mouseup", () => {
        window.setTimeout(() => {
          window.pywebview?.api.window_state().then(applyWindowState).catch(() => {});
        }, 120);
      }, { once: true });
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
  content.addEventListener("contextmenu", toggleBrain);
}

themeButton.addEventListener("click", () => {
  setTheme(root.dataset.theme === "dark" ? "light" : "dark");
});
refreshButton.addEventListener("click", refresh);
minimizeButton.addEventListener("click", () => window.pywebview?.api.minimize());
maximizeButton.addEventListener("click", async () => {
  const state = await window.pywebview?.api.toggle_maximize();
  applyWindowState(state);
});
closeButton.addEventListener("click", () => window.pywebview?.api.close());

window.addEventListener("pywebviewready", () => {
  bridgeReady = true;
  window.pywebview.api.window_state().then(applyWindowState).catch(() => {});
  refresh();
  window.setInterval(refresh, 3000);
});

setTheme(localStorage.getItem("lians-theme") === "light" ? "light" : "dark", false);
setBrainState(localStorage.getItem("lians-brain-state") || "compressed", false);
installInteractions();
startAmbientBackground();
runIntro();
