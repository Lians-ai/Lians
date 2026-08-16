"""Guided desktop setup for people who should never need a terminal."""

from __future__ import annotations

import ctypes
import json
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
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

COMPANION_THEMES = {
    "dark": {
        "background": "#05070A",
        "surface": "#0A1019",
        "surface_soft": "#101824",
        "border": "#1F2A3A",
        "text": "#F7F9FC",
        "muted": "#A4AFBF",
        "blue": "#3777FF",
        "blue_hover": "#2D68E5",
        "blue_soft": "#10224A",
        "green": "#50E3A4",
        "amber": "#F2BE5C",
        "red": "#FF7087",
    },
    "light": {
        "background": "#F5F7FB",
        "surface": "#FFFFFF",
        "surface_soft": "#EAF0F8",
        "border": "#D6DFEA",
        "text": "#07111F",
        "muted": "#566477",
        "blue": "#245FF5",
        "blue_hover": "#1B50D5",
        "blue_soft": "#E5EDFF",
        "green": "#087F58",
        "amber": "#9A6500",
        "red": "#C93651",
    },
}


def _register_sora(root: tk.Tk) -> str:
    """Load the bundled Sora font privately for this process on Windows."""

    if sys.platform == "win32":
        try:
            font_path = files("lians_easy").joinpath(
                "desktop", "fonts", "Sora-Variable.ttf"
            )
            loaded = ctypes.windll.gdi32.AddFontResourceExW(str(font_path), 0x10, None)
            if loaded:
                root.update_idletasks()
        except (AttributeError, OSError, tk.TclError):
            pass
    try:
        if "Sora" in tkfont.families(root):
            return "Sora"
    except tk.TclError:
        pass
    return "Segoe UI"


def _saved_companion_theme() -> str:
    try:
        value = json.loads((user_data_dir() / "ui-preferences.json").read_text(encoding="utf-8"))
        theme = value.get("theme")
        return theme if theme in COMPANION_THEMES else "dark"
    except (AttributeError, json.JSONDecodeError, OSError, TypeError):
        return "dark"


def _save_companion_theme(theme: str) -> None:
    try:
        destination = user_data_dir() / "ui-preferences.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({"theme": theme}), encoding="utf-8")
    except OSError:
        pass


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
    """Resident branded dashboard shown while AI clients use Lians."""

    def __init__(
        self,
        root: tk.Tk,
        bridge: Any,
        *,
        start_bridge: bool = True,
        owns_bridge: bool | None = None,
        theme: str | None = None,
    ) -> None:
        self.root = root
        self.bridge = bridge
        self.owns_bridge = start_bridge if owns_bridge is None else owns_bridge
        self._stopping = False
        self._bridge_error: str | None = None
        self._bridge_thread: threading.Thread | None = None
        self._refresh_job: str | None = None
        self._window_handle: int | None = None
        self._drag_origin: tuple[int, int, int, int] | None = None
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        window_width = min(1180, max(920, screen_width - 80))
        window_height = min(900, max(720, screen_height - 80))
        self._normal_geometry = f"{window_width}x{window_height}"
        self._maximized = False
        self._snap_state: str | None = None
        self.theme_name = theme if theme in COMPANION_THEMES else _saved_companion_theme()
        self.colors = COMPANION_THEMES[self.theme_name]
        self.font_family = _register_sora(root)
        self.target_labels: dict[str, tk.Label] = {}
        self.target_details: dict[str, tk.Label] = {}
        self.activity_labels: list[tk.Label] = []
        self._activity_state: tuple[tuple[str, str, str], ...] | None = None

        self.status = tk.StringVar(value="Starting Lians")
        self.memory_status = tk.StringVar(value="0 saved memories")
        self.token_value = tk.StringVar(value="0")
        self.event_value = tk.StringVar(value="0")
        self.reuse_value = tk.StringVar(value="0")
        self.reduction_status = tk.StringVar(value="Waiting for your first context handoff")
        self.notice = tk.StringVar(
            value="Token savings are estimates from encrypted local context receipts."
        )

        self.root.title("Lians")
        self.root.geometry(self._normal_geometry)
        self.root.minsize(920, 760)
        self.root.protocol("WM_DELETE_WINDOW", self._minimize)
        if sys.platform == "win32":
            self.root.overrideredirect(True)
            try:
                self.root.iconbitmap(default=sys.executable)
            except tk.TclError:
                pass

        self._load_brand_images()
        self._build()
        if sys.platform == "win32":
            self.root.after(50, self._apply_windows_taskbar_style)
        if start_bridge:
            self._start_bridge()
        self._refresh_job = self.root.after(200, self._refresh)

    def _font(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        return (self.font_family, size, weight)

    def _label(self, parent: tk.Widget, text: str = "", **kwargs: Any) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            background=kwargs.pop("background", self.colors["background"]),
            **kwargs,
        )

    def _load_brand_images(self) -> None:
        try:
            logo_path = files("lians_easy").joinpath("app", "logo-blue.png")
            self.logo = tk.PhotoImage(file=str(logo_path))
            if self.logo.width() > 150:
                factor = max(1, round(self.logo.width() / 150))
                self.logo = self.logo.subsample(factor, factor)
        except (OSError, tk.TclError):
            self.logo = None
        try:
            lotus_path = files("lians_easy").joinpath("desktop", "lotus.png")
            self.lotus = tk.PhotoImage(file=str(lotus_path))
            if self.lotus.width() > 32:
                factor = max(1, round(self.lotus.width() / 32))
                self.lotus = self.lotus.subsample(factor, factor)
        except (OSError, tk.TclError):
            self.lotus = None

    def _build(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()
        self.colors = COMPANION_THEMES[self.theme_name]
        self.target_labels = {}
        self.target_details = {}
        self._activity_state = None
        self.root.configure(background=self.colors["background"])
        self.root.option_add("*Font", self._font(11))
        self._build_titlebar()

        body = tk.Frame(self.root, background=self.colors["background"])
        body.pack(fill="both", expand=True)
        self.body_canvas = tk.Canvas(
            body,
            background=self.colors["background"],
            borderwidth=0,
            highlightthickness=0,
        )
        scrollbar = tk.Scrollbar(
            body,
            orient="vertical",
            command=self.body_canvas.yview,
            background=self.colors["surface_soft"],
            activebackground=self.colors["blue"],
            troughcolor=self.colors["background"],
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
            width=10,
        )
        self.body_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.body_canvas.pack(side="left", fill="both", expand=True)
        self.shell = tk.Frame(
            self.body_canvas,
            background=self.colors["background"],
            padx=44,
            pady=20,
        )
        canvas_window = self.body_canvas.create_window(
            (0, 0), window=self.shell, anchor="nw"
        )
        self.shell.bind(
            "<Configure>",
            lambda _event: self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all")),
        )
        self.body_canvas.bind(
            "<Configure>",
            lambda event: self.body_canvas.itemconfigure(canvas_window, width=event.width),
        )
        self.root.bind("<MouseWheel>", self._scroll_body)

        top = tk.Frame(self.shell, background=self.colors["background"])
        top.pack(fill="x")
        if self.logo is not None:
            tk.Label(top, image=self.logo, background=self.colors["background"]).pack(
                side="left"
            )
        else:
            self._label(
                top,
                "lians",
                foreground=self.colors["blue"],
                font=self._font(30, "bold"),
            ).pack(side="left")

        status_card = tk.Frame(
            top,
            background=self.colors["surface"],
            highlightbackground=self.colors["border"],
            highlightthickness=1,
            padx=15,
            pady=9,
        )
        status_card.pack(side="right")
        self.status_dot = self._label(
            status_card,
            "●",
            background=self.colors["surface"],
            foreground=self.colors["blue"],
            font=self._font(12, "bold"),
        )
        self.status_dot.pack(side="left", padx=(0, 8))
        self._label(
            status_card,
            textvariable=self.status,
            background=self.colors["surface"],
            foreground=self.colors["text"],
            font=self._font(11, "bold"),
        ).pack(side="left")

        self._label(
            self.shell,
            "Your agent lifeline.",
            foreground=self.colors["text"],
            font=self._font(32, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(13, 2))
        self._label(
            self.shell,
            "A clear view of what Lians carries forward across Claude, Codex, and Cursor.",
            foreground=self.colors["muted"],
            font=self._font(12),
            anchor="w",
            wraplength=900,
        ).pack(fill="x", pady=(0, 13))

        metrics = tk.Frame(self.shell, background=self.colors["background"])
        metrics.pack(fill="x")
        metric_data = (
            ("Estimated tokens saved", self.token_value, "Repeated context left out"),
            ("Context handoffs", self.event_value, "Useful context delivered"),
            ("Memories reused", self.reuse_value, "Details carried into new work"),
        )
        for index, data in enumerate(metric_data):
            self._metric_card(metrics, *data, index=index)

        activity_heading = tk.Frame(self.shell, background=self.colors["background"])
        activity_heading.pack(fill="x", pady=(17, 8))
        self._label(
            activity_heading,
            "Recent agent activity",
            foreground=self.colors["text"],
            font=self._font(15, "bold"),
        ).pack(side="left")
        self._label(
            activity_heading,
            textvariable=self.reduction_status,
            foreground=self.colors["muted"],
            font=self._font(10),
        ).pack(side="right")

        self.activity_frame = tk.Frame(
            self.shell,
            background=self.colors["surface"],
            highlightbackground=self.colors["border"],
            highlightthickness=1,
            padx=18,
            pady=10,
        )
        self.activity_frame.pack(fill="both", expand=True)
        self._show_activity([])

        connections_heading = tk.Frame(self.shell, background=self.colors["background"])
        connections_heading.pack(fill="x", pady=(14, 8))
        self._label(
            connections_heading,
            "Connections",
            foreground=self.colors["text"],
            font=self._font(14, "bold"),
        ).pack(side="left")
        self._label(
            connections_heading,
            textvariable=self.memory_status,
            foreground=self.colors["muted"],
            font=self._font(10),
        ).pack(side="right")

        integrations = tk.Frame(self.shell, background=self.colors["background"])
        integrations.pack(fill="x")
        targets = client_targets()
        for index, key in enumerate(("claude", "codex", "cursor")):
            self._connection_card(integrations, key, targets[key], index)

        actions = tk.Frame(self.shell, background=self.colors["background"])
        actions.pack(fill="x", pady=(15, 0))
        self.open_button = tk.Button(
            actions,
            text="Refresh lifeline",
            command=self._refresh_now,
            state="disabled",
            background=self.colors["blue"],
            foreground="#FFFFFF",
            activebackground=self.colors["blue_hover"],
            activeforeground="#FFFFFF",
            disabledforeground="#8290A4",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=self._font(11, "bold"),
            padx=22,
            pady=9,
        )
        self.open_button.pack(side="left")
        tk.Button(
            actions,
            text="Minimize and use my AI",
            command=self._minimize,
            background=self.colors["surface_soft"],
            foreground=self.colors["text"],
            activebackground=self.colors["surface"],
            activeforeground=self.colors["text"],
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=self._font(11, "bold"),
            padx=20,
            pady=9,
        ).pack(side="left", padx=(10, 0))
        self.stop_button = tk.Button(
            actions,
            text="Stop Lians" if self.owns_bridge else "Close window",
            command=self._stop,
            background=self.colors["background"],
            foreground=self.colors["muted"],
            activebackground=self.colors["surface_soft"],
            activeforeground=self.colors["text"],
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=self._font(10, "bold"),
            padx=14,
            pady=9,
        )
        self.stop_button.pack(side="right")
        self._label(
            self.shell,
            textvariable=self.notice,
            foreground=self.colors["muted"],
            justify="left",
            wraplength=960,
            anchor="w",
            font=self._font(9),
        ).pack(fill="x", pady=(9, 0))

    def _scroll_body(self, event: tk.Event) -> str:
        delta = int(-event.delta / 120) if event.delta else 0
        if delta:
            self.body_canvas.yview_scroll(delta, "units")
        return "break"

    def _build_titlebar(self) -> None:
        bar = tk.Frame(
            self.root,
            background=self.colors["background"],
            height=48,
            highlightbackground=self.colors["border"],
            highlightthickness=1,
        )
        bar.pack(fill="x")
        bar.pack_propagate(False)
        brand = tk.Frame(bar, background=self.colors["background"])
        brand.pack(side="left", fill="y", padx=(14, 0))
        if self.lotus is not None:
            tk.Label(brand, image=self.lotus, background=self.colors["background"]).pack(
                side="left", padx=(0, 7)
            )
        self._label(
            brand,
            "Lians",
            foreground=self.colors["text"],
            font=self._font(11, "bold"),
        ).pack(side="left")
        mode_label = "Light mode" if self.theme_name == "dark" else "Dark mode"
        mode = tk.Button(
            bar,
            text=mode_label,
            command=self._toggle_theme,
            background=self.colors["background"],
            foreground=self.colors["muted"],
            activebackground=self.colors["surface_soft"],
            activeforeground=self.colors["text"],
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=self._font(9, "bold"),
            padx=14,
        )
        self.theme_button = mode
        for text, command in (
            ("×", self._minimize),
            ("□", self._toggle_maximize),
            ("−", self._minimize),
        ):
            button = tk.Button(
                bar,
                text=text,
                command=command,
                background=self.colors["background"],
                foreground=self.colors["muted"],
                activebackground=self.colors["surface_soft"],
                activeforeground=self.colors["text"],
                relief="flat",
                borderwidth=0,
                cursor="hand2",
                font=self._font(12),
                width=4,
            )
            button.pack(side="right", fill="y")
        mode.pack(side="right", fill="y", padx=(0, 8))
        for widget in (bar, brand, *brand.winfo_children()):
            widget.bind("<ButtonPress-1>", self._begin_drag)
            widget.bind("<B1-Motion>", self._drag_window)
            widget.bind("<ButtonRelease-1>", self._finish_drag)
            widget.bind("<Double-Button-1>", lambda _event: self._toggle_maximize())

    def _metric_card(
        self,
        parent: tk.Widget,
        title: str,
        value: tk.StringVar,
        detail: str,
        *,
        index: int,
    ) -> None:
        card = tk.Frame(
            parent,
            background=self.colors["surface"],
            highlightbackground=self.colors["border"],
            highlightthickness=1,
            padx=16,
            pady=10,
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
            background=self.colors["surface"],
            foreground=self.colors["muted"],
            font=self._font(10),
            anchor="w",
            wraplength=250,
        ).pack(fill="x")
        self._label(
            card,
            textvariable=value,
            background=self.colors["surface"],
            foreground=self.colors["text"],
            font=self._font(25, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(2, 1))
        self._label(
            card,
            detail,
            background=self.colors["surface"],
            foreground=self.colors["muted"],
            font=self._font(9),
            anchor="w",
            wraplength=250,
        ).pack(fill="x")

    def _connection_presentation(self, target: ClientTarget) -> tuple[str, str, str]:
        if target.configured:
            return "Connected", "Lians is available in this app", self.colors["green"]
        if target.detected:
            return "Ready to connect", "App found, Lians is not configured", self.colors["amber"]
        return "Not found", "Install the app to connect Lians", self.colors["muted"]

    def _connection_card(
        self, parent: tk.Widget, key: str, target: ClientTarget, index: int
    ) -> None:
        card = tk.Frame(
            parent,
            background=self.colors["surface_soft"],
            highlightbackground=self.colors["border"],
            highlightthickness=1,
            padx=14,
            pady=7,
        )
        card.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0 if index == 0 else 5, 0 if index == 2 else 5),
        )
        name = "Claude" if key == "claude" else target.label
        self._label(
            card,
            name,
            background=self.colors["surface_soft"],
            foreground=self.colors["text"],
            font=self._font(11, "bold"),
            anchor="w",
        ).pack(fill="x")
        status, detail, color = self._connection_presentation(target)
        state = self._label(
            card,
            f"●  {status}",
            background=self.colors["surface_soft"],
            foreground=color,
            font=self._font(9, "bold"),
            anchor="w",
        )
        state.pack(fill="x", pady=(3, 0))
        detail_label = self._label(
            card,
            detail,
            background=self.colors["surface_soft"],
            foreground=self.colors["muted"],
            font=self._font(8),
            anchor="w",
            wraplength=250,
        )
        detail_label.pack(fill="x", pady=(2, 0))
        self.target_labels[key] = state
        self.target_details[key] = detail_label

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
                background=self.colors["surface"],
                foreground=self.colors["muted"],
                font=self._font(11),
                anchor="w",
            )
            empty.pack(fill="both", expand=True, pady=22)
            self.activity_labels.append(empty)
            return
        for index, item in enumerate(activity):
            row = tk.Frame(self.activity_frame, background=self.colors["surface"], pady=6)
            row.pack(fill="x")
            heading = self._label(
                row,
                item["title"],
                background=self.colors["surface"],
                foreground=self.colors["text"],
                font=self._font(11, "bold"),
                anchor="w",
            )
            heading.pack(side="left")
            self._label(
                row,
                item["time"],
                background=self.colors["surface"],
                foreground=self.colors["muted"],
                font=self._font(9),
            ).pack(side="right")
            detail = self._label(
                self.activity_frame,
                item["detail"],
                background=self.colors["surface"],
                foreground=self.colors["muted"],
                font=self._font(9),
                anchor="w",
            )
            detail.pack(fill="x", pady=(0, 5))
            self.activity_labels.extend((heading, detail))
            if index < len(activity) - 1:
                tk.Frame(
                    self.activity_frame, background=self.colors["border"], height=1
                ).pack(fill="x")

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
            self.status_dot.configure(foreground=self.colors["red"])
            self.notice.set(self._bridge_error)
            self.open_button.configure(state="disabled")
        elif self.bridge.running:
            self.status.set("Lians is running")
            self.status_dot.configure(foreground=self.colors["green"])
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
                    self.reduction_status.set("Waiting for your first context handoff")
                self._show_activity(snapshot["activity"])
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                self.memory_status.set("Encrypted memory ready")
        targets = client_targets()
        for key, label in self.target_labels.items():
            status, detail, color = self._connection_presentation(targets[key])
            label.configure(
                text=f"●  {status}",
                foreground=color,
            )
            self.target_details[key].configure(text=detail)
        self._refresh_job = self.root.after(2000, self._refresh)

    def _toggle_theme(self) -> None:
        if self._refresh_job is not None:
            try:
                self.root.after_cancel(self._refresh_job)
            except tk.TclError:
                pass
            self._refresh_job = None
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        _save_companion_theme(self.theme_name)
        self._build()
        if sys.platform == "win32":
            self.root.after(20, self._apply_windows_taskbar_style)
        self._refresh()

    def _apply_windows_taskbar_style(self) -> None:
        if sys.platform != "win32":
            return
        try:
            self.root.update_idletasks()
            user32 = ctypes.windll.user32
            handle = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            style = user32.GetWindowLongW(handle, -20)
            style = (style & ~0x00000080) | 0x00040000
            user32.SetWindowLongW(handle, -20, style)
            user32.SetWindowPos(handle, 0, 0, 0, 0, 0x0027)
            self._window_handle = handle
        except (AttributeError, OSError, tk.TclError):
            self._window_handle = None

    def _begin_drag(self, event: tk.Event) -> None:
        if self._maximized or self._snap_state is not None:
            current_width = max(1, self.root.winfo_width())
            pointer_ratio = (event.x_root - self.root.winfo_x()) / current_width
            saved_size = self._normal_geometry.split("+")[0]
            width_text, height_text = saved_size.split("x", maxsplit=1)
            restored_width = max(self.root.minsize()[0], int(width_text))
            restored_height = max(self.root.minsize()[1], int(height_text))
            restored_x = round(event.x_root - restored_width * pointer_ratio)
            restored_y = max(0, event.y_root - 24)
            self.root.geometry(
                f"{restored_width}x{restored_height}+{restored_x}+{restored_y}"
            )
            self.root.update_idletasks()
            self._maximized = False
            self._snap_state = None
        self._drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag_window(self, event: tk.Event) -> None:
        if self._drag_origin is None or self._maximized:
            return
        start_x, start_y, window_x, window_y = self._drag_origin
        self.root.geometry(f"+{window_x + event.x_root - start_x}+{window_y + event.y_root - start_y}")

    def _work_area(self) -> tuple[int, int, int, int]:
        if sys.platform == "win32":
            try:
                class WorkArea(ctypes.Structure):
                    _fields_ = [
                        ("left", ctypes.c_long),
                        ("top", ctypes.c_long),
                        ("right", ctypes.c_long),
                        ("bottom", ctypes.c_long),
                    ]

                area = WorkArea()
                ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0)
                return area.left, area.top, area.right, area.bottom
            except (AttributeError, OSError):
                pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _finish_drag(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        self._drag_origin = None
        _left, top, _right, _bottom = self._work_area()
        snap_margin = 12
        if event.y_root <= top + snap_margin:
            self._maximize_to_work_area()

    def _maximize_to_work_area(self) -> None:
        if not self._maximized and self._snap_state is None:
            self._normal_geometry = self.root.geometry()
        left, top, right, bottom = self._work_area()
        self.root.geometry(f"{right - left}x{bottom - top}+{left}+{top}")
        self._maximized = True
        self._snap_state = "maximized"

    def _toggle_maximize(self) -> None:
        if self._maximized or self._snap_state is not None:
            self.root.geometry(self._normal_geometry)
            self._maximized = False
            self._snap_state = None
            return
        self._maximize_to_work_area()

    def _refresh_now(self) -> None:
        if self._refresh_job is not None:
            try:
                self.root.after_cancel(self._refresh_job)
            except tk.TclError:
                pass
            self._refresh_job = None
        self._refresh()

    def _minimize(self) -> None:
        self.notice.set("Lians is still running. Open it from the Windows taskbar when needed.")
        if sys.platform == "win32" and self._window_handle is not None:
            try:
                ctypes.windll.user32.ShowWindow(self._window_handle, 6)
                return
            except (AttributeError, OSError):
                pass
        if sys.platform == "win32":
            self.root.overrideredirect(False)
        self.root.iconify()

    def _stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self.open_button.configure(state="disabled")
        if self._refresh_job is not None:
            try:
                self.root.after_cancel(self._refresh_job)
            except tk.TclError:
                pass
            self._refresh_job = None

        if not self.owns_bridge:
            self.root.destroy()
            return

        self.status.set("Stopping Lians")

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


def _focus_existing_companion() -> bool:
    """Restore the existing Windows dashboard instead of opening a browser."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        handle = user32.FindWindowW(None, "Lians")
        if not handle:
            return False
        user32.ShowWindowAsync(handle, 9)
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
        return True
    except (AttributeError, OSError):
        return False


class _AttachedBridge:
    """Read-only connection metadata for a bridge owned by another process."""

    def __init__(self, origin: str, store: Any) -> None:
        self.origin = origin
        self.store = store

    @property
    def running(self) -> bool:
        return _running_bridge_origin() == self.origin

    def shutdown(self) -> None:
        return None


def _launch_companion() -> None:
    from .bridge import BridgeApplication
    from .mcp import default_data_path
    from .store import MemoryStore

    data_path = default_data_path()
    existing = _running_bridge_origin()
    if existing is not None:
        if _focus_existing_companion():
            return
        root = tk.Tk()
        CompanionApp(
            root,
            _AttachedBridge(existing, MemoryStore(data_path)),
            start_bridge=False,
            owns_bridge=False,
        )
        root.mainloop()
        return
    root = tk.Tk()
    CompanionApp(root, BridgeApplication(MemoryStore(data_path)))
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
