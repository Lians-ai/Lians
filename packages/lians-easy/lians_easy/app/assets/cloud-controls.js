(() => {
  "use strict";

  const ID = "lians-cloud-controls";
  const STATUS_PATH = "/v1/cloud/status";
  const POLL_MS = 30_000;
  const ENROLLMENT_POLL_MS = 3_000;
  const MAX_RECOVERY_BACKUP_BYTES = 32 * 1024 * 1024;
  let deleteTimer = null;
  let enrollmentTimer = null;
  let busy = false;
  let currentStatus = { state: "loading", sync_state: "not_started" };
  let currentEnrollment = null;
  let recoveryBackup = "";
  let recoveryReview = null;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  async function request(path, method = "GET", payload = {}) {
    const options = {
      method,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    };
    if (method !== "GET") {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify({ confirmed: true, ...payload });
    }
    const response = await fetch(path, options);
    const document = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(document.error || "cloud-action-failed");
    return document;
  }

  function viewModel(status) {
    const state = status.state || "unavailable";
    const syncState = status.sync_state || "not_started";
    const hasWorkspace = syncState === "ready" || Number(status.head_revision || 0) > 0;
    if ((state === "connected" || state === "current" || state === "synced") && !hasWorkspace) {
      return {
        tone: "on",
        trigger: "CLOUD · SIGNED IN",
        status: "CHOOSE THIS DEVICE'S ROLE",
        title: "Start here or join your memory",
        description:
          "If this is your first device, start encrypted sync. If your memory already lives on another device, join it with a short matching code.",
        primary: "Start new cloud memory",
        primaryAction: "sync",
        secondary: "Sign out",
        danger: false,
      };
    }
    if (state === "connected" || state === "current" || state === "synced") {
      return {
        tone: "on",
        trigger: "CLOUD SYNC · ON",
        status: "ENCRYPTED SYNC ON",
        title: "Your memory follows you",
        description:
          "Lians checks for changes before an AI uses memory and saves corrections everywhere after you make them.",
        primary: "Sync now",
        primaryAction: "sync",
        secondary: "Turn off sync",
        danger: hasWorkspace,
      };
    }
    if (state === "refresh_required" || state === "needs_attention" || state === "pending") {
      return {
        tone: "attention",
        trigger: "SYNC · ACTION NEEDED",
        status: "LOCAL MEMORY IS SAFE",
        title: "Cloud sync needs attention",
        description:
          "Your memory is still available on this device. Reconnect to continue carrying changes between AI tools.",
        primary: "Reconnect",
        primaryAction: state === "refresh_required" ? "sync" : "signin",
        secondary: "Turn off sync",
        danger: hasWorkspace,
      };
    }
    if (state === "signed_out") {
      return {
        tone: "off",
        trigger: "CLOUD SYNC · OFF",
        status: "PRIVATE ON THIS DEVICE",
        title: "Take your memory to every AI",
        description:
          "Turn on encrypted sync with a normal browser sign-in. The Lians App never asks for or displays an API key.",
        primary: "Sign in to Lians Cloud",
        primaryAction: "signin",
        secondary: "",
        danger: false,
      };
    }
    if (state === "loading") {
      return {
        tone: "loading",
        trigger: "CHECKING MEMORY",
        status: "CHECKING",
        title: "Checking encrypted memory",
        description: "Lians is checking whether cloud continuity is available in this build.",
        primary: "",
        primaryAction: "",
        secondary: "",
        danger: false,
      };
    }
    return {
      tone: "local",
      trigger: "MEMORY · THIS DEVICE",
      status: "ENCRYPTED LOCAL",
      title: "Your memory stays on this device",
      description:
        "Local memory is ready and works without an account. Encrypted cloud continuity is not configured in this build.",
      primary: "",
      primaryAction: "",
      secondary: "",
      danger: false,
    };
  }

  function build() {
    const root = element("section", "lians-cloud");
    root.id = ID;

    const trigger = element("button", "lians-cloud__trigger");
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-controls", "lians-cloud-dialog");
    const triggerDot = element("span", "lians-cloud__dot");
    triggerDot.setAttribute("aria-hidden", "true");
    const triggerText = element("span", "lians-cloud__trigger-text", "CHECKING MEMORY");
    trigger.append(triggerDot, triggerText);

    const backdrop = element("div", "lians-cloud__backdrop");
    backdrop.hidden = true;
    const dialog = element("div", "lians-cloud__dialog");
    dialog.id = "lians-cloud-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "lians-cloud-title");
    dialog.tabIndex = -1;

    const top = element("div", "lians-cloud__top");
    const eyebrow = element("p", "lians-cloud__eyebrow", "LIANS CLOUD");
    const close = element("button", "lians-cloud__close", "Close");
    close.type = "button";
    close.setAttribute("aria-label", "Close cloud memory controls");
    top.append(eyebrow, close);

    const status = element("p", "lians-cloud__status", "CHECKING");
    const title = element("h2", "lians-cloud__title", "Checking encrypted memory");
    title.id = "lians-cloud-title";
    const description = element("p", "lians-cloud__description");

    const facts = element("div", "lians-cloud__facts");
    [
      ["01", "Encrypted before upload", "Cloud storage receives ciphertext, not your memory."],
      ["02", "Lians cannot read it", "The workspace key stays with devices you approve."],
      ["03", "Corrections travel", "Update or forget once and every connected AI uses it."],
    ].forEach(([number, heading, copy]) => {
      const fact = element("div", "lians-cloud__fact");
      fact.append(
        element("span", "lians-cloud__fact-number", number),
        element("strong", "lians-cloud__fact-title", heading),
        element("span", "lians-cloud__fact-copy", copy),
      );
      facts.append(fact);
    });

    const devices = element("section", "lians-cloud__devices");
    const devicesTop = element("div", "lians-cloud__devices-top");
    const devicesCopy = element("div");
    devicesCopy.append(
      element("strong", "lians-cloud__devices-title", "Connected devices"),
      element(
        "span",
        "lians-cloud__devices-copy",
        "Join without copying keys, files, workspace IDs, or terminal commands.",
      ),
    );
    const deviceCount = element("span", "lians-cloud__device-count", "1 DEVICE");
    devicesTop.append(devicesCopy, deviceCount);
    const deviceActions = element("div", "lians-cloud__device-actions");
    const join = element(
      "button",
      "lians-cloud__button lians-cloud__button--device",
      "Add this device",
    );
    join.type = "button";
    const review = element(
      "button",
      "lians-cloud__button lians-cloud__button--device",
      "Approve a device",
    );
    review.type = "button";
    const manage = element(
      "button",
      "lians-cloud__button lians-cloud__button--device",
      "Manage devices",
    );
    manage.type = "button";
    deviceActions.append(join, review, manage);

    const enrollment = element("div", "lians-cloud__enrollment");
    enrollment.hidden = true;
    const enrollmentLabel = element("p", "lians-cloud__enrollment-label");
    const enrollmentCode = element("strong", "lians-cloud__code");
    const enrollmentCopy = element("p", "lians-cloud__enrollment-copy");
    const enrollmentActions = element("div", "lians-cloud__enrollment-actions");
    const check = element("button", "lians-cloud__text-action", "Check approval");
    check.type = "button";
    const cancel = element("button", "lians-cloud__text-action", "Cancel request");
    cancel.type = "button";
    enrollmentActions.append(check, cancel);
    enrollment.append(enrollmentLabel, enrollmentCode, enrollmentCopy, enrollmentActions);

    const requestList = element("div", "lians-cloud__request-list");
    requestList.hidden = true;
    const deviceList = element("div", "lians-cloud__device-list");
    deviceList.hidden = true;
    devices.append(devicesTop, deviceActions, enrollment, requestList, deviceList);

    const recovery = element("div", "lians-cloud__recovery");
    const recoveryButton = element(
      "button",
      "lians-cloud__text-action lians-cloud__recovery-action",
      "Recover from encrypted backup",
    );
    recoveryButton.type = "button";
    const recoveryPanel = element("div", "lians-cloud__recovery-panel");
    recoveryPanel.hidden = true;
    const recoveryFileLabel = element("label", "", "Encrypted Lians backup");
    recoveryFileLabel.htmlFor = "lians-cloud-recovery-file";
    const recoveryFile = element("input", "lians-cloud__recovery-file");
    recoveryFile.id = "lians-cloud-recovery-file";
    recoveryFile.type = "file";
    recoveryFile.accept = ".liansbackup,application/vnd.lians.backup+json";
    const recoveryPassphraseLabel = element("label", "", "Backup passphrase");
    recoveryPassphraseLabel.htmlFor = "lians-cloud-recovery-passphrase";
    const recoveryPassphrase = element("input", "lians-cloud__recovery-passphrase");
    recoveryPassphrase.id = "lians-cloud-recovery-passphrase";
    recoveryPassphrase.type = "password";
    recoveryPassphrase.autocomplete = "current-password";
    const recoveryReviewButton = element(
      "button",
      "lians-cloud__button lians-cloud__button--device",
      "Review encrypted backup",
    );
    recoveryReviewButton.type = "button";
    const recoverySummary = element("div", "lians-cloud__recovery-summary");
    recoverySummary.hidden = true;
    const recoverySummaryText = element("p", "");
    const recoveryWarning = element(
      "p",
      "lians-cloud__managed-device-warning",
      "Recovery starts new encrypted cloud memory. An inaccessible old encrypted cloud copy may remain until account deletion.",
    );
    const recoveryConfirm = element(
      "button",
      "lians-cloud__button lians-cloud__button--approve",
      "Recover here & start new cloud memory",
    );
    recoveryConfirm.type = "button";
    const recoveryCancel = element("button", "lians-cloud__text-action", "Cancel recovery");
    recoveryCancel.type = "button";
    recoverySummary.append(
      recoverySummaryText,
      recoveryWarning,
      recoveryConfirm,
      recoveryCancel,
    );
    recoveryPanel.append(
      recoveryFileLabel,
      recoveryFile,
      recoveryPassphraseLabel,
      recoveryPassphrase,
      recoveryReviewButton,
      recoverySummary,
    );
    recovery.append(
      element("strong", "", "You control trust and recovery"),
      element(
        "span",
        "",
        "Removing a device gives every remaining device a new key. If every trusted device is lost, only your encrypted backup and its separate passphrase can restore memory. Lians cannot reset that encryption.",
      ),
      recoveryButton,
      recoveryPanel,
    );

    const metrics = element("p", "lians-cloud__metrics");
    const feedback = element("p", "lians-cloud__feedback");
    feedback.setAttribute("role", "status");
    feedback.setAttribute("aria-live", "polite");

    const actions = element("div", "lians-cloud__actions");
    const primary = element("button", "lians-cloud__button lians-cloud__button--primary");
    primary.type = "button";
    const secondary = element("button", "lians-cloud__button lians-cloud__button--secondary");
    secondary.type = "button";
    const danger = element("button", "lians-cloud__button lians-cloud__button--danger", "Delete cloud copy");
    danger.type = "button";
    actions.append(primary, secondary, danger);

    dialog.append(
      top,
      status,
      title,
      description,
      facts,
      devices,
      recovery,
      metrics,
      feedback,
      actions,
    );
    backdrop.append(dialog);
    root.append(trigger, backdrop);
    document.body.append(root);

    return {
      root,
      trigger,
      triggerText,
      backdrop,
      dialog,
      close,
      status,
      title,
      description,
      devices,
      deviceCount,
      join,
      review,
      manage,
      enrollment,
      enrollmentLabel,
      enrollmentCode,
      enrollmentCopy,
      check,
      cancel,
      requestList,
      deviceList,
      recoveryButton,
      recoveryPanel,
      recoveryFile,
      recoveryPassphrase,
      recoveryReviewButton,
      recoverySummary,
      recoverySummaryText,
      recoveryConfirm,
      recoveryCancel,
      metrics,
      feedback,
      primary,
      secondary,
      danger,
    };
  }

  const ui = build();

  function clearEnrollmentPoll() {
    if (enrollmentTimer !== null) window.clearTimeout(enrollmentTimer);
    enrollmentTimer = null;
  }

  function scheduleEnrollmentPoll() {
    clearEnrollmentPoll();
    if (!currentEnrollment || currentEnrollment.state !== "waiting_for_approval") return;
    enrollmentTimer = window.setTimeout(() => checkEnrollment(true), ENROLLMENT_POLL_MS);
  }

  function setOpen(open) {
    ui.backdrop.hidden = !open;
    document.documentElement.classList.toggle("lians-cloud-open", open);
    ui.trigger.setAttribute("aria-expanded", String(open));
    if (open) {
      ui.dialog.focus();
      refresh().then(() => restoreEnrollment());
    } else {
      clearEnrollmentPoll();
      clearRecovery();
      ui.recoveryPanel.hidden = true;
      if (window.location.hash === "#cloud-sync") {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      }
      ui.trigger.focus();
    }
  }

  function renderEnrollment(enrollment) {
    currentEnrollment = enrollment;
    const waiting = enrollment && enrollment.state === "waiting_for_approval";
    ui.enrollment.hidden = !waiting;
    if (waiting) {
      ui.enrollmentLabel.textContent = "MATCH THIS CODE ON A CONNECTED DEVICE";
      ui.enrollmentCode.textContent = enrollment.verification_code || "";
      ui.enrollmentCopy.textContent =
        "Leave Lians open here. On your connected device, choose Approve a device and approve only if both codes match.";
      scheduleEnrollmentPoll();
    } else {
      clearEnrollmentPoll();
    }
  }

  function render(status, feedback = "") {
    currentStatus = status;
    const model = viewModel(status);
    const connected = ["connected", "current", "synced"].includes(status.state);
    const ready = status.sync_state === "ready" || Number(status.head_revision || 0) > 0;
    ui.root.dataset.tone = model.tone;
    ui.triggerText.textContent = model.trigger;
    ui.status.textContent = model.status;
    ui.title.textContent = model.title;
    ui.description.textContent = model.description;
    ui.feedback.textContent = feedback;
    ui.primary.hidden = !model.primary;
    ui.primary.textContent = model.primary;
    ui.primary.dataset.action = model.primaryAction;
    ui.secondary.hidden = !model.secondary;
    ui.secondary.textContent = model.secondary;
    ui.danger.hidden = !model.danger;
    ui.danger.dataset.armed = "false";
    ui.danger.textContent = "Delete cloud copy";
    ui.devices.hidden = !connected;
    ui.join.hidden = !connected || ready;
    ui.review.hidden = !connected || !ready;
    ui.manage.hidden = !connected || !ready;
    const revision = Number(status.head_revision || 0);
    const devices = Math.max(1, Number(status.device_count || 1));
    ui.deviceCount.textContent = `${devices} ${devices === 1 ? "DEVICE" : "DEVICES"}`;
    ui.metrics.textContent = ready
      ? `${devices} ${devices === 1 ? "device" : "devices"} · encrypted revision ${revision}`
      : "Local memory stays available whether cloud sync is on or off.";
    [
      ui.primary,
      ui.secondary,
      ui.danger,
      ui.join,
      ui.review,
      ui.manage,
      ui.check,
      ui.cancel,
      ui.recoveryButton,
      ui.recoveryFile,
      ui.recoveryPassphrase,
      ui.recoveryReviewButton,
      ui.recoveryConfirm,
      ui.recoveryCancel,
    ].forEach((button) => {
      button.disabled = busy;
    });
    ui.recoveryButton.disabled = busy || !connected;
    ui.recoveryButton.textContent = connected
      ? "Recover from encrypted backup"
      : "Sign in to recover a backup";
  }

  async function refresh(feedback = "") {
    try {
      const status = await request(STATUS_PATH);
      render(status, feedback);
      return status;
    } catch {
      render(
        { state: "needs_attention", sync_state: currentStatus.sync_state },
        "Lians could not check cloud sync. Your local memory is still available.",
      );
      return currentStatus;
    }
  }

  async function restoreEnrollment() {
    const connected = ["connected", "current", "synced"].includes(currentStatus.state);
    const ready = currentStatus.sync_state === "ready" || Number(currentStatus.head_revision || 0) > 0;
    if (!connected || ready || busy) return;
    await checkEnrollment(true);
  }

  async function checkEnrollment(quiet = false) {
    if (busy) return;
    try {
      const result = await request("/v1/cloud/device-enrollment/check", "POST");
      if (result.state === "connected") {
        renderEnrollment(null);
        await refresh("This device is connected. Your encrypted memory is ready here.");
        return;
      }
      if (result.state === "waiting_for_approval") {
        renderEnrollment(result);
        if (!quiet) ui.feedback.textContent = "Still waiting for approval on your connected device.";
        return;
      }
      renderEnrollment(null);
      if (!quiet && result.state !== "not_requested") ui.feedback.textContent = result.message || "";
    } catch {
      if (!quiet) ui.feedback.textContent = "Lians could not check approval yet. This request is still safe.";
      scheduleEnrollmentPoll();
    }
  }

  function requestCard(item) {
    const card = element("article", "lians-cloud__request");
    const name = item.device && item.device.display_name ? item.device.display_name : "New device";
    card.append(
      element("span", "lians-cloud__request-label", "DEVICE REQUEST"),
      element("strong", "lians-cloud__request-name", name),
      element("span", "lians-cloud__request-code", item.verification_code || ""),
      element(
        "span",
        "lians-cloud__request-help",
        "Approve only if this exact code is visible on the new device.",
      ),
    );
    const approve = element(
      "button",
      "lians-cloud__button lians-cloud__button--approve",
      "Code matches · approve",
    );
    approve.type = "button";
    approve.addEventListener("click", async () => {
      if (busy) return;
      busy = true;
      render(currentStatus, `Approving ${name}…`);
      try {
        await request("/v1/cloud/device-requests/approve", "POST", {
          request_id: item.request_id,
          verification_code: item.verification_code,
        });
        await refresh(`${name} was approved. It can now finish connecting.`);
        card.remove();
        if (!ui.requestList.children.length) {
          ui.requestList.append(
            element("p", "lians-cloud__request-empty", "No other device is waiting."),
          );
        }
      } catch {
        await refresh("Lians could not approve that device. Nothing was connected.");
      } finally {
        busy = false;
        render(currentStatus, ui.feedback.textContent);
      }
    });
    card.append(approve);
    return card;
  }

  async function loadDeviceRequests() {
    if (busy) return;
    ui.deviceList.hidden = true;
    ui.requestList.hidden = false;
    ui.requestList.replaceChildren(
      element("p", "lians-cloud__request-empty", "Checking for new devices…"),
    );
    try {
      const result = await request("/v1/cloud/device-requests");
      const requests = Array.isArray(result.requests) ? result.requests : [];
      if (!requests.length) {
        ui.requestList.replaceChildren(
          element(
            "p",
            "lians-cloud__request-empty",
            "No device is waiting. On the new device, sign in and choose Add this device.",
          ),
        );
        return;
      }
      ui.requestList.replaceChildren(...requests.map(requestCard));
    } catch {
      ui.requestList.replaceChildren(
        element("p", "lians-cloud__request-empty", "Lians could not load device requests yet."),
      );
    }
  }

  function deviceCard(item) {
    const card = element("article", "lians-cloud__managed-device");
    const top = element("div", "lians-cloud__managed-device-top");
    const identity = element("div");
    const name = item.display_name || "Connected device";
    identity.append(
      element("strong", "lians-cloud__managed-device-name", name),
      element(
        "span",
        "lians-cloud__managed-device-state",
        item.current
          ? "THIS DEVICE · ACTIVE"
          : item.state === "active"
            ? "ACTIVE · RECEIVES FUTURE MEMORY"
            : "FUTURE SYNC BLOCKED · SIGNED RECEIPT",
      ),
    );
    top.append(identity);
    card.append(top);

    if (item.can_remove) {
      const warning = element(
        "p",
        "lians-cloud__managed-device-warning",
        "Removing rotates the memory key. Memory already saved on this device may remain there.",
      );
      const remove = element(
        "button",
        "lians-cloud__button lians-cloud__button--remove-device",
        "Protect future memory",
      );
      remove.type = "button";
      remove.dataset.armed = "false";
      remove.addEventListener("click", async () => {
        if (busy) return;
        if (remove.dataset.armed !== "true") {
          remove.dataset.armed = "true";
          remove.textContent = "Click again to remove & rotate key";
          ui.feedback.textContent =
            `${name} will stop receiving future memory. Data it already received cannot be remotely erased.`;
          window.setTimeout(() => {
            remove.dataset.armed = "false";
            remove.textContent = "Protect future memory";
          }, 8_000);
          return;
        }
        busy = true;
        render(currentStatus, `Rotating the memory key without ${name}…`);
        try {
          const result = await request("/v1/cloud/devices/remove", "POST", {
            device_id: item.device_id,
          });
          await refresh(result.message || `${name} cannot decrypt future cloud memory.`);
          busy = false;
          await loadDevices();
        } catch {
          await refresh(`Lians could not remove ${name}. The current key and device access were unchanged.`);
        } finally {
          busy = false;
          render(currentStatus, ui.feedback.textContent);
        }
      });
      card.append(warning, remove);
    } else if (item.state === "revoked") {
      card.append(
        element(
          "p",
          "lians-cloud__managed-device-warning",
          "This signed removal blocks decryption of future cloud memory. Previously received local memory may remain.",
        ),
      );
    }
    return card;
  }

  async function loadDevices() {
    if (busy) return;
    ui.requestList.hidden = true;
    ui.deviceList.hidden = false;
    ui.deviceList.replaceChildren(
      element("p", "lians-cloud__request-empty", "Verifying connected devices…"),
    );
    try {
      const result = await request("/v1/cloud/devices");
      const devices = Array.isArray(result.devices) ? result.devices : [];
      if (!devices.length) {
        ui.deviceList.replaceChildren(
          element("p", "lians-cloud__request-empty", result.message || "No active devices found."),
        );
        return;
      }
      ui.deviceList.replaceChildren(...devices.map(deviceCard));
    } catch {
      ui.deviceList.replaceChildren(
        element("p", "lians-cloud__request-empty", "Lians could not verify devices yet."),
      );
    }
  }

  function clearRecovery() {
    recoveryBackup = "";
    recoveryReview = null;
    ui.recoveryFile.value = "";
    ui.recoveryPassphrase.value = "";
    ui.recoverySummary.hidden = true;
    ui.recoverySummaryText.textContent = "";
  }

  async function encodeRecoveryBackup(file) {
    if (!file || file.size < 1) throw new Error("Choose an encrypted Lians backup.");
    if (file.size > MAX_RECOVERY_BACKUP_BYTES) {
      throw new Error("This backup is too large for the Lians App.");
    }
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("Lians could not read that backup."));
      reader.onload = () => {
        const encoded = String(reader.result || "");
        const separator = encoded.indexOf(",");
        if (separator < 0 || !encoded.slice(separator + 1)) {
          reject(new Error("The selected backup is invalid."));
          return;
        }
        resolve(encoded.slice(separator + 1));
      };
      reader.readAsDataURL(file);
    });
  }

  async function reviewRecovery() {
    if (busy) return;
    const file = ui.recoveryFile.files && ui.recoveryFile.files[0];
    const passphrase = ui.recoveryPassphrase.value;
    if (!file || !passphrase) {
      ui.feedback.textContent = "Choose your encrypted backup and enter its passphrase.";
      return;
    }
    busy = true;
    render(currentStatus, "Checking the encrypted backup before changing memory…");
    try {
      recoveryBackup = await encodeRecoveryBackup(file);
      recoveryReview = await request("/v1/backups/verify", "POST", {
        backup: recoveryBackup,
        passphrase,
      });
      ui.recoverySummaryText.textContent =
        `${Number(recoveryReview.memories || 0)} memories · ` +
        `${Number(recoveryReview.activity || 0)} activity records · ` +
        `${Number(recoveryReview.receipts || 0)} receipts verified`;
      ui.recoverySummary.hidden = false;
      ui.feedback.textContent = "Backup verified. Review the recovery boundary, then confirm.";
    } catch (error) {
      clearRecovery();
      ui.feedback.textContent =
        error instanceof Error ? error.message : "Lians could not verify that backup.";
    } finally {
      busy = false;
      render(currentStatus, ui.feedback.textContent);
    }
  }

  async function confirmRecovery() {
    if (busy || !recoveryBackup || !recoveryReview) return;
    busy = true;
    render(currentStatus, "Recovering memory locally before starting new encrypted sync…");
    try {
      const result = await request("/v1/backups/import", "POST", {
        backup: recoveryBackup,
        passphrase: ui.recoveryPassphrase.value,
        confirmed: true,
        recover_cloud: true,
      });
      const cloud = result.cloud_recovery || {};
      const message =
        cloud.message ||
        "Memory was recovered locally. Sign in or retry sync to carry it to your AI tools.";
      clearRecovery();
      ui.recoveryPanel.hidden = true;
      await refresh(message);
    } catch (error) {
      await refresh(
        error instanceof Error
          ? error.message
          : "Lians could not complete cloud recovery. Check local Memory before retrying.",
      );
    } finally {
      busy = false;
      render(currentStatus, ui.feedback.textContent);
    }
  }

  async function act(action) {
    if (busy) return;
    busy = true;
    render(currentStatus, action === "signin" ? "Finish signing in with your browser…" : "Working…");
    try {
      if (action === "signin") {
        await request("/v1/cloud/sign-in", "POST");
        await refresh("Signed in. Start new cloud memory or join the memory on another device.");
        await restoreEnrollment();
      } else if (action === "sync") {
        await request("/v1/cloud/sync", "POST");
        await refresh("Encrypted memory is up to date.");
      } else if (action === "signout") {
        await request("/v1/cloud/sign-out", "POST");
        renderEnrollment(null);
        await refresh("Sync is off. Local memory was preserved.");
      } else if (action === "delete") {
        await request("/v1/cloud/delete", "POST");
        renderEnrollment(null);
        await refresh("The cloud copy was deleted and sync was turned off. Local memory remains here.");
      } else if (action === "join") {
        const result = await request("/v1/cloud/device-enrollment/start", "POST");
        renderEnrollment(result);
        render(currentStatus, "Request created. Compare the code on a connected device.");
      } else if (action === "cancel-enrollment") {
        await request("/v1/cloud/device-enrollment/cancel", "POST");
        renderEnrollment(null);
        render(currentStatus, "The device request was cancelled. Nothing was connected.");
      }
    } catch {
      await refresh("Lians could not complete that action. Your local memory was not changed.");
    } finally {
      busy = false;
      render(currentStatus, ui.feedback.textContent);
    }
  }

  ui.trigger.addEventListener("click", () => {
    window.history.replaceState(null, "", "#cloud-sync");
    setOpen(true);
  });
  ui.close.addEventListener("click", () => setOpen(false));
  ui.backdrop.addEventListener("click", (event) => {
    if (event.target === ui.backdrop) setOpen(false);
  });
  ui.primary.addEventListener("click", () => act(ui.primary.dataset.action));
  ui.secondary.addEventListener("click", () => act("signout"));
  ui.join.addEventListener("click", () => act("join"));
  ui.review.addEventListener("click", loadDeviceRequests);
  ui.manage.addEventListener("click", loadDevices);
  ui.recoveryButton.addEventListener("click", () => {
    if (!ui.recoveryPanel.hidden) {
      clearRecovery();
      ui.recoveryPanel.hidden = true;
      ui.feedback.textContent = "Recovery form closed. Nothing was replaced.";
      return;
    }
    ui.recoveryPanel.hidden = false;
    ui.requestList.hidden = true;
    ui.deviceList.hidden = true;
    ui.recoveryFile.focus();
  });
  ui.recoveryFile.addEventListener("change", () => {
    recoveryBackup = "";
    recoveryReview = null;
    ui.recoverySummary.hidden = true;
  });
  ui.recoveryPassphrase.addEventListener("input", () => {
    recoveryBackup = "";
    recoveryReview = null;
    ui.recoverySummary.hidden = true;
  });
  ui.recoveryReviewButton.addEventListener("click", reviewRecovery);
  ui.recoveryConfirm.addEventListener("click", confirmRecovery);
  ui.recoveryCancel.addEventListener("click", () => {
    clearRecovery();
    ui.recoveryPanel.hidden = true;
    ui.feedback.textContent = "Recovery cancelled. Nothing was replaced.";
  });
  ui.check.addEventListener("click", () => checkEnrollment(false));
  ui.cancel.addEventListener("click", () => act("cancel-enrollment"));
  ui.danger.addEventListener("click", () => {
    if (ui.danger.dataset.armed === "true") {
      clearTimeout(deleteTimer);
      act("delete");
      return;
    }
    ui.danger.dataset.armed = "true";
    ui.danger.textContent = "Click again to delete cloud copy";
    ui.feedback.textContent = "This deletes encrypted cloud data and turns sync off. Local memory stays here.";
    deleteTimer = setTimeout(() => {
      ui.danger.dataset.armed = "false";
      ui.danger.textContent = "Delete cloud copy";
    }, 8_000);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !ui.backdrop.hidden) setOpen(false);
  });
  window.addEventListener("focus", () => refresh().then(() => restoreEnrollment()));
  window.addEventListener("hashchange", () => {
    if (window.location.hash === "#cloud-sync") setOpen(true);
  });
  window.setInterval(() => {
    if (document.visibilityState === "visible") refresh();
  }, POLL_MS);
  if (window.location.hash === "#cloud-sync") setOpen(true);
  refresh();
})();
