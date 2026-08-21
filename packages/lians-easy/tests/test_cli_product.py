from __future__ import annotations

import io
import json

import pytest
from lians_easy import cli
from lians_easy.installer import ClientTarget
from lians_easy.project import detect_project
from lians_easy.store import MemoryStore
from lians_easy.task_contract import TaskContractService


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
    assert result["headline"] == "Lians is connected to 1 AI app."
    assert result["configured_clients"] == 1
    assert result["detected_clients"] == 2
    assert result["privacy"]["ai_account_credentials_required"] is False
    assert result["efficiency"]["context_events"] == 1
    assert result["efficiency"]["successful_context_events"] == 1
    assert result["efficiency"]["repeated_memory_tokens_avoided_estimate"] > 0
    assert result["activation"] == {
        "status": "reuse_observed",
        "connected_client_observed": True,
        "successful_context_events": 1,
        "measurement": "local_only",
        "content_collected": False,
        "fresh_session_confirmation": "self_reported",
    }
    assert result["control"]["policy"]["mode"] == "guide"


def test_status_command_outputs_machine_readable_product_state(tmp_path, monkeypatch, capsys) -> None:
    data = tmp_path / "memory.sqlite3"
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)
    monkeypatch.setattr(cli, "client_targets", lambda _home=None: {})

    cli.main(["status", "--data", str(data), "--json"])

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "not_configured"
    assert result["efficiency"]["context_events"] == 0
    assert result["efficiency"]["successful_context_events"] == 0
    assert result["activation"]["status"] == "not_connected"


def test_product_status_does_not_call_empty_context_an_activation(
    tmp_path, monkeypatch
) -> None:
    data = tmp_path / "memory.sqlite3"
    store = MemoryStore(data)
    store.context_pack("What should I do?", project=None, client="codex", limit=1)
    targets = {
        "codex": ClientTarget(
            key="codex",
            label="Codex",
            config_path=tmp_path / "config.toml",
            kind="toml",
            detected=True,
            configured=True,
        )
    }
    monkeypatch.setattr(cli, "client_targets", lambda _home=None: targets)

    result = cli.product_status(data_path=data)

    assert result["efficiency"]["context_events"] == 1
    assert result["efficiency"]["successful_context_events"] == 0
    assert result["activation"]["status"] == "connected"


def test_share_card_is_explicit_content_free_and_requires_observed_reuse(
    tmp_path, monkeypatch
) -> None:
    data = tmp_path / "memory.sqlite3"
    store = MemoryStore(data)
    store.remember("Private fixture detail that must never enter the card.")
    store.context_pack("Continue the fixture", project=None, client="codex", limit=1)
    targets = {
        "codex": ClientTarget(
            key="codex",
            label="Codex",
            config_path=tmp_path / "config.toml",
            kind="toml",
            detected=True,
            configured=True,
        )
    }
    monkeypatch.setattr(cli, "client_targets", lambda _home=None: targets)

    result = cli.continuity_share_card(data_path=data)

    assert result["schema"] == "lians.continuity-share-card.v1"
    assert result["status"] == "local_context_reuse_observed"
    assert result["content_included"] is False
    assert result["certification"] is False
    assert "Private fixture detail" not in result["markdown"]
    assert "No project or memory content" in result["markdown"]


def test_share_card_rejects_connected_without_successful_reuse(
    tmp_path, monkeypatch
) -> None:
    data = tmp_path / "memory.sqlite3"
    targets = {
        "codex": ClientTarget(
            key="codex",
            label="Codex",
            config_path=tmp_path / "config.toml",
            kind="toml",
            detected=True,
            configured=True,
        )
    }
    monkeypatch.setattr(cli, "client_targets", lambda _home=None: targets)

    with pytest.raises(ValueError, match="successful fresh-task context reuse"):
        cli.continuity_share_card(data_path=data)


def test_optimize_is_the_plain_language_install_path() -> None:
    parsed = cli.parser().parse_args(
        ["optimize", "--clients", "claude,cursor", "--plan", "--json"]
    )

    assert parsed.command == "optimize"
    assert parsed.clients == "claude,cursor"
    assert parsed.plan is True


def test_claude_experiment_defaults_to_an_offline_plan() -> None:
    parsed = cli.parser().parse_args(["experiment", "claude", "--json"])

    assert parsed.command == "experiment"
    assert parsed.experiment_name == "claude"
    assert parsed.run is False
    assert parsed.model == "sonnet"
    assert parsed.repetitions == 1
    assert parsed.scenario == "baseline"
    assert parsed.max_context_tokens is None


def test_claude_experiment_accepts_market_research_scenario() -> None:
    parsed = cli.parser().parse_args(
        [
            "experiment",
            "claude",
            "--scenario",
            "market-research",
            "--repetitions",
            "2",
        ]
    )

    assert parsed.scenario == "market-research"


def test_stretch_experiment_defaults_to_an_offline_capacity_plan() -> None:
    parsed = cli.parser().parse_args(
        ["experiment", "stretch", "--workload", "social-research", "--json"]
    )

    assert parsed.experiment_name == "stretch"
    assert parsed.workload == "social-research"
    assert parsed.records is None
    assert parsed.run is False
    assert parsed.paired is False
    assert parsed.repetitions == 1


def test_brief_command_is_local_and_simple() -> None:
    parsed = cli.parser().parse_args(
        ["brief", "research", "posts.jsonl", "--output", "brief.json"]
    )

    assert parsed.command == "brief"
    assert parsed.kind == "research"
    assert parsed.input.name == "posts.jsonl"
    assert parsed.output.name == "brief.json"
    assert parsed.evidence == 12


def test_continue_command_is_the_plain_resume_path() -> None:
    parsed = cli.parser().parse_args(
        ["continue", "release-test", "--client", "codex", "--max-tokens", "512"]
    )

    assert parsed.command == "continue"
    assert parsed.task_id == "release-test"
    assert parsed.client == "codex"
    assert parsed.max_tokens == 512


def test_report_command_returns_a_content_light_guard_summary(
    tmp_path, monkeypatch, capsys
) -> None:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    data = tmp_path / "memory.sqlite3"
    project = detect_project(root)
    TaskContractService(MemoryStore(data)).start(
        "Ship the Guard report",
        ["The report is visible"],
        project_id=project.id,
        task_id="guard-report",
    )
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)

    cli.main(["report", "--cwd", str(root), "--data", str(data), "--json"])

    result = json.loads(capsys.readouterr().out)
    assert result["tasks"]["total"] == 1
    assert result["criteria"]["missing"] == 1
    assert "does not establish time saved" in result["claim_boundary"]


def test_human_confirmation_fails_closed_without_an_interactive_terminal(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("CONFIRM criterion-1\n"))

    with pytest.raises(SystemExit, match="interactive terminal"):
        cli.main(
            [
                "confirm",
                "guard-report",
                "criterion-1",
                "--evidence",
                "Reviewed",
                "--data",
                str(tmp_path / "memory.sqlite3"),
            ]
        )


def test_ci_evidence_import_parser_requires_explicit_trust_binding() -> None:
    parsed = cli.parser().parse_args(
        [
            "ci-evidence",
            "import",
            "evidence.json",
            "--repo",
            "Lians-ai/Lians",
            "--signer-workflow",
            "Lians-ai/Lians/.github/workflows/lians-guard.yml",
            "--source-ref",
            "refs/heads/master",
            "--task",
            "guard-release",
            "--criterion",
            "criterion-1",
            "--check",
            "guard-tests",
        ]
    )

    assert parsed.repo == "Lians-ai/Lians"
    assert parsed.signer_workflow.endswith("lians-guard.yml")
    assert parsed.criterion == ["criterion-1"]
    assert parsed.check == ["guard-tests"]


def test_ci_evidence_mapping_fails_closed_without_an_interactive_terminal(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("IMPORT guard-release\n"))

    with pytest.raises(SystemExit, match="interactive terminal"):
        cli.main(
            [
                "ci-evidence",
                "import",
                str(tmp_path / "evidence.json"),
                "--repo",
                "Lians-ai/Lians",
                "--signer-workflow",
                "Lians-ai/Lians/.github/workflows/lians-guard.yml",
                "--source-ref",
                "refs/heads/master",
                "--task",
                "guard-release",
                "--criterion",
                "criterion-1",
                "--check",
                "guard-tests",
            ]
        )


def test_claude_experiment_plan_does_not_call_claude(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)
    monkeypatch.setattr(
        cli,
        "run_claude_experiment",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("live call attempted")),
    )

    cli.main(["experiment", "claude", "--json"])

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "planned"
    assert result["fixture"]["synthetic"] is True


def test_claude_experiment_refuses_existing_report_before_live_call(
    tmp_path, monkeypatch
) -> None:
    output = tmp_path / "existing.json"
    output.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)
    monkeypatch.setattr(
        cli,
        "run_claude_experiment",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("live call attempted")),
    )

    with pytest.raises(SystemExit, match="Output already exists"):
        cli.main(
            [
                "experiment",
                "claude",
                "--run",
                "--output",
                str(output),
            ]
        )

    assert output.read_text(encoding="utf-8") == "keep me"
