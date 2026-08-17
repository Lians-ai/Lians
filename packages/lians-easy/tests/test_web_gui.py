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
    monkeypatch.setattr(web_gui, "codex_lifeline_snapshot", lambda **_kwargs: None)
    api = web_gui.DesktopApi(FakeStore())

    result = api.snapshot()

    assert result["agent"] == {"key": "codex", "label": "Codex", "connected": True}
    assert result["metrics"]["saved_memories"] == 4
    assert result["metrics"]["context_events"] == 8
    assert result["metrics"]["memories_reused"] == 12
    assert result["metrics"]["repeated_tokens_avoided_estimate"] == 3200


def test_desktop_api_uses_installed_codex_receipts(monkeypatch) -> None:
    from lians_easy import web_gui

    codex_metrics = {
        "saved_memories": 20,
        "context_events": 9,
        "memories_reused": 34,
        "context_tokens_sent_estimate": 2400,
        "repeated_tokens_avoided_estimate": 0,
        "activity": [],
    }
    monkeypatch.setattr(web_gui, "_active_ai_client", lambda _preferred=None: "codex")
    monkeypatch.setattr(
        web_gui, "codex_lifeline_snapshot", lambda **_kwargs: codex_metrics
    )

    result = web_gui.DesktopApi(FakeStore()).snapshot()

    assert result["metrics"] is codex_metrics


def test_desktop_api_saves_a_redacted_help_report_without_returning_user_path(
    tmp_path, monkeypatch
) -> None:
    from lians_easy import web_gui

    captured: list[Path] = []

    def fake_write(destination: Path) -> Path:
        captured.append(destination)
        destination.write_text('{"schema":"lians-support-report/v1"}\n')
        return destination

    monkeypatch.setattr(web_gui, "write_support_report", fake_write)
    (tmp_path / "Lians-help-report.json").write_text("existing")

    result = web_gui.DesktopApi(FakeStore(), downloads_dir=tmp_path).save_help_report()

    assert result == {
        "saved": True,
        "filename": "Lians-help-report-2.json",
        "location": "Downloads",
    }
    assert captured == [tmp_path / "Lians-help-report-2.json"]
    assert str(tmp_path) not in str(result)


def test_auto_hide_taskbar_trigger_strip_stays_outside_maximized_window() -> None:
    from lians_easy.web_gui import _reserve_taskbar_trigger

    monitor = (0, 0, 1920, 1080)

    assert _reserve_taskbar_trigger(monitor, "bottom") == (0, 0, 1920, 1078)
    assert _reserve_taskbar_trigger(monitor, "top") == (0, 2, 1920, 1080)
    assert _reserve_taskbar_trigger(monitor, "left") == (2, 0, 1920, 1080)
    assert _reserve_taskbar_trigger(monitor, "right") == (0, 0, 1918, 1080)
    assert _reserve_taskbar_trigger(monitor, None) == monitor


def test_side_snap_divides_the_taskbar_safe_work_area() -> None:
    from lians_easy.web_gui import _side_snap_bounds

    work_area = (2, 0, 1918, 1040)

    assert _side_snap_bounds(work_area, "left") == (2, 0, 960, 1040)
    assert _side_snap_bounds(work_area, "right") == (960, 0, 1918, 1040)


def test_desktop_header_uses_the_approved_favicon_byte_for_byte() -> None:
    desktop = files("lians_easy").joinpath("desktop")
    source = desktop.joinpath("favicon.png").read_bytes()
    web = desktop.joinpath("web", "favicon.png").read_bytes()

    assert web == source
    assert hashlib.sha256(web).hexdigest() == (
        "8c01e301e8c9a775f2bece5027cffcbb043d94c286bb10b2a6986ef9e4edb4f6"
    )
    wordmark_source = files("lians_easy").joinpath("app", "logo-blue.png").read_bytes()
    wordmark_web = desktop.joinpath("web", "lians-wordmark.png").read_bytes()
    assert wordmark_web == wordmark_source


def test_desktop_ui_uses_local_animation_libraries_and_no_remote_assets() -> None:
    package_root = Path(__file__).resolve().parents[1]
    source = (package_root / "desktop-ui" / "src" / "main.js").read_text(encoding="utf-8")
    html = (package_root / "lians_easy" / "desktop" / "web" / "index.html").read_text(
        encoding="utf-8"
    )
    css = (package_root / "lians_easy" / "desktop" / "web" / "style.css").read_text(
        encoding="utf-8"
    )
    web_gui_source = (package_root / "lians_easy" / "web_gui.py").read_text(
        encoding="utf-8"
    )

    assert 'from "animejs"' in source
    assert 'from "motion"' in source
    assert "createTimeline" in source
    assert all(name in source for name in ("hover", "press", "resize", "frame"))
    assert "wordmarkTargetPoints" in source
    assert 'loadImage("lians-wordmark.png")' in source
    assert "startAmbientBackground" in source
    assert 'class="water-scene water-scene-base"' in html
    assert 'class="water-scene water-scene-drift"' in html
    assert 'background-image: url("water-lights.png")' in css
    assert "water-refraction" in css
    assert ':root[data-theme="light"] .ambient-field' in css
    assert "filter: blur(8px)" in css
    assert 'id="tokens-label"' in html
    assert 'id="tokens-detail"' in html
    assert "metrics.token_metric" in source
    assert 'class="brand-wordmark"' in html
    assert 'src="lians-wordmark.png"' in html
    assert 'width="108"' in html
    assert "connection[data-agent=\"codex\"] img" in css
    assert "filter: invert(1)" in css
    assert "connection.dataset.agent = snapshot.agent.key" in source
    assert "delete connection.dataset.agent" in source
    assert 'id="titlebar" class="topbar reveal"' in html
    assert 'id="intro-particles"' in html
    assert 'id="ambient-canvas"' not in html
    assert 'id="ambient-field"' in html
    assert 'id="light-stage"' in html
    assert 'id="ambient-status"' in html
    assert "ambient-brain" not in html
    assert 'src="lotus.png"' in html
    assert "Extended usage. Better memory." in html
    assert 'id="titlebar"' in html
    assert 'class="restore-icon"' in html
    assert "resize-handle" in html
    assert "intro-ring" not in html
    assert "radial-gradient" not in css
    assert '"IvyPresto Text"' in css
    assert '"IvyPresto Display"' in css
    assert "water-drift" in css
    assert "prefers-reduced-motion" in css
    assert 'content.addEventListener("contextmenu", toggleLightField)' in source
    assert 'localStorage.setItem("lians-light-field-state", next)' in source
    assert "items.slice(0, 3)" in source
    assert "AUTO_REFRESH_INTERVAL_MS = 10 * 60 * 1000" in source
    assert "window.setInterval(refresh, AUTO_REFRESH_INTERVAL_MS)" in source
    assert "window.setInterval(refresh, 3000)" not in source
    assert "motionAnimate(" in source
    assert 'id="support-report"' in html
    assert 'id="support-status"' in html
    assert "save_help_report" in source
    assert "start_drag" in source
    assert "_side_snap_bounds" in web_gui_source
    assert 'self._snap_state = "left"' in web_gui_source
    assert 'self._snap_state = "right"' in web_gui_source
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


def test_windows_layout_checks_use_fresh_tk_processes() -> None:
    package_root = Path(__file__).resolve().parents[1]
    workflow = (package_root.parents[1] / ".github" / "workflows" / "build-lians-easy.yml").read_text(
        encoding="utf-8"
    )

    assert workflow.count(
        "python -m pytest packages/lians-easy/tests/test_gui_layout.py::"
    ) == 2
    assert "python -m pytest packages/lians-easy/tests/test_gui_layout.py -q" not in workflow


def test_native_launcher_owns_the_particle_intro_and_skips_the_web_replay() -> None:
    package_root = Path(__file__).resolve().parents[1]
    launcher = (package_root / "windows-launcher.cs").read_text(encoding="utf-8")
    entrypoint = (package_root / "companion_entrypoint.py").read_text(encoding="utf-8")
    source = (package_root / "desktop-ui" / "src" / "main.js").read_text(encoding="utf-8")

    assert "BuildParticles" in launcher
    assert '"lians-wordmark.png"' in launcher
    assert "ParticleWordmarkSurface" in launcher
    assert "requestedCount = 4200" in launcher
    assert "start.UseShellExecute = false" in launcher
    assert "start.CreateNoWindow = true" in launcher
    assert "start.WindowStyle = ProcessWindowStyle.Hidden" in launcher
    assert 'querySelector(".window-actions")' in source
    assert "event.stopPropagation()" in source
    assert "api.drag_window" not in source
    assert "api.start_drag" in source
    web_gui = (package_root / "lians_easy" / "web_gui.py").read_text(encoding="utf-8")
    assert "GetAsyncKeyState" in web_gui
    assert "lians-native-window-drag" in web_gui
    assert "time.sleep(1 / 120)" in web_gui
    assert "window_state" in source
    assert "applyWindowState" in source
    assert 'WithArgument(args, "--intro-complete")' in launcher
    assert 'parser.add_argument("--intro-complete"' in entrypoint
    assert "listen_for_windows_installer_shutdown" in entrypoint


def test_codex_setup_explains_the_required_hook_trust_step() -> None:
    package_root = Path(__file__).resolve().parents[1]
    source = (package_root / "lians_easy" / "gui.py").read_text(encoding="utf-8")

    assert '"codex" in result.get("requires_trust", [])' in source
    assert "open /hooks" in source
    assert "Copy /hooks" in source
