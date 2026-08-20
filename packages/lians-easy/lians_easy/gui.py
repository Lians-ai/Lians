"""Guided desktop setup for people who should never need a terminal."""

from __future__ import annotations

import ctypes
import json
import math
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
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
        "background": "#030405",
        "surface": "#080A0D",
        "surface_soft": "#0D1014",
        "border": "#171B22",
        "grid": "#10141B",
        "grid_strong": "#202836",
        "text": "#EEF1F5",
        "muted": "#828B98",
        "blue": "#315FE9",
        "blue_hover": "#294FC2",
        "blue_soft": "#0C1633",
        "green": "#4BC99B",
        "amber": "#F2BE5C",
        "red": "#FF7087",
    },
    "light": {
        "background": "#F1F0EC",
        "surface": "#FAFAF8",
        "surface_soft": "#EAE9E4",
        "border": "#D7D5CF",
        "grid": "#E4E2DC",
        "grid_strong": "#C8CDD8",
        "text": "#15171A",
        "muted": "#626A74",
        "blue": "#3159C8",
        "blue_hover": "#284BAA",
        "blue_soft": "#E2E8F8",
        "green": "#087F58",
        "amber": "#9A6500",
        "red": "#C93651",
    },
}

_AI_PROCESS_NAMES = {
    "claude": {"claude.exe", "claude desktop.exe"},
    "codex": {"codex.exe", "codex app.exe"},
    "cursor": {"cursor.exe"},
}
_AI_LABELS = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor"}


def _anime_in_out_sine(progress: float) -> float:
    """Mirror Anime.js's built-in inOutSine ease for native Tk animation."""

    progress = min(1.0, max(0.0, progress))
    return -(math.cos(math.pi * progress) - 1.0) / 2.0


def _windows_process_snapshot() -> dict[int, str]:
    """Read process names without spawning tasklist or adding a dependency."""

    if sys.platform != "win32":
        return {}

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    try:
        kernel32 = ctypes.windll.kernel32
        create_snapshot = kernel32.CreateToolhelp32Snapshot
        create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        create_snapshot.restype = wintypes.HANDLE
        process_first = kernel32.Process32FirstW
        process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
        process_first.restype = wintypes.BOOL
        process_next = kernel32.Process32NextW
        process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
        process_next.restype = wintypes.BOOL
        snapshot = create_snapshot(0x00000002, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return {}
        processes: dict[int, str] = {}
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(ProcessEntry)
            if process_first(snapshot, ctypes.byref(entry)):
                while True:
                    processes[int(entry.th32ProcessID)] = entry.szExeFile.lower()
                    if not process_next(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)
        return processes
    except (AttributeError, OSError, ValueError):
        return {}


def _foreground_process_id() -> int | None:
    if sys.platform != "win32":
        return None
    try:
        process_id = wintypes.DWORD()
        window = ctypes.windll.user32.GetForegroundWindow()
        if not window:
            return None
        ctypes.windll.user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        return int(process_id.value) or None
    except (AttributeError, OSError):
        return None


def _active_ai_client(
    preferred: str | None = None,
    *,
    processes: dict[int, str] | None = None,
    foreground_process_id: int | None = None,
) -> str | None:
    """Choose the foreground AI, then retain the last active AI if still open."""

    if processes is None:
        processes = _windows_process_snapshot()
    if foreground_process_id is None:
        foreground_process_id = _foreground_process_id()

    def client_for_process(name: str | None) -> str | None:
        normalized = (name or "").lower()
        for key, process_names in _AI_PROCESS_NAMES.items():
            if normalized in process_names:
                return key
        return None

    foreground_client = client_for_process(processes.get(foreground_process_id or -1))
    if foreground_client is not None:
        return foreground_client

    running = {
        client
        for name in processes.values()
        if (client := client_for_process(name)) is not None
    }
    if preferred in running:
        return preferred
    return next((key for key in ("codex", "claude", "cursor") if key in running), None)

def _draw_round_rectangle(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    *,
    fill: str,
    tags: str,
) -> int:
    """Draw one compact smooth rounded rectangle without platform chrome."""

    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    points = (
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    )
    return canvas.create_polygon(
        points,
        smooth=True,
        splinesteps=24,
        fill=fill,
        outline="",
        tags=tags,
    )

_WINDOWS_DPI_AWARE = False


def _enable_windows_dpi_awareness() -> None:
    """Keep Tk geometry and the Windows work area in the same pixel space."""
    global _WINDOWS_DPI_AWARE
    if sys.platform != "win32" or _WINDOWS_DPI_AWARE:
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            _WINDOWS_DPI_AWARE = True
            return
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            _WINDOWS_DPI_AWARE = True
            return
    except (AttributeError, OSError):
        pass
    try:
        _WINDOWS_DPI_AWARE = bool(ctypes.windll.user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        pass


def _windows_display_scale() -> float:
    if sys.platform != "win32":
        return 1.0
    if _WINDOWS_DPI_AWARE:
        return 1.0
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96)
    except (AttributeError, OSError):
        return 1.0


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


def _display_font(root: tk.Tk, fallback: str) -> str:
    """Use a quieter Japanese UI face for display copy when Windows has it."""
    try:
        families = set(tkfont.families(root))
    except tk.TclError:
        return fallback
    for candidate in (
        "Yu Mincho",
        "Yu Gothic UI",
        "Yu Gothic",
        fallback,
        "Segoe UI",
    ):
        if candidate in families:
            return candidate
    return fallback


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
            "Recover the task. Guard what done means.",
            foreground=TEXT,
            font=("Segoe UI", 22, "bold"),
            anchor="w",
            justify="left",
            wraplength=640,
        ).pack(fill="x", pady=(16, 6))
        self._label(
            self.shell,
            (
                "Lians restores current work, detects stale checkpoints, and separates measured "
                "evidence from an agent's own claims. No AI account password or API key required."
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
            else "Choose the AI coding apps you want to connect"
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
            ("connecting", "Connect Lians Guard"),
            ("verifying", "Check that recovery is ready"),
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
            text="Connect Lians Guard",
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
            "Lians Guard is connected.",
            background=PANEL,
            foreground=GREEN,
            font=("Segoe UI", 22, "bold"),
            anchor="w",
        ).pack(fill="x")
        self._label(
            self.card,
            f"Recovery is active in {connected}. Keep using the apps normally.",
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
            "Try recovery in two chats",
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

        if "codex" in result.get("requires_trust", []):
            self._label(
                try_card,
                "One Codex step",
                background=BLUE_SOFT,
                foreground=TEXT,
                font=("Segoe UI", 11, "bold"),
                anchor="w",
            ).pack(fill="x", pady=(2, 0))
            self._label(
                try_card,
                (
                    "Restart Codex, open /hooks, review the Lians memory hook, and choose "
                    "Trust. Codex will not run a new or changed local hook until you approve "
                    "its exact contents."
                ),
                background=BLUE_SOFT,
                foreground=MUTED,
                justify="left",
                wraplength=590,
                anchor="w",
            ).pack(fill="x", pady=(8, 10))
            tk.Button(
                try_card,
                text="Copy /hooks",
                command=lambda: self._copy("/hooks"),
                background=PANEL_SOFT,
                foreground=TEXT,
                activebackground=PANEL,
                activeforeground=TEXT,
                relief="flat",
                borderwidth=0,
                cursor="hand2",
                padx=16,
                pady=8,
            ).pack(anchor="w", pady=(0, 12))
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
        animate_intro: bool = True,
        auto_minimize: bool = False,
        theme: str | None = None,
    ) -> None:
        self.root = root
        self.bridge = bridge
        self.owns_bridge = start_bridge if owns_bridge is None else owns_bridge
        self._stopping = False
        self._bridge_error: str | None = None
        self._bridge_thread: threading.Thread | None = None
        self._refresh_job: str | None = None
        self._intro_job: str | None = None
        self._intro_frame_job: str | None = None
        self._intro_started: float | None = None
        self._intro_lotus_item: int | None = None
        self._lifeline_lotus_item: int | None = None
        self._pet_job: str | None = None
        self._pet_reaction_started: float | None = None
        self._shell_min_height = 0
        self._shell_layout_size: tuple[int, int, int] | None = None
        self._active_client_key = _active_ai_client()
        self._window_handle: int | None = None
        self._drag_origin: tuple[int, int, int, int] | None = None
        self._display_scale = _windows_display_scale()
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        usable_width = round((screen_width - 80) / self._display_scale)
        usable_height = round((screen_height - 70) / self._display_scale)
        window_width = min(1120, max(880, usable_width))
        window_height = min(820, max(620, usable_height))
        offset_x = max(0, round((screen_width / self._display_scale - window_width) / 2))
        offset_y = max(0, round((screen_height / self._display_scale - window_height) / 2))
        self._normal_geometry = (
            f"{window_width}x{window_height}+{offset_x}+{offset_y}"
        )
        self._maximized = False
        self._snap_state: str | None = None
        self.theme_name = theme if theme in COMPANION_THEMES else _saved_companion_theme()
        self.colors = COMPANION_THEMES[self.theme_name]
        self.font_family = _register_sora(root)
        # One type family renders more cleanly than mixing Windows' Japanese
        # display fonts with Sora at different ClearType hinting boundaries.
        self.display_font_family = self.font_family
        self.activity_labels: list[tk.Label] = []
        self._activity_state: tuple[tuple[str, str, str], ...] | None = None
        self.animate_intro = animate_intro
        self.auto_minimize = auto_minimize

        self.status = tk.StringVar(value="Starting Lians")
        self.memory_status = tk.StringVar(value="0 saved memories")
        self.token_value = tk.StringVar(value="0")
        self.event_value = tk.StringVar(value="0")
        self.reuse_value = tk.StringVar(value="0")
        self.reduction_status = tk.StringVar(value="Waiting for the first handoff")
        self.connection_status = tk.StringVar(
            value=(
                _AI_LABELS[self._active_client_key]
                if self._active_client_key is not None
                else "No connection detected"
            )
        )
        self.notice = tk.StringVar(value="Estimates from encrypted local receipts.")

        self.root.title("Lians")
        self.root.geometry(self._normal_geometry)
        self.root.minsize(880, 620)
        self.root.protocol("WM_DELETE_WINDOW", self._stop)
        if sys.platform == "win32":
            self.root.overrideredirect(True)
            try:
                self.root.iconbitmap(default=sys.executable)
            except tk.TclError:
                pass

        self._load_brand_images()
        if sys.platform == "win32":
            self.root.after(50, self._apply_windows_taskbar_style)
        if start_bridge:
            self._start_bridge()
        if self.animate_intro:
            self._build_intro()
        else:
            self._build()
            self._refresh_job = self.root.after(200, self._refresh)
            if self.auto_minimize:
                self.root.after(260, self._minimize)

    def _font(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        return (self.font_family, size, weight)

    def _display(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        return (self.display_font_family, size, weight)

    def _label(self, parent: tk.Widget, text: str = "", **kwargs: Any) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            background=kwargs.pop("background", self.colors["background"]),
            **kwargs,
        )

    def _rounded_panel(
        self,
        parent: tk.Widget,
        *,
        height: int,
        fill_key: str = "surface",
        radius: int = 24,
        padding: int = 20,
    ) -> tuple[tk.Canvas, tk.Frame]:
        """Create a responsive rounded surface with a normal Tk content frame."""

        background = self.colors[fill_key]
        canvas = tk.Canvas(
            parent,
            height=height,
            background=self.colors["background"],
            borderwidth=0,
            highlightthickness=0,
        )
        inner = tk.Frame(canvas, background=background)
        inner.place(
            x=padding,
            y=padding,
            relwidth=1,
            width=-(padding * 2),
            relheight=1,
            height=-(padding * 2),
        )

        redraw_job: list[str | None] = [None]
        pending_size = [0, 0]
        rendered_size = [-1, -1]

        def render() -> None:
            redraw_job[0] = None
            width, canvas_height = pending_size
            if [width, canvas_height] == rendered_size or not canvas.winfo_exists():
                return
            rendered_size[:] = [width, canvas_height]
            canvas.delete("panel-shape")
            _draw_round_rectangle(
                canvas,
                1,
                1,
                max(2, width - 1),
                max(2, canvas_height - 1),
                radius,
                fill=self.colors["border"],
                tags="panel-shape",
            )
            _draw_round_rectangle(
                canvas,
                2,
                2,
                max(3, width - 2),
                max(3, canvas_height - 2),
                max(1, radius - 1),
                fill=background,
                tags="panel-shape",
            )
            canvas.tag_lower("panel-shape")

        def redraw(event: tk.Event) -> None:
            pending_size[:] = [event.width, event.height]
            if redraw_job[0] is None:
                redraw_job[0] = canvas.after_idle(render)

        canvas.bind("<Configure>", redraw)
        return canvas, inner

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
            self.lotus_mark = tk.PhotoImage(file=str(lotus_path))
            factor = max(1, round(self.lotus_mark.width() / 32))
            self.lotus = self.lotus_mark.subsample(factor, factor)
        except (OSError, tk.TclError):
            self.lotus = None
            self.lotus_mark = None
        try:
            favicon_path = files("lians_easy").joinpath("desktop", "favicon.png")
            self.intro_favicon = tk.PhotoImage(file=str(favicon_path))
        except (OSError, tk.TclError):
            self.intro_favicon = None
        self.agent_icons: dict[str, tk.PhotoImage] = {}
        for key in _AI_LABELS:
            try:
                icon_path = files("lians_easy").joinpath(
                    "desktop", "agents", f"{key}.png"
                )
                self.agent_icons[key] = tk.PhotoImage(file=str(icon_path))
            except (OSError, tk.TclError):
                continue
        self.theme_icons: dict[str, tk.PhotoImage] = {}
        for key in ("sun", "moon"):
            try:
                icon_path = files("lians_easy").joinpath(
                    "desktop", "ui", f"theme-{key}.png"
                )
                self.theme_icons[key] = tk.PhotoImage(file=str(icon_path))
            except (OSError, tk.TclError):
                continue

    def _build_intro(self) -> None:
        """Settle the exact native-size Lotus once the window has real geometry."""
        for child in self.root.winfo_children():
            child.destroy()
        self.root.configure(background="#000000")
        self.intro_canvas = tk.Canvas(
            self.root,
            background="#000000",
            borderwidth=0,
            highlightthickness=0,
        )
        self.intro_canvas.pack(fill="both", expand=True)
        self._intro_lotus_item = None
        self._intro_started = None
        self.intro_canvas.bind("<Configure>", self._position_intro_lotus)
        self._intro_frame_job = self.root.after(16, self._animate_intro)

    def _position_intro_lotus(self, _event: tk.Event | None = None) -> None:
        """Keep the intro centered even while Windows is mapping the window."""

        if self._stopping or not self.intro_canvas.winfo_exists():
            return
        width = self.intro_canvas.winfo_width()
        height = self.intro_canvas.winfo_height()
        if width < 64 or height < 64:
            return
        elapsed = (
            0.0
            if self._intro_started is None
            else max(0.0, time.monotonic() - self._intro_started)
        )
        progress = min(1.0, elapsed / 0.32)
        offset_y = round((1.0 - _anime_in_out_sine(progress)) * 12)
        frame = self.intro_favicon or self.lotus_mark
        if frame is None:
            return
        center_x = width / 2
        center_y = height / 2 + offset_y
        if self._intro_lotus_item is None:
            self._intro_lotus_item = self.intro_canvas.create_image(
                center_x,
                center_y,
                image=frame,
                tags="intro-motion",
            )
            return
        self.intro_canvas.coords(self._intro_lotus_item, center_x, center_y)

    def _animate_intro(self) -> None:
        if self._stopping or not self.intro_canvas.winfo_exists():
            return
        if self.intro_canvas.winfo_width() < 64 or self.intro_canvas.winfo_height() < 64:
            self._intro_frame_job = self.root.after(16, self._animate_intro)
            return
        if self._intro_started is None:
            self._intro_started = time.monotonic()
            self._intro_job = self.root.after(680, self._finish_intro)
        self._position_intro_lotus()
        if time.monotonic() - self._intro_started < 0.32:
            self._intro_frame_job = self.root.after(16, self._animate_intro)
            return
        self._intro_frame_job = None

    def _finish_intro(self) -> None:
        self._intro_job = None
        if self._stopping:
            return
        if self._intro_frame_job is not None:
            try:
                self.root.after_cancel(self._intro_frame_job)
            except tk.TclError:
                pass
            self._intro_frame_job = None
        redraw_suspended = self._set_windows_redraw(False)
        try:
            self._build()
            self.root.update_idletasks()
        finally:
            if redraw_suspended:
                self._set_windows_redraw(True)
        self._refresh_job = self.root.after(20, self._refresh)
        if self.auto_minimize:
            self.root.after(180, self._minimize)

    def _build(self) -> None:
        self._cancel_pet_animation()
        for child in self.root.winfo_children():
            child.destroy()
        self.colors = COMPANION_THEMES[self.theme_name]
        self._activity_state = None
        self._shell_min_height = 0
        self._shell_layout_size = None
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
        self.scrollbar = tk.Canvas(
            body,
            width=7,
            background=self.colors["background"],
            borderwidth=0,
            highlightthickness=0,
        )
        self.scrollbar.bind("<Button-1>", self._scroll_from_indicator)
        self.scrollbar.bind("<B1-Motion>", self._scroll_from_indicator)
        self.body_canvas.configure(yscrollcommand=self._update_scroll_indicator)
        self.scrollbar.pack(side="right", fill="y")
        self.body_canvas.pack(side="left", fill="both", expand=True)
        self.shell = tk.Frame(
            self.body_canvas,
            background=self.colors["background"],
            padx=54,
            pady=26,
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
            lambda event: self._resize_shell(event, canvas_window),
        )
        self.root.bind("<MouseWheel>", self._scroll_body)

        hero = tk.Frame(self.shell, background=self.colors["background"])
        hero.pack(fill="x", pady=(6, 22))
        hero_left = tk.Frame(hero, background=self.colors["background"])
        hero_left.pack(side="left", fill="both", expand=True, padx=(12, 20), pady=18)
        state = tk.Frame(hero_left, background=self.colors["background"])
        state.pack(fill="x", pady=(0, 16))
        self.connection_icon_label = self._label(
            state,
            background=self.colors["background"],
        )
        self.connection_icon_label.pack(side="left", padx=(0, 9))
        self.connection_label = self._label(
            state,
            textvariable=self.connection_status,
            background=self.colors["background"],
            foreground=self.colors["text"],
            font=self._font(10, "bold"),
        )
        self.connection_label.pack(side="left")
        self._render_connection_identity()
        self._label(
            hero_left,
            "Current-state guard",
            background=self.colors["background"],
            foreground=self.colors["text"],
            font=self._display(32, "bold"),
            anchor="w",
        ).pack(fill="x")
        self._label(
            hero_left,
            "Recover work. See what is stale. Know what still has to pass.",
            background=self.colors["background"],
            foreground=self.colors["muted"],
            font=self._font(11),
            anchor="w",
            justify="left",
            wraplength=400,
        ).pack(fill="x", pady=(7, 0))
        self.lifeline_canvas = tk.Canvas(
            hero,
            width=260,
            height=170,
            background=self.colors["background"],
            borderwidth=0,
            highlightthickness=0,
        )
        self.lifeline_canvas.pack(side="right", fill="y", padx=(0, 20))
        self.lifeline_canvas.configure(cursor="hand2")
        self._lifeline_lotus_item = None
        self.lifeline_canvas.bind("<Configure>", self._render_lifeline)
        self.lifeline_canvas.bind("<Button-1>", self._animate_pet)
        self.root.after_idle(self._render_lifeline)

        self.metrics_panel, metrics = self._rounded_panel(
            self.shell,
            height=166,
            fill_key="surface",
            radius=24,
            padding=18,
        )
        self.metrics_panel.pack(fill="x")
        metric_data = (
            ("Tokens saved", self.token_value, "Repeated context removed"),
            ("Handoffs", self.event_value, "Context carried forward"),
            ("Memories reused", self.reuse_value, "Useful details returned"),
        )
        for index, data in enumerate(metric_data):
            self._metric_card(metrics, *data, index=index)

        activity_heading = tk.Frame(self.shell, background=self.colors["background"])
        activity_heading.pack(fill="x", pady=(20, 9), padx=4)
        self._label(
            activity_heading,
            "Activity",
            foreground=self.colors["text"],
            font=self._font(15, "bold"),
        ).pack(side="left")
        self._label(
            activity_heading,
            textvariable=self.reduction_status,
            foreground=self.colors["muted"],
            font=self._font(10),
        ).pack(side="right")

        self.activity_panel, self.activity_frame = self._rounded_panel(
            self.shell,
            height=168,
            fill_key="surface",
            radius=24,
            padding=20,
        )
        self.activity_panel.pack(fill="both", expand=True)
        self._show_activity([])

        actions = tk.Frame(self.shell, background=self.colors["background"])
        actions.pack(fill="x", pady=(18, 0), padx=4)
        self.open_button = tk.Button(
            actions,
            text="Refresh",
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
            text="Minimize",
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
            text="Quit Lians" if self.owns_bridge else "Close",
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
            actions,
            textvariable=self.memory_status,
            background=self.colors["background"],
            foreground=self.colors["muted"],
            font=self._font(9),
        ).pack(side="right", padx=(0, 14))
        self._label(
            self.shell,
            textvariable=self.notice,
            foreground=self.colors["muted"],
            justify="left",
            wraplength=960,
            anchor="w",
            font=self._font(9),
        ).pack(fill="x", pady=(9, 4), padx=4)

    def _resize_shell(self, event: tk.Event, canvas_window: int) -> None:
        """Fill tall windows while keeping short windows scrollable."""

        width = min(event.width, 1320)
        if not self._shell_min_height:
            self._shell_min_height = self.shell.winfo_reqheight()
        height = max(event.height, self._shell_min_height)
        offset_x = round(max(0, (event.width - width) / 2))
        layout = (width, height, offset_x)
        if layout == self._shell_layout_size:
            return
        self._shell_layout_size = layout
        self.body_canvas.itemconfigure(canvas_window, width=width, height=height)
        self.body_canvas.coords(canvas_window, offset_x, 0)

    def _scroll_body(self, event: tk.Event) -> str:
        delta = int(-event.delta / 120) if event.delta else 0
        if delta:
            self.body_canvas.yview_scroll(delta, "units")
        return "break"

    def _update_scroll_indicator(self, first: str, last: str) -> None:
        try:
            start = float(first)
            end = float(last)
            height = max(1, self.scrollbar.winfo_height())
            self.scrollbar.delete("thumb")
            if end - start >= 0.999:
                return
            thumb_start = round(start * height)
            thumb_end = max(thumb_start + 34, round(end * height))
            thumb_end = min(height, thumb_end)
            self.scrollbar.create_rectangle(
                2,
                thumb_start,
                6,
                thumb_end,
                fill=self.colors["blue"],
                outline="",
                tags="thumb",
            )
        except (tk.TclError, TypeError, ValueError):
            return

    def _scroll_from_indicator(self, event: tk.Event) -> str:
        height = max(1, self.scrollbar.winfo_height())
        visible = self.body_canvas.yview()
        visible_fraction = visible[1] - visible[0]
        target = event.y / height - visible_fraction / 2
        self.body_canvas.yview_moveto(min(1.0 - visible_fraction, max(0.0, target)))
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
        mode = tk.Canvas(
            bar,
            width=36,
            height=36,
            background=self.colors["background"],
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.theme_button = mode
        self._theme_toggle_hover = False
        self._draw_theme_toggle()
        mode.bind("<Button-1>", lambda _event: self._toggle_theme())
        mode.bind("<Enter>", lambda _event: self._set_theme_toggle_hover(True))
        mode.bind("<Leave>", lambda _event: self._set_theme_toggle_hover(False))
        for text, command in (
            ("×", self._stop),
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
            if text == "×":
                self.close_button = button
        mode.pack(side="right", padx=(0, 8), pady=6)
        for widget in (bar, brand, *brand.winfo_children()):
            widget.bind("<ButtonPress-1>", self._begin_drag)
            widget.bind("<B1-Motion>", self._drag_window)
            widget.bind("<ButtonRelease-1>", self._finish_drag)
            widget.bind("<Double-Button-1>", lambda _event: self._toggle_maximize())

    def _set_theme_toggle_hover(self, active: bool) -> None:
        self._theme_toggle_hover = active
        self._draw_theme_toggle()

    def _draw_theme_toggle(self) -> None:
        """Render a neutral circle around a pre-antialiased sun or moon."""

        canvas = self.theme_button
        canvas.delete("all")
        if self.theme_name == "dark":
            fill = self.colors["surface_soft"] if self._theme_toggle_hover else "#0B1019"
            border = "#273247" if self._theme_toggle_hover else "#202836"
            self.theme_toggle_icon = "sun"
        else:
            fill = self.colors["surface_soft"] if self._theme_toggle_hover else "#FFFFFF"
            border = "#B9BEC6" if self._theme_toggle_hover else "#C7CDD5"
            self.theme_toggle_icon = "moon"
        canvas.create_oval(
            1,
            1,
            35,
            35,
            fill=fill,
            outline=border,
            width=1,
            tags="theme-toggle-ring",
        )
        icon = self.theme_icons.get(self.theme_toggle_icon)
        if icon is not None:
            canvas.create_image(
                18,
                18,
                image=icon,
                tags="theme-toggle-icon",
            )
            return
        canvas.create_text(
            18,
            18,
            text="○",
            fill=self.colors["text"],
            font=self._font(14),
            tags="theme-toggle-icon",
        )

    def _render_connection_identity(self) -> None:
        """Show one active AI identity, never a row of passive installations."""

        key = self._active_client_key
        icon = self.agent_icons.get(key or "")
        if key is None:
            self.connection_status.set("No connection detected")
            self.connection_icon_label.configure(image="")
            self.connection_icon_label.pack_forget()
            self.connection_label.configure(foreground=self.colors["muted"])
            return
        self.connection_status.set(f"{_AI_LABELS[key]} connected")
        self.connection_icon_label.configure(image=icon or "")
        if icon is not None and not self.connection_icon_label.winfo_manager():
            self.connection_icon_label.pack(
                side="left", padx=(0, 9), before=self.connection_label
            )
        self.connection_label.configure(foreground=self.colors["text"])

    def _render_lifeline(self, _event: tk.Event | None = None) -> None:
        """Render the exact branded PNG as the dashboard's small companion."""
        if self._stopping:
            return
        try:
            if not self.lifeline_canvas.winfo_exists():
                return
            canvas = self.lifeline_canvas
            width = max(240, canvas.winfo_width())
            height = max(140, canvas.winfo_height())
            center_x = width / 2
            center_y = height / 2
            if self.lotus_mark is not None:
                if self._lifeline_lotus_item is None:
                    self._lifeline_lotus_item = canvas.create_image(
                        center_x,
                        center_y,
                        image=self.lotus_mark,
                        tags="motion",
                    )
                else:
                    canvas.coords(self._lifeline_lotus_item, center_x, center_y)
        except tk.TclError:
            return

    def _cancel_pet_animation(self) -> None:
        if self._pet_job is not None:
            try:
                self.root.after_cancel(self._pet_job)
            except tk.TclError:
                pass
            self._pet_job = None
        self._pet_reaction_started = None

    def _animate_pet(self, _event: tk.Event | None = None) -> None:
        """Give the native Lotus a restrained 500ms spring-like hop on click."""

        self._cancel_pet_animation()
        self._pet_reaction_started = time.monotonic()
        self._animate_pet_frame()

    def _animate_pet_frame(self) -> None:
        if self._stopping or self._pet_reaction_started is None:
            return
        try:
            if not self.lifeline_canvas.winfo_exists() or self._lifeline_lotus_item is None:
                return
            elapsed = time.monotonic() - self._pet_reaction_started
            progress = min(1.0, elapsed / 0.5)
            eased = _anime_in_out_sine(progress)
            arc = math.sin(math.pi * eased)
            settle = math.sin(math.pi * 3 * eased) * (1.0 - eased)
            center_x = max(240, self.lifeline_canvas.winfo_width()) / 2
            center_y = max(140, self.lifeline_canvas.winfo_height()) / 2
            self.lifeline_canvas.coords(
                self._lifeline_lotus_item,
                center_x,
                center_y - 10 * arc + 2 * settle,
            )
            if progress < 1.0:
                self._pet_job = self.root.after(16, self._animate_pet_frame)
                return
            self._pet_job = None
            self._pet_reaction_started = None
            self.lifeline_canvas.coords(self._lifeline_lotus_item, center_x, center_y)
        except tk.TclError:
            self._pet_job = None
            self._pet_reaction_started = None

    def _metric_card(
        self,
        parent: tk.Widget,
        title: str,
        value: tk.StringVar,
        detail: str,
        *,
        index: int,
    ) -> None:
        if index:
            tk.Frame(parent, background=self.colors["border"], width=1).pack(
                side="left", fill="y", padx=8
            )
        card = tk.Frame(
            parent,
            background=self.colors["surface"],
            padx=14,
            pady=2,
        )
        card.pack(side="left", fill="both", expand=True)
        heading = tk.Frame(card, background=self.colors["surface"])
        heading.pack(fill="x")
        self._label(
            heading,
            title,
            background=self.colors["surface"],
            foreground=self.colors["muted"],
            font=self._font(10),
            anchor="w",
            wraplength=250,
        ).pack(side="left")
        self._label(
            card,
            textvariable=value,
            background=self.colors["surface"],
            foreground=self.colors["blue"],
            font=self._display(25, "bold"),
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
                "Waiting for the first handoff.",
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
            self.notice.set(self._bridge_error)
            self.open_button.configure(state="disabled")
        elif self.bridge.running:
            self.status.set("Lians Guard is running")
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
                    self.reduction_status.set("Waiting for the first handoff")
                self._show_activity(snapshot["activity"])
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                self.memory_status.set("Encrypted memory ready")
        self._active_client_key = _active_ai_client(self._active_client_key)
        self._render_connection_identity()
        self._refresh_job = self.root.after(5000, self._refresh)

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
        # Repaint the chosen theme before querying receipts. The live website
        # swaps state immediately; doing I/O inside the click made Tk feel late.
        self.root.update_idletasks()
        if sys.platform == "win32":
            self.root.after(20, self._apply_windows_taskbar_style)
        self._refresh_job = self.root.after(20, self._refresh)

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

    def _set_windows_redraw(self, enabled: bool) -> bool:
        """Hold one frame while replacing the intro or an entire themed tree."""

        if sys.platform != "win32":
            return False
        try:
            self.root.update_idletasks()
            user32 = ctypes.windll.user32
            handle = self._window_handle
            if handle is None:
                handle = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            user32.SendMessageW(handle, 0x000B, int(enabled), 0)
            if enabled:
                redraw_flags = 0x0001 | 0x0004 | 0x0080 | 0x0100
                user32.RedrawWindow(handle, None, None, redraw_flags)
            return True
        except (AttributeError, OSError, tk.TclError):
            return False

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
                return tuple(
                    round(value / self._display_scale)
                    for value in (area.left, area.top, area.right, area.bottom)
                )
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
        if hasattr(self, "open_button"):
            self.open_button.configure(state="disabled")
        if self._refresh_job is not None:
            try:
                self.root.after_cancel(self._refresh_job)
            except tk.TclError:
                pass
            self._refresh_job = None
        for attribute in ("_intro_job", "_intro_frame_job"):
            job = getattr(self, attribute)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, attribute, None)
        self._cancel_pet_animation()

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
        flags = 0x0001 | 0x0002 | 0x0040
        user32.SetWindowPos(handle, -1, 0, 0, 0, 0, flags)
        user32.SetWindowPos(handle, -2, 0, 0, 0, 0, flags)
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


def _ensure_windows_autostart() -> None:
    """Register the frozen app for a quiet per-user Windows startup."""

    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        # A frozen Windows executable already exposes an absolute native path.
        # Resolving it through pathlib is harmful in cross-platform validation:
        # a Linux runner interprets ``C:\\...`` as a relative POSIX path.
        command = f'"{sys.executable}" --background'
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            key_path,
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as key:
            try:
                current, _kind = winreg.QueryValueEx(key, "Lians")
            except FileNotFoundError:
                current = None
            if current != command:
                winreg.SetValueEx(key, "Lians", 0, winreg.REG_SZ, command)
    except (OSError, ValueError):
        return


def _launch_companion(*, background_start: bool = False) -> None:
    from .bridge import BridgeApplication
    from .mcp import default_data_path
    from .store import MemoryStore

    _ensure_windows_autostart()
    data_path = default_data_path()
    existing = _running_bridge_origin()
    if existing is not None:
        if background_start:
            return
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
    CompanionApp(
        root,
        BridgeApplication(MemoryStore(data_path)),
        auto_minimize=background_start,
    )
    root.mainloop()


def launch(*, background_start: bool = False) -> None:
    _enable_windows_dpi_awareness()
    if any(target.configured for target in client_targets().values()):
        _launch_companion(background_start=background_start)
        return
    if background_start:
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
