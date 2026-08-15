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
    monkeypatch.setattr("lians_easy.installer.shutil.which", lambda _name: None)
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
    monkeypatch.setattr("lians_easy.installer.shutil.which", lambda _name: None)
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


def test_antigravity_uses_plugin_mcp_path_on_every_platform(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr("lians_easy.installer.shutil.which", lambda _name: None)

    for platform in ("win32", "darwin", "linux"):
        monkeypatch.setattr("sys.platform", platform)
        target = client_targets(home)["antigravity"]

        assert target.label == "Antigravity CLI"
        assert target.config_path == (
            home
            / ".gemini"
            / "config"
            / "plugins"
            / "lians-memory"
            / "mcp_config.json"
        )
        assert target.kind == "antigravity_plugin"
        assert target.detected is False


def test_antigravity_plugin_round_trip_preserves_other_components(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plugin_root = home / ".gemini" / "config" / "plugins"
    plugin_dir = plugin_root / "lians-memory"
    config = plugin_dir / "mcp_config.json"
    manifest = plugin_dir / "plugin.json"
    registry = home / ".gemini" / "config" / "plugins.json"
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
    manifest.write_text(json.dumps({"name": "lians-memory", "channel": "test"}))
    registry.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "path": str(plugin_root.resolve()).replace("\\", "/"),
                        "include_only": ["other-plugin"],
                    },
                    {"path": "C:/shared/plugins", "include_only": ["shared-plugin"]},
                ]
            }
        )
    )
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    first = install(["antigravity"], home=home)
    install(["antigravity"], home=home)
    updated = json.loads(config.read_text())
    updated_manifest = json.loads(manifest.read_text())
    updated_registry = json.loads(registry.read_text())

    assert updated["theme"] == "dark"
    assert updated["mcpServers"]["other"] == {"serverUrl": "https://example.test/mcp"}
    assert updated["mcpServers"]["lians"]["args"][-1] == "mcp"
    assert updated_manifest == {"name": "lians-memory", "channel": "test"}
    assert updated_registry["entries"][0]["include_only"] == [
        "other-plugin",
        "lians-memory",
    ]
    assert client_targets(home)["antigravity"].configured is True
    assert first["clients"][0]["config"] == str(config)
    assert first["clients"][0]["backup"]

    removed = uninstall(["antigravity"], home=home)
    final = json.loads(config.read_text())
    final_registry = json.loads(registry.read_text())

    assert "lians" not in final["mcpServers"]
    assert final["mcpServers"]["other"] == {"serverUrl": "https://example.test/mcp"}
    assert final_registry["entries"][0]["include_only"] == ["other-plugin"]
    assert final_registry["entries"][1] == {
        "path": "C:/shared/plugins",
        "include_only": ["shared-plugin"],
    }
    assert client_targets(home)["antigravity"].configured is False
    assert removed["data_preserved"].endswith("memory.sqlite3")


def test_antigravity_plugin_fresh_install_uninstalls_to_inert_backups(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    install(["antigravity"], home=home)
    removed = uninstall(["antigravity"], home=home)

    plugin_dir = home / ".gemini" / "config" / "plugins" / "lians-memory"
    registry = json.loads(
        (home / ".gemini" / "config" / "plugins.json").read_text()
    )
    active_files = [
        path.name for path in plugin_dir.iterdir() if ".lians-backup-" not in path.name
    ]

    assert active_files == []
    assert registry["entries"] == []
    assert removed["clients"][0]["status"] == "removed"


def test_antigravity_invalid_registry_fails_before_writing_plugin(tmp_path, monkeypatch):
    home = tmp_path / "home"
    registry = home / ".gemini" / "config" / "plugins.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"entries": "not-an-array"}))
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    with pytest.raises(TypeError, match="entries must be an array"):
        install(["antigravity"], home=home)

    plugin_dir = home / ".gemini" / "config" / "plugins" / "lians-memory"
    assert not plugin_dir.exists()
    assert json.loads(registry.read_text()) == {"entries": "not-an-array"}


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_opencode_target_uses_documented_global_path(platform, tmp_path, monkeypatch):
    home = tmp_path / platform
    monkeypatch.setattr("lians_easy.installer.shutil.which", lambda _name: None)
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
