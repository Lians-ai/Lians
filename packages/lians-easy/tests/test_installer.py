from __future__ import annotations

import json
from pathlib import Path

import pytest
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


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_cline_cli_target_uses_supplied_home_on_every_platform(platform, tmp_path, monkeypatch):
    home = tmp_path / platform
    monkeypatch.setattr("sys.platform", platform)

    target = client_targets(home)["cline"]

    assert target.label == "Cline CLI"
    assert target.config_path == home / ".cline/data/settings/cline_mcp_settings.json"


def test_cline_cli_config_round_trip_is_safe_and_idempotent(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".cline" / "data" / "settings" / "cline_mcp_settings.json"
    config.parent.mkdir(parents=True)
    original = {"theme": "dark", "mcpServers": {"other": {"command": "other-mcp"}}}
    config.write_text(json.dumps(original), encoding="utf-8")
    data_home = tmp_path / "lians"
    monkeypatch.setenv("LIANS_EASY_HOME", str(data_home))

    target = client_targets(home)["cline"]
    assert target.label == "Cline CLI"
    assert target.config_path == config
    assert target.detected is True
    assert target.configured is False

    first = install(["cline"], home=home)
    installed = json.loads(config.read_text(encoding="utf-8"))
    second = install(["cline"], home=home)

    assert json.loads(config.read_text(encoding="utf-8")) == installed
    assert set(installed) == set(original)
    assert installed["theme"] == "dark"
    assert set(installed["mcpServers"]) == {"other", "lians"}
    assert installed["mcpServers"]["other"] == {"command": "other-mcp"}
    assert installed["mcpServers"]["lians"]["args"][-1] == "mcp"
    first_backup = Path(first["clients"][0]["backup"])
    second_backup = Path(second["clients"][0]["backup"])
    assert json.loads(first_backup.read_text(encoding="utf-8")) == original
    assert json.loads(second_backup.read_text(encoding="utf-8")) == installed
    assert client_targets(home)["cline"].configured is True

    removed = uninstall(["cline"], home=home)
    after_removal = json.loads(config.read_text(encoding="utf-8"))
    uninstall(["cline"], home=home)

    assert after_removal == original
    assert json.loads(config.read_text(encoding="utf-8")) == original
    assert removed["data_preserved"] == str(data_home / "memory.sqlite3")


def test_plan_reports_targets_without_writing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    result = plan(["codex"], action="install", home=home)

    assert result["changes_made"] is False
    assert result["clients"][0]["key"] == "codex"
    assert not (home / ".codex" / "config.toml").exists()
