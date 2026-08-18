from __future__ import annotations

import pytest
from lians_easy.mcp import MCPServer
from lians_easy.project import Project
from lians_easy.store import MemoryStore


def _call(server, request_id, name, arguments):
    return server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )["result"]


def test_named_current_state_preserves_bitemporal_history(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    project_id = "project-checkout"
    first = store.set_current(
        "architecture/database",
        "Use SQLite for the local prototype.",
        project_id=project_id,
        source_client="cursor",
        event_time="2026-08-01T09:00:00-04:00",
    )
    second = store.set_current(
        "architecture/database",
        "Use PostgreSQL for the shared production service.",
        project_id=project_id,
        source_client="codex",
        event_time="2026-08-10T15:00:00-04:00",
        reason="production concurrency requirement",
    )

    history = store.memory_history("architecture/database", project_id=project_id)
    assert [item["id"] for item in history] == [first["id"], second["id"]]
    assert [item["version"] for item in history] == [1, 2]
    assert history[0]["valid_to"] == "2026-08-10T19:00:00+00:00"
    assert history[0]["recorded_to"] == second["recorded_at"]
    assert history[1]["supersession_reason"] == "production concurrency requirement"
    assert history[1]["is_current"] is True

    before_change = store.memory_at(
        "architecture/database",
        project_id=project_id,
        valid_at="2026-08-05T12:00:00Z",
        known_at=second["recorded_at"],
    )
    after_change = store.memory_at(
        "architecture/database",
        project_id=project_id,
        valid_at="2026-08-12T12:00:00Z",
        known_at=second["recorded_at"],
    )
    before_correction_was_known = store.memory_at(
        "architecture/database",
        project_id=project_id,
        valid_at="2026-08-12T12:00:00Z",
        known_at=first["recorded_at"],
    )

    assert before_change["id"] == first["id"]
    assert after_change["id"] == second["id"]
    assert before_correction_was_known["id"] == first["id"]


def test_named_state_rejects_stale_update_and_confirms_idempotently(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    current = store.set_current(
        "launch/status",
        "Soft launch is active.",
        scope="global",
        event_time="2026-08-17T12:00:00Z",
    )
    confirmed = store.set_current(
        "launch/status",
        "Soft launch is active.",
        scope="global",
        event_time="2026-08-17T12:00:00Z",
    )

    assert confirmed["id"] == current["id"]
    with pytest.raises(ValueError, match="older than the current state"):
        store.set_current(
            "launch/status",
            "Launch has not started.",
            scope="global",
            event_time="2026-08-16T12:00:00Z",
        )
    assert store.memory_history("launch/status", scope="global") == [
        {**confirmed, "version": 1, "is_current": True}
    ]
    assert {item["event"] for item in store.activity()} >= {
        "current_state_created",
        "current_state_confirmed",
    }


def test_current_decision_crosses_agents_with_signed_lineage_receipt(tmp_path):
    database = tmp_path / "shared.sqlite3"
    project = Project(
        id="project-lians",
        name="Lians",
        root=str(tmp_path),
        origin="github.com/lians-ai/lians",
    )
    cursor = MemoryStore(database)
    first = cursor.set_current(
        "product/positioning",
        "Lead with token reduction.",
        project_id=project.id,
        source_client="cursor",
        event_time="2026-08-01T12:00:00Z",
    )
    current = cursor.set_current(
        "product/positioning",
        "Lead with agent continuity, correctness, and control.",
        project_id=project.id,
        source_client="claude",
        event_time="2026-08-17T12:00:00Z",
        reason="broader user outcome",
    )

    codex = MemoryStore(database)
    pack = codex.context_pack(
        "How should we position the product?",
        project=project,
        client="codex",
    )

    assert current["content"] in pack["context"]
    assert first["content"] not in pack["context"]
    [receipt_memory] = pack["receipt"]["memories"]
    assert receipt_memory["memory_key"] == "product/positioning"
    assert receipt_memory["supersedes_id"] == first["id"]
    assert receipt_memory["supersession_reason"] == "broader user outcome"
    assert "Current decision product/positioning" in receipt_memory["reason"]


def test_mcp_exposes_current_state_history_and_time_travel(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    server = MCPServer(MemoryStore(tmp_path / "memory.sqlite3"))

    first = _call(
        server,
        1,
        "set_current",
        {
            "memory_key": "research/sample-size",
            "content": "Analyze 1,000 posts.",
            "event_time": "2026-08-01T12:00:00Z",
        },
    )["structuredContent"]
    second = _call(
        server,
        2,
        "set_current",
        {
            "memory_key": "research/sample-size",
            "content": "Analyze 10,000 posts.",
            "event_time": "2026-08-10T12:00:00Z",
            "reason": "expanded launch analysis",
        },
    )["structuredContent"]
    history = _call(
        server,
        3,
        "memory_history",
        {"memory_key": "research/sample-size"},
    )["structuredContent"]
    point_in_time = _call(
        server,
        4,
        "memory_at",
        {
            "memory_key": "research/sample-size",
            "valid_at": "2026-08-05T12:00:00Z",
            "known_at": second["recorded_at"],
        },
    )["structuredContent"]["memory"]

    assert [item["id"] for item in history["versions"]] == [first["id"], second["id"]]
    assert point_in_time["id"] == first["id"]
