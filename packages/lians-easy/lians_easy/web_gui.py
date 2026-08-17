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


def _native_window_state(handle: int) -> str:
    """Return the state Windows is actually using for the companion window."""

    if sys.platform != "win32" or not handle:
        return "windowed"
    try:
        is_zoomed = ctypes.windll.user32.IsZoomed
        is_zoomed.argtypes = (wintypes.HWND,)
        is_zoomed.restype = wintypes.BOOL
        return "maximized" if is_zoomed(handle) else "windowed"
    except (AttributeError, OSError, ValueError):
        return "windowed"


def _begin_native_window_drag(handle: int) -> bool:
    """Start the real Windows move loop, restoring a maximized window first."""

    if sys.platform != "win32" or not handle:
        return False

    class WindowPlacement(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.UINT),
            ("flags", wintypes.UINT),
            ("show_cmd", wintypes.UINT),
            ("min_position", wintypes.POINT),
            ("max_position", wintypes.POINT),
            ("normal_position", wintypes.RECT),
        ]

    try:
        user32 = ctypes.windll.user32
        is_zoomed = user32.IsZoomed
        is_zoomed.argtypes = (wintypes.HWND,)
        is_zoomed.restype = wintypes.BOOL
        get_cursor = user32.GetCursorPos
        get_cursor.argtypes = (ctypes.POINTER(wintypes.POINT),)
        get_cursor.restype = wintypes.BOOL
        get_rect = user32.GetWindowRect
        get_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        get_rect.restype = wintypes.BOOL
        get_placement = user32.GetWindowPlacement
        get_placement.argtypes = (wintypes.HWND, ctypes.POINTER(WindowPlacement))
        get_placement.restype = wintypes.BOOL
        show_window = user32.ShowWindow
        show_window.argtypes = (wintypes.HWND, ctypes.c_int)
        show_window.restype = wintypes.BOOL
        set_window_pos = user32.SetWindowPos
        set_window_pos.argtypes = (
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        )
        set_window_pos.restype = wintypes.BOOL
        release_capture = user32.ReleaseCapture
        release_capture.argtypes = ()
        release_capture.restype = wintypes.BOOL
        send_message = user32.SendMessageW
        send_message.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        send_message.restype = ctypes.c_ssize_t

        if is_zoomed(handle):
            cursor = wintypes.POINT()
            current = wintypes.RECT()
            placement = WindowPlacement()
            placement.length = ctypes.sizeof(WindowPlacement)
            if get_cursor(ctypes.byref(cursor)) and get_rect(handle, ctypes.byref(current)):
                current_width = max(1, current.right - current.left)
                horizontal_ratio = min(
                    0.9,
                    max(0.1, (cursor.x - current.left) / current_width),
                )
                if get_placement(handle, ctypes.byref(placement)):
                    normal = placement.normal_position
                    width = max(900, normal.right - normal.left)
                    height = max(640, normal.bottom - normal.top)
                else:
                    width, height = 1440, 900
                show_window(handle, 9)  # SW_RESTORE
                set_window_pos(
                    handle,
                    None,
                    int(cursor.x - width * horizontal_ratio),
                    int(cursor.y - 34),
                    width,
                    height,
                    0x0004 | 0x0040,  # SWP_NOZORDER | SWP_SHOWWINDOW
                )

        release_capture()
        send_message(handle, 0x00A1, 2, 0)  # WM_NCLBUTTONDOWN, HTCAPTION
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


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

    def drag_window(self) -> str:
        """Hand dragging to Windows so restore, movement, and snap stay native."""

        handle = _lians_window_handle()
        _begin_native_window_drag(handle)
        return _native_window_state(handle)

    def resize_window(self, edge: int) -> bool:
        """Resize a frameless window through the standard Windows hit targets."""

        handle = _lians_window_handle()
        hit_test = int(edge)
        if not handle or hit_test not in range(10, 18):
            return False
        ctypes.windll.user32.ReleaseCapture()
        ctypes.windll.user32.SendMessageW(handle, 0x00A1, hit_test, 0)
        return True

    def window_state(self) -> str:
        return _native_window_state(_lians_window_handle())

    def toggle_maximize(self) -> str:
        handle = _lians_window_handle()
        if not handle:
            return "windowed"
        user32 = ctypes.windll.user32
        show_window = user32.ShowWindow
        show_window.argtypes = (wintypes.HWND, ctypes.c_int)
        show_window.restype = wintypes.BOOL
        target = "windowed" if _native_window_state(handle) == "maximized" else "maximized"
        show_window(handle, 9 if target == "windowed" else 3)
        return target

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


def launch(*, background_start: bool = False, intro_complete: bool = False) -> None:
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
    page_url = Path(str(page)).resolve().as_uri()
    if intro_complete:
        page_url += "#intro-complete"
    window = webview.create_window(
        "Lians",
        page_url,
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
