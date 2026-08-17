"""Chromium-backed native Windows companion for the Lians lifeline."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .installer import client_targets, user_data_dir
from .lifeline import lifeline_snapshot
from .mcp import default_data_path
from .store import MemoryStore

_AI_PROCESS_NAMES = {
    "claude": {"claude.exe", "claude desktop.exe"},
    "codex": {"codex.exe", "codex app.exe"},
    "cursor": {"cursor.exe"},
}
_AI_LABELS = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor"}


def _enable_windows_dpi_awareness() -> None:
    """Keep WebView CSS pixels aligned with the native maximized work area."""

    if sys.platform != "win32":
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        return


def _windows_process_snapshot() -> dict[int, str]:
    """Read process names directly so refreshing never spawns a console."""

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
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        processes: dict[int, str] = {}
        try:
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


def _active_ai_client(preferred: str | None = None) -> str | None:
    processes = _windows_process_snapshot()

    def match(name: str | None) -> str | None:
        normalized = (name or "").lower()
        for key, process_names in _AI_PROCESS_NAMES.items():
            if normalized in process_names:
                return key
        return None

    foreground_id = wintypes.DWORD()
    try:
        foreground = ctypes.windll.user32.GetForegroundWindow()
        ctypes.windll.user32.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_id))
    except (AttributeError, OSError):
        foreground_id.value = 0
    foreground_client = match(processes.get(int(foreground_id.value)))
    if foreground_client:
        return foreground_client
    running = {client for name in processes.values() if (client := match(name))}
    if preferred in running:
        return preferred
    return next((key for key in ("codex", "claude", "cursor") if key in running), None)


def _running_bridge_origin() -> str | None:
    origin = "http://127.0.0.1:7317"
    try:
        with urlopen(origin, timeout=0.35) as response:
            server = response.headers.get("Server", "")
    except (OSError, URLError):
        return None
    return origin if server.startswith("LiansBridge/") else None


def _focus_existing_window(*, timeout: float = 0.0) -> bool:
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        find_window = user32.FindWindowW
        find_window.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        find_window.restype = wintypes.HWND
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            handle = find_window(None, "Lians")
            if handle:
                user32.ShowWindowAsync(handle, 9)
                user32.SetForegroundWindow(handle)
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.04)
    except (AttributeError, OSError):
        return False


def _ensure_windows_autostart() -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        import winreg

        command = f'"{Path(sys.executable).resolve()}" --background'
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
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


def _lians_window_handle() -> int:
    if sys.platform != "win32":
        return 0
    try:
        find_window = ctypes.windll.user32.FindWindowW
        find_window.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        find_window.restype = wintypes.HWND
        return int(find_window(None, "Lians") or 0)
    except (AttributeError, OSError, ValueError):
        return 0


class DesktopApi:
    """Small, read-only JS bridge for the lifeline console."""

    def __init__(self, store: MemoryStore, bridge: Any | None = None) -> None:
        self.store = store
        self.bridge = bridge
        self._window: Any | None = None
        self.preferred_client: str | None = None

    def snapshot(self) -> dict[str, Any]:
        client = _active_ai_client(self.preferred_client)
        self.preferred_client = client
        metrics = lifeline_snapshot(self.store, limit=4)
        return {
            "agent": (
                {"key": client, "label": _AI_LABELS[client], "connected": True}
                if client
                else None
            ),
            "metrics": metrics,
        }

    def minimize(self) -> bool:
        if self._window is not None:
            self._window.minimize()
        return True

    def drag_window(self) -> bool:
        """Hand dragging to Windows so edge snap and top-edge maximize stay native."""

        handle = _lians_window_handle()
        if not handle:
            return False
        ctypes.windll.user32.ReleaseCapture()
        ctypes.windll.user32.SendMessageW(handle, 0x00A1, 2, 0)
        return True

    def resize_window(self, edge: int) -> bool:
        """Resize a frameless window through the standard Windows hit targets."""

        handle = _lians_window_handle()
        hit_test = int(edge)
        if not handle or hit_test not in range(10, 18):
            return False
        ctypes.windll.user32.ReleaseCapture()
        ctypes.windll.user32.SendMessageW(handle, 0x00A1, hit_test, 0)
        return True

    def toggle_maximize(self) -> bool:
        if self._window is None:
            return False
        handle = _lians_window_handle()
        if handle and ctypes.windll.user32.IsZoomed(handle):
            self._window.restore()
        else:
            self._window.maximize()
        return True

    def close(self) -> bool:
        if self._window is not None:
            self._window.destroy()
        return True


def _start_bridge(store: MemoryStore) -> Any | None:
    if _running_bridge_origin() is not None:
        return None
    from .bridge import BridgeApplication

    bridge = BridgeApplication(store)
    threading.Thread(target=bridge.serve, daemon=True, name="lians-loopback-bridge").start()
    return bridge


def _prepare_window(window: Any, background_start: bool) -> None:
    """Apply the initial state after WinForms has installed the frameless chrome."""

    if not background_start:
        window.maximize()


def launch(*, background_start: bool = False) -> None:
    """Open the native WebView companion without a browser or console window."""

    if sys.platform != "win32":
        from .gui import launch as launch_tk

        launch_tk(background_start=background_start)
        return
    _enable_windows_dpi_awareness()
    if not any(target.configured for target in client_targets().values()):
        from .gui import launch as launch_tk

        launch_tk(background_start=background_start)
        return

    _ensure_windows_autostart()
    if _running_bridge_origin() is not None:
        if background_start:
            return
        if _focus_existing_window(timeout=4.0):
            return

    import webview

    store = MemoryStore(default_data_path())
    bridge = _start_bridge(store)
    api = DesktopApi(store, bridge)
    page = files("lians_easy").joinpath("desktop", "web", "index.html")
    window = webview.create_window(
        "Lians",
        str(page),
        js_api=api,
        width=1440,
        height=900,
        min_size=(900, 640),
        hidden=background_start,
        maximized=False,
        background_color="#020304",
        text_select=False,
        zoomable=False,
        frameless=True,
        easy_drag=False,
    )
    api._window = window

    try:
        webview.start(
            _prepare_window,
            (window, background_start),
            gui="edgechromium",
            debug=False,
            private_mode=False,
            storage_path=str(user_data_dir() / "desktop-webview"),
        )
    finally:
        if bridge is not None:
            bridge.shutdown()
