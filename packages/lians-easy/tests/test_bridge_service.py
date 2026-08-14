from __future__ import annotations

import json
import sys
import threading
from http.server import ThreadingHTTPServer
from io import StringIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from lians_easy.bridge import (
    BridgeApplication,
    context_for_event,
    render_hook_output,
    run_hook,
    write_cursor_rule,
)
from lians_easy.mcp import call_tool
from lians_easy.store import MemoryStore


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


def test_hook_adapter_and_cursor_rule_use_the_same_context(tmp_path):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
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


def test_cursor_remember_creates_the_first_project_rule(tmp_path):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
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


def test_hook_accepts_a_utf8_bom_from_windows_hosts(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
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


def test_gemini_before_agent_hook_injects_bounded_context(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
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


def test_loopback_app_uses_http_only_session_and_blocks_cross_origin_writes(tmp_path):
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
        assert body["memory"]["encrypted"] is True
        assert {item["key"] for item in body["integrations"]} >= {"claude", "cursor", "codex"}

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
