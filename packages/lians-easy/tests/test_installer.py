from __future__ import annotations

import json

from lians_easy.installer import MANAGED_END, MANAGED_START, install, plan, uninstall


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


def test_opencode_config_uses_mcp_key(tmp_path, monkeypatch):
    """OpenCode uses 'mcp' key with 'type', 'command', 'enabled', 'environment'."""
    home = tmp_path / "home"
    config = home / ".opencode" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"theme": "dark"}))
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    result = install(["opencode"], home=home)
    updated = json.loads(config.read_text())

    assert updated["theme"] == "dark"
    assert "lians" in updated["mcp"]
    lians_cfg = updated["mcp"]["lians"]
    assert lians_cfg["type"] == "local"
    assert lians_cfg["command"][0] in ("python", "python3", "lians-memory") or "python" in lians_cfg["command"][0].lower()
    assert lians_cfg["enabled"] is True
    assert "LIANS_MCP_ENABLED_TOOLS" in lians_cfg["environment"]
    assert result["clients"][0]["backup"]

    removed = uninstall(["opencode"], home=home)
    assert "lians" not in json.loads(config.read_text()).get("mcp", {})
    assert removed["data_preserved"].endswith("memory.sqlite3")


def test_opencode_config_creates_new_file(tmp_path, monkeypatch):
    """OpenCode config should be created if it doesn't exist."""
    home = tmp_path / "home"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    result = install(["opencode"], home=home)
    config = home / ".opencode" / "config.json"
    assert config.exists()
    updated = json.loads(config.read_text())
    assert "lians" in updated["mcp"]
    assert updated["mcp"]["lians"]["type"] == "local"
