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

from .codex_lifeline import codex_lifeline_snapshot
from .installer import client_targets, user_data_dir, write_support_report
from .lifeline import lifeline_snapshot
from .mcp import default_data_path
from .store import MemoryStore

_AI_PROCESS_NAMES = {
    "claude": {"claude.exe", "claude desktop.exe"},
    "codex": {"codex.exe", "codex app.exe"},
    "cursor": {"cursor.exe"},
}
_AI_LABELS = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor"}
_AUTO_HIDE_TASKBAR_GAP = 2


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class _AppBarData(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


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


def _window_rect(handle: int) -> tuple[int, int, int, int] | None:
    if sys.platform != "win32" or not handle:
        return None
    try:
        rect = wintypes.RECT()
        get_rect = ctypes.windll.user32.GetWindowRect
        get_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        get_rect.restype = wintypes.BOOL
        if not get_rect(handle, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _taskbar_edge_for_monitor(
    monitor: int, monitor_bounds: tuple[int, int, int, int]
) -> str | None:
    """Locate the taskbar edge for the monitor owning the Lians window."""

    try:
        user32 = ctypes.windll.user32
        get_class_name = user32.GetClassNameW
        get_class_name.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        get_class_name.restype = ctypes.c_int
        monitor_from_window = user32.MonitorFromWindow
        monitor_from_window.argtypes = (wintypes.HWND, wintypes.DWORD)
        monitor_from_window.restype = wintypes.HMONITOR
        get_rect = user32.GetWindowRect
        get_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        get_rect.restype = wintypes.BOOL
        taskbars: list[tuple[int, int, int, int]] = []

        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def collect(handle: int, _lparam: int) -> bool:
            class_name = ctypes.create_unicode_buffer(64)
            if not get_class_name(handle, class_name, len(class_name)):
                return True
            if class_name.value not in {"Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
                return True
            if int(monitor_from_window(handle, 2) or 0) != int(monitor):
                return True
            rect = wintypes.RECT()
            if get_rect(handle, ctypes.byref(rect)):
                taskbars.append((rect.left, rect.top, rect.right, rect.bottom))
            return True

        enum_windows = user32.EnumWindows
        enum_windows.argtypes = (callback_type, wintypes.LPARAM)
        enum_windows.restype = wintypes.BOOL
        enum_windows(collect, 0)
        if not taskbars:
            return None
        left, top, right, bottom = taskbars[0]
        monitor_left, monitor_top, monitor_right, monitor_bottom = monitor_bounds
        if right - left >= bottom - top:
            return (
                "top"
                if abs(top - monitor_top) <= abs(bottom - monitor_bottom)
                else "bottom"
            )
        return (
            "left"
            if abs(left - monitor_left) <= abs(right - monitor_right)
            else "right"
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _reserve_taskbar_trigger(
    bounds: tuple[int, int, int, int], edge: str | None
) -> tuple[int, int, int, int]:
    """Leave Windows' auto-hide trigger strip outside a frameless window."""

    left, top, right, bottom = bounds
    if edge == "left":
        left += _AUTO_HIDE_TASKBAR_GAP
    elif edge == "top":
        top += _AUTO_HIDE_TASKBAR_GAP
    elif edge == "right":
        right -= _AUTO_HIDE_TASKBAR_GAP
    elif edge == "bottom":
        bottom -= _AUTO_HIDE_TASKBAR_GAP
    return (left, top, right, bottom)


def _taskbar_safe_work_area(handle: int) -> tuple[int, int, int, int] | None:
    if sys.platform != "win32" or not handle:
        return None
    try:
        user32 = ctypes.windll.user32
        monitor_from_window = user32.MonitorFromWindow
        monitor_from_window.argtypes = (wintypes.HWND, wintypes.DWORD)
        monitor_from_window.restype = wintypes.HMONITOR
        get_monitor_info = user32.GetMonitorInfoW
        get_monitor_info.argtypes = (wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo))
        get_monitor_info.restype = wintypes.BOOL
        monitor = int(monitor_from_window(handle, 2) or 0)
        if not monitor:
            return None
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if not get_monitor_info(monitor, ctypes.byref(info)):
            return None
        work = (info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom)
        monitor_bounds = (
            info.rcMonitor.left,
            info.rcMonitor.top,
            info.rcMonitor.right,
            info.rcMonitor.bottom,
        )
        appbar = _AppBarData()
        appbar.cbSize = ctypes.sizeof(_AppBarData)
        appbar_state = ctypes.windll.shell32.SHAppBarMessage(4, ctypes.byref(appbar))
        if int(appbar_state) & 0x1:
            edge = _taskbar_edge_for_monitor(monitor, monitor_bounds)
            work = _reserve_taskbar_trigger(work, edge)
        return work
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _set_window_bounds(handle: int, bounds: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = bounds
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return False
    try:
        set_window_pos = ctypes.windll.user32.SetWindowPos
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
        return bool(
            set_window_pos(
                handle,
                None,
                left,
                top,
                width,
                height,
                0x0004 | 0x0020 | 0x0040,
            )
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _native_window_state(handle: int) -> str:
    """Return the state Windows is actually using for the companion window."""

    if sys.platform != "win32" or not handle:
        return "windowed"
    try:
        is_zoomed = ctypes.windll.user32.IsZoomed
        is_zoomed.argtypes = (wintypes.HWND,)
        is_zoomed.restype = wintypes.BOOL
        if is_zoomed(handle):
            return "maximized"
        rect = _window_rect(handle)
        work = _taskbar_safe_work_area(handle)
        if rect is not None and work is not None and all(
            abs(current - expected) <= 2 for current, expected in zip(rect, work)
        ):
            return "maximized"
        return "windowed"
    except (AttributeError, OSError, ValueError):
        return "windowed"


class DesktopApi:
    """Narrow JS bridge for the lifeline console and safe local actions."""

    def __init__(
        self,
        store: MemoryStore,
        bridge: Any | None = None,
        *,
        downloads_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.bridge = bridge
        self._downloads_dir = downloads_dir
        self._window: Any | None = None
        self._drag_lock = threading.Lock()
        self._dragging = False
        self._restore_bounds: tuple[int, int, int, int] | None = None
        self.preferred_client: str | None = None

    def snapshot(self) -> dict[str, Any]:
        client = _active_ai_client(self.preferred_client)
        self.preferred_client = client
        metrics = lifeline_snapshot(self.store, limit=4)
        if client == "codex":
            metrics = codex_lifeline_snapshot(limit=4) or metrics
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

    def save_help_report(self) -> dict[str, Any]:
        """Save a redacted report without exposing an absolute user path to JS."""

        try:
            directory = self._downloads_dir or (Path.home() / "Downloads")
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / "Lians-help-report.json"
            suffix = 2
            while destination.exists():
                destination = directory / f"Lians-help-report-{suffix}.json"
                suffix += 1
                if suffix > 1000:
                    raise OSError("too many existing help reports")
            write_support_report(destination)
            return {
                "saved": True,
                "filename": destination.name,
                "location": "Downloads",
            }
        except OSError:
            return {
                "saved": False,
                "message": "Could not save the help report. Try again after checking Downloads access.",
            }

    def start_drag(self) -> bool:
        """Follow the physical Windows cursor while the title bar is held."""

        handle = _lians_window_handle()
        if not handle or sys.platform != "win32":
            return False
        with self._drag_lock:
            if self._dragging:
                return True
            self._dragging = True
        threading.Thread(
            target=self._drag_window_loop,
            args=(handle,),
            daemon=True,
            name="lians-native-window-drag",
        ).start()
        return True

    def _drag_window_loop(self, handle: int) -> None:
        try:
            user32 = ctypes.windll.user32
            get_key_state = user32.GetAsyncKeyState
            get_key_state.argtypes = (ctypes.c_int,)
            get_key_state.restype = ctypes.c_short
            get_cursor = user32.GetCursorPos
            get_cursor.argtypes = (ctypes.POINTER(wintypes.POINT),)
            get_cursor.restype = wintypes.BOOL
            get_rect = user32.GetWindowRect
            get_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
            get_rect.restype = wintypes.BOOL
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

            cursor = wintypes.POINT()
            rect = wintypes.RECT()
            if not (get_key_state(0x01) & 0x8000):
                return
            if not get_cursor(ctypes.byref(cursor)) or not get_rect(
                handle, ctypes.byref(rect)
            ):
                return

            if _native_window_state(handle) == "maximized":
                current_width = max(1, rect.right - rect.left)
                ratio = min(0.9, max(0.1, (cursor.x - rect.left) / current_width))
                restore = self._clamped_restore_bounds(handle)
                width = restore[2] - restore[0]
                height = restore[3] - restore[1]
                offset_x = int(width * ratio)
                offset_y = 34
                show_window(handle, 9)  # SW_RESTORE
                set_window_pos(
                    handle,
                    None,
                    cursor.x - offset_x,
                    cursor.y - offset_y,
                    width,
                    height,
                    0x0004 | 0x0040,
                )
            else:
                offset_x = cursor.x - rect.left
                offset_y = cursor.y - rect.top

            last_position: tuple[int, int] | None = None
            while get_key_state(0x01) & 0x8000:
                if not get_cursor(ctypes.byref(cursor)):
                    break
                position = (cursor.x - offset_x, cursor.y - offset_y)
                if position != last_position:
                    set_window_pos(
                        handle,
                        None,
                        position[0],
                        position[1],
                        0,
                        0,
                        0x0001 | 0x0004 | 0x0040,
                    )
                    last_position = position
                time.sleep(1 / 120)

            work = _taskbar_safe_work_area(handle)
            snap_top = work[1] if work is not None else 0
            if get_cursor(ctypes.byref(cursor)) and cursor.y <= snap_top + 8:
                current = _window_rect(handle)
                self._maximize_to_work_area(handle, restore_bounds=current)
        except (AttributeError, OSError, TypeError, ValueError):
            return
        finally:
            with self._drag_lock:
                self._dragging = False

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

    def _clamped_restore_bounds(self, handle: int) -> tuple[int, int, int, int]:
        work = _taskbar_safe_work_area(handle) or (0, 0, 1920, 1080)
        work_left, work_top, work_right, work_bottom = work
        work_width = max(1, work_right - work_left)
        work_height = max(1, work_bottom - work_top)
        source = self._restore_bounds
        if source is None:
            width = min(1440, work_width)
            height = min(900, work_height)
            left = work_left + max(0, (work_width - width) // 2)
            top = work_top + max(0, (work_height - height) // 2)
            return (left, top, left + width, top + height)
        width = min(max(900, source[2] - source[0]), work_width)
        height = min(max(640, source[3] - source[1]), work_height)
        left = min(max(source[0], work_left), work_right - width)
        top = min(max(source[1], work_top), work_bottom - height)
        return (left, top, left + width, top + height)

    def _maximize_to_work_area(
        self,
        handle: int,
        *,
        restore_bounds: tuple[int, int, int, int] | None = None,
    ) -> bool:
        if restore_bounds is not None:
            self._restore_bounds = restore_bounds
        work = _taskbar_safe_work_area(handle)
        if work is None:
            return False
        ctypes.windll.user32.ShowWindow(handle, 9)  # SW_RESTORE
        return _set_window_bounds(handle, work)

    def toggle_maximize(self) -> str:
        handle = _lians_window_handle()
        if not handle:
            return "windowed"
        if _native_window_state(handle) == "maximized":
            ctypes.windll.user32.ShowWindow(handle, 9)  # SW_RESTORE
            _set_window_bounds(handle, self._clamped_restore_bounds(handle))
            return "windowed"
        current = _window_rect(handle)
        return (
            "maximized"
            if self._maximize_to_work_area(handle, restore_bounds=current)
            else "windowed"
        )

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
            gui="edgechromium",
            debug=False,
            private_mode=False,
            storage_path=str(user_data_dir() / "desktop-webview"),
        )
    finally:
        if bridge is not None:
            bridge.shutdown()
