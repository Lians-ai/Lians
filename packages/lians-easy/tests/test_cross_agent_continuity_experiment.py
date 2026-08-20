from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).parents[3]
    / "experiments"
    / "cross-agent-continuity"
    / "continuity_experiment.py"
)
SPEC = importlib.util.spec_from_file_location("lians_cross_agent_continuity", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

ContinuityExperiment = MODULE.ContinuityExperiment
MemoryStore = MODULE.MemoryStore
Project = MODULE.Project
evaluate = MODULE.evaluate


def _project(tmp_path: Path, name: str) -> object:
    root = tmp_path / name
    root.mkdir()
    return Project(id=f"project-{name}", name=name, root=str(root), origin=None)


def _session() -> dict:
    return {
        "schema": MODULE.SCHEMA,
        "session_id": "claude-auth-v2",
        "event_time": "2026-08-18T15:00:00Z",
        "source_reference": "claude-session:auth-v2",
        "task": {
            "id": "auth-migration",
            "title": "Migrate authentication",
            "goal": "Migrate authentication without undoing the existing security model.",
        },
        "project_truth": [
            {"key": "project/tests", "content": "Tests use pytest."},
        ],
        "completed": [
            {
                "id": "middleware",
                "description": "Migrate the authentication middleware.",
                "evidence": "src/auth.py changed and its tests passed.",
            },
            {
                "id": "tests",
                "description": "Update the authentication tests.",
                "evidence": "pytest tests/test_auth.py passed.",
            },
        ],
        "blocked": [
            {
                "id": "callback-test",
                "description": "Fix the OAuth callback integration test.",
                "blocker": "The OAuth callback integration test is failing.",
            }
        ],
        "not_started": ["Update the authentication documentation."],
        "decisions": [
            {
                "key": "decisions/pkce",
                "decision": "Keep PKCE.",
                "reason": "The security model requires it.",
            }
        ],
        "constraints": [
            {"key": "constraints/pkce", "content": "Do not remove PKCE."},
        ],
        "changes": [
            {
                "key": "api/auth-callback",
                "description": "The callback route is /v2/auth/callback.",
                "previous": "The callback route is /v1/auth.",
                "current": "The callback route is /v2/auth/callback.",
                "previous_event_time": "2026-08-18T14:00:00Z",
                "reason": "Auth API migration.",
            }
        ],
        "do_not_repeat": ["Do not redo the authentication middleware migration."],
        "next_actions": ["Fix the OAuth callback integration test before touching UI."],
        "files_touched": ["src/auth.py", "tests/test_auth.py"],
        "commit": "82ac4f",
    }


def test_session_end_capture_and_fresh_codex_handoff(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    project = _project(tmp_path, "auth")
    service = ContinuityExperiment(store)

    captured = service.capture(_session(), project=project, client="claude")
    handoff = ContinuityExperiment(MemoryStore(store.path)).handoff(
        project=project,
        client="codex",
    )

    assert captured["task"]["state"]["client"] == "claude"
    assert handoff["status"] == "ready"
    assert "Migrate the authentication middleware" in handoff["context"]
    assert "OAuth callback integration test is failing" in handoff["context"]
    assert "Tests use pytest" in handoff["context"]
    assert "Keep PKCE" in handoff["context"]
    assert "/v2/auth/callback" in handoff["context"]
    assert "/v1/auth" in handoff["context"] and "now stale" in handoff["context"]
    assert "before touching UI" in handoff["context"]
    assert handoff["receipt"]["client"] == "codex"
    assert handoff["receipt"]["token_estimate"] <= 1_200
    assert len(handoff["receipt"]["signature"]["value"]) > 40


def test_existing_codex_hook_injects_captured_continuity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lians_easy.bridge import context_for_event
    from lians_easy.project import detect_project

    root = tmp_path / "hook-project"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    project = detect_project(root)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    ContinuityExperiment(store).capture(_session(), project=project, client="claude")

    pack = context_for_event(
        {"prompt": "Pick up where we left off.", "cwd": str(root)},
        client="codex",
        store=store,
    )

    assert pack["task_selection"] == {
        "status": "automatic",
        "task_ids": ["auth-migration"],
    }
    assert "Migrate the authentication middleware" in pack["context"]
    assert "Fix the OAuth callback integration test before touching UI" in pack["context"]
    assert "Keep PKCE" in pack["context"]
    assert "The callback route is /v2/auth/callback" in pack["context"]
    assert "Do not redo the authentication middleware migration" in pack["context"]
    assert "The callback route is /v1/auth" in pack["context"]
    assert "superseded and stale" in pack["context"]
    assert pack["task_context"]["receipt"]["client"] == "codex"


def test_project_scope_prevents_cross_project_leakage(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    first = _project(tmp_path, "first")
    second = _project(tmp_path, "second")
    service = ContinuityExperiment(store)
    service.capture(_session(), project=first)
    other = _session()
    other["session_id"] = "claude-second"
    other["task"]["id"] = "second-task"
    other["task"]["title"] = "Second project task"
    other["task"]["goal"] = "Keep the second project isolated."
    other["completed"] = [
        {"id": "private", "description": "Finish SECOND-ONLY work.", "evidence": "done"}
    ]
    other["blocked"] = []
    other["not_started"] = ["Review SECOND-ONLY output."]
    other["project_truth"] = []
    other["decisions"] = []
    other["constraints"] = []
    other["changes"] = []
    other["do_not_repeat"] = []
    other["next_actions"] = ["Review SECOND-ONLY output."]
    service.capture(other, project=second)

    first_handoff = service.handoff(project=first)
    second_handoff = service.handoff(project=second)

    assert "SECOND-ONLY" not in first_handoff["context"]
    assert "authentication middleware" not in second_handoff["context"]


def test_supersession_exposes_current_value_and_marks_previous_stale(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    project = _project(tmp_path, "temporal")
    service = ContinuityExperiment(store)
    service.capture(_session(), project=project)

    history = store.memory_history("api/auth-callback", project_id=project.id)
    handoff = service.handoff(project=project)

    assert [item["content"] for item in history] == [
        "The callback route is /v1/auth.",
        "The callback route is /v2/auth/callback.",
    ]
    assert history[0]["is_current"] is False
    assert history[1]["is_current"] is True
    assert handoff["state"]["changes"][0]["current"] == history[1]["content"]
    assert handoff["receipt"]["stale_memory_ids_excluded_from_current"] == [history[0]["id"]]


def test_new_session_supersedes_decision_in_task_and_named_state(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    project = _project(tmp_path, "decision-update")
    service = ContinuityExperiment(store)
    service.capture(_session(), project=project)
    later = _session()
    later["session_id"] = "claude-auth-device-flow"
    later["event_time"] = "2026-08-18T16:00:00Z"
    later["decisions"] = [
        {
            "key": "decisions/pkce",
            "decision": "Keep PKCE and use the OAuth device flow for CLI login.",
            "reason": "The CLI cannot receive a browser callback reliably.",
        }
    ]
    service.capture(later, project=project)

    handoff = service.handoff(project=project)
    history = store.memory_history("decisions/pkce", project_id=project.id)

    assert len(history) == 2
    assert history[0]["is_current"] is False
    assert history[1]["is_current"] is True
    assert handoff["state"]["decisions"] == [
        "Keep PKCE and use the OAuth device flow for CLI login."
    ]
    assert "The security model requires it" not in handoff["context"]


def test_capture_is_idempotent_and_does_not_duplicate_session_state(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    project = _project(tmp_path, "idempotent")
    service = ContinuityExperiment(store)

    first = service.capture(_session(), project=project)
    second = service.capture(_session(), project=project)

    assert first["task"]["task_id"] == second["task"]["task_id"]
    assert len(store.memory_history("api/auth-callback", project_id=project.id)) == 2
    assert len(store.memory_history("decisions/pkce", project_id=project.id)) == 1
    task_states = store.list(kind="task_state", project_id=project.id, state="all", limit=20)
    assert len(task_states) == 1


def test_bounded_handoff_and_offline_acceptance_metrics(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    project = _project(tmp_path, "evaluation")
    service = ContinuityExperiment(store)
    service.capture(_session(), project=project)
    handoff = service.handoff(project=project, max_tokens=500, max_items=12)
    expected = {
        "reported_completed": ["Migrate the authentication middleware."],
        "unfinished": ["Fix the OAuth callback integration test."],
        "project_truth": ["Tests use pytest."],
        "decisions": ["Keep PKCE."],
        "constraints": ["Do not remove PKCE."],
        "do_not_repeat": ["Do not redo the authentication middleware migration."],
        "current_facts": ["The callback route is /v2/auth/callback."],
        "stale_facts": ["The callback route is /v1/auth."],
        "next_actions": ["Fix the OAuth callback integration test before touching UI."],
    }

    result = evaluate(handoff, expected)

    assert handoff["receipt"]["token_estimate"] <= 500
    assert handoff["receipt"]["selected_item_count"] <= 12
    assert result["continuity_accuracy"] == 1.0
    assert result["stale_fact_error_rate"] == 0.0
    assert result["passed"] is True


def test_ambiguous_active_work_is_not_guessed(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    project = _project(tmp_path, "ambiguous")
    service = ContinuityExperiment(store)
    service.capture(_session(), project=project)
    second = _session()
    second["session_id"] = "claude-second-task"
    second["task"]["id"] = "second-task"
    second["task"]["title"] = "Second task"
    second["task"]["goal"] = "Complete another independent task."
    service.capture(second, project=project)

    result = service.handoff(project=project)

    assert result["status"] == "ambiguous"
    assert result["context"] == ""
    assert {item["task_id"] for item in result["tasks"]} == {
        "auth-migration",
        "second-task",
    }


def test_invalid_or_empty_session_is_rejected(tmp_path: Path) -> None:
    service = ContinuityExperiment(MemoryStore(tmp_path / "memory.sqlite3"))
    project = _project(tmp_path, "invalid")
    payload = _session()
    payload["completed"] = []
    payload["blocked"] = []
    payload["not_started"] = []

    with pytest.raises(ValueError, match="session must include"):
        service.capture(payload, project=project)


def test_fixture_files_remain_valid_json() -> None:
    fixture_root = MODULE_PATH.parent / "fixtures"
    assert json.loads((fixture_root / "claude-session.json").read_text(encoding="utf-8"))
    assert json.loads((fixture_root / "expected.json").read_text(encoding="utf-8"))


def test_capture_cli_emits_bounded_receipt_without_session_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sensitive = "private-session-value-that-must-not-appear"
    payload = _session()
    payload["summary"] = sensitive
    payload["completed"][0]["evidence"] = sensitive
    session = tmp_path / "session.json"
    session.write_text(json.dumps(payload), encoding="utf-8")
    project_root = tmp_path / "project"
    project_root.mkdir()

    exit_code = MODULE.main(
        [
            "--data",
            str(tmp_path / "memory.sqlite3"),
            "--project-root",
            str(project_root),
            "capture",
            "--session",
            str(session),
            "--client",
            "claude",
        ]
    )

    output = capsys.readouterr().out
    receipt = json.loads(output)
    assert exit_code == 0
    assert receipt["status"] == "captured"
    assert receipt["memories_captured_or_confirmed"] > 0
    assert sensitive not in output
