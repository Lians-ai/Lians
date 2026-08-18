(() => {
  "use strict";

  const root = document.documentElement;
  const button = document.querySelector("#work-map");
  const panel = document.querySelector("#map-panel");
  const closeButton = document.querySelector("#map-close");
  const resetButton = document.querySelector("#map-reset");
  const canvas = document.querySelector("#work-map-canvas");
  const summary = document.querySelector("#map-summary");
  const detailKind = document.querySelector("#map-detail-kind");
  const detailTitle = document.querySelector("#map-detail-title");
  const detailBody = document.querySelector("#map-detail-body");
  const detailMeta = document.querySelector("#map-detail-meta");
  const detailAction = document.querySelector("#map-node-action");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let graph = null;
  let world = new Map();
  let projected = [];
  let hovered = null;
  let selected = null;
  let animationFrame = 0;
  let lastFrame = 0;
  let dragging = false;
  let dragMoved = false;
  let pointer = { x: 0, y: 0 };
  const camera = { yaw: 0.42, pitch: -0.18, zoom: 1 };

  const formatNumber = (value) => new Intl.NumberFormat().format(
    Math.max(0, Number(value || 0)),
  );

  const palette = {
    memory: "#6d91ff",
    agent: "#57d7b0",
    receipt: "#d6b86b",
    project: "#f2f4f7",
    topic: "#8c96a6",
    task: "#f2f4f7",
    criterion: "#9ab0ee",
    constraint: "#d6b86b",
    evidence: "#57d7b0",
    blocker: "#ec7f86",
    session: "#b899ff",
    policy: "#4f8cff",
    invalidation: "#ec7f86",
    affected_work: "#d6b86b",
    verification_policy: "#b899ff",
    verification_receipt: "#57d7b0",
  };

  function nodeColor(node) {
    if (node.state === "invalidated") return "#ec7f86";
    if (node.type === "verification_receipt" && node.label === "Verification blocked") {
      return "#ec7f86";
    }
    if (node.type === "task" && ["blocked", "at_risk"].includes(node.state)) return "#ec7f86";
    if (node.type === "criterion" && node.state === "missing") return "#7b8494";
    if (node.type === "constraint" && node.state === "failed") return "#ec7f86";
    return palette[node.type] || "#8c96a6";
  }

  function hash(value) {
    let result = 2166136261;
    for (const character of String(value)) {
      result ^= character.charCodeAt(0);
      result = Math.imul(result, 16777619);
    }
    return result >>> 0;
  }

  function unit(value, salt = 0) {
    return (hash(`${value}:${salt}`) % 100000) / 100000;
  }

  function radiusFor(node) {
    return {
      project: 16,
      task: 14,
      agent: 12,
      blocker: 10,
      evidence: 9,
      criterion: 8,
      constraint: 8,
      memory: 7,
      session: 6,
      policy: 11,
      invalidation: 11,
      affected_work: 9,
      verification_policy: 10,
      verification_receipt: 11,
      receipt: 6,
      topic: 5,
    }[node.type] || 5;
  }

  function buildWorld() {
    const nodes = (graph?.nodes || []).slice(0, 120);
    const nodeIds = new Set(nodes.map((node) => node.id));
    const edges = (graph?.edges || []).filter(
      (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target),
    );
    const result = new Map();
    const projects = nodes.filter((node) => node.type === "project");
    const tasks = nodes.filter((node) => node.type === "task");

    projects.forEach((node, index) => {
      const offset = (index - (projects.length - 1) / 2) * 240;
      result.set(node.id, { x: offset, y: 0, z: 0 });
    });
    tasks.forEach((node, index) => {
      const angle = (index / Math.max(1, tasks.length)) * Math.PI * 2 - Math.PI / 2;
      const layer = 135 + (index % 2) * 34;
      result.set(node.id, {
        x: Math.cos(angle) * layer,
        y: Math.sin(angle) * layer * 0.72,
        z: (unit(node.id, 1) - 0.5) * 170,
      });
    });

    const connectedTask = (node) => {
      const edge = edges.find((candidate) => (
        candidate.source === node.id && String(candidate.target).startsWith("task:")
      ) || (
        candidate.target === node.id && String(candidate.source).startsWith("task:")
      ));
      const taskId = edge
        ? (String(edge.source).startsWith("task:") ? edge.source : edge.target)
        : null;
      return taskId ? result.get(taskId) : null;
    };

    nodes.forEach((node, index) => {
      if (result.has(node.id)) return;
      const parent = connectedTask(node);
      if (parent) {
        const angle = unit(node.id, 2) * Math.PI * 2;
        const distance = 54 + unit(node.id, 3) * 52;
        result.set(node.id, {
          x: parent.x + Math.cos(angle) * distance,
          y: parent.y + Math.sin(angle) * distance * 0.7,
          z: parent.z + (unit(node.id, 4) - 0.5) * 95,
        });
        return;
      }
      const longitude = unit(node.id, 5) * Math.PI * 2;
      const latitude = Math.acos(2 * unit(node.id, 6) - 1) - Math.PI / 2;
      const distance = node.type === "agent" ? 220 : 195 + unit(node.id, 7) * 150;
      result.set(node.id, {
        x: Math.cos(latitude) * Math.cos(longitude) * distance,
        y: Math.sin(latitude) * distance * 0.78,
        z: Math.cos(latitude) * Math.sin(longitude) * distance,
      });
      if (node.type === "agent") {
        const current = result.get(node.id);
        current.y -= 90 + index * 2;
      }
    });
    world = result;
  }

  function canvasContext() {
    const bounds = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    const width = Math.max(1, Math.round(bounds.width));
    const height = Math.max(1, Math.round(bounds.height));
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }
    const context = canvas.getContext("2d");
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    return { context, width, height };
  }

  function rotate(point) {
    const cosineYaw = Math.cos(camera.yaw);
    const sineYaw = Math.sin(camera.yaw);
    const x1 = point.x * cosineYaw - point.z * sineYaw;
    const z1 = point.x * sineYaw + point.z * cosineYaw;
    const cosinePitch = Math.cos(camera.pitch);
    const sinePitch = Math.sin(camera.pitch);
    return {
      x: x1,
      y: point.y * cosinePitch - z1 * sinePitch,
      z: point.y * sinePitch + z1 * cosinePitch,
    };
  }

  function project(node, width, height) {
    const point = rotate(world.get(node.id) || { x: 0, y: 0, z: 0 });
    const focal = 680;
    const depth = Math.max(260, focal - point.z * camera.zoom);
    const scale = (focal / depth) * camera.zoom;
    return {
      node,
      x: width / 2 + point.x * scale,
      y: height / 2 + point.y * scale,
      z: point.z,
      scale,
      radius: Math.max(2.5, radiusFor(node) * scale),
    };
  }

  function drawBackdrop(context, width, height, styles) {
    const line = styles.getPropertyValue("--line").trim() || "#172033";
    context.save();
    context.translate(width / 2, height / 2);
    context.strokeStyle = line;
    context.globalAlpha = 0.35;
    for (const radius of [90, 180, 280, 390]) {
      context.beginPath();
      context.ellipse(0, 0, radius * camera.zoom, radius * 0.31 * camera.zoom, 0, 0, Math.PI * 2);
      context.stroke();
    }
    context.restore();
  }

  function draw() {
    if (!graph || panel.hidden) return;
    const { context, width, height } = canvasContext();
    const styles = getComputedStyle(root);
    const line = styles.getPropertyValue("--line-strong").trim() || "#253149";
    const text = styles.getPropertyValue("--text").trim() || "#f2f4f7";
    context.clearRect(0, 0, width, height);
    drawBackdrop(context, width, height, styles);
    projected = (graph.nodes || []).slice(0, 120).map((node) => project(node, width, height));
    const byId = new Map(projected.map((point) => [point.node.id, point]));
    const showInferred = graph.control?.policy?.show_inferred_links === true;

    for (const edge of graph.edges || []) {
      if (edge.method === "neural" && !showInferred) continue;
      const source = byId.get(edge.source);
      const target = byId.get(edge.target);
      if (!source || !target) continue;
      const depthAlpha = Math.max(0.12, Math.min(0.7, (source.scale + target.scale) / 3.4));
      context.beginPath();
      context.strokeStyle = edge.method === "neural" ? "rgba(109,145,255,.38)" : line;
      context.globalAlpha = depthAlpha;
      context.lineWidth = edge.method === "neural" ? 0.7 : 1;
      context.moveTo(source.x, source.y);
      context.lineTo(target.x, target.y);
      context.stroke();
    }

    const ordered = [...projected].sort((left, right) => left.z - right.z);
    for (const point of ordered) {
      const active = selected?.id === point.node.id || hovered?.id === point.node.id;
      context.beginPath();
      context.fillStyle = nodeColor(point.node);
      context.globalAlpha = point.node.state === "forgotten"
        ? 0.2
        : Math.max(0.38, Math.min(1, point.scale * 0.86));
      context.arc(point.x, point.y, point.radius + (active ? 3 : 0), 0, Math.PI * 2);
      context.fill();
      if (active) {
        context.beginPath();
        context.strokeStyle = nodeColor(point.node);
        context.globalAlpha = 0.38;
        context.lineWidth = 1;
        context.arc(point.x, point.y, point.radius + 8, 0, Math.PI * 2);
        context.stroke();
      }
      if (active || point.node.type === "project" || point.node.type === "task") {
        context.globalAlpha = Math.max(0.55, Math.min(1, point.scale));
        context.fillStyle = text;
        context.font = `${Math.max(11, Math.min(17, 14 * point.scale))}px "IvyPresto Text", Georgia, serif`;
        context.textAlign = "center";
        context.fillText(
          String(point.node.label || "").slice(0, 42),
          point.x,
          point.y - point.radius - 11,
        );
      }
    }
    context.globalAlpha = 1;
  }

  function updateDetails(node) {
    const current = node || selected;
    if (!current) {
      detailKind.textContent = "Work graph";
      detailTitle.textContent = "Select any point";
      detailBody.textContent = "Inspect where information came from, what it affected, and whether it is current.";
      detailMeta.textContent = "Verified links stay separate from inferred relationships.";
      detailAction.hidden = true;
      return;
    }
    detailKind.textContent = current.type || "Work";
    detailTitle.textContent = current.label || "Untitled";
    detailBody.textContent = current.detail || current.current_action || "No additional detail recorded.";
    const details = [];
    if (current.state) details.push(`State: ${current.state}`);
    if (current.type === "task" && current.criterion_count) {
      details.push(`${current.satisfied_criteria || 0}/${current.criterion_count} criteria verified`);
    }
    if (current.created_at) details.push("Recorded activity");
    detailMeta.textContent = details.join(" · ");
    delete detailAction.dataset.action;
    delete detailAction.dataset.memoryId;
    delete detailAction.dataset.invalidationId;
    if (current.type === "invalidation" && current.invalidation_id) {
      detailAction.hidden = false;
      detailAction.dataset.action = "repair";
      detailAction.dataset.invalidationId = current.invalidation_id;
      detailAction.textContent = "Copy repair brief";
    } else if (current.type === "memory" && current.memory_id && ["current", "paused"].includes(current.state)) {
      detailAction.hidden = false;
      detailAction.dataset.action = "pause";
      detailAction.dataset.memoryId = current.memory_id;
      detailAction.dataset.paused = String(current.state !== "paused");
      detailAction.textContent = current.state === "paused" ? "Resume memory" : "Pause memory";
    } else {
      detailAction.hidden = true;
    }
  }

  function nearest(x, y) {
    return projected
      .map((point) => ({ point, distance: Math.hypot(point.x - x, point.y - y) }))
      .filter((candidate) => candidate.distance <= candidate.point.radius + 12)
      .sort((left, right) => left.distance - right.distance)[0]?.point.node || null;
  }

  function frame(now) {
    if (panel.hidden) return;
    if (!lastFrame) lastFrame = now;
    const elapsed = Math.min(40, now - lastFrame);
    lastFrame = now;
    if (!reduceMotion && !dragging && !hovered) camera.yaw += elapsed * 0.000035;
    draw();
    animationFrame = requestAnimationFrame(frame);
  }

  async function loadGraph(preferredNodeId = null) {
    graph = await window.pywebview.api.work_graph();
    buildWorld();
    if (preferredNodeId) {
      selected = graph.nodes?.find((node) => node.id === preferredNodeId) || null;
    }
    const totals = graph.summary || {};
    const verifiedLinks = (graph.edges || []).filter((edge) => edge.method !== "neural").length;
    const review = Number(totals.invalidation_count || 0);
    summary.textContent = `${formatNumber(totals.task_count)} tasks · ${formatNumber(
      totals.memory_count,
    )} memories · ${formatNumber(verifiedLinks)} verified links${
      review ? ` · ${formatNumber(review)} changes to review` : ""
    }`;
    updateDetails(selected);
  }

  async function open() {
    if (!window.pywebview?.api || button.getAttribute("aria-busy") === "true") return;
    button.setAttribute("aria-busy", "true");
    try {
      await loadGraph();
      document.querySelector("#control-panel").hidden = true;
      panel.hidden = false;
      lastFrame = 0;
      cancelAnimationFrame(animationFrame);
      animationFrame = requestAnimationFrame(frame);
    } catch {
      summary.textContent = "Work graph unavailable";
    } finally {
      button.removeAttribute("aria-busy");
    }
  }

  function close() {
    panel.hidden = true;
    hovered = null;
    cancelAnimationFrame(animationFrame);
  }

  function reset() {
    camera.yaw = 0.42;
    camera.pitch = -0.18;
    camera.zoom = 1;
    selected = null;
    updateDetails(null);
    draw();
  }

  button.addEventListener("click", open);
  closeButton.addEventListener("click", close);
  resetButton.addEventListener("click", reset);
  canvas.addEventListener("pointerdown", (event) => {
    dragging = true;
    dragMoved = false;
    pointer = { x: event.clientX, y: event.clientY };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    const bounds = canvas.getBoundingClientRect();
    if (dragging) {
      const deltaX = event.clientX - pointer.x;
      const deltaY = event.clientY - pointer.y;
      if (Math.abs(deltaX) + Math.abs(deltaY) > 1) dragMoved = true;
      camera.yaw += deltaX * 0.007;
      camera.pitch = Math.max(-1.15, Math.min(1.15, camera.pitch + deltaY * 0.006));
      pointer = { x: event.clientX, y: event.clientY };
      hovered = null;
    } else {
      hovered = nearest(event.clientX - bounds.left, event.clientY - bounds.top);
      if (!selected) updateDetails(hovered);
    }
  });
  canvas.addEventListener("pointerup", (event) => {
    const bounds = canvas.getBoundingClientRect();
    if (!dragMoved) {
      selected = nearest(event.clientX - bounds.left, event.clientY - bounds.top);
      updateDetails(selected);
    }
    dragging = false;
    canvas.releasePointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointerleave", () => {
    if (!dragging) {
      hovered = null;
      if (!selected) updateDetails(null);
    }
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    camera.zoom = Math.max(0.55, Math.min(2.1, camera.zoom * Math.exp(-event.deltaY * 0.001)));
  }, { passive: false });
  detailAction.addEventListener("click", async () => {
    const action = detailAction.dataset.action;
    if (action === "repair") {
      const invalidationId = detailAction.dataset.invalidationId;
      if (!invalidationId || !window.pywebview?.api) return;
      detailAction.setAttribute("aria-busy", "true");
      try {
        const brief = await window.pywebview.api.state_repair_brief(invalidationId);
        const context = String(brief?.context || "");
        if (!context) return;
        try {
          await navigator.clipboard.writeText(context);
        } catch {
          const field = document.createElement("textarea");
          field.value = context;
          field.setAttribute("readonly", "");
          field.style.position = "fixed";
          field.style.opacity = "0";
          document.body.appendChild(field);
          field.select();
          document.execCommand("copy");
          field.remove();
        }
        detailAction.textContent = "Repair brief copied";
        window.setTimeout(() => { detailAction.textContent = "Copy repair brief"; }, 1400);
      } finally {
        detailAction.removeAttribute("aria-busy");
      }
      return;
    }
    const memoryId = detailAction.dataset.memoryId;
    if (!memoryId || !window.pywebview?.api) return;
    detailAction.setAttribute("aria-busy", "true");
    try {
      await window.pywebview.api.set_memory_paused(
        memoryId,
        detailAction.dataset.paused === "true",
      );
      await loadGraph(`memory:${memoryId}`);
    } finally {
      detailAction.removeAttribute("aria-busy");
    }
  });
  window.addEventListener("keydown", (event) => {
    if (panel.hidden) return;
    if (event.key === "Escape") close();
    if (event.key === "ArrowLeft") camera.yaw -= 0.08;
    if (event.key === "ArrowRight") camera.yaw += 0.08;
    if (event.key === "ArrowUp") camera.pitch -= 0.08;
    if (event.key === "ArrowDown") camera.pitch += 0.08;
  });
  window.addEventListener("lians-control-changed", (event) => {
    if (graph) graph.control = event.detail;
  });
  new ResizeObserver(draw).observe(canvas);
  new MutationObserver(draw).observe(root, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  root.dataset.workGraphReady = "true";
})();
