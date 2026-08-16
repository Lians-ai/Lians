from __future__ import annotations


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
