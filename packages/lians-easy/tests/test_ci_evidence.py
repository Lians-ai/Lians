from __future__ import annotations

import subprocess

import pytest
from lians_easy.ci_evidence import (
    CIEvidenceError,
    emit_github_actions_evidence,
    import_github_actions_evidence,
)
from lians_easy.project import detect_project
from lians_easy.store import MemoryStore
from lians_easy.task_contract import TaskContractService


def _git(root, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repository(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Lians Test")
    (root / "README.md").write_text("test\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "initial")
    return root, _git(root, "rev-parse", "HEAD")


def _environment(commit_sha: str) -> dict[str, str]:
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "Lians-ai/Lians",
        "GITHUB_SHA": commit_sha,
        "GITHUB_REF": "refs/heads/master",
        "GITHUB_WORKFLOW_REF": (
            "Lians-ai/Lians/.github/workflows/lians-guard.yml@refs/heads/master"
        ),
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_EVENT_NAME": "push",
    }


def test_emit_requires_github_actions_and_writes_only_passing_checks(tmp_path):
    path = tmp_path / "evidence.json"
    with pytest.raises(CIEvidenceError, match="inside GitHub Actions"):
        emit_github_actions_evidence(path, checks=["guard-tests"], environment={})

    document = emit_github_actions_evidence(
        path,
        checks=["guard-tests", "guard-lint", "guard-tests"],
        environment=_environment("a" * 40),
    )

    assert document["checks"] == [
        {"id": "guard-tests", "status": "passed"},
        {"id": "guard-lint", "status": "passed"},
    ]
    assert path.is_file()


def test_attested_ci_import_opens_gate_and_binds_current_head(tmp_path):
    root, head = _repository(tmp_path)
    artifact = tmp_path / "evidence.json"
    emit_github_actions_evidence(
        artifact,
        checks=["guard-tests", "guard-lint"],
        environment=_environment(head),
    )
    store = MemoryStore(tmp_path / "memory.sqlite3")
    project = detect_project(root)
    TaskContractService(store).start(
        "Keep the Guard release surface green",
        ["The Guard suite and lint gate pass"],
        project_id=project.id,
        task_id="guard-release",
    )
    calls: list[list[str]] = []

    def verified_runner(arguments, **_kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout="[{}]", stderr="")

    result = import_github_actions_evidence(
        artifact,
        repository="Lians-ai/Lians",
        signer_workflow="Lians-ai/Lians/.github/workflows/lians-guard.yml",
        source_ref="refs/heads/master",
        task_id="guard-release",
        criterion_ids=["criterion-1"],
        check_ids=["guard-tests", "guard-lint"],
        project_root=root,
        store=store,
        runner=verified_runner,
    )

    assessment = result["task"]["assessment"]
    assert assessment["status"] == "ready_for_human_review"
    criterion = assessment["criteria"][0]
    assert criterion["trust_class"] == "measured_ci"
    assert criterion["trust_provenance"]["issuer"] == (
        "github_actions_attestation"
    )
    assert "--deny-self-hosted-runners" in calls[0]
    assert calls[0][calls[0].index("--source-digest") + 1] == head


def test_ci_import_rejects_failed_attestation(tmp_path):
    root, head = _repository(tmp_path)
    artifact = tmp_path / "evidence.json"
    emit_github_actions_evidence(
        artifact,
        checks=["guard-tests"],
        environment=_environment(head),
    )

    def rejected_runner(arguments, **_kwargs):
        return subprocess.CompletedProcess(
            arguments,
            1,
            stdout="",
            stderr="attestation signature is invalid",
        )

    with pytest.raises(CIEvidenceError, match="GitHub rejected"):
        import_github_actions_evidence(
            artifact,
            repository="Lians-ai/Lians",
            signer_workflow="Lians-ai/Lians/.github/workflows/lians-guard.yml",
            source_ref="refs/heads/master",
            task_id="guard-release",
            criterion_ids=["criterion-1"],
            check_ids=["guard-tests"],
            project_root=root,
            store=MemoryStore(tmp_path / "memory.sqlite3"),
            runner=rejected_runner,
        )


def test_ci_import_rejects_an_internal_workflow_identity_mismatch(tmp_path):
    root, head = _repository(tmp_path)
    artifact = tmp_path / "evidence.json"
    environment = _environment(head)
    environment["GITHUB_WORKFLOW_REF"] = (
        "Lians-ai/Lians/.github/workflows/other.yml@refs/heads/master"
    )
    emit_github_actions_evidence(
        artifact,
        checks=["guard-tests"],
        environment=environment,
    )

    with pytest.raises(CIEvidenceError, match="workflow identity"):
        import_github_actions_evidence(
            artifact,
            repository="Lians-ai/Lians",
            signer_workflow="Lians-ai/Lians/.github/workflows/lians-guard.yml",
            source_ref="refs/heads/master",
            task_id="guard-release",
            criterion_ids=["criterion-1"],
            check_ids=["guard-tests"],
            project_root=root,
            store=MemoryStore(tmp_path / "memory.sqlite3"),
            runner=lambda arguments, **_kwargs: subprocess.CompletedProcess(
                arguments, 0, stdout="[{}]", stderr=""
            ),
        )
