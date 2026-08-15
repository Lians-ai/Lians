from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named event contract")
def test_installer_event_stops_a_running_lians_process() -> None:
    event_name = rf"Local\LiansLifecycleTest-{uuid.uuid4()}"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_event = kernel32.CreateEventW
    create_event.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    create_event.restype = wintypes.HANDLE
    set_event = kernel32.SetEvent
    set_event.argtypes = (wintypes.HANDLE,)
    set_event.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    event = create_event(None, True, False, event_name)
    assert event
    package_root = Path(__file__).parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(package_root), environment.get("PYTHONPATH"))
        if value
    )
    code = (
        "import time; "
        "from lians_easy.lifecycle import listen_for_windows_installer_shutdown; "
        f"listen_for_windows_installer_shutdown({event_name!r}); "
        "print('ready', flush=True); time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        assert set_event(event)
        assert process.wait(timeout=10) == 0
    finally:
        close_handle(event)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
