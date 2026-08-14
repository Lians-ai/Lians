from __future__ import annotations

import json

from lians_easy.installer import (
    MANAGED_END,
    MANAGED_START,
    client_targets,
    install,
    plan,
    uninstall,
)


def test_installer_preserves_json_and_creates_backup(tmp_path, monkeypatch):
    roaming = tmp_path / "roaming"
    home = tmp_path / "home"
    config = roaming / "Claude" / "claude_desktop_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    result = install(["claude"], home=home)
    updated = json.loads(config.read_text())
    assert updated["theme"] == "dark"
    assert "other" in updated["mcpServers"]
    assert updated["mcpServers"]["lians"]["args"][-1] == "mcp"
    assert result["clients"][0]["backup"]

    removed = uninstall(["claude"], home=home)
    assert "lians" not in json.loads(config.read_text())["mcpServers"]
    assert removed["data_preserved"].endswith("memory.sqlite3")


def test_codex_managed_block_is_idempotent(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('model = "gpt-5"\n')
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    install(["codex"], home=home)
    install(["codex"], home=home)
    content = config.read_text()
    assert content.count(MANAGED_START) == 1
    assert content.count(MANAGED_END) == 1
    assert 'model = "gpt-5"' in content


def test_plan_reports_targets_without_writing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    result = plan(["codex"], action="install", home=home)

    assert result["changes_made"] is False
    assert result["clients"][0]["key"] == "codex"
    assert not (home / ".codex" / "config.toml").exists()


def test_antigravity_uses_current_global_mcp_path_on_every_platform(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr("lians_easy.installer.shutil.which", lambda _name: None)

    for platform in ("win32", "darwin", "linux"):
        monkeypatch.setattr("sys.platform", platform)
        target = client_targets(home)["antigravity"]

        assert target.label == "Antigravity CLI"
        assert target.config_path == home / ".gemini" / "config" / "mcp_config.json"
        assert target.detected is False


def test_antigravity_install_round_trips_without_touching_other_servers(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".gemini" / "config" / "mcp_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "theme": "dark",
                "mcpServers": {
                    "other": {"serverUrl": "https://example.test/mcp"},
                },
            }
        )
    )
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    first = install(["antigravity"], home=home)
    install(["antigravity"], home=home)
    updated = json.loads(config.read_text())

    assert updated["theme"] == "dark"
    assert updated["mcpServers"]["other"] == {"serverUrl": "https://example.test/mcp"}
    assert updated["mcpServers"]["lians"]["args"][-1] == "mcp"
    assert first["clients"][0]["config"] == str(config)
    assert first["clients"][0]["backup"]

    removed = uninstall(["antigravity"], home=home)
    final = json.loads(config.read_text())

    assert "lians" not in final["mcpServers"]
    assert final["mcpServers"]["other"] == {"serverUrl": "https://example.test/mcp"}
    assert removed["data_preserved"].endswith("memory.sqlite3")
