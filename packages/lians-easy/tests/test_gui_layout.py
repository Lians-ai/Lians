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
def test_resident_companion_is_clear_and_fits_at_125_percent_scaling() -> None:
    import tkinter as tk

    from lians_easy.gui import CompanionApp

    class FakeStore:
        def stats(self):
            return {"current": 3}

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
        app = CompanionApp(root, FakeBridge(), start_bridge=False)
        root.update_idletasks()
        root.update()
        app._refresh()
        root.update_idletasks()

        assert app.shell.winfo_reqwidth() <= root.winfo_width()
        assert app.shell.winfo_reqheight() <= root.winfo_height()
        assert app.open_button.winfo_ismapped()
        assert str(app.open_button["state"]) == "normal"
        assert app.status.get() == "Lians is running in the background"
        assert app.memory_status.get() == "3 saved memories"
    finally:
        root.destroy()
