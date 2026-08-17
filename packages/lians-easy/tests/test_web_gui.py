from __future__ import annotations

import hashlib
from importlib.resources import files
from pathlib import Path


class FakeStore:
    def stats(self):
        return {
            "current": 4,
            "efficiency": {
                "context_events": 8,
                "memories_reused": 12,
                "available_memory_tokens_estimate": 5000,
                "repeated_memory_tokens_avoided_estimate": 3200,
            },
        }

    def receipts(self, *, limit):
        return []


def test_desktop_api_reports_only_the_active_agent_and_lifeline(monkeypatch) -> None:
    from lians_easy import web_gui

    monkeypatch.setattr(web_gui, "_active_ai_client", lambda _preferred=None: "codex")
    api = web_gui.DesktopApi(FakeStore())

    result = api.snapshot()

    assert result["agent"] == {"key": "codex", "label": "Codex", "connected": True}
    assert result["metrics"]["saved_memories"] == 4
    assert result["metrics"]["context_events"] == 8
    assert result["metrics"]["memories_reused"] == 12
    assert result["metrics"]["repeated_tokens_avoided_estimate"] == 3200


def test_desktop_header_uses_the_approved_favicon_byte_for_byte() -> None:
    desktop = files("lians_easy").joinpath("desktop")
    source = desktop.joinpath("favicon.png").read_bytes()
    web = desktop.joinpath("web", "favicon.png").read_bytes()

    assert web == source
    assert hashlib.sha256(web).hexdigest() == (
        "8c01e301e8c9a775f2bece5027cffcbb043d94c286bb10b2a6986ef9e4edb4f6"
    )


def test_desktop_ui_uses_local_animation_libraries_and_no_remote_assets() -> None:
    package_root = Path(__file__).resolve().parents[1]
    source = (package_root / "desktop-ui" / "src" / "main.js").read_text(encoding="utf-8")
    html = (package_root / "lians_easy" / "desktop" / "web" / "index.html").read_text(
        encoding="utf-8"
    )
    css = (package_root / "lians_easy" / "desktop" / "web" / "style.css").read_text(
        encoding="utf-8"
    )

    assert 'from "animejs"' in source
    assert 'from "motion"' in source
    assert "createTimeline" in source
    assert "animeSpring" in source
    assert all(name in source for name in ("hover", "press", "resize", "frame"))
    assert '<img src="favicon.png" width="40" height="40"' in html
    assert "Extended usage. Better memory." in html
    assert 'id="titlebar"' in html
    assert "resize-handle" in html
    assert "drag_window" in source
    assert "https://" not in html
    assert "overflow: hidden" in css


def test_companion_build_separates_windowed_app_from_console_runtime() -> None:
    package_root = Path(__file__).resolve().parents[1]
    script = (package_root / "scripts" / "build_windows_companion.ps1").read_text(
        encoding="utf-8"
    )

    assert "--windowed" in script
    assert "--onedir" in script
    assert "companion_entrypoint.py" in script
    assert "LiansMemory.exe" in script
    assert "windows-launcher.cs" in script
