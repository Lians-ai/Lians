"""Guided desktop setup for people who should never need a terminal."""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from .installer import (
    ClientTarget,
    client_targets,
    install,
    user_data_dir,
    write_support_report,
)

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

        self.root.title("Lians Setup")
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
            "Your AI apps, one memory.",
            foreground=TEXT,
            font=("Segoe UI", 22, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(16, 6))
        self._label(
            self.shell,
            (
                "Lians carries your preferences and useful project context between the AI "
                "apps you already use. No account or API key required."
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
            else "Choose the AI apps you use"
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
            "Connect at least two to experience memory moving between them.",
            background=PANEL,
            foreground=MUTED,
            anchor="w",
        ).pack(fill="x", pady=(2, 10))

        app_list = tk.Frame(self.card, background=PANEL)
        app_list.pack(fill="x")
        for target in self.targets.values():
            variable = tk.BooleanVar(value=target.detected or target.configured)
            self.choices[target.key] = variable
            row = self._client_row(app_list, target, variable)
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
                "✓  Memory stays encrypted on this computer\n"
                "✓  Existing settings are backed up before Lians changes them\n"
                "✓  Pause, correct, or permanently forget a memory whenever you want"
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
            ("connecting", "Connect your AI apps"),
            ("verifying", "Check that memory is ready"),
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

        self.status = tk.StringVar(value="Ready to connect")
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
            text="Set up Lians",
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
            "Lians adds a local memory connection only to the apps you select. It does not "
            "install Git, Python, build tools, or a model. Existing files are backed up first.\n\n"
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

        self.install_button.configure(state="disabled", text="Setting up...")
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
            "Memory is ready.",
            background=PANEL,
            foreground=GREEN,
            font=("Segoe UI", 22, "bold"),
            anchor="w",
        ).pack(fill="x")
        self._label(
            self.card,
            f"Lians connected {connected}.",
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
            "Try the cross-app memory moment",
            background=BLUE_SOFT,
            foreground=TEXT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).pack(fill="x")
        self._label(
            try_card,
            (
                "1. Restart the connected AI apps.\n"
                "2. In one app, say: Remember that we use FastAPI and never write migrations manually.\n"
                "3. Start a new task in another app and ask: What are our project rules?"
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
                "Remember that we use FastAPI and never write migrations manually."
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
                "When a memory appears, Lians shows a small receipt with what was used, "
                "why it was selected, the project, and its estimated token cost."
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


def launch() -> None:
    if any(target.configured for target in client_targets().values()):
        from .bridge import BridgeApplication
        from .mcp import default_data_path
        from .store import MemoryStore

        BridgeApplication(MemoryStore(default_data_path())).serve(open_browser=True)
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
        from .bridge import BridgeApplication
        from .mcp import default_data_path
        from .store import MemoryStore

        BridgeApplication(MemoryStore(default_data_path())).serve(open_browser=True)
