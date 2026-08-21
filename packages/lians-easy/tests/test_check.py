from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from lians_easy import cli
from lians_easy import check as check_module
from lians_easy.check import (
    CHECK_PATH,
    CheckConfigError,
    CheckSpec,
    LiansCheckService,
    detect_checks,
)
from lians_easy.project import detect_project
from lians_easy.store import MemoryStore


def _run(root: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)


def _repository(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _run(root, "init")
    _run(root, "config", "user.email", "tests@lians.ai")
    _run(root, "config", "user.name", "Lians Tests")
    (root / "value.txt").write_text("base\n", encoding="utf-8")
    _run(root, "add", "value.txt")
    _run(root, "commit", "-m", "initial")
    monkeypatch.chdir(root)
    return root


def _content_check() -> CheckSpec:
    source = (
        "from pathlib import Path; "
        "raise SystemExit(0 if Path('value.txt').read_text() == 'pass\\n' else 1)"
    )
    return CheckSpec("tests", "Tests", (sys.executable, "-c", source), 30)


def test_detect_checks_keeps_the_default_set_short_and_high_signal(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": "vitest run",
                    "build": "vite build",
                    "typecheck": "tsc --noEmit",
                    "lint": "eslint .",
                    "storybook": "storybook dev",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")

    checks = detect_checks(tmp_path)

    assert [item.id for item in checks] == ["test", "build", "typecheck", "lint"]
    assert checks[0].argv == ("pnpm", "run", "test")


def test_custom_check_rejects_credentials() -> None:
    with pytest.raises(CheckConfigError, match="must not contain credentials"):
        check_module.parse_custom_check("tests=pytest --api-key secret-value")


def test_lians_check_produces_a_measured_current_code_receipt(tmp_path, monkeypatch) -> None:
    root = _repository(tmp_path, monkeypatch)
    data = tmp_path / "memory.sqlite3"
    project = detect_project(root)
    service = LiansCheckService(MemoryStore(data))
    setup = service.initialize(project, checks=[_content_check()])
    (root / "value.txt").write_text("pass\n", encoding="utf-8")

    result = service.run(project)

    assert setup["headline"] == "LIANS CHECK IS READY"
    assert result["status"] == "ready_for_review"
    assert result["headline"] == "READY TO REVIEW"
    assert result["checks"][0]["status"] == "passed"
    assert result["policy_sha256"] == setup["policy_sha256"]
    assert len(result["policy_sha256"]) == 64
    assert result["verification"]["external_check_trust"] == "lians_measured"
    assert result["verification"]["changed_file_count"] == 2
    assert result["claim_boundary"].startswith("Lians measured")


def test_check_policy_does_not_replace_the_users_active_work(tmp_path, monkeypatch) -> None:
    root = _repository(tmp_path, monkeypatch)
    project = detect_project(root)
    service = LiansCheckService(MemoryStore(tmp_path / "memory.sqlite3"))
    service.initialize(project, checks=[_content_check()])

    continued = service.tasks.continue_work(project_id=project.id)

    assert continued["status"] == "no_active_work"


def test_a_failed_rerun_replaces_old_success_evidence(tmp_path, monkeypatch) -> None:
    root = _repository(tmp_path, monkeypatch)
    data = tmp_path / "memory.sqlite3"
    project = detect_project(root)
    service = LiansCheckService(MemoryStore(data))
    setup = service.initialize(project, checks=[_content_check()])
    (root / "value.txt").write_text("pass\n", encoding="utf-8")
    assert service.run(project)["status"] == "ready_for_review"
    (root / "value.txt").write_text("fail\n", encoding="utf-8")

    result = service.run(project)
    task = service.tasks.status(setup["task_id"], project_id=project.id)

    assert result["status"] == "needs_work"
    assert result["checks"][0]["status"] == "failed"
    assert task["assessment"]["missing_criteria"] == ["criterion-1"]
    assert task["assessment"]["failed_constraints"] == ["constraint-1"]


def test_changed_check_configuration_fails_closed_before_execution(
    tmp_path, monkeypatch
) -> None:
    root = _repository(tmp_path, monkeypatch)
    project = detect_project(root)
    service = LiansCheckService(MemoryStore(tmp_path / "memory.sqlite3"))
    service.initialize(project, checks=[_content_check()])
    config_path = root / CHECK_PATH
    config = json.loads(config_path.read_text(encoding="utf-8"))
    marker = root / "should-not-exist"
    config["checks"][0]["argv"] = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).touch()",
    ]
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(CheckConfigError, match="changed after authorization"):
        service.run(project)

    assert not marker.exists()


def test_a_check_that_changes_source_cannot_mark_its_own_result_ready(
    tmp_path, monkeypatch
) -> None:
    root = _repository(tmp_path, monkeypatch)
    project = detect_project(root)
    source = "from pathlib import Path; Path('value.txt').write_text('changed\\n')"
    service = LiansCheckService(MemoryStore(tmp_path / "memory.sqlite3"))
    service.initialize(
        project,
        checks=[CheckSpec("tests", "Tests", (sys.executable, "-c", source), 30)],
    )

    result = service.run(project)

    assert result["status"] == "needs_work"
    assert result["workspace_changed_during_check"] is True
    assert any(
        item["code"] == "task_blocker" for item in result["verification"]["blockers"]
    )


def test_run_check_stops_unbounded_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(check_module, "_MAX_TOTAL_OUTPUT_BYTES", 1_024)
    source = "import sys; sys.stdout.write('x' * 4096)"

    result = check_module._run_check(
        tmp_path,
        CheckSpec("tests", "Tests", (sys.executable, "-c", source), 30),
    )

    assert result["status"] == "failed"
    assert result["exit_code"] == 125
    assert "safety limit" in result["detail"]


def test_cli_exposes_the_two_command_product_surface() -> None:
    initialized = cli.parser().parse_args(
        ["init", "--command", "tests=python -m pytest -q", "--yes"]
    )
    checked = cli.parser().parse_args(["check", "--base", "origin/main", "--json"])

    assert initialized.command == "init"
    assert initialized.check_commands == ["tests=python -m pytest -q"]
    assert initialized.yes is True
    assert checked.command == "check"
    assert checked.base == "origin/main"


def test_top_level_help_keeps_advanced_surfaces_out_of_the_first_run() -> None:
    help_text = cli.parser().format_help()

    assert "init" in help_text
    assert "check" in help_text
    assert "experiment" not in help_text
    assert "video" not in help_text


def test_check_without_setup_returns_no_proof_and_a_nonzero_exit(
    tmp_path, monkeypatch, capsys
) -> None:
    root = _repository(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)

    with pytest.raises(SystemExit) as stopped:
        cli.main(["check", "--cwd", str(root), "--data", str(tmp_path / "memory.sqlite3")])

    assert stopped.value.code == 2
    output = capsys.readouterr().out
    assert "NO PROOF" in output
    assert "lians init" in output


def test_check_cli_returns_the_ready_result_as_json(tmp_path, monkeypatch, capsys) -> None:
    root = _repository(tmp_path, monkeypatch)
    data = tmp_path / "memory.sqlite3"
    project = detect_project(root)
    LiansCheckService(MemoryStore(data)).initialize(project, checks=[_content_check()])
    (root / "value.txt").write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)

    cli.main(["check", "--cwd", str(root), "--data", str(data), "--json"])

    result = json.loads(capsys.readouterr().out)
    assert result["headline"] == "READY TO REVIEW"
    assert result["verification"]["external_check_trust"] == "lians_measured"


def test_init_cli_shows_authorized_commands_without_failure_labels(
    tmp_path, monkeypatch, capsys
) -> None:
    root = _repository(tmp_path, monkeypatch)
    data = tmp_path / "memory.sqlite3"
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)

    cli.main(
        [
            "init",
            "--cwd",
            str(root),
            "--data",
            str(data),
            "--command",
            f"tests={sys.executable} -c 'raise SystemExit(0)'",
            "--yes",
        ]
    )

    output = capsys.readouterr().out
    assert "LIANS CHECK IS READY" in output
    assert "- Tests:" in output
    assert "FAIL" not in output
