from __future__ import annotations

import json
from pathlib import Path

import lians_easy.installer as installer_module
import pytest
from lians_easy.installer import (
    HOOK_STATUS,
    LIANS_HOOK_NAME,
    MANAGED_END,
    MANAGED_START,
    client_targets,
    install,
    plan,
    runtime_command,
    support_report,
    uninstall,
    write_support_report,
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
    expected_database = tmp_path / "local" / "Lians" / "memory.sqlite3"
    assert updated["mcpServers"]["lians"]["args"][-2:] == [
        "--data",
        str(expected_database),
    ]
    assert result["database"] == str(expected_database)
    assert result["clients"][0]["backup"]
    hooks = json.loads((home / ".claude" / "settings.json").read_text())["hooks"]
    [group] = hooks["UserPromptSubmit"]
    [hook] = group["hooks"]
    assert hook["statusMessage"] == HOOK_STATUS
    assert "hook --client claude" in hook["command"]
    assert str(expected_database) in hook["command"]
    [session_group] = hooks["SessionEnd"]
    [session_hook] = session_group["hooks"]
    assert session_hook["statusMessage"] == "Lians is saving project continuity"
    assert "hook --client claude" in session_hook["command"]
    assert session_hook["timeout"] == 12
    assert session_hook["command"].startswith("'C:\\")

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
    expected_database = tmp_path / "lians" / "memory.sqlite3"
    assert json.dumps("--data") in content
    assert json.dumps(str(expected_database)) in content
    hooks = json.loads((home / ".codex" / "hooks.json").read_text())
    [group] = hooks["hooks"]["UserPromptSubmit"]
    [hook] = group["hooks"]
    assert hook["statusMessage"] == HOOK_STATUS
    assert hook["additionalContextLimit"] == 2048
    assert str(expected_database) in hook["command"]


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
    expected_database = tmp_path / "lians" / "memory.sqlite3"
    assert updated["mcpServers"]["lians"]["args"][-2:] == [
        "--data",
        str(expected_database),
    ]
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
    assert str(expected_database) in hook["command"]

    uninstall(["gemini"], home=home)
    restored = json.loads(config.read_text())
    assert "lians" not in restored["mcpServers"]
    assert restored["mcpServers"]["other"]["command"] == "other"
    assert restored["hooks"]["BeforeAgent"] == [unrelated]


def test_antigravity_install_uses_current_mcp_and_hook_contracts(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / ".gemini" / "config"
    plugin_dir = config_dir / "plugins" / "lians-memory"
    plugin_dir.mkdir(parents=True)
    mcp_config = plugin_dir / "mcp_config.json"
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
    expected_database = tmp_path / "lians" / "memory.sqlite3"
    assert updated_mcp["mcpServers"]["lians"]["args"][-2:] == [
        "--data",
        str(expected_database),
    ]
    assert result["clients"][0]["automatic_recall"] is True
    assert updated_hooks["other-hook"]["PreInvocation"][0]["command"] == "other"
    [hook] = updated_hooks[LIANS_HOOK_NAME]["PreInvocation"]
    assert hook["timeout"] == 8
    assert "hook --client antigravity" in hook["command"]
    assert str(expected_database) in hook["command"]

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
    plugin_dir = config_dir / "plugins" / "lians-memory"
    plugin_dir.mkdir(parents=True)
    mcp_config = plugin_dir / "mcp_config.json"
    mcp_config.write_text("")
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    result = install(["antigravity"], home=home)

    updated = json.loads(mcp_config.read_text())
    expected_database = tmp_path / "lians" / "memory.sqlite3"
    assert updated["mcpServers"]["lians"]["args"][-2:] == [
        "--data",
        str(expected_database),
    ]
    assert result["clients"][0]["backup"]
    assert (config_dir / "hooks.json").is_file()


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
    assert installed["mcpServers"]["lians"]["args"][-2:] == [
        "--data",
        str(data_home / "memory.sqlite3"),
    ]
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
    assert result["runtime"]["args"][-2:] == [
        "--data",
        str(tmp_path / "lians" / "memory.sqlite3"),
    ]
    assert not (home / ".codex" / "config.toml").exists()


def test_runtime_and_hooks_pin_the_reported_database(tmp_path, monkeypatch):
    data_home = tmp_path / "portable-lians-data"
    monkeypatch.setenv("LIANS_EASY_HOME", str(data_home))
    expected_database = str(data_home / "memory.sqlite3")

    _command, mcp_args = installer_module.runtime_command()
    hook_argv = installer_module._runtime_argv("hook", "--client", "codex")
    result = plan(["codex"], action="install", home=tmp_path / "home")

    assert mcp_args[-3:] == ["mcp", "--data", expected_database]
    assert hook_argv[-2:] == ["--data", expected_database]
    assert result["runtime"]["args"] == mcp_args


def test_frozen_runtime_is_replaced_with_atomic_writer(tmp_path, monkeypatch):
    source = tmp_path / "source-runtime"
    source.write_bytes(b"complete-frozen-runtime")
    data_dir = tmp_path / "lians"
    monkeypatch.setenv("LIANS_EASY_HOME", str(data_dir))
    monkeypatch.setattr("sys.executable", str(source))
    monkeypatch.setattr("sys.frozen", True, raising=False)
    writes = []
    original_write = installer_module._write_bytes

    def observe_atomic_write(path, content, *, mode=None):
        writes.append((path, content, mode))
        original_write(path, content, mode=mode)

    monkeypatch.setattr(installer_module, "_write_bytes", observe_atomic_write)
    installed = installer_module._install_runtime()

    assert installed is not None
    assert installed.read_bytes() == source.read_bytes()
    assert writes == [
        (
            installed,
            b"complete-frozen-runtime",
            None if installer_module.sys.platform == "win32" else 0o755,
        )
    ]


def test_durable_replace_atomically_replaces_existing_file(tmp_path):
    source = tmp_path / "replacement.tmp"
    destination = tmp_path / "settings.json"
    source.write_bytes(b"complete-new-settings")
    destination.write_bytes(b"old-settings")

    installer_module._durable_replace(source, destination)

    assert destination.read_bytes() == b"complete-new-settings"
    assert not source.exists()


def test_settings_backup_uses_the_durable_atomic_writer(tmp_path, monkeypatch):
    source = tmp_path / "settings.json"
    source.write_bytes(b"private-original-settings")
    replacements = []
    original_replace = installer_module._durable_replace

    def observe_replace(temporary, destination):
        replacements.append((temporary, destination))
        original_replace(temporary, destination)

    monkeypatch.setattr(installer_module, "_durable_replace", observe_replace)
    backup = installer_module._backup(source)

    assert backup is not None
    assert backup.read_bytes() == source.read_bytes()
    assert len(replacements) == 1
    temporary, destination = replacements[0]
    assert destination == backup
    assert not temporary.exists()


@pytest.mark.skipif(installer_module.os.name == "nt", reason="POSIX durability path")
def test_posix_durable_replace_fsyncs_parent_directory(tmp_path, monkeypatch):
    source = tmp_path / "replacement.tmp"
    destination = tmp_path / "settings.json"
    source.write_text("new")
    synced = []
    monkeypatch.setattr(installer_module, "_sync_directory", synced.append)

    installer_module._durable_replace(source, destination)

    assert synced == [tmp_path]


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
    monkeypatch.setattr("lians_easy.installer.shutil.which", lambda _name: None)
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
    monkeypatch.setattr("lians_easy.installer.shutil.which", lambda _name: None)
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


def test_support_report_excludes_paths_settings_memory_and_error_content(
    tmp_path, monkeypatch
):
    home = tmp_path / "private-user-name"
    config = home / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    sensitive = "do-not-share-this-memory-or-key"
    config.write_text(json.dumps({"secret": sensitive, "mcpServers": {"lians": {}}}))
    data_dir = tmp_path / "private-lians-data"
    data_dir.mkdir()
    (data_dir / "memory.sqlite3").write_text(sensitive)
    transaction_dir = data_dir / "setup-transactions"
    transaction_dir.mkdir()
    (transaction_dir / "pending.json").write_text(sensitive)
    monkeypatch.setenv("LIANS_EASY_HOME", str(data_dir))
    setup_result = {
        "status": "failed",
        "clients": [
            {
                "client": "cursor",
                "label": "Cursor",
                "status": "failed",
                "error": sensitive,
                "config": str(config),
                "rolled_back": True,
                "retryable": True,
            }
        ],
        "retry_clients": ["cursor"],
    }

    report = support_report(home=home, setup_result=setup_result)
    destination = tmp_path / "Lians-help-report.json"
    write_support_report(destination, home=home, setup_result=setup_result)
    rendered = destination.read_text()

    assert report["schema"] == "lians-support-report/v1"
    assert report["memory_store"] == {"exists": True, "size_bytes": len(sensitive)}
    assert report["setup_recovery"] == {"pending_transactions": 1}
    assert report["last_setup"]["clients"] == [
        {
            "client": "cursor",
            "label": "Cursor",
            "status": "failed",
            "rolled_back": True,
            "retryable": True,
        }
    ]
    assert sensitive not in rendered
    assert str(home) not in rendered
    assert str(data_dir) not in rendered


def test_crash_diagnostics_and_support_report_never_include_exception_content(
    tmp_path, monkeypatch
):
    from lians_easy import diagnostics

    data_dir = tmp_path / "private-lians-data"
    secret = "do-not-share-this-prompt-or-api-key"
    private_path = tmp_path / "private-user" / secret / "worker.py"
    monkeypatch.setenv("LIANS_EASY_HOME", str(data_dir))

    try:
        raise RuntimeError(f"{secret} at {private_path}")
    except RuntimeError as error:
        diagnostics.record_exception(
            type(error), error, error.__traceback__, component="test-runtime"
        )

    report = support_report(home=tmp_path / "home")
    rendered = json.dumps(report)
    crash_log = (data_dir / "diagnostics" / "crashes.jsonl").read_text()

    assert report["application_errors"]["recent"][0]["exception_type"] == "RuntimeError"
    assert report["application_errors"]["recent"][0]["component"] == "test-runtime"
    assert report["application_errors"]["recent"][0]["crash_fingerprint"]
    assert secret not in crash_log
    assert secret not in rendered
    assert str(private_path) not in crash_log
    assert str(private_path) not in rendered


def test_interrupted_existing_config_is_exactly_recovered_before_retry(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    config = home / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    sensitive = "private-setting-value"
    original = json.dumps({"theme": "dark", "secret": sensitive}).encode()
    config.write_bytes(original)
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))
    original_write = installer_module._write_text
    crashed = False

    def interrupt_after_config_write(path, content):
        nonlocal crashed
        original_write(path, content)
        if path == config and not crashed:
            crashed = True
            raise KeyboardInterrupt

    monkeypatch.setattr(installer_module, "_write_text", interrupt_after_config_write)
    with pytest.raises(KeyboardInterrupt):
        install(["cursor"], home=home)

    journals = list((tmp_path / "lians" / "setup-transactions").glob("*.json"))
    assert len(journals) == 1
    assert sensitive not in journals[0].read_text()
    assert config.read_bytes() != original

    monkeypatch.setattr(installer_module, "_write_text", original_write)
    original_install_client = installer_module._install_client
    observed_before_retry = []

    def observe_recovered_config(key, **kwargs):
        observed_before_retry.append(config.read_bytes())
        return original_install_client(key, **kwargs)

    monkeypatch.setattr(installer_module, "_install_client", observe_recovered_config)
    result = install(["cursor"], home=home)

    assert result["recovered_clients"] == ["cursor"]
    assert observed_before_retry == [original]
    assert json.loads(config.read_text())["secret"] == sensitive
    assert not list((tmp_path / "lians" / "setup-transactions").glob("*.json"))


def test_interrupted_new_config_is_removed_during_recovery(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".cursor" / "mcp.json"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))
    original_write = installer_module._write_text
    crashed = False

    def interrupt_after_config_write(path, content):
        nonlocal crashed
        original_write(path, content)
        if path == config and not crashed:
            crashed = True
            raise KeyboardInterrupt

    monkeypatch.setattr(installer_module, "_write_text", interrupt_after_config_write)
    with pytest.raises(KeyboardInterrupt):
        install(["cursor"], home=home)
    assert config.is_file()

    monkeypatch.setattr(installer_module, "_write_text", original_write)
    recovered = installer_module._recover_interrupted_transactions(home=home)

    assert recovered == ["cursor"]
    assert not config.exists()


def test_recovery_rejects_tampered_target_without_touching_it(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".cursor" / "mcp.json"
    outside = tmp_path / "outside.json"
    outside.write_text("untouched")
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))
    journal = installer_module.SetupJournal.begin("cursor", [config])
    document = json.loads(journal.path.read_text())
    document["entries"][0]["path"] = str(outside)
    journal.path.write_text(json.dumps(document))

    with pytest.raises(ValueError, match="unexpected file"):
        installer_module._recover_interrupted_transactions(home=home)

    assert outside.read_text() == "untouched"
    assert journal.path.is_file()


def test_setup_lock_rejects_a_second_mutation_process(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LIANS_EASY_HOME", str(tmp_path / "lians"))

    with installer_module._setup_lock(), pytest.raises(
        RuntimeError, match="already running"
    ):
        install(["cursor"], home=home)

    with installer_module._setup_lock(), pytest.raises(
        RuntimeError, match="already running"
    ):
        uninstall(["cursor"], home=home)


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
    assert updated["mcpServers"]["lians"]["args"][-2:] == [
        "--data",
        str(tmp_path / "lians" / "memory.sqlite3"),
    ]
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

    result = install(["antigravity"], home=home)

    plugin_dir = home / ".gemini" / "config" / "plugins" / "lians-memory"
    assert result["status"] == "failed"
    assert result["clients"][0]["rolled_back"] is True
    assert "entries must be an array" in result["clients"][0]["error"]
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
