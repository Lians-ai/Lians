from __future__ import annotations

import json

from lians_easy import cli
from lians_easy.installer import ClientTarget
from lians_easy.store import MemoryStore


def test_product_status_keeps_setup_and_measured_impact_simple(tmp_path, monkeypatch) -> None:
    data = tmp_path / "memory.sqlite3"
    store = MemoryStore(data)
    store.remember("Use Python 3.12 for this project.", kind="preference")
    store.remember("Run tests with pytest.", kind="preference")
    store.context_pack("Which Python version?", project=None, client="claude", limit=1)

    targets = {
        "claude": ClientTarget(
            key="claude",
            label="Claude Desktop",
            config_path=tmp_path / "claude.json",
            kind="json",
            detected=True,
            configured=True,
        ),
        "cursor": ClientTarget(
            key="cursor",
            label="Cursor",
            config_path=tmp_path / "cursor.json",
            kind="json",
            detected=True,
            configured=False,
        ),
    }
    monkeypatch.setattr(cli, "client_targets", lambda _home=None: targets)

    result = cli.product_status(data_path=data)

    assert result["status"] == "optimized"
    assert result["headline"] == "Lians is active in 1 AI app."
    assert result["configured_clients"] == 1
    assert result["detected_clients"] == 2
    assert result["privacy"]["ai_account_credentials_required"] is False
    assert result["efficiency"]["context_events"] == 1
    assert result["efficiency"]["repeated_memory_tokens_avoided_estimate"] > 0


def test_status_command_outputs_machine_readable_product_state(tmp_path, monkeypatch, capsys) -> None:
    data = tmp_path / "memory.sqlite3"
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)
    monkeypatch.setattr(cli, "client_targets", lambda _home=None: {})

    cli.main(["status", "--data", str(data), "--json"])

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "not_configured"
    assert result["efficiency"]["context_events"] == 0


def test_optimize_is_the_plain_language_install_path() -> None:
    parsed = cli.parser().parse_args(
        ["optimize", "--clients", "claude,cursor", "--plan", "--json"]
    )

    assert parsed.command == "optimize"
    assert parsed.clients == "claude,cursor"
    assert parsed.plan is True
