from __future__ import annotations

import json

from lians_easy.bridge import context_for_event
from lians_easy.project import detect_project
from lians_easy.session_capture import (
    capture_claude_session_end,
    extract_continuity,
    read_claude_transcript,
)
from lians_easy.store import MemoryStore


def _transcript(path, *, cwd) -> None:
    rows = [
        {
            "type": "user",
            "timestamp": "2026-08-18T12:00:00+00:00",
            "cwd": str(cwd),
            "message": {
                "role": "user",
                "content": "Migrate the orders API, keep pytest, and leave docs for the next agent.",
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-08-18T12:05:00+00:00",
            "cwd": str(cwd),
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": str(cwd / "api.py")},
                    },
                    {
                        "type": "text",
                        "text": """# Completed
- Migrated the orders API implementation
- Updated the pytest coverage

# Still open
- Update the API documentation

# Decisions
- Keep pytest instead of replacing it with unittest

# Constraints
- Preserve backward-compatible response fields

# Changed
- Moved the orders endpoint from /v1/orders to /v2/orders.

# Do not redo
- Do not redo the API migration

# Next action
- Update the API documentation
""",
                    },
                ],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_session_end_capture_creates_bounded_cross_agent_continuity(tmp_path) -> None:
    root = tmp_path / "orders"
    root.mkdir()
    (root / ".git").mkdir()
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, cwd=root)
    store = MemoryStore(tmp_path / "lians.sqlite3")

    result = capture_claude_session_end(
        {
            "hook_event_name": "SessionEnd",
            "session_id": "claude-session-1",
            "transcript_path": str(transcript),
            "cwd": str(root),
            "reason": "exit",
        },
        store=store,
    )

    assert result["status"] == "captured"
    assert result["transcript_retained"] is False
    assert result["task"]["assessment"]["status"] == "active"
    state = result["task"]["state"]
    assert len(state["evidence"]) == 2
    assert state["current_action"] == "Update the API documentation"
    assert any("pytest" in item["decision"] for item in state["decisions"])
    assert any("superseded and stale" in item["decision"] for item in state["decisions"])

    pack = context_for_event(
        {"prompt": "Pick up where Claude left off.", "cwd": str(root)},
        client="codex",
        store=store,
        max_tokens=900,
    )
    context = pack["context"]
    assert "Migrated the orders API implementation" in context
    assert "Update the API documentation" in context
    assert "Keep pytest" in context
    assert "/v2/orders" in context
    assert "superseded and stale" in context
    assert "transcript_retained" not in context
    assert len(context) <= 900 * 4


def test_session_end_capture_is_idempotent_and_project_scoped(tmp_path) -> None:
    root = tmp_path / "orders"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    (root / ".git").mkdir()
    (other / ".git").mkdir()
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, cwd=root)
    store = MemoryStore(tmp_path / "lians.sqlite3")
    event = {
        "hook_event_name": "SessionEnd",
        "session_id": "claude-session-2",
        "transcript_path": str(transcript),
        "cwd": str(root),
    }

    first = capture_claude_session_end(event, store=store)
    second = capture_claude_session_end(event, store=store)

    assert first["status"] == "captured"
    assert second["status"] == "already_captured"
    other_pack = context_for_event(
        {"prompt": "What did Claude finish?", "cwd": str(other)},
        client="codex",
        store=store,
    )
    assert "Migrated the orders API implementation" not in other_pack["context"]
    assert first["project_id"] == detect_project(root).id


def test_transcript_is_evidence_not_persisted_memory(tmp_path) -> None:
    root = tmp_path / "orders"
    root.mkdir()
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, cwd=root)

    bounded = read_claude_transcript(transcript)
    extracted = extract_continuity(bounded)

    assert extracted["completed"] == [
        "Migrated the orders API implementation",
        "Updated the pytest coverage",
    ]
    assert extracted["unfinished"] == ["Update the API documentation"]
    assert extracted["changes"][0]["previous"] == "/v1/orders"
    assert extracted["changes"][0]["current"] == "/v2/orders"
