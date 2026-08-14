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
    runtime_command,
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


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_opencode_target_uses_documented_global_path(platform, tmp_path, monkeypatch):
    home = tmp_path / platform
    monkeypatch.setattr("sys.platform", platform)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    target = client_targets(home)["opencode"]

    assert target.label == "OpenCode"
    assert target.config_path == home / ".config" / "opencode" / "opencode.json"


def test_opencode_target_respects_xdg_config_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    target = client_targets(home)["opencode"]

    assert target.config_path == config_home / "opencode" / "opencode.json"


def test_opencode_config_uses_mcp_key(tmp_path, monkeypatch):
    """OpenCode uses 'mcp' with a local type and one command array."""
    home = tmp_path / "home"
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    config = home / ".config" / "opencode" / "opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"theme": "dark"}))
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    first = install(["opencode"], home=home)
    updated = json.loads(config.read_text())
    second = install(["opencode"], home=home)

    assert updated["theme"] == "dark"
    assert "lians" in updated["mcp"]
    lians_cfg = updated["mcp"]["lians"]
    assert lians_cfg["type"] == "local"
    command, args = runtime_command()
    assert lians_cfg["command"] == [command, *args]
    assert lians_cfg["enabled"] is True
    assert "LIANS_MCP_ENABLED_TOOLS" in lians_cfg["environment"]
    assert json.loads(config.read_text()) == updated
    assert json.loads(Path(first["clients"][0]["backup"]).read_text()) == {"theme": "dark"}
    assert json.loads(Path(second["clients"][0]["backup"]).read_text()) == updated
    assert client_targets(home)["opencode"].configured is True

    removed = uninstall(["opencode"], home=home)
    assert "lians" not in json.loads(config.read_text()).get("mcp", {})
    assert removed["data_preserved"].endswith("memory.sqlite3")


def test_opencode_config_creates_new_file(tmp_path, monkeypatch):
    """OpenCode config should be created if it doesn't exist."""
    home = tmp_path / "home"
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    install(["opencode"], home=home)
    config = home / ".config" / "opencode" / "opencode.json"
    assert config.exists()
    updated = json.loads(config.read_text())
    assert "lians" in updated["mcp"]
    assert updated["mcp"]["lians"]["type"] == "local"
