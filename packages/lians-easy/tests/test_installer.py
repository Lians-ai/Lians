from __future__ import annotations

import json

from lians_easy.installer import HOOK_STATUS, MANAGED_END, MANAGED_START, install, plan, uninstall


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
    hooks = json.loads((home / ".claude" / "settings.json").read_text())["hooks"]
    [group] = hooks["UserPromptSubmit"]
    [hook] = group["hooks"]
    assert hook["statusMessage"] == HOOK_STATUS
    assert "hook --client claude" in hook["command"]

    removed = uninstall(["claude"], home=home)
    assert "lians" not in json.loads(config.read_text())["mcpServers"]
    assert "hooks" not in json.loads((home / ".claude" / "settings.json").read_text())
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
    hooks = json.loads((home / ".codex" / "hooks.json").read_text())
    [group] = hooks["hooks"]["UserPromptSubmit"]
    [hook] = group["hooks"]
    assert hook["statusMessage"] == HOOK_STATUS
    assert hook["additionalContextLimit"] == 2048


def test_hook_install_preserves_unrelated_groups_and_uninstall_removes_only_lians(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    config = home / ".codex" / "hooks.json"
    config.parent.mkdir(parents=True)
    existing = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "x"}]}]}}
    config.write_text(json.dumps(existing))
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    install(["codex"], home=home)
    groups = json.loads(config.read_text())["hooks"]["UserPromptSubmit"]
    assert len(groups) == 2

    uninstall(["codex"], home=home)
    groups = json.loads(config.read_text())["hooks"]["UserPromptSubmit"]
    assert groups == existing["hooks"]["UserPromptSubmit"]


def test_plan_reports_targets_without_writing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    result = plan(["codex"], action="install", home=home)

    assert result["changes_made"] is False
    assert result["clients"][0]["key"] == "codex"
    assert not (home / ".codex" / "config.toml").exists()
