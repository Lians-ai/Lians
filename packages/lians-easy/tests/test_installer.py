from __future__ import annotations

import json

from lians_easy.installer import (
    HOOK_STATUS,
    LIANS_HOOK_NAME,
    MANAGED_END,
    MANAGED_START,
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


def test_gemini_install_adds_before_agent_recall_and_preserves_settings(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".gemini" / "settings.json"
    config.parent.mkdir(parents=True)
    unrelated = {
        "matcher": "*",
        "hooks": [{"name": "other-hook", "type": "command", "command": "other"}],
    }
    config.write_text(
        json.dumps(
            {
                "$schema": "https://example.invalid/settings.schema.json",
                "security": {"folderTrust": {"enabled": True}},
                "mcpServers": {"other": {"command": "other"}},
                "hooks": {"BeforeAgent": [unrelated]},
            }
        )
    )
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    result = install(["gemini"], home=home)
    updated = json.loads(config.read_text())
    assert updated["security"]["folderTrust"]["enabled"] is True
    assert "other" in updated["mcpServers"]
    assert updated["mcpServers"]["lians"]["args"][-1] == "mcp"
    assert result["clients"][0]["automatic_recall"] is True
    assert result["clients"][0]["hook_backup"]
    assert len(updated["hooks"]["BeforeAgent"]) == 2
    [lians_group] = [
        group
        for group in updated["hooks"]["BeforeAgent"]
        if any(hook.get("name") == LIANS_HOOK_NAME for hook in group["hooks"])
    ]
    [hook] = lians_group["hooks"]
    assert lians_group["sequential"] is True
    assert hook["timeout"] == 8000
    assert "hook --client gemini" in hook["command"]

    uninstall(["gemini"], home=home)
    restored = json.loads(config.read_text())
    assert "lians" not in restored["mcpServers"]
    assert restored["mcpServers"]["other"]["command"] == "other"
    assert restored["hooks"]["BeforeAgent"] == [unrelated]


def test_antigravity_install_uses_current_mcp_and_hook_contracts(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / ".gemini" / "config"
    config_dir.mkdir(parents=True)
    mcp_config = config_dir / "mcp_config.json"
    hooks_config = config_dir / "hooks.json"
    mcp_config.write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}))
    hooks_config.write_text(
        json.dumps(
            {
                "other-hook": {
                    "PreInvocation": [{"type": "command", "command": "other"}]
                }
            }
        )
    )
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    result = install(["antigravity"], home=home)
    updated_mcp = json.loads(mcp_config.read_text())
    updated_hooks = json.loads(hooks_config.read_text())
    assert updated_mcp["mcpServers"]["other"]["command"] == "other"
    assert updated_mcp["mcpServers"]["lians"]["args"][-1] == "mcp"
    assert result["clients"][0]["automatic_recall"] is True
    assert updated_hooks["other-hook"]["PreInvocation"][0]["command"] == "other"
    [hook] = updated_hooks[LIANS_HOOK_NAME]["PreInvocation"]
    assert hook["timeout"] == 8
    assert "hook --client antigravity" in hook["command"]

    uninstall(["antigravity"], home=home)
    restored_mcp = json.loads(mcp_config.read_text())
    restored_hooks = json.loads(hooks_config.read_text())
    assert "lians" not in restored_mcp["mcpServers"]
    assert restored_mcp["mcpServers"]["other"]["command"] == "other"
    assert LIANS_HOOK_NAME not in restored_hooks
    assert "other-hook" in restored_hooks


def test_antigravity_install_accepts_client_created_empty_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / ".gemini" / "config"
    config_dir.mkdir(parents=True)
    mcp_config = config_dir / "mcp_config.json"
    mcp_config.write_text("")
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    result = install(["antigravity"], home=home)

    updated = json.loads(mcp_config.read_text())
    assert updated["mcpServers"]["lians"]["args"][-1] == "mcp"
    assert result["clients"][0]["backup"]
    assert (config_dir / "hooks.json").is_file()


def test_plan_reports_targets_without_writing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    result = plan(["codex"], action="install", home=home)

    assert result["changes_made"] is False
    assert result["clients"][0]["key"] == "codex"
    assert not (home / ".codex" / "config.toml").exists()
