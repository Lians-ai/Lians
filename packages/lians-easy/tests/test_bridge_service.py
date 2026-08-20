from __future__ import annotations

import base64
import json
import re
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from io import StringIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from lians_easy import __version__
from lians_easy.bridge import (
    ERASE_ALL_CONFIRMATION,
    BridgeApplication,
    context_for_event,
    render_hook_output,
    run_hook,
    write_cursor_rule,
)
from lians_easy.control_policy import ControlPolicyService
from lians_easy.installer import ClientTarget
from lians_easy.mcp import call_tool
from lians_easy.project import detect_project
from lians_easy.store import MemoryStore
from lians_easy.task_contract import TaskContractService


def _json_request(url, *, cookie, data=None, origin=None):
    headers = {"Cookie": cookie}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if origin:
        headers["Origin"] = origin
    request = Request(
        url,
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    with urlopen(request) as response:
        return response.status, json.loads(response.read())


def test_hook_adapter_and_cursor_rule_use_the_same_context(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    store = MemoryStore(tmp_path / "bridge.sqlite3")
    remembered = call_tool(
        store,
        "remember",
        {
            "content": "We use FastAPI and never write migrations manually.",
            "kind": "preference",
            "scope": "project",
            "project_root": str(project),
            "source_client": "cursor",
        },
    )["structuredContent"]

    event = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "Implement the next API migration",
        "cwd": str(project),
    }
    pack = context_for_event(event, client="claude", store=store)
    output = json.loads(render_hook_output("claude", pack))
    context = output["hookSpecificOutput"]["additionalContext"]

    assert "FastAPI" in context
    assert pack["receipt_line"] in context
    assert pack["receipt"]["memories"][0]["reason"]
    assert pack["receipt"]["memories"][0]["source_client"] == "cursor"
    assert pack["receipt"]["memories"][0]["updated_at"]

    codex_output = json.loads(
        render_hook_output("codex", context_for_event(event, client="codex", store=store))
    )
    assert "FastAPI" in codex_output["hookSpecificOutput"]["additionalContext"]

    gemini_output = json.loads(
        render_hook_output("gemini", context_for_event(event, client="gemini", store=store))
    )
    assert gemini_output["hookSpecificOutput"]["hookEventName"] == "BeforeAgent"
    assert "FastAPI" in gemini_output["hookSpecificOutput"]["additionalContext"]

    antigravity_event = {
        "invocationNum": 0,
        "workspacePaths": [str(project)],
    }
    antigravity_output = json.loads(
        render_hook_output(
            "antigravity",
            context_for_event(
                antigravity_event,
                client="antigravity",
                store=store,
                default_query="Active project preferences constraints decisions and handoff",
            ),
        )
    )
    assert "FastAPI" in antigravity_output["injectSteps"][0]["ephemeralMessage"]

    rule = write_cursor_rule(project, store=store)
    rule_path = project / ".cursor" / "rules" / "lians-memory.mdc"
    assert rule["path"] == str(rule_path)
    assert "alwaysApply: true" in rule_path.read_text(encoding="utf-8")

    corrected = call_tool(
        store,
        "correct_memory",
        {
            "memory_id": remembered["id"],
            "content": "We use FastAPI with Alembic-generated migrations only.",
            "project_root": str(project),
        },
    )["structuredContent"]
    refreshed = rule_path.read_text(encoding="utf-8")
    assert corrected["content"] in refreshed
    assert remembered["content"] not in refreshed
    corrected_codex = context_for_event(event, client="codex", store=store)
    assert corrected["content"] in corrected_codex["context"]
    assert remembered["content"] not in corrected_codex["context"]

    call_tool(
        store,
        "forget_memory",
        {
            "memory_id": corrected["id"],
            "confirmed": True,
            "project_root": str(project),
        },
    )
    assert corrected["content"] not in rule_path.read_text(encoding="utf-8")
    assert context_for_event(event, client="claude", store=store)["context"] == ""


def test_cursor_remember_creates_the_first_project_rule(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    store = MemoryStore(tmp_path / "bridge.sqlite3")

    call_tool(
        store,
        "remember",
        {
            "content": "Never use em dashes.",
            "kind": "preference",
            "scope": "project",
            "project_root": str(project),
            "source_client": "cursor",
        },
    )

    rule = project / ".cursor" / "rules" / "lians-memory.mdc"
    assert rule.is_file()
    assert "Never use em dashes." in rule.read_text(encoding="utf-8")


def test_hook_context_pulls_cloud_memory_before_building_its_receipt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore(tmp_path / "bridge.sqlite3")

    class PullingCloud:
        def __init__(self):
            self.calls = 0

        def pull_if_connected(self):
            self.calls += 1
            if self.calls == 1:
                store.remember(
                    "Never use em dashes.",
                    kind="preference",
                    scope="global",
                    source_client="cursor",
                )
            return {"state": "current", "attempted": True, "revisions_pulled": 1}

    cloud = PullingCloud()
    pack = context_for_event(
        {"prompt": "Draft the response", "cwd": str(tmp_path)},
        client="claude",
        store=store,
        cloud_sync=cloud,
    )

    assert cloud.calls == 1
    assert "Never use em dashes." in pack["context"]
    assert pack["receipt"]["client"] == "claude"
    assert pack["cloud_sync"]["revisions_pulled"] == 1


def test_hook_automatically_injects_the_only_unresolved_task_contract(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    store = MemoryStore(tmp_path / "bridge.sqlite3")
    detected = detect_project(project)
    tasks = TaskContractService(store)
    tasks.start(
        "Ship the verified Windows package",
        ["The executable starts", "The runtime lists every tool"],
        constraints=["Do not expose credentials"],
        project_id=detected.id,
        task_id="windows-release",
        client="cursor",
    )

    pack = context_for_event(
        {"prompt": "Continue the work", "cwd": str(project)},
        client="codex",
        store=store,
    )

    assert pack["task_selection"] == {
        "status": "automatic",
        "task_ids": ["windows-release"],
    }
    assert "Ship the verified Windows package" in pack["context"]
    assert "criterion-2" in pack["context"]
    assert pack["task_context"]["receipt"]["client"] == "codex"
    assert all(
        item["kind"] not in {"task_contract", "task_state"}
        for item in pack["memories"]
    )


def test_hook_refuses_to_guess_between_tasks_but_accepts_an_exact_task_id(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    store = MemoryStore(tmp_path / "bridge.sqlite3")
    detected = detect_project(project)
    tasks = TaskContractService(store)
    for task_id, goal in (("desktop", "Ship the desktop app"), ("docs", "Publish the docs")):
        tasks.start(
            goal,
            ["The work is verified"],
            project_id=detected.id,
            task_id=task_id,
        )

    ambiguous = context_for_event(
        {"prompt": "Continue", "cwd": str(project)},
        client="claude",
        store=store,
    )
    assert ambiguous["task_selection"]["status"] == "ambiguous"
    assert set(ambiguous["task_selection"]["task_ids"]) == {"desktop", "docs"}
    assert "No contract was injected" in ambiguous["context"]

    exact = context_for_event(
        {"prompt": "Continue", "cwd": str(project), "lians_task_id": "docs"},
        client="claude",
        store=store,
    )
    assert exact["task_selection"] == {"status": "exact", "task_ids": ["docs"]}
    assert "Publish the docs" in exact["context"]
    assert "Ship the desktop app" not in exact["context"]


def test_hook_control_modes_observe_guide_and_protect_without_storing_prompt(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    database = tmp_path / "bridge.sqlite3"
    store = MemoryStore(database)
    store.remember(
        "Use the verified release checklist.",
        kind="preference",
        scope="global",
    )
    control = ControlPolicyService(store)

    control.update({"mode": "observe"})
    observed = context_for_event(
        {"prompt": "Private prompt text that must not be stored", "cwd": str(project)},
        client="claude",
        store=store,
    )
    assert observed["context"] == ""
    assert observed["task_selection"]["status"] == "observe"
    assert observed["observation"]["content_stored"] is False
    assert b"Private prompt text that must not be stored" not in database.read_bytes()

    control.update({"mode": "guide", "context_budget_tokens": 256})
    guided = context_for_event(
        {"prompt": "Continue the release", "cwd": str(project)},
        client="claude",
        store=store,
    )
    assert "verified release checklist" in guided["context"]
    assert guided["receipt"]["limits"]["max_tokens"] == 256
    assert "Lians user control policy" not in guided["context"]

    control.update(
        {
            "mode": "protect",
            "approval_actions": ["publishing", "destructive_filesystem"],
        }
    )
    protected = context_for_event(
        {"prompt": "Publish the release", "cwd": str(project)},
        client="codex",
        store=store,
    )
    assert "Lians user control policy" in protected["context"]
    assert "Never infer approval" in protected["context"]
    assert protected["control"]["enforcement"]["requests_approval"] is True


def test_hook_accepts_a_utf8_bom_from_windows_hosts(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    data = tmp_path / "bridge.sqlite3"
    store = MemoryStore(data)
    detected = call_tool(
        store,
        "remember",
        {
            "content": "Use FastAPI for services.",
            "kind": "preference",
            "scope": "project",
            "project_root": str(project),
        },
    )["structuredContent"]
    assert detected["project_id"]
    event = json.dumps(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Build the FastAPI service",
            "cwd": str(project),
        }
    )
    output = StringIO()
    monkeypatch.setattr(sys, "stdin", StringIO("\ufeff" + event))
    monkeypatch.setattr(sys, "stdout", output)

    assert run_hook(client="codex", data_path=data) == 0
    assert "Use FastAPI for services." in output.getvalue()


def test_claude_session_end_hook_captures_without_prompt_output(tmp_path, monkeypatch):
    event = {
        "hook_event_name": "SessionEnd",
        "session_id": "session-1",
        "transcript_path": str(tmp_path / "session.jsonl"),
        "cwd": str(tmp_path),
    }
    captured = []
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(event)))
    output = StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(
        "lians_easy.bridge.capture_claude_session_end",
        lambda received, *, store: captured.append((received, store.path)),
    )

    assert run_hook(client="claude", data_path=tmp_path / "bridge.sqlite3") == 0
    assert captured[0][0] == event
    assert captured[0][1] == tmp_path / "bridge.sqlite3"
    assert output.getvalue() == ""


def test_claude_precompact_hook_captures_before_context_is_rewritten(
    tmp_path, monkeypatch
):
    event = {
        "hook_event_name": "PreCompact",
        "session_id": "session-1",
        "transcript_path": str(tmp_path / "session.jsonl"),
        "cwd": str(tmp_path),
        "trigger": "auto",
    }
    captured = []
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(event)))
    output = StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(
        "lians_easy.bridge.capture_claude_session_end",
        lambda received, *, store: captured.append((received, store.path)),
    )

    assert run_hook(client="claude", data_path=tmp_path / "bridge.sqlite3") == 0
    assert captured[0][0] == event
    assert output.getvalue() == ""


def test_gemini_before_agent_hook_injects_bounded_context(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    data = tmp_path / "bridge.sqlite3"
    store = MemoryStore(data)
    call_tool(
        store,
        "remember",
        {
            "content": "Use FastAPI for Gemini services.",
            "kind": "preference",
            "scope": "project",
            "project_root": str(project),
            "source_client": "cursor",
        },
    )
    event = json.dumps(
        {
            "hook_event_name": "BeforeAgent",
            "prompt": "Build the service",
            "cwd": str(project),
        }
    )
    output = StringIO()
    monkeypatch.setattr(sys, "stdin", StringIO(event))
    monkeypatch.setattr(sys, "stdout", output)

    assert run_hook(client="gemini", data_path=data) == 0
    payload = json.loads(output.getvalue())
    assert payload["hookSpecificOutput"]["hookEventName"] == "BeforeAgent"
    assert "Use FastAPI for Gemini services." in payload["hookSpecificOutput"]["additionalContext"]


def test_antigravity_hook_injects_once_per_agent_loop(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    data = tmp_path / "bridge.sqlite3"
    store = MemoryStore(data)
    call_tool(
        store,
        "remember",
        {
            "content": "Use FastAPI for Antigravity services.",
            "kind": "preference",
            "scope": "project",
            "project_root": str(project),
            "source_client": "cursor",
        },
    )

    first_output = StringIO()
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(json.dumps({"invocationNum": 0, "workspacePaths": [str(project)]})),
    )
    monkeypatch.setattr(sys, "stdout", first_output)
    assert run_hook(client="antigravity", data_path=data) == 0
    first_payload = json.loads(first_output.getvalue())
    assert "Use FastAPI for Antigravity services." in first_payload["injectSteps"][0][
        "ephemeralMessage"
    ]

    later_output = StringIO()
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(json.dumps({"invocationNum": 1, "workspacePaths": [str(project)]})),
    )
    monkeypatch.setattr(sys, "stdout", later_output)
    assert run_hook(client="antigravity", data_path=data) == 0
    assert json.loads(later_output.getvalue()) == {}


def test_antigravity_empty_workspace_injects_global_memory_only(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    data = tmp_path / "bridge.sqlite3"
    store = MemoryStore(data)
    call_tool(
        store,
        "remember",
        {
            "content": "Use FastAPI only inside this project.",
            "kind": "preference",
            "scope": "project",
            "project_root": str(project),
            "source_client": "antigravity",
        },
    )
    call_tool(
        store,
        "remember",
        {
            "content": "Never use em dashes.",
            "kind": "preference",
            "scope": "global",
            "source_client": "antigravity",
        },
    )
    monkeypatch.chdir(project)

    pack = context_for_event(
        {"invocationNum": 0, "workspacePaths": []},
        client="antigravity",
        store=store,
        default_query="Active project preferences constraints decisions and handoff",
    )

    assert "Never use em dashes." in pack["context"]
    assert "Use FastAPI only inside this project." not in pack["context"]
    assert pack["receipt"]["project"]["name"] == "global"
    assert pack["receipt"]["excluded"]["scope"] == 1


def test_loopback_app_uses_http_only_session_and_blocks_cross_origin_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MemoryStore(tmp_path / "bridge.sqlite3")
    app = BridgeApplication(store, port=0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
    app.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(app.origin) as response:
            cookie_header = response.headers["Set-Cookie"]
        assert "HttpOnly" in cookie_header
        assert "SameSite=Strict" in cookie_header
        cookie = cookie_header.split(";", 1)[0]

        status, body = _json_request(f"{app.origin}/v1/status", cookie=cookie)
        assert status == 200
        assert body["bridge"] == "ready"
        assert body["version"] == __version__
        assert body["memory"]["encrypted"] is True
        assert body["cloud"]["state"] == "unavailable"
        assert {item["key"] for item in body["integrations"]} >= {"claude", "cursor", "codex"}

        status, control = _json_request(f"{app.origin}/v1/control", cookie=cookie)
        assert status == 200
        assert control["policy"]["mode"] == "guide"

        status, control = _json_request(
            f"{app.origin}/v1/control",
            cookie=cookie,
            origin=app.origin,
            data={
                "policy": {
                    "mode": "protect",
                    "context_budget_tokens": 768,
                    "approval_actions": ["publishing"],
                }
            },
        )
        assert status == 200
        assert control["policy"]["mode"] == "protect"
        assert control["enforcement"]["requests_approval"] is True

        status, remembered = _json_request(
            f"{app.origin}/v1/remember",
            cookie=cookie,
            origin=app.origin,
            data={
                "content": "We use FastAPI and never write migrations manually.",
                "cwd": str(tmp_path),
                "client": "cursor",
            },
        )
        assert status == 201
        assert remembered["memory"]["source_client"] == "cursor"

        status, pack = _json_request(
            f"{app.origin}/v1/context",
            cookie=cookie,
            origin=app.origin,
            data={
                "prompt": "Implement the FastAPI migration",
                "cwd": str(tmp_path),
                "client": "codex",
            },
        )
        assert status == 200
        assert pack["receipt_line"].startswith("1 memories used")
        assert "FastAPI" in pack["context"]

        status, task = _json_request(
            f"{app.origin}/v1/tasks",
            cookie=cookie,
            origin=app.origin,
            data={
                "task_id": "bridge-task",
                "goal": "Ship the bridge",
                "success_criteria": ["The endpoint works"],
                "constraints": ["Keep memory local"],
                "cwd": str(tmp_path),
            },
        )
        assert status == 201
        assert task["assessment"]["status"] == "active"

        status, checkpoint = _json_request(
            f"{app.origin}/v1/task-checkpoints",
            cookie=cookie,
            origin=app.origin,
            data={
                "task_id": "bridge-task",
                "summary": "Endpoint test passed",
                "evidence": [
                    {
                        "criterion_id": "criterion-1",
                        "evidence": "HTTP 200 response",
                        "trust_class": "measured_local",
                        "source": "loopback integration test",
                    }
                ],
                "constraint_checks": [
                    {
                        "constraint_id": "constraint-1",
                        "status": "passed",
                        "evidence": "Loopback-only service",
                        "trust_class": "measured_local",
                        "source": "loopback binding inspection",
                    }
                ],
                "cwd": str(tmp_path),
            },
        )
        assert status == 200
        assert checkpoint["assessment"]["status"] == "active"
        assert checkpoint["assessment"]["untrusted_criteria"] == ["criterion-1"]

        status, task_status = _json_request(
            f"{app.origin}/v1/task-status?task_id=bridge-task",
            cookie=cookie,
        )
        assert status == 200
        assert task_status["assessment"]["may_claim_completion"] is False

        status, report = _json_request(
            f"{app.origin}/v1/guard-report?cwd={tmp_path}",
            cookie=cookie,
        )
        assert status == 200
        assert report["tasks"]["total"] == 1
        assert report["criteria"]["untrusted_with_evidence"] == 1

        with pytest.raises(HTTPError) as error:
            _json_request(
                f"{app.origin}/v1/remember",
                cookie=cookie,
                origin="https://attacker.example",
                data={"content": "This must not be stored"},
            )
        assert error.value.code == 403
        assert all(item["content"] != "This must not be stored" for item in store.list())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_bridge_application_can_be_started_and_stopped_as_a_resident_service(tmp_path):
    app = BridgeApplication(MemoryStore(tmp_path / "resident.sqlite3"), port=0)
    thread = threading.Thread(target=app.serve, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not app.running and time.monotonic() < deadline:
        time.sleep(0.01)
    assert app.running is True
    assert app.port > 0
    with urlopen(app.origin, timeout=2) as response:
        assert response.headers["Server"].startswith("LiansBridge/")
    app.shutdown()
    thread.join(timeout=5)
    assert thread.is_alive() is False
    assert app.running is False


def test_loopback_cloud_auth_exposes_status_and_confirmed_actions_without_tokens(tmp_path):
    calls = []

    class FakeCloudAuth:
        def status(self):
            return {"state": "signed_out", "configured": True, "message": "Sign in."}

        def sign_in(self):
            calls.append("sign-in")
            return {"state": "connected", "configured": True, "message": "Connected."}

        def sign_out(self, *, confirmed=False):
            if not confirmed:
                raise ValueError("Signing out requires confirmed=true")
            calls.append("sign-out")
            return {
                "state": "signed_out",
                "configured": True,
                "local_memory_preserved": True,
            }

    app = BridgeApplication(
        MemoryStore(tmp_path / "bridge.sqlite3"),
        port=0,
        cloud_auth=FakeCloudAuth(),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
    app.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(app.origin) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        status, cloud = _json_request(f"{app.origin}/v1/cloud/status", cookie=cookie)
        assert status == 200
        assert cloud["state"] == "signed_out"

        with pytest.raises(HTTPError) as unconfirmed:
            _json_request(
                f"{app.origin}/v1/cloud/sign-in",
                cookie=cookie,
                origin=app.origin,
                data={"confirmed": False},
            )
        assert unconfirmed.value.code == 400
        assert calls == []
        status, connected = _json_request(
            f"{app.origin}/v1/cloud/sign-in",
            cookie=cookie,
            origin=app.origin,
            data={"confirmed": True},
        )
        assert status == 200
        assert connected["state"] == "connected"
        status, signed_out = _json_request(
            f"{app.origin}/v1/cloud/sign-out",
            cookie=cookie,
            origin=app.origin,
            data={"confirmed": True},
        )
        assert status == 200
        assert signed_out["local_memory_preserved"] is True
        assert calls == ["sign-in", "sign-out"]
        assert "token" not in json.dumps([cloud, connected, signed_out]).lower()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_loopback_add_device_routes_are_short_code_only_and_confirmation_guarded(tmp_path):
    calls = []

    class FakeCloudAuth:
        def status(self):
            return {"state": "connected", "configured": True, "message": "Connected."}

    class FakeCloudSync:
        def status(self):
            return {"state": "connected", "sync_state": "not_started"}

        def pending_device_requests(self):
            calls.append("list")
            return {
                "state": "ready",
                "count": 1,
                "requests": [
                    {
                        "request_id": "2446b8a9-0f7c-4ea6-9434-8fb1857aa10d",
                        "verification_code": "ABCD-1234",
                        "device": {"display_name": "Laptop", "device_id": "device-2"},
                    }
                ],
            }

        def connected_devices(self):
            calls.append("devices")
            return {
                "state": "ready",
                "count": 2,
                "devices": [
                    {
                        "device_id": "a" * 64,
                        "display_name": "Laptop",
                        "state": "active",
                        "current": False,
                        "can_remove": True,
                    }
                ],
            }

        def remove_device(self, device_id, *, confirmed=False):
            if not confirmed:
                raise ValueError("Protecting future memory requires confirmed=true")
            assert device_id == "a" * 64
            calls.append("remove")
            return {
                "state": "removed",
                "future_memory_protected": True,
                "already_received_may_remain": True,
                "message": "Laptop cannot decrypt future cloud memory.",
            }

        def start_device_enrollment(self):
            calls.append("start")
            return {
                "state": "waiting_for_approval",
                "request_id": "2446b8a9-0f7c-4ea6-9434-8fb1857aa10d",
                "verification_code": "ABCD-1234",
            }

        def device_enrollment_status(self):
            calls.append("check")
            return {"state": "connected", "device_count": 2}

        def cancel_device_enrollment(self, *, confirmed=False):
            assert confirmed is True
            calls.append("cancel")
            return {"state": "cancelled"}

        def approve_device_request(
            self, request_id, verification_code, *, confirmed=False
        ):
            assert request_id == "2446b8a9-0f7c-4ea6-9434-8fb1857aa10d"
            assert verification_code == "ABCD-1234"
            assert confirmed is True
            calls.append("approve")
            return {
                "state": "approved",
                "device": {"display_name": "Laptop", "device_id": "device-2"},
            }

    app = BridgeApplication(
        MemoryStore(tmp_path / "bridge.sqlite3"),
        port=0,
        cloud_auth=FakeCloudAuth(),
        cloud_sync=FakeCloudSync(),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
    app.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(app.origin) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        status, requests = _json_request(
            f"{app.origin}/v1/cloud/device-requests", cookie=cookie
        )
        assert status == 200
        assert requests["requests"][0]["verification_code"] == "ABCD-1234"
        status, devices = _json_request(
            f"{app.origin}/v1/cloud/devices", cookie=cookie
        )
        assert status == 200
        assert devices["devices"][0]["display_name"] == "Laptop"

        with pytest.raises(HTTPError) as unconfirmed:
            _json_request(
                f"{app.origin}/v1/cloud/device-enrollment/start",
                cookie=cookie,
                origin=app.origin,
                data={"confirmed": False},
            )
        assert unconfirmed.value.code == 400
        _, started = _json_request(
            f"{app.origin}/v1/cloud/device-enrollment/start",
            cookie=cookie,
            origin=app.origin,
            data={"confirmed": True},
        )
        _, checked = _json_request(
            f"{app.origin}/v1/cloud/device-enrollment/check",
            cookie=cookie,
            origin=app.origin,
            data={},
        )
        _, approved = _json_request(
            f"{app.origin}/v1/cloud/device-requests/approve",
            cookie=cookie,
            origin=app.origin,
            data={
                "request_id": started["request_id"],
                "verification_code": started["verification_code"],
                "confirmed": True,
            },
        )
        _, cancelled = _json_request(
            f"{app.origin}/v1/cloud/device-enrollment/cancel",
            cookie=cookie,
            origin=app.origin,
            data={"confirmed": True},
        )
        with pytest.raises(HTTPError) as unconfirmed_removal:
            _json_request(
                f"{app.origin}/v1/cloud/devices/remove",
                cookie=cookie,
                origin=app.origin,
                data={"device_id": "a" * 64, "confirmed": False},
            )
        assert unconfirmed_removal.value.code == 400
        _, removed = _json_request(
            f"{app.origin}/v1/cloud/devices/remove",
            cookie=cookie,
            origin=app.origin,
            data={"device_id": "a" * 64, "confirmed": True},
        )
        assert checked["state"] == "connected"
        assert approved["state"] == "approved"
        assert cancelled["state"] == "cancelled"
        assert removed["future_memory_protected"] is True
        assert calls == ["list", "devices", "start", "check", "approve", "cancel", "remove"]
        public = [requests, devices, started, checked, approved, cancelled, removed]
        assert "workspace" not in json.dumps(public)
        assert "signing_public_key" not in json.dumps(public)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_loopback_memory_operations_pull_then_write_through_to_cloud(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    class FakeCloudAuth:
        def status(self):
            return {"state": "connected", "configured": True, "message": "Connected."}

    class FakeCloudSync:
        def status(self):
            return {"state": "connected", "sync_state": "ready", "head_revision": 1}

        def pull_if_connected(self):
            calls.append("pull")
            return {"state": "current", "attempted": True, "revisions_pulled": 0}

        def sync_if_connected(self):
            calls.append("push")
            return {
                "state": "synced",
                "attempted": True,
                "memory_scope": "everywhere",
                "pending": False,
            }

    store = MemoryStore(tmp_path / "bridge.sqlite3")
    app = BridgeApplication(
        store,
        port=0,
        cloud_auth=FakeCloudAuth(),
        cloud_sync=FakeCloudSync(),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
    app.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(app.origin) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        status, remembered = _json_request(
            f"{app.origin}/v1/remember",
            cookie=cookie,
            origin=app.origin,
            data={
                "content": "Use FastAPI.",
                "scope": "global",
                "client": "cursor",
                "cwd": str(tmp_path),
            },
        )
        assert status == 201
        assert remembered["cloud_sync"]["memory_scope"] == "everywhere"
        assert calls == ["pull", "push"]

        status, context = _json_request(
            f"{app.origin}/v1/context",
            cookie=cookie,
            origin=app.origin,
            data={"prompt": "Build the API", "client": "codex"},
        )
        assert status == 200
        assert "Use FastAPI." in context["context"]
        assert context["cloud_sync"]["state"] == "current"
        assert calls == ["pull", "push", "pull"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_update_check_returns_only_validated_public_release_state(tmp_path):
    calls = []
    app = BridgeApplication(
        MemoryStore(tmp_path / "bridge.sqlite3"),
        port=0,
        update_checker=lambda: calls.append(True)
        or {
            "status": "available",
            "current_version": "0.5.0",
            "available_version": "0.6.0",
            "release_url": "https://github.com/Lians-ai/Lians/releases/tag/v0.6.0",
            "package_name": "Lians-Setup-0.6.0.exe",
            "download_url": (
                "https://github.com/Lians-ai/Lians/releases/download/v0.6.0/"
                "Lians-Setup-0.6.0.exe"
            ),
            "checksum_url": (
                "https://github.com/Lians-ai/Lians/releases/download/v0.6.0/"
                "Lians-Setup-0.6.0.exe.sha256"
            ),
            "checksum_published": True,
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
    app.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(app.origin) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        assert calls == []
        status, result = _json_request(f"{app.origin}/v1/update", cookie=cookie)
        assert status == 200
        assert result["status"] == "available"
        assert result["checksum_published"] is True
        assert "download_url" not in result
        assert "checksum_url" not in result
        assert calls == [True]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_update_download_and_open_are_separate_confirmed_path_private_actions(tmp_path):
    package = tmp_path / "private" / "Lians-Setup-0.6.0.exe"
    package.parent.mkdir()
    package.write_bytes(b"verified package")
    checks = []
    downloads = []
    opens = []
    release = {
        "status": "available",
        "current_version": "0.5.0",
        "available_version": "0.6.0",
        "release_url": "https://github.com/Lians-ai/Lians/releases/tag/v0.6.0",
        "package_name": package.name,
        "download_url": (
            "https://github.com/Lians-ai/Lians/releases/download/v0.6.0/"
            "Lians-Setup-0.6.0.exe"
        ),
        "checksum_url": (
            "https://github.com/Lians-ai/Lians/releases/download/v0.6.0/"
            "Lians-Setup-0.6.0.exe.sha256"
        ),
        "checksum_published": True,
    }

    def downloader(candidate):
        downloads.append(candidate)
        return {
            "status": "downloaded",
            "available_version": "0.6.0",
            "package_name": package.name,
            "original_package_name": package.name,
            "path": str(package),
            "sha256": "a" * 64,
            "saved_location": "Downloads",
            "trust": "publisher_verified",
            "trust_message": "Checksum and Windows publisher verified.",
            "can_open": True,
        }

    def opener(prepared):
        opens.append(prepared)
        return {
            "status": "opened",
            "package_name": prepared["package_name"],
            "saved_location": "Downloads",
            "trust": "publisher_verified",
            "trust_message": "Checksum and Windows publisher verified.",
            "can_open": True,
        }

    app = BridgeApplication(
        MemoryStore(tmp_path / "bridge.sqlite3"),
        port=0,
        update_checker=lambda: checks.append(True) or release,
        update_downloader=downloader,
        update_opener=opener,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
    app.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(app.origin) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]

        with pytest.raises(HTTPError) as error:
            _json_request(
                f"{app.origin}/v1/update/download",
                cookie=cookie,
                origin=app.origin,
                data={"confirmed": False},
            )
        assert error.value.code == 400
        assert downloads == []

        status, downloaded = _json_request(
            f"{app.origin}/v1/update/download",
            cookie=cookie,
            origin=app.origin,
            data={"confirmed": True},
        )
        assert status == 200
        assert downloaded["status"] == "downloaded"
        assert downloaded["can_open"] is True
        assert downloaded["prepared_id"]
        assert "path" not in downloaded
        assert "private" not in json.dumps(downloaded)
        assert checks == [True]
        assert downloads == [release]
        assert opens == []

        status, opened = _json_request(
            f"{app.origin}/v1/update/open",
            cookie=cookie,
            origin=app.origin,
            data={"confirmed": True, "prepared_id": downloaded["prepared_id"]},
        )
        assert status == 200
        assert opened["status"] == "opened"
        assert "path" not in opened
        assert len(opens) == 1

        with pytest.raises(HTTPError) as replay:
            _json_request(
                f"{app.origin}/v1/update/open",
                cookie=cookie,
                origin=app.origin,
                data={"confirmed": True, "prepared_id": downloaded["prepared_id"]},
            )
        assert replay.value.code == 400
        assert len(opens) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_update_download_failure_is_sanitized_and_never_prepares_an_action(tmp_path):
    def fail(_release):
        raise OSError("C:/Users/private-name/Downloads appeared in a network failure")

    app = BridgeApplication(
        MemoryStore(tmp_path / "bridge.sqlite3"),
        port=0,
        update_checker=lambda: {"status": "available"},
        update_downloader=fail,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
    app.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(app.origin) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        with pytest.raises(HTTPError) as error:
            _json_request(
                f"{app.origin}/v1/update/download",
                cookie=cookie,
                origin=app.origin,
                data={"confirmed": True},
            )
        body = error.value.read().decode()
        assert error.value.code == 400
        assert "private-name" not in body
        assert "Nothing was opened" in body
        assert app.prepared_update is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_update_check_fails_closed_without_exposing_network_errors(tmp_path):
    def unavailable():
        raise OSError("C:/Users/private-name was present in a proxy error")

    app = BridgeApplication(
        MemoryStore(tmp_path / "bridge.sqlite3"),
        port=0,
        update_checker=unavailable,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
    app.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(app.origin) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        status, result = _json_request(f"{app.origin}/v1/update", cookie=cookie)
        assert status == 200
        assert result["status"] == "unavailable"
        assert result["current_version"] == __version__
        assert "private-name" not in json.dumps(result)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_control_center_disconnect_and_erasure_are_separate_confirmed_actions(
    tmp_path, monkeypatch
):
    store = MemoryStore(tmp_path / "bridge.sqlite3")
    store.remember("Keep this until erasure is separately confirmed", scope="global")
    target = ClientTarget(
        key="cursor",
        label="Cursor",
        config_path=tmp_path / ".cursor" / "mcp.json",
        detected=True,
        configured=True,
    )
    disconnected: list[list[str]] = []

    monkeypatch.setattr("lians_easy.bridge.client_targets", lambda: {"cursor": target})

    def fake_uninstall(keys):
        disconnected.append(keys)
        return {
            "status": "uninstalled",
            "clients": [
                {
                    "client": "cursor",
                    "status": "removed",
                    "backup": str(tmp_path / "private-backup-path"),
                }
            ],
        }

    monkeypatch.setattr("lians_easy.bridge.uninstall", fake_uninstall)
    app = BridgeApplication(store, port=0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
    app.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(app.origin) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]

        with pytest.raises(HTTPError) as error:
            _json_request(
                f"{app.origin}/v1/integrations/disconnect",
                cookie=cookie,
                origin=app.origin,
                data={"clients": ["cursor"]},
            )
        assert error.value.code == 400
        assert disconnected == []

        status, result = _json_request(
            f"{app.origin}/v1/integrations/disconnect",
            cookie=cookie,
            origin=app.origin,
            data={"clients": ["cursor"], "confirmed": True},
        )
        assert status == 200
        assert result == {
            "clients": [{"key": "cursor", "label": "Cursor", "status": "removed"}],
            "memory_preserved": True,
            "status": "disconnected",
        }
        assert disconnected == [["cursor"]]
        assert store.stats()["current"] == 1
        assert "private-backup-path" not in json.dumps(result)

        with pytest.raises(HTTPError) as error:
            _json_request(
                f"{app.origin}/v1/privacy/erase",
                cookie=cookie,
                origin=app.origin,
                data={"confirmed": True, "confirmation": "ERASE"},
            )
        assert error.value.code == 400
        assert store.stats()["current"] == 1

        status, erased = _json_request(
            f"{app.origin}/v1/privacy/erase",
            cookie=cookie,
            origin=app.origin,
            data={"confirmed": True, "confirmation": ERASE_ALL_CONFIRMATION},
        )
        assert status == 200
        assert erased["status"] == "erased"
        assert erased["memory_records_erased"] == 1
        assert store.list(state="all") == []
        assert store.activity() == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_control_center_exports_verifies_and_imports_encrypted_portable_memory(tmp_path):
    recovery_calls = []

    class RecoveryCloudSync:
        def recover_from_backup(self, *, confirmed=False):
            assert confirmed is True
            recovery_calls.append("recover")
            return {
                "state": "recovered",
                "local_memory_recovered": True,
                "cloud_memory_started": True,
                "old_cloud_copy_may_remain": True,
                "memory_scope": "everywhere",
                "message": "Recovered safely.",
            }

    store = MemoryStore(tmp_path / "bridge.sqlite3")
    content = "Portable app preference: keep every status update concise."
    remembered = store.remember(content, scope="global", source="Lians App backup test")
    app = BridgeApplication(store, port=0, cloud_sync=RecoveryCloudSync())
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
    app.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    passphrase = "a strong app backup passphrase"
    try:
        with urlopen(app.origin) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]

        export_request = Request(
            f"{app.origin}/v1/backups/export",
            data=json.dumps(
                {"passphrase": passphrase, "confirmation": passphrase}
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                "Origin": app.origin,
            },
            method="POST",
        )
        with urlopen(export_request) as response:
            backup = response.read()
            assert response.status == 200
            assert response.headers["Content-Type"] == "application/vnd.lians.backup+json"
            assert response.headers["Content-Disposition"] == (
                'attachment; filename="Lians-Memory.liansbackup"'
            )
            assert response.headers["Cache-Control"] == "no-store"
        assert content.encode() not in backup

        status, verified = _json_request(
            f"{app.origin}/v1/backups/verify",
            cookie=cookie,
            origin=app.origin,
            data={
                "passphrase": passphrase,
                "backup": base64.b64encode(backup).decode(),
            },
        )
        assert status == 200
        assert verified["status"] == "verified"
        assert verified["memories"] == 1
        assert "path" not in verified

        store.erase_profile(confirmed=True, confirmation=ERASE_ALL_CONFIRMATION)
        assert store.list(state="all") == []
        status, imported = _json_request(
            f"{app.origin}/v1/backups/import",
            cookie=cookie,
            origin=app.origin,
            data={
                "passphrase": passphrase,
                "backup": base64.b64encode(backup).decode(),
                "confirmed": True,
                "recover_cloud": True,
            },
        )
        assert status == 200
        assert imported["status"] == "imported"
        assert imported["imported"]["memories"] == 1
        assert imported["re_encrypted_for_this_device"] is True
        assert imported["cloud_recovery"]["state"] == "recovered"
        assert imported["cloud_recovery"]["old_cloud_copy_may_remain"] is True
        assert recovery_calls == ["recover"]
        assert "path" not in imported
        [restored] = store.list(state="current")
        assert restored["id"] == remembered["id"]
        assert restored["content"] == content
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_loopback_default_serves_the_packaged_control_center(tmp_path):
    app = BridgeApplication(MemoryStore(tmp_path / "bridge.sqlite3"), port=0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
    app.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(app.origin) as response:
            html = response.read().decode("utf-8")
            cookie = response.headers["Set-Cookie"]
            policy = response.headers["Content-Security-Policy"]

        assert "<title>Lians Memory</title>" in html
        assert "Lians Bridge is running" not in html
        assert "HttpOnly" in cookie
        assert "connect-src 'self'" in policy
        assert "object-src 'none'" in policy
        assert "/assets/cloud-controls.js" in html
        assert "/assets/cloud-controls.css" in html

        script_match = re.search(r'src="([^"]+\.js)"', html)
        assert script_match is not None
        with urlopen(f"{app.origin}{script_match.group(1)}") as response:
            script = response.read().decode("utf-8")
            assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "MEMORY CONTROL CENTER" in script
        assert "MOVE MEMORY SAFELY" in script
        assert "/v1/memories?state=all" in script
        assert "/v1/context" in script
        assert "/v1/backups/export" in script
        assert "/v1/backups/verify" in script
        assert "/v1/backups/import" in script
        with urlopen(f"{app.origin}/assets/cloud-controls.js") as response:
            cloud_script = response.read().decode("utf-8")
            assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "/v1/cloud/sign-in" in cloud_script
        assert "/v1/cloud/delete" in cloud_script
        assert "Authorization" not in cloud_script
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
