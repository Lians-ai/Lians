from __future__ import annotations

import base64
import json
import re
import sys
import threading
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
from lians_easy.installer import ClientTarget
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


def test_antigravity_empty_workspace_injects_global_memory_only(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
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
        assert body["version"] == __version__
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
    store = MemoryStore(tmp_path / "bridge.sqlite3")
    content = "Portable app preference: keep every status update concise."
    remembered = store.remember(content, scope="global", source="Lians App backup test")
    app = BridgeApplication(store, port=0)
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
            },
        )
        assert status == 200
        assert imported["status"] == "imported"
        assert imported["imported"]["memories"] == 1
        assert imported["re_encrypted_for_this_device"] is True
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
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
