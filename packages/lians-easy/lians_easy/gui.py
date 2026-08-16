"""Guided desktop setup for people who should never need a terminal."""

from __future__ import annotations

import sys
import threading
import tkinter as tk
import webbrowser
from importlib.resources import files
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .installer import (
    ClientTarget,
    client_targets,
    install,
    user_data_dir,
    write_support_report,
)
from .lifeline import format_count, lifeline_snapshot

BACKGROUND = "#05070b"
PANEL = "#0b1019"
PANEL_SOFT = "#111827"
LINE = "#273247"
TEXT = "#f4f7fb"
MUTED = "#9ba8ba"
BLUE = "#3777ff"
BLUE_SOFT = "#13244a"
GREEN = "#4fe0a0"
RED = "#ff6d83"


class SetupApp:
    """A small consumer setup flow with progressive technical disclosure."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.targets = client_targets()
        self.choices: dict[str, tk.BooleanVar] = {}
        self.step_labels: dict[str, tk.Label] = {}
        self.other_rows: list[tk.Widget] = []
        self.connected_labels: list[str] = []
        self.retry_clients: list[str] = []
        self.last_result: dict[str, Any] | None = None
        self.details_visible = False
        self.other_visible = False
        self.open_requested = False

        self.root.title("Lians AI Efficiency")
        self.root.geometry("780x720")
        self.root.minsize(680, 620)
        self.root.configure(background=BACKGROUND)
        self.root.option_add("*Font", ("Segoe UI", 10))

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Lians.Horizontal.TProgressbar",
            troughcolor=PANEL_SOFT,
            background=BLUE,
            bordercolor=PANEL_SOFT,
            lightcolor=BLUE,
            darkcolor=BLUE,
        )

        self.shell = tk.Frame(root, background=BACKGROUND, padx=32, pady=20)
        self.shell.pack(fill="both", expand=True)
        self._build_setup()

    def _label(self, parent: tk.Widget, text: str = "", **kwargs: Any) -> tk.Label:
        return tk.Label(parent, text=text, background=kwargs.pop("background", BACKGROUND), **kwargs)

    def _build_setup(self) -> None:
        for child in self.shell.winfo_children():
            child.destroy()

        top = tk.Frame(self.shell, background=BACKGROUND)
        top.pack(fill="x")
        self._label(
            top,
            "LIANS",
            foreground=BLUE,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")
        self._label(
            top,
            "PRIVATE ON THIS DEVICE",
            foreground=GREEN,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right")

        self._label(
            self.shell,
            "Use less context. Get more AI.",
            foreground=TEXT,
            font=("Segoe UI", 22, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(16, 6))
        self._label(
            self.shell,
            (
                "Lians gives each new task only the useful context it needs, so your AI apps "
                "do not have to reread everything. Keep using them normally. No AI account "
                "password or API key required."
            ),
            foreground=MUTED,
            font=("Segoe UI", 11),
            justify="left",
            wraplength=700,
            anchor="w",
        ).pack(fill="x", pady=(0, 16))

        self.card = tk.Frame(
            self.shell,
            background=PANEL,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=20,
            pady=16,
        )
        self.card.pack(fill="both", expand=True)

        detected = [target for target in self.targets.values() if target.detected]
        detected_text = (
            f"We found {len(detected)} AI app{'s' if len(detected) != 1 else ''}"
            if detected
            else "Choose the AI apps you want to optimize"
        )
        self._label(
            self.card,
            detected_text,
            background=PANEL,
            foreground=TEXT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).pack(fill="x")
        self._label(
            self.card,
            "Connect one or more. Lians works quietly in the apps you already use.",
            background=PANEL,
            foreground=MUTED,
            anchor="w",
        ).pack(fill="x", pady=(2, 10))

        app_list = tk.Frame(self.card, background=PANEL)
        app_list.pack(fill="x")
        app_columns = [
            tk.Frame(app_list, background=PANEL),
            tk.Frame(app_list, background=PANEL),
        ]
        app_columns[0].pack(side="left", fill="x", expand=True, padx=(0, 14))
        app_columns[1].pack(side="left", fill="x", expand=True, padx=(14, 0))
        for index, target in enumerate(self.targets.values()):
            variable = tk.BooleanVar(value=target.detected or target.configured)
            self.choices[target.key] = variable
            row = self._client_row(app_columns[index % 2], target, variable)
            if detected and not target.detected and not target.configured:
                row.pack_forget()
                self.other_rows.append(row)

        if self.other_rows:
            self.other_button = tk.Button(
                self.card,
                text="+ Add another AI app",
                command=self._toggle_other_apps,
                background=PANEL,
                foreground=BLUE,
                activebackground=PANEL,
                activeforeground=TEXT,
                relief="flat",
                borderwidth=0,
                cursor="hand2",
                anchor="w",
                padx=0,
                pady=4,
            )
            self.other_button.pack(fill="x", pady=(2, 0))

        trust = tk.Frame(self.card, background=PANEL_SOFT, padx=12, pady=8)
        trust.pack(fill="x", pady=(8, 8))
        self._label(
            trust,
            (
                "✓  No Claude, Cursor, or Codex password is requested\n"
                "✓  Saved context is encrypted; existing settings are backed up\n"
                "✓  Only a small, relevant context pack is added to a task"
            ),
            background=PANEL_SOFT,
            foreground=MUTED,
            font=("Segoe UI", 9),
            justify="left",
            anchor="w",
        ).pack(fill="x")

        self.progress_frame = tk.Frame(self.card, background=PANEL)
        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            style="Lians.Horizontal.TProgressbar",
            maximum=100,
            value=0,
        )
        self.progress_bar.pack(fill="x", pady=(0, 12))
        steps = (
            ("protecting", "Protect your existing settings"),
            ("connecting", "Optimize your AI apps"),
            ("verifying", "Check that Lians is ready"),
        )
        for key, copy in steps:
            label = self._label(
                self.progress_frame,
                f"○  {copy}",
                background=PANEL,
                foreground=MUTED,
                anchor="w",
            )
            label.pack(fill="x", pady=2)
            self.step_labels[key] = label

        self.status = tk.StringVar(value="Ready to optimize")
        self.status_label = self._label(
            self.card,
            textvariable=self.status,
            background=PANEL,
            foreground=MUTED,
            anchor="w",
        )
        self.status_label.pack(fill="x", pady=(0, 4))

        actions = tk.Frame(self.card, background=PANEL)
        actions.pack(fill="x")
        self.install_button = tk.Button(
            actions,
            text="Optimize my AI apps",
            command=self._start_install,
            background=BLUE,
            foreground="white",
            activebackground="#2e67de",
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
            padx=24,
            pady=8,
        )
        self.install_button.pack(side="left")
        tk.Button(
            actions,
            text="Technical details",
            command=self._toggle_details,
            background=PANEL,
            foreground=MUTED,
            activebackground=PANEL,
            activeforeground=TEXT,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=18,
            pady=8,
        ).pack(side="left")
        tk.Button(
            actions,
            text="Save help report",
            command=self._save_support_report,
            background=PANEL,
            foreground=MUTED,
            activebackground=PANEL,
            activeforeground=TEXT,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=8,
            pady=8,
        ).pack(side="right")

        self.details = self._label(
            self.card,
            self._technical_details(),
            background=PANEL_SOFT,
            foreground=MUTED,
            justify="left",
            wraplength=620,
            anchor="w",
            padx=14,
            pady=12,
        )

    def _client_row(
        self, parent: tk.Widget, target: ClientTarget, variable: tk.BooleanVar
    ) -> tk.Frame:
        row = tk.Frame(parent, background=PANEL)
        row.pack(fill="x")
        check = tk.Checkbutton(
            row,
            text=target.label,
            variable=variable,
            background=PANEL,
            foreground=TEXT,
            activebackground=PANEL,
            activeforeground=TEXT,
            selectcolor=BLUE_SOFT,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            cursor="hand2",
        )
        check.pack(side="left")
        state = "Connected" if target.configured else "Found" if target.detected else "Not found yet"
        color = GREEN if target.configured else BLUE if target.detected else MUTED
        self._label(
            row,
            state,
            background=PANEL,
            foreground=color,
            font=("Segoe UI", 9),
        ).pack(side="right")
        return row

    def _toggle_other_apps(self) -> None:
        self.other_visible = not self.other_visible
        for row in self.other_rows:
            if self.other_visible:
                row.pack(fill="x")
            else:
                row.pack_forget()
        self.other_button.configure(
            text="Hide other apps" if self.other_visible else "+ Add another AI app"
        )
        if self.other_visible:
            self.root.update_idletasks()
            requested_height = self.shell.winfo_reqheight()
            current_height = self.root.winfo_height()
            available_height = max(current_height, self.root.winfo_screenheight() - 40)
            if requested_height > current_height:
                self.root.geometry(
                    f"{self.root.winfo_width()}x{min(requested_height, available_height)}"
                )

    def _toggle_details(self) -> None:
        self.details_visible = not self.details_visible
        if self.details_visible:
            self.details.pack(fill="x", pady=(14, 0))
        else:
            self.details.pack_forget()

    def _technical_details(self) -> str:
        paths = [
            f"{target.label}: {target.config_path}"
            for target in self.targets.values()
            if target.detected or target.configured
        ]
        detected = "\n".join(paths) if paths else "No supported app settings found yet."
        return (
            "What setup changes\n"
            "Lians adds a local context connection only to the apps you select. It does not "
            "ask for an AI account password, install Git, build tools, or a model. Existing "
            "files are backed up first.\n\n"
            f"Encrypted memory: {user_data_dir() / 'memory.sqlite3'}\n"
            f"Detected settings:\n{detected}"
        )

    def _start_install(self, selected_override: list[str] | None = None) -> None:
        selected = selected_override or [
            key for key, value in self.choices.items() if value.get()
        ]
        if not selected:
            self.status.set("Choose at least one AI app to continue.")
            return

        self.install_button.configure(state="disabled", text="Optimizing...")
        self.progress_frame.pack(fill="x", pady=(0, 10), before=self.status_label)
        self._update_progress("protecting", "Protecting your existing settings")

        def worker() -> None:
            try:
                result = install(selected, on_progress=self._queue_progress)
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                self.root.after(0, self._show_error, str(error))
                return
            if result["status"] == "installed":
                self.root.after(0, self._show_success, result)
            else:
                self.root.after(0, self._show_partial, result)

        threading.Thread(target=worker, daemon=True).start()

    def _queue_progress(self, stage: str, detail: str) -> None:
        self.root.after(0, lambda: self._update_progress(stage, detail))

    def _update_progress(self, stage: str, detail: str) -> None:
        order = ("protecting", "connecting", "verifying")
        values = {
            "protecting": 18,
            "connecting": 55,
            "verifying": 84,
            "complete": 100,
            "partial": 100,
        }
        if stage == "partial":
            for key in ("protecting", "connecting"):
                label = self.step_labels[key]
                label.configure(text=f"●  {label.cget('text')[3:]}", foreground=GREEN)
            verification = self.step_labels["verifying"]
            verification.configure(
                text=f"●  {verification.cget('text')[3:]}", foreground=RED
            )
            self.progress_bar.configure(value=values[stage])
            self.status.set(detail)
            return
        current = order.index(stage) if stage in order else len(order)
        for index, key in enumerate(order):
            label = self.step_labels[key]
            copy = label.cget("text")[3:]
            if index < current or stage == "complete":
                label.configure(text=f"●  {copy}", foreground=GREEN)
            elif index == current:
                label.configure(text=f"●  {copy}", foreground=BLUE)
            else:
                label.configure(text=f"○  {copy}", foreground=MUTED)
        self.progress_bar.configure(value=values.get(stage, 0))
        self.status.set(detail)

    def _show_error(self, detail: str) -> None:
        self.last_result = {"status": "error", "clients": [], "retry_clients": []}
        self.status.set("Setup could not start. No AI app settings were changed.")
        self.install_button.configure(
            state="normal", text="Try again", command=self._start_install
        )
        self.details.configure(
            text=f"Setup report\n{detail}\n\n{self._technical_details()}", foreground=RED
        )
        if not self.details_visible:
            self._toggle_details()

    def _record_installed(self, result: dict[str, Any]) -> None:
        for item in result["clients"]:
            if item["status"] == "installed" and item["label"] not in self.connected_labels:
                self.connected_labels.append(item["label"])

    def _show_partial(self, result: dict[str, Any]) -> None:
        self.last_result = result
        self._record_installed(result)
        failed = [item for item in result["clients"] if item["status"] == "failed"]
        self.retry_clients = result["retry_clients"]
        failed_labels = ", ".join(item["label"] for item in failed)
        connected_count = len(self.connected_labels)
        was_or_were = "was" if len(failed) == 1 else "were"
        needs_or_need = "needs" if len(failed) == 1 else "need"
        self.status.set(
            f"{connected_count} connected. {failed_labels} {was_or_were} restored and "
            f"{needs_or_need} another try."
        )
        if self.retry_clients:
            self.install_button.configure(
                state="normal",
                text=f"Retry {failed_labels}",
                command=lambda: self._start_install(self.retry_clients),
            )
        else:
            self.install_button.configure(state="disabled", text="See technical details")
        reports = "\n".join(
            f"{item['label']}: {item['error']}"
            + ("\nOriginal settings restored." if item["rolled_back"] else "")
            for item in failed
        )
        self.details.configure(
            text=f"Setup report\n{reports}\n\n{self._technical_details()}", foreground=RED
        )
        if not self.details_visible:
            self._toggle_details()

    def _show_success(self, result: dict[str, Any]) -> None:
        self.last_result = result
        self._record_installed(result)
        for child in self.card.winfo_children():
            child.destroy()

        connected = ", ".join(self.connected_labels)
        self._label(
            self.card,
            "Your AI apps are optimized.",
            background=PANEL,
            foreground=GREEN,
            font=("Segoe UI", 22, "bold"),
            anchor="w",
        ).pack(fill="x")
        self._label(
            self.card,
            f"Lians is active in {connected}. Keep using the apps normally.",
            background=PANEL,
            foreground=TEXT,
            font=("Segoe UI", 11),
            anchor="w",
            wraplength=620,
        ).pack(fill="x", pady=(8, 22))

        try_card = tk.Frame(self.card, background=BLUE_SOFT, padx=18, pady=16)
        try_card.pack(fill="x")
        self._label(
            try_card,
            "Try it in two chats",
            background=BLUE_SOFT,
            foreground=TEXT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).pack(fill="x")
        self._label(
            try_card,
            (
                "1. Restart the connected AI apps.\n"
                "2. Say: Remember that this project uses FastAPI and pytest.\n"
                "3. Start a new chat in the same or another app and ask: What does this project use?"
            ),
            background=BLUE_SOFT,
            foreground=MUTED,
            justify="left",
            wraplength=590,
            anchor="w",
        ).pack(fill="x", pady=(10, 14))
        tk.Button(
            try_card,
            text="Copy the first prompt",
            command=lambda: self._copy(
                "Remember that this project uses FastAPI and pytest."
            ),
            background=BLUE,
            foreground="white",
            activebackground="#2e67de",
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=16,
            pady=8,
        ).pack(anchor="w")

        self._label(
            self.card,
            (
                "Lians adds only the saved details relevant to the new task. Its receipt shows "
                "what was reused, what was left out, and the estimated context size."
            ),
            background=PANEL,
            foreground=MUTED,
            justify="left",
            wraplength=620,
            anchor="w",
        ).pack(fill="x", pady=(20, 20))
        tk.Button(
            self.card,
            text="Open Lians",
            command=self._open_lians,
            background=BLUE,
            foreground="white",
            activebackground="#2e67de",
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=("Segoe UI", 11, "bold"),
            padx=24,
            pady=10,
        ).pack(anchor="w")

        tk.Button(
            self.card,
            text="Save help report",
            command=self._save_support_report,
            background=PANEL,
            foreground=MUTED,
            activebackground=PANEL,
            activeforeground=TEXT,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=0,
            pady=10,
        ).pack(anchor="w")

    def _save_support_report(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save Lians help report",
            initialfile="Lians-help-report.json",
            defaultextension=".json",
            filetypes=(("JSON report", "*.json"), ("All files", "*.*")),
        )
        if not destination:
            return
        try:
            path = write_support_report(Path(destination), setup_result=self.last_result)
        except OSError:
            self.status.set("The help report could not be saved. Choose another folder.")
            return
        self.status.set(f"Help report saved as {path.name}.")

    def _copy(self, value: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(value)

    def _open_lians(self) -> None:
        self.open_requested = True
        self.root.destroy()


class CompanionApp:
    """Resident local dashboard shown while AI clients use Lians."""

    def __init__(self, root: tk.Tk, bridge: Any, *, start_bridge: bool = True) -> None:
        self.root = root
        self.bridge = bridge
        self._stopping = False
        self._bridge_error: str | None = None
        self._bridge_thread: threading.Thread | None = None
        self.target_labels: dict[str, tk.Label] = {}
        self.activity_labels: list[tk.Label] = []
        self._activity_state: tuple[tuple[str, str, str], ...] | None = None

        self.root.title("Lians")
        self.root.geometry("920x760")
        self.root.minsize(820, 700)
        self.root.configure(background=BACKGROUND)
        self.root.option_add("*Font", ("Segoe UI", 10))
        self.root.protocol("WM_DELETE_WINDOW", self._minimize)
        if sys.platform == "win32":
            try:
                self.root.iconbitmap(default=sys.executable)
            except tk.TclError:
                pass

        self.shell = tk.Frame(root, background=BACKGROUND, padx=34, pady=24)
        self.shell.pack(fill="both", expand=True)
        self._build()
        if start_bridge:
            self._start_bridge()
        self.root.after(200, self._refresh)

    def _label(self, parent: tk.Widget, text: str = "", **kwargs: Any) -> tk.Label:
        return tk.Label(parent, text=text, background=kwargs.pop("background", BACKGROUND), **kwargs)

    def _build(self) -> None:
        top = tk.Frame(self.shell, background=BACKGROUND)
        top.pack(fill="x")
        try:
            logo_path = files("lians_easy").joinpath("app", "logo-blue.png")
            self.logo = tk.PhotoImage(file=str(logo_path))
            width = self.logo.width()
            if width > 142:
                factor = max(1, round(width / 142))
                self.logo = self.logo.subsample(factor, factor)
            tk.Label(top, image=self.logo, background=BACKGROUND).pack(side="left")
        except (OSError, tk.TclError):
            self._label(
                top, "lians", foreground=BLUE, font=("Segoe UI", 24, "bold")
            ).pack(side="left")

        status_card = tk.Frame(
            top,
            background=PANEL,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=12,
            pady=7,
        )
        status_card.pack(side="right")
        self.status_dot = self._label(
            status_card, "●", background=PANEL, foreground=BLUE, font=("Segoe UI", 11, "bold")
        )
        self.status_dot.pack(side="left", padx=(0, 7))
        self.status = tk.StringVar(value="Starting the encrypted local memory bridge")
        self._label(
            status_card,
            textvariable=self.status,
            background=PANEL,
            foreground=TEXT,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")

        self._label(
            self.shell,
            "Your agent lifeline.",
            foreground=TEXT,
            font=("Segoe UI", 26, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(24, 4))
        self._label(
            self.shell,
            "See how Lians supports Claude, Codex, and Cursor while everything stays local.",
            foreground=MUTED,
            font=("Segoe UI", 11),
            anchor="w",
        ).pack(fill="x", pady=(0, 16))

        self.token_value = tk.StringVar(value="0")
        self.event_value = tk.StringVar(value="0")
        self.reuse_value = tk.StringVar(value="0")
        metrics = tk.Frame(self.shell, background=BACKGROUND)
        metrics.pack(fill="x")
        metric_data = (
            ("Estimated tokens saved", self.token_value, "Repeated context Lians left out"),
            ("Context handoffs", self.event_value, "Times an agent asked Lians for context"),
            ("Memories reused", self.reuse_value, "Useful details carried into new work"),
        )
        for index, (title, value, detail) in enumerate(metric_data):
            card = tk.Frame(
                metrics,
                background=PANEL,
                highlightbackground=LINE,
                highlightthickness=1,
                padx=16,
                pady=12,
            )
            card.pack(
                side="left",
                fill="both",
                expand=True,
                padx=(0 if index == 0 else 6, 0 if index == 2 else 6),
            )
            self._label(
                card,
                title,
                background=PANEL,
                foreground=MUTED,
                font=("Segoe UI", 9),
                anchor="w",
            ).pack(fill="x")
            self._label(
                card,
                textvariable=value,
                background=PANEL,
                foreground=TEXT,
                font=("Segoe UI", 22, "bold"),
                anchor="w",
            ).pack(fill="x", pady=(2, 1))
            self._label(
                card,
                detail,
                background=PANEL,
                foreground=MUTED,
                font=("Segoe UI", 8),
                anchor="w",
            ).pack(fill="x")

        activity_heading = tk.Frame(self.shell, background=BACKGROUND)
        activity_heading.pack(fill="x", pady=(18, 8))
        self._label(
            activity_heading,
            "Recent agent activity",
            foreground=TEXT,
            font=("Segoe UI", 13, "bold"),
        ).pack(side="left")
        self.reduction_status = tk.StringVar(value="Waiting for the first context handoff")
        self._label(
            activity_heading,
            textvariable=self.reduction_status,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="right")

        self.activity_frame = tk.Frame(
            self.shell,
            background=PANEL,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=14,
            pady=8,
        )
        self.activity_frame.pack(fill="both", expand=True)
        self._show_activity([])

        integrations = tk.Frame(self.shell, background=BACKGROUND)
        integrations.pack(fill="x", pady=(12, 12))
        self._label(
            integrations, "Connections", foreground=MUTED, font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=(0, 12))
        targets = client_targets()
        for key in ("claude", "codex", "cursor"):
            target = targets[key]
            label = "Claude" if key == "claude" else target.label
            state = self._label(
                integrations,
                f"●  {label}",
                foreground=GREEN if target.configured else MUTED,
                font=("Segoe UI", 9, "bold"),
            )
            state.pack(side="left", padx=(0, 16))
            self.target_labels[key] = state
        self.memory_status = tk.StringVar(value="0 saved memories")
        self._label(
            integrations,
            textvariable=self.memory_status,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="right")

        actions = tk.Frame(self.shell, background=BACKGROUND)
        actions.pack(fill="x")
        self.open_button = tk.Button(
            actions,
            text="Open detailed dashboard",
            command=self._open_dashboard,
            state="disabled",
            background=BLUE,
            foreground="white",
            activebackground="#2e67de",
            activeforeground="white",
            disabledforeground="#8390a4",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
            padx=20,
            pady=9,
        )
        self.open_button.pack(side="left")
        tk.Button(
            actions,
            text="Minimize and use my AI",
            command=self._minimize,
            background=PANEL_SOFT,
            foreground=TEXT,
            activebackground=PANEL,
            activeforeground=TEXT,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=18,
            pady=9,
        ).pack(side="left", padx=(10, 0))
        tk.Button(
            actions,
            text="Stop Lians",
            command=self._stop,
            background=BACKGROUND,
            foreground=MUTED,
            activebackground=BACKGROUND,
            activeforeground=TEXT,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=12,
            pady=9,
        ).pack(side="right")

        self.notice = tk.StringVar(
            value="Closing this window minimizes Lians. Token savings are estimates from local context receipts."
        )
        self._label(
            self.shell,
            textvariable=self.notice,
            foreground=MUTED,
            justify="left",
            wraplength=840,
            anchor="w",
            font=("Segoe UI", 8),
        ).pack(fill="x", pady=(9, 0))

    def _show_activity(self, activity: list[dict[str, Any]]) -> None:
        state = tuple((item["title"], item["time"], item["detail"]) for item in activity)
        if state == self._activity_state:
            return
        self._activity_state = state
        for child in self.activity_frame.winfo_children():
            child.destroy()
        self.activity_labels = []
        if not activity:
            empty = self._label(
                self.activity_frame,
                "No activity yet. Use a connected AI app and your lifeline will appear here.",
                background=PANEL,
                foreground=MUTED,
                font=("Segoe UI", 10),
                anchor="w",
            )
            empty.pack(fill="both", expand=True, pady=16)
            self.activity_labels.append(empty)
            return
        for index, item in enumerate(activity):
            row = tk.Frame(self.activity_frame, background=PANEL, pady=5)
            row.pack(fill="x")
            heading = self._label(
                row,
                item["title"],
                background=PANEL,
                foreground=TEXT,
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            )
            heading.pack(side="left")
            self._label(
                row,
                item["time"],
                background=PANEL,
                foreground=MUTED,
                font=("Segoe UI", 8),
            ).pack(side="right")
            detail = self._label(
                self.activity_frame,
                item["detail"],
                background=PANEL,
                foreground=MUTED,
                font=("Segoe UI", 8),
                anchor="w",
            )
            detail.pack(fill="x", pady=(0, 4))
            self.activity_labels.extend((heading, detail))
            if index < len(activity) - 1:
                tk.Frame(self.activity_frame, background=LINE, height=1).pack(fill="x")

    def _start_bridge(self) -> None:
        def serve() -> None:
            try:
                self.bridge.serve()
            except (OSError, RuntimeError) as error:
                self._bridge_error = str(error)

        self._bridge_thread = threading.Thread(target=serve, daemon=True)
        self._bridge_thread.start()

    def _refresh(self) -> None:
        if self._stopping:
            return
        if self._bridge_error:
            self.status.set("Lians could not start")
            self.status_dot.configure(foreground=RED)
            self.notice.set(self._bridge_error)
            self.open_button.configure(state="disabled")
        elif self.bridge.running:
            self.status.set("Lians is running in the background")
            self.status_dot.configure(foreground=GREEN)
            self.open_button.configure(state="normal")
            try:
                snapshot = lifeline_snapshot(self.bridge.store, limit=3)
                count = snapshot["saved_memories"]
                self.memory_status.set(
                    f"{count} saved memor{'y' if count == 1 else 'ies'}"
                )
                self.token_value.set(
                    f"~{format_count(snapshot['repeated_tokens_avoided_estimate'])}"
                )
                self.event_value.set(format_count(snapshot["context_events"]))
                self.reuse_value.set(format_count(snapshot["memories_reused"]))
                events = snapshot["context_events"]
                if events:
                    self.reduction_status.set(
                        f"About {snapshot['reduction_percent_estimate']}% less repeated context"
                    )
                else:
                    self.reduction_status.set("Waiting for the first context handoff")
                self._show_activity(snapshot["activity"])
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                self.memory_status.set("Encrypted memory ready")
        for key, label in self.target_labels.items():
            target = client_targets()[key]
            configured = target.configured
            name = "Claude" if key == "claude" else target.label
            label.configure(
                text=f"●  {name}",
                foreground=GREEN if configured else MUTED,
            )
        self.root.after(2000, self._refresh)

    def _open_dashboard(self) -> None:
        if self.bridge.running:
            webbrowser.open(self.bridge.origin)

    def _minimize(self) -> None:
        self.notice.set("Lians is still running. Open it from the Windows taskbar when needed.")
        self.root.iconify()

    def _stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self.status.set("Stopping Lians")
        self.open_button.configure(state="disabled")

        def stop() -> None:
            self.bridge.shutdown()
            try:
                self.root.after(0, self.root.destroy)
            except RuntimeError:
                pass

        threading.Thread(target=stop, daemon=False).start()


def _running_bridge_origin() -> str | None:
    from .bridge import DEFAULT_HOST, DEFAULT_PORT

    origin = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
    try:
        with urlopen(origin, timeout=0.6) as response:
            server = response.headers.get("Server", "")
    except (OSError, URLError):
        return None
    return origin if server.startswith("LiansBridge/") else None


def _launch_companion() -> None:
    from .bridge import BridgeApplication
    from .mcp import default_data_path
    from .store import MemoryStore

    existing = _running_bridge_origin()
    if existing is not None:
        webbrowser.open(existing)
        return
    root = tk.Tk()
    CompanionApp(root, BridgeApplication(MemoryStore(default_data_path())))
    root.mainloop()


def launch() -> None:
    if any(target.configured for target in client_targets().values()):
        _launch_companion()
        return
    root = tk.Tk()
    if sys.platform == "win32":
        try:
            # The frozen executable carries the Lians lotus as its first icon
            # resource. Reuse it for the window and taskbar instead of Tk's
            # generic feather icon.
            root.iconbitmap(default=sys.executable)
        except tk.TclError:
            pass
    app = SetupApp(root)
    root.mainloop()
    if app.open_requested:
        _launch_companion()
