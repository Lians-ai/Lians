from __future__ import annotations

import json

import lians_easy.installer as installer_module
import pytest
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


def test_install_reports_plain_language_progress(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))
    events = []

    install(["cursor", "codex"], home=home, on_progress=lambda stage, detail: events.append((stage, detail)))

    assert [stage for stage, _ in events] == [
        "protecting",
        "connecting",
        "connecting",
        "verifying",
        "complete",
    ]
    assert events[0][1] == "Protecting your existing settings"
    assert events[1][1] == "Connecting Cursor"
    assert events[2][1] == "Connecting Codex"
    assert events[-1][1] == "Lians is ready"


def test_failed_client_restores_exact_files_and_removes_transaction_backups(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    roaming = tmp_path / "roaming"
    config = roaming / "Claude" / "claude_desktop_config.json"
    hook = home / ".claude" / "settings.json"
    config.parent.mkdir(parents=True)
    hook.parent.mkdir(parents=True)
    original_config = b'{"theme":"dark","mcpServers":{"other":{"command":"x"}}}'
    invalid_hook = b"[]"
    config.write_bytes(original_config)
    hook.write_bytes(invalid_hook)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    result = install(["claude"], home=home)

    assert result["status"] == "failed"
    assert result["retry_clients"] == ["claude"]
    assert result["clients"][0]["rolled_back"] is True
    assert config.read_bytes() == original_config
    assert hook.read_bytes() == invalid_hook
    assert not list(config.parent.glob("*.lians-backup-*"))
    assert not list(hook.parent.glob("*.lians-backup-*"))


def test_retry_targets_only_failed_client_and_preserves_success(tmp_path, monkeypatch):
    home = tmp_path / "home"
    roaming = tmp_path / "roaming"
    cursor = home / ".cursor" / "mcp.json"
    claude = roaming / "Claude" / "claude_desktop_config.json"
    hook = home / ".claude" / "settings.json"
    cursor.parent.mkdir(parents=True)
    claude.parent.mkdir(parents=True)
    hook.parent.mkdir(parents=True)
    cursor.write_text(json.dumps({"theme": "dark"}))
    original_claude = b'{"theme":"light"}'
    claude.write_bytes(original_claude)
    hook.write_text("[]")
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    first = install(["cursor", "claude"], home=home)

    assert first["status"] == "partial"
    assert [item["status"] for item in first["clients"]] == ["installed", "failed"]
    assert first["retry_clients"] == ["claude"]
    cursor_after_success = cursor.read_bytes()
    assert "lians" in json.loads(cursor_after_success)["mcpServers"]
    assert claude.read_bytes() == original_claude

    hook.write_text("{}")
    retried = install(first["retry_clients"], home=home)

    assert retried["status"] == "installed"
    assert [item["client"] for item in retried["clients"]] == ["claude"]
    assert cursor.read_bytes() == cursor_after_success
    assert "lians" in json.loads(claude.read_text())["mcpServers"]


def test_verification_failure_rolls_back_primary_and_hook_files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    hook = home / ".codex" / "hooks.json"
    config.parent.mkdir(parents=True)
    original_config = b'model = "gpt-5"\n'
    original_hook = b'{"hooks":{"Other":[]}}'
    config.write_bytes(original_config)
    hook.write_bytes(original_hook)
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    def fail_verification(key, *, home):
        raise RuntimeError(f"forced verification failure for {key} in {home}")

    monkeypatch.setattr(installer_module, "_verify_client", fail_verification)

    result = install(["codex"], home=home)

    assert result["status"] == "failed"
    assert result["clients"][0]["rolled_back"] is True
    assert config.read_bytes() == original_config
    assert hook.read_bytes() == original_hook
    assert not list(config.parent.glob("*.lians-backup-*"))


def test_verification_failure_removes_files_created_by_failed_transaction(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    hook = home / ".codex" / "hooks.json"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    def fail_verification(key, *, home):
        raise RuntimeError(f"forced verification failure for {key} in {home}")

    monkeypatch.setattr(installer_module, "_verify_client", fail_verification)

    result = install(["codex"], home=home)

    assert result["status"] == "failed"
    assert result["clients"][0]["rolled_back"] is True
    assert not config.exists()
    assert not hook.exists()


def test_install_rejects_empty_selection_and_deduplicates_clients(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    with pytest.raises(ValueError, match="at least one"):
        install([], home=home)

    result = install(["cursor", "cursor"], home=home)

    assert result["status"] == "installed"
    assert [item["client"] for item in result["clients"]] == ["cursor"]
