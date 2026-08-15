"""Process lifecycle hooks used by trusted desktop installers."""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import wintypes

WINDOWS_SHUTDOWN_EVENT = r"Local\LiansRuntimeShutdown-1c5da632-9c9f-4d41-a910-395372560303"
_WAIT_OBJECT_0 = 0
_INFINITE = 0xFFFFFFFF
_listener_started = False


def listen_for_windows_installer_shutdown(
    event_name: str = WINDOWS_SHUTDOWN_EVENT,
) -> None:
    """Exit frozen Windows runtimes when the per-user installer replaces them.

    The named event is scoped to the interactive Windows session. The installer
    briefly signals it before touching the executable, allowing every Lians mode
    (App, Bridge, or MCP) from this build to release its image-file lock.
    """

    global _listener_started
    if sys.platform != "win32" or _listener_started:
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_event = kernel32.CreateEventW
    create_event.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    create_event.restype = wintypes.HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for_single_object.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    event = create_event(None, True, False, event_name)
    if not event:
        return
    _listener_started = True

    def wait_for_installer() -> None:
        try:
            if wait_for_single_object(event, _INFINITE) == _WAIT_OBJECT_0:
                os._exit(0)
        finally:
            close_handle(event)

    threading.Thread(
        target=wait_for_installer,
        name="lians-installer-shutdown",
        daemon=True,
    ).start()
