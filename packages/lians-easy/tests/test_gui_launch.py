from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

try:
    import tkinter  # noqa: F401
except ImportError:
    pytest.skip(
        "The optional native GUI requires a Python build with Tk support",
        allow_module_level=True,
    )


def test_active_ai_client_prefers_foreground_then_retains_last_open_client() -> None:
    from lians_easy.gui import _active_ai_client

    processes = {
        10: "cursor.exe",
        20: "claude.exe",
        30: "explorer.exe",
    }

    assert (
        _active_ai_client(
            "cursor", processes=processes, foreground_process_id=20
        )
        == "claude"
    )
    assert (
        _active_ai_client(
            "cursor", processes=processes, foreground_process_id=30
        )
        == "cursor"
    )
    assert (
        _active_ai_client(
            processes={30: "explorer.exe"}, foreground_process_id=30
        )
        is None
    )


def test_launch_restores_existing_native_window(monkeypatch) -> None:
    from lians_easy import gui

    focused: list[bool] = []
    monkeypatch.setattr(
        gui,
        "_running_bridge_origin",
        lambda: "http://127.0.0.1:7317",
    )
    monkeypatch.setattr(
        gui,
        "_focus_existing_companion",
        lambda: focused.append(True) or True,
    )
    monkeypatch.setattr(
        gui.tk,
        "Tk",
        lambda: (_ for _ in ()).throw(AssertionError("must reuse the native window")),
    )

    gui._launch_companion()

    assert focused == [True]


def test_background_launch_does_not_restore_an_existing_window(monkeypatch) -> None:
    from lians_easy import gui

    monkeypatch.setattr(
        gui,
        "_running_bridge_origin",
        lambda: "http://127.0.0.1:7317",
    )
    monkeypatch.setattr(
        gui,
        "_focus_existing_companion",
        lambda: (_ for _ in ()).throw(AssertionError("background launch must stay quiet")),
    )

    gui._launch_companion(background_start=True)


def test_frozen_windows_app_registers_quiet_user_startup(monkeypatch) -> None:
    from lians_easy import gui

    writes: list[tuple[str, int, str]] = []

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_QUERY_VALUE=1,
        KEY_SET_VALUE=2,
        REG_SZ=1,
        CreateKeyEx=lambda *_args: FakeKey(),
        QueryValueEx=lambda *_args: (_ for _ in ()).throw(FileNotFoundError()),
        SetValueEx=lambda _key, name, _reserved, kind, value: writes.append(
            (name, kind, value)
        ),
    )
    monkeypatch.setattr(gui.sys, "platform", "win32")
    monkeypatch.setattr(gui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui.sys, "executable", r"C:\Apps\Lians.exe")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    gui._ensure_windows_autostart()

    assert writes == [("Lians", 1, '"C:\\Apps\\Lians.exe" --background')]


def test_launch_attaches_native_window_when_only_bridge_exists(monkeypatch) -> None:
    from lians_easy import gui, mcp, store

    class FakeRoot:
        mainloop_called = False

        def mainloop(self) -> None:
            self.mainloop_called = True

    fake_root = FakeRoot()
    fake_store = object()
    companion_calls: list[tuple[object, object, bool, bool]] = []

    monkeypatch.setattr(
        gui,
        "_running_bridge_origin",
        lambda: "http://127.0.0.1:7317",
    )
    monkeypatch.setattr(gui, "_focus_existing_companion", lambda: False)
    monkeypatch.setattr(gui.tk, "Tk", lambda: fake_root)
    monkeypatch.setattr(mcp, "default_data_path", lambda: "memory.json")
    monkeypatch.setattr(store, "MemoryStore", lambda _path: fake_store)
    monkeypatch.setattr(
        gui,
        "CompanionApp",
        lambda root, bridge, *, start_bridge, owns_bridge: companion_calls.append(
            (root, bridge, start_bridge, owns_bridge)
        ),
    )

    gui._launch_companion()

    assert fake_root.mainloop_called
    assert len(companion_calls) == 1
    root, bridge, start_bridge, owns_bridge = companion_calls[0]
    assert root is fake_root
    assert bridge.origin == "http://127.0.0.1:7317"
    assert bridge.store is fake_store
    assert not start_bridge
    assert not owns_bridge
