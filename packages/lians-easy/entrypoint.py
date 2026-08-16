"""PyInstaller entry point kept outside the package for reliable imports."""

from __future__ import annotations

import ctypes
import os
import sys


def _hide_owned_windows_console() -> None:
    """Hide Explorer's transient console while preserving real CLI output."""

    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        window = kernel32.GetConsoleWindow()
        if not window:
            return
        process_ids = (ctypes.c_ulong * 16)()
        count = kernel32.GetConsoleProcessList(process_ids, len(process_ids))
        if not count or count > len(process_ids):
            return
        executable = os.path.normcase(os.path.abspath(sys.executable))
        for process_id in process_ids[:count]:
            handle = kernel32.OpenProcess(0x1000, False, process_id)
            if not handle:
                return
            try:
                length = ctypes.c_ulong(32768)
                buffer = ctypes.create_unicode_buffer(length.value)
                if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(length)
                ):
                    return
                if os.path.normcase(os.path.abspath(buffer.value)) != executable:
                    return
            finally:
                kernel32.CloseHandle(handle)
        user32.ShowWindow(window, 0)
    except (AttributeError, OSError):
        return


_hide_owned_windows_console()

from lians_easy.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
