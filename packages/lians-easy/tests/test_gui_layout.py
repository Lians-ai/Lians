from __future__ import annotations

import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop layout check")
def test_primary_setup_action_fits_at_125_percent_scaling() -> None:
    import tkinter as tk

    from lians_easy.gui import SetupApp

    root = tk.Tk()
    try:
        # 1.75 is Tk's common effective scale on a Windows display configured
        # at 125%. Keep the primary action visible without requiring scrolling.
        root.tk.call("tk", "scaling", 1.75)
        app = SetupApp(root)
        root.update_idletasks()
        root.update()

        assert app.shell.winfo_reqwidth() <= root.winfo_width()
        assert app.shell.winfo_reqheight() <= root.winfo_height()
        assert app.install_button.winfo_ismapped()
        assert (
            app.install_button.winfo_rooty() + app.install_button.winfo_height()
            <= root.winfo_rooty() + root.winfo_height()
        )

        if app.other_rows:
            app._toggle_other_apps()
            root.update_idletasks()
            root.update()
            assert app.install_button.winfo_ismapped()
            assert (
                app.install_button.winfo_rooty() + app.install_button.winfo_height()
                <= root.winfo_rooty() + root.winfo_height()
            )
    finally:
        root.destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop layout check")
def test_resident_companion_is_clear_and_fits_at_125_percent_scaling(monkeypatch) -> None:
    import tkinter as tk

    from lians_easy.gui import CompanionApp
    active_client: dict[str, str | None] = {"key": "codex"}
    monkeypatch.setattr(
        "lians_easy.gui._active_ai_client",
        lambda _preferred=None: active_client["key"],
    )
    monkeypatch.setattr("lians_easy.gui._save_companion_theme", lambda _theme: None)

    class FakeStore:
        def stats(self):
            return {
                "current": 3,
                "efficiency": {
                    "context_events": 7,
                    "memories_reused": 12,
                    "available_memory_tokens_estimate": 4000,
                    "repeated_memory_tokens_avoided_estimate": 3000,
                },
            }

        def receipts(self, *, limit):
            return [
                {
                    "created_at": "2026-08-16T14:30:00+00:00",
                    "client": "codex",
                    "project": {"name": "Lians"},
                    "memory_count": 3,
                    "token_estimate": 200,
                    "efficiency": {
                        "repeated_memory_tokens_avoided_estimate": 800,
                    },
                }
            ]

    class FakeBridge:
        running = True
        origin = "http://127.0.0.1:7317"
        store = FakeStore()

        def serve(self):
            raise AssertionError("layout test must not start a server")

        def shutdown(self):
            return None

    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.75)
        app = CompanionApp(
            root,
            FakeBridge(),
            start_bridge=False,
            animate_intro=False,
            theme="dark",
        )
        root.update_idletasks()
        root.update()
        app._refresh()
        root.update_idletasks()

        assert app.shell.winfo_reqwidth() <= app.body_canvas.winfo_width()
        assert app.body_canvas.winfo_height() <= root.winfo_height()
        assert app.body_canvas.bbox("all") is not None
        assert app.scrollbar.winfo_class() == "Canvas"
        app.body_canvas.yview_moveto(1.0)
        root.update_idletasks()
        assert app.open_button.winfo_ismapped()
        assert app.open_button.winfo_rooty() < root.winfo_rooty() + root.winfo_height()
        assert str(app.open_button["state"]) == "normal"
        assert app.open_button["text"] == "Refresh"
        assert app.status.get() == "Lians is running"
        assert app.memory_status.get() == "3 saved memories"
        assert app.token_value.get() == "~3,000"
        assert app.event_value.get() == "7"
        assert app.reuse_value.get() == "12"
        assert app.reduction_status.get() == "About 75% less repeated context"
        assert any(label["text"] == "Codex · Lians" for label in app.activity_labels)
        assert app.font_family == "Sora"
        assert app.display_font_family == "Sora"
        assert app.intro_lotus_frames[0].width() == 1400
        assert app.intro_lotus_frames[-1].width() == 128
        assert bool(root.overrideredirect())
        assert app.connection_status.get() == "Codex active"
        assert app.connection_dot["foreground"] == app.colors["green"]
        app._render_lifeline()
        canvas_items = app.lifeline_canvas.find_all()
        assert len(canvas_items) == 1
        assert app.lifeline_canvas.type(canvas_items[0]) == "image"
        assert app.lifeline_canvas.itemcget(canvas_items[0], "image") == str(
            app.lotus_mark
        )
        assert all(app.lifeline_canvas.type(item) != "text" for item in canvas_items)
        assert app.close_button["text"] == "×"
        assert app.stop_button["text"] == "Close"

        active_client["key"] = None
        app._refresh()
        assert app.connection_status.get() == "No connection detected"
        assert app.connection_dot["foreground"] == app.colors["muted"]

        app._toggle_theme()
        root.update_idletasks()
        assert app.theme_name == "light"
        assert app.theme_button["text"] == "☀"
        assert app.shell["background"] == "#F1F0EC"

        app._work_area = lambda: (0, 0, 1200, 800)
        top_drag = type("DragEvent", (), {"x_root": 600, "y_root": 200})()
        top_release = type("DragEvent", (), {"x_root": 600, "y_root": 0})()
        app._begin_drag(top_drag)
        app._finish_drag(top_release)
        root.update_idletasks()
        assert app._maximized
        assert app._snap_state == "maximized"
        assert root.geometry().startswith("1200x800")

        app._toggle_maximize()
        root.update_idletasks()
        assert not app._maximized
        assert app._snap_state is None
    finally:
        root.destroy()
