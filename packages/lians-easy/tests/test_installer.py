from __future__ import annotations

import json

from lians_easy.installer import (
    MANAGED_END,
    MANAGED_START,
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
    expected_database = tmp_path / "local" / "Lians" / "memory.sqlite3"
    assert updated["mcpServers"]["lians"]["args"][-2:] == [
        "--data",
        str(expected_database),
    ]
    assert result["database"] == str(expected_database)
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
    assert json.dumps("--data") in content
    assert json.dumps(str(tmp_path / "lians" / "memory.sqlite3")) in content


def test_plan_reports_targets_without_writing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    result = plan(["codex"], action="install", home=home)

    assert result["changes_made"] is False
    assert result["clients"][0]["key"] == "codex"
    assert result["runtime"]["args"][-2:] == [
        "--data",
        str(tmp_path / "lians" / "memory.sqlite3"),
    ]
    assert not (home / ".codex" / "config.toml").exists()


def test_runtime_command_pins_the_reported_database(tmp_path, monkeypatch):
    data_home = tmp_path / "portable-lians-data"
    monkeypatch.setenv("LIANS_EASY_HOME", str(data_home))

    _command, args = runtime_command()
    result = plan(["codex"], action="install", home=tmp_path / "home")

    assert args[-3:] == ["mcp", "--data", str(data_home / "memory.sqlite3")]
    assert result["runtime"]["args"] == args
