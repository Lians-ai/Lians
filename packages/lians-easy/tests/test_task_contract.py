from __future__ import annotations

import pytest
from lians_easy.store import MemoryStore
from lians_easy.task_contract import TaskContractService


def _service(tmp_path) -> TaskContractService:
    return TaskContractService(MemoryStore(tmp_path / "memory.sqlite3"))


def _start(service: TaskContractService, **overrides):
    values = {
        "goal": "Ship a reliable Windows tester",
        "success_criteria": [
            "The package launches without a console window",
            "The MCP runtime lists every supported tool",
        ],
        "constraints": ["Do not expose credentials"],
        "project_id": "project-1",
        "task_id": "release-test",
        "client": "codex",
    }
    values.update(overrides)
    return service.start(**values)


def _measured(criterion_id: str, evidence: str) -> dict[str, str]:
    return {
        "criterion_id": criterion_id,
        "evidence": evidence,
        "trust_class": "measured_local",
        "source": "pytest fixture",
    }


def _measured_check(
    constraint_id: str, status: str, evidence: str
) -> dict[str, str]:
    return {
        "constraint_id": constraint_id,
        "status": status,
        "evidence": evidence,
        "trust_class": "measured_local",
        "source": "pytest fixture",
    }


def test_task_stays_active_until_every_criterion_and_constraint_has_evidence(tmp_path):
    service = _service(tmp_path)
    started = _start(service)

    assert started["assessment"]["status"] == "active"
    assert started["assessment"]["missing_criteria"] == ["criterion-1", "criterion-2"]
    assert started["assessment"]["unknown_constraints"] == ["constraint-1"]
    assert started["assessment"]["may_claim_completion"] is False

    partial = service._checkpoint_trusted(
        "release-test",
        "The launcher smoke test passed",
        issuer="local_verification",
        project_id="project-1",
        current_action="Inspect the MCP tool list",
        evidence=[_measured("criterion-1", "Process test exit code 0")],
        constraint_checks=[
            _measured_check(
                "constraint-1", "passed", "Credential scanner found zero values"
            )
        ],
        client="claude",
    )

    assert partial["assessment"]["status"] == "active"
    assert partial["assessment"]["missing_criteria"] == ["criterion-2"]

    complete = service._checkpoint_trusted(
        "release-test",
        "The runtime exposed the complete supported tool set",
        issuer="local_verification",
        project_id="project-1",
        evidence=[_measured("criterion-2", "tools/list returned 12 tools")],
        client="cursor",
    )

    assert complete["assessment"]["status"] == "ready_for_human_review"
    assert complete["assessment"]["missing_criteria"] == []
    assert complete["assessment"]["may_claim_completion"] is True
    assert complete["state"]["client"] == "cursor"
    assert complete["state"]["evidence"]["criterion-1"]["evidence"] == (
        "Process test exit code 0"
    )
    assert complete["state"]["evidence"]["criterion-1"]["trust_class"] == (
        "measured_local"
    )


def test_blockers_and_failed_constraints_prevent_completion(tmp_path):
    service = _service(tmp_path)
    _start(service)
    evidence = [
        _measured("criterion-1", "Launch test passed"),
        _measured("criterion-2", "Tool contract test passed"),
    ]
    check = [_measured_check("constraint-1", "passed", "Secret scan passed")]

    blocked = service.checkpoint(
        "release-test",
        "All checks passed but signing is unavailable",
        project_id="project-1",
        evidence=evidence,
        constraint_checks=check,
        blockers=["The executable is unsigned"],
    )
    assert blocked["assessment"]["status"] == "blocked"
    assert blocked["assessment"]["may_claim_completion"] is False

    failed = service.checkpoint(
        "release-test",
        "A credential appeared in the packaged logs",
        project_id="project-1",
        constraint_checks=[
            _measured_check(
                "constraint-1",
                "failed",
                "Scanner matched a credential in verbose.log",
            )
        ],
        blockers=[],
    )
    assert failed["assessment"]["status"] == "at_risk"
    assert failed["assessment"]["failed_constraints"] == ["constraint-1"]


def test_checkpoint_rejects_unsupported_claims_and_stale_agents(tmp_path):
    service = _service(tmp_path)
    _start(service, event_time="2026-08-17T12:00:00Z")

    with pytest.raises(ValueError, match="Unknown criterion_id"):
        service.checkpoint(
            "release-test",
            "Everything is done",
            project_id="project-1",
            evidence=[
                {
                    "criterion_id": "criterion-99",
                    "evidence": "Trust me",
                    "trust_class": "agent_attested",
                }
            ],
        )

    service.checkpoint(
        "release-test",
        "Current checkpoint",
        project_id="project-1",
        current_action="Verify the Windows tester",
        event_time="2026-08-17T13:00:00Z",
    )
    with pytest.raises(ValueError, match="older than the current state"):
        service.checkpoint(
            "release-test",
            "Delayed checkpoint from another agent",
            project_id="project-1",
            event_time="2026-08-17T12:30:00Z",
        )


def test_task_context_is_bounded_signed_and_cross_agent(tmp_path):
    service = _service(tmp_path)
    _start(service)
    service.checkpoint(
        "release-test",
        "Launcher validation is complete",
        project_id="project-1",
        evidence=[_measured("criterion-1", "Launch test passed")],
        current_action="Verify the MCP runtime",
        decisions=[
            {
                "decision": "Keep the runtime local",
                "reason": "The package must work offline",
                "source": "release constraint",
            }
        ],
        open_questions=["Who signs the executable?"],
        client="claude",
    )

    context = TaskContractService(service.store).context(
        "release-test",
        project_id="project-1",
        client="codex",
        max_tokens=512,
    )

    assert "Ship a reliable Windows tester" in context["context"]
    assert "criterion-2" in context["context"]
    assert context["receipt"]["client"] == "codex"
    assert context["receipt"]["signature"]["algorithm"] == "Ed25519"
    assert len(context["context"]) <= 512 * 4
    assert "Decisions:" in context["context"]
    assert "Who signs the executable?" in context["context"]
    assert "Sources and time:" in context["context"]


def test_continue_work_selects_one_goal_and_never_guesses_between_two(tmp_path):
    service = _service(tmp_path)
    _start(service)

    automatic = service.continue_work(
        project_id="project-1",
        client="codex",
        max_tokens=512,
    )
    assert automatic["status"] == "ready"
    assert automatic["selection"] == "automatic"
    assert automatic["task_id"] == "release-test"

    _start(
        service,
        task_id="second-task",
        goal="Prepare the release notes",
        success_criteria=["The notes list verified changes"],
    )
    ambiguous = service.continue_work(project_id="project-1")
    assert ambiguous["status"] == "ambiguous"
    assert {item["task_id"] for item in ambiguous["tasks"]} == {
        "release-test",
        "second-task",
    }
    assert ambiguous["context"] == ""


def test_drift_is_a_visible_signal_not_a_semantic_verdict(tmp_path):
    service = _service(tmp_path)
    _start(service)

    status = service.checkpoint(
        "release-test",
        "Draft beach vacation recipes and compare airline loyalty programs",
        project_id="project-1",
        current_action="Choose hotel breakfast options",
    )

    assert status["assessment"]["drift"]["signal"] == "possible_drift"
    assert "not a semantic judgment" in status["assessment"]["drift"]["basis"]


def test_agent_attested_and_inferred_activity_do_not_satisfy_criteria(tmp_path):
    service = _service(tmp_path)
    _start(service)

    result = service.checkpoint(
        "release-test",
        "The agent says the launcher and tool contract are complete",
        project_id="project-1",
        evidence=[
            {
                "criterion_id": "criterion-1",
                "evidence": "The agent reported that the launch test passed",
                "trust_class": "agent_attested",
                "source": "assistant final answer",
            },
            {
                "criterion_id": "criterion-2",
                "evidence": "mcp.py was touched",
                "trust_class": "inferred_activity",
                "source": "changed-file observation",
            },
        ],
    )

    assert result["assessment"]["status"] == "active"
    assert result["assessment"]["missing_criteria"] == ["criterion-1", "criterion-2"]
    assert result["assessment"]["untrusted_criteria"] == ["criterion-1", "criterion-2"]
    assert result["assessment"]["may_claim_ready_for_review"] is False


def test_agent_cannot_self_declare_measured_or_human_evidence(tmp_path):
    service = _service(tmp_path)
    _start(service)

    result = service.checkpoint(
        "release-test",
        "The agent attempted to assign its own trust labels",
        project_id="project-1",
        evidence=[
            _measured("criterion-1", "Agent supplied a claimed local measurement"),
            {
                "criterion_id": "criterion-2",
                "evidence": "Agent supplied a claimed human approval",
                "trust_class": "human_confirmed",
                "source": "agent request",
            },
        ],
    )

    assert result["assessment"]["status"] == "active"
    assert result["assessment"]["missing_criteria"] == [
        "criterion-1",
        "criterion-2",
    ]
    assert result["state"]["evidence"]["criterion-1"]["trust_class"] == (
        "agent_attested"
    )
    assert result["state"]["evidence"]["criterion-1"]["declared_trust_class"] == (
        "measured_local"
    )
    assert result["state"]["evidence"]["criterion-2"]["declared_trust_class"] == (
        "human_confirmed"
    )


def test_workspace_change_marks_ready_checkpoint_stale(tmp_path):
    service = _service(tmp_path)
    service.start(
        "Ship the launcher",
        ["The launcher passes"],
        project_id="project-1",
        task_id="launcher",
    )
    saved_workspace = {
        "status": "measured_local",
        "repository_sha256": "repo-a",
        "head": "abc123",
        "dirty": True,
        "changes_sha256": "changes-a",
    }
    ready = service._checkpoint_trusted(
        "launcher",
        "The local launcher check passed",
        issuer="local_verification",
        project_id="project-1",
        evidence=[_measured("criterion-1", "launcher exit code 0")],
        workspace=saved_workspace,
    )
    assert ready["assessment"]["status"] == "ready_for_human_review"

    fresh = service.status(
        "launcher",
        project_id="project-1",
        workspace=saved_workspace,
    )
    assert fresh["assessment"]["workspace"]["status"] == "fresh"

    changed_workspace = {**saved_workspace, "head": "def456", "dirty": False}
    stale = service.status(
        "launcher",
        project_id="project-1",
        workspace=changed_workspace,
    )
    assert stale["assessment"]["status"] == "stale"
    assert stale["assessment"]["may_claim_completion"] is False
    assert stale["assessment"]["stale_reasons"] == [
        "Git HEAD changed",
        "working-tree clean or dirty state changed",
    ]


def test_contract_content_is_encrypted_and_credential_like_values_are_rejected(tmp_path):
    database = tmp_path / "memory.sqlite3"
    service = TaskContractService(MemoryStore(database))

    _start(service)
    raw = database.read_bytes()
    assert b"Ship a reliable Windows tester" not in raw

    with pytest.raises(ValueError, match="Credential-like"):
        service.start(
            "Use api_key=sk-secretvalue123456789 in the build",
            ["The build completes"],
            project_id="project-1",
            task_id="unsafe-task",
        )


def test_guard_report_counts_status_and_untrusted_claims_without_claiming_roi(tmp_path):
    service = _service(tmp_path)
    _start(service)
    service.checkpoint(
        "release-test",
        "The agent reported one criterion complete",
        project_id="project-1",
        evidence=[
            {
                "criterion_id": "criterion-1",
                "evidence": "The agent says the launcher passes",
                "trust_class": "agent_attested",
            }
        ],
    )

    report = service.report(project_id="project-1")

    assert report["tasks"]["total"] == 1
    assert report["tasks"]["by_status"]["active"] == 1
    assert report["criteria"]["untrusted_with_evidence"] == 1
    assert report["criteria"]["satisfied"] == 0
    assert "does not establish time saved" in report["claim_boundary"]
