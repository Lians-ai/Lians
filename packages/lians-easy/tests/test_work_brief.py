from __future__ import annotations

import json

import pytest
from lians_easy import cli
from lians_easy.work_brief import (
    WorkBriefError,
    compile_browser_brief,
    compile_research_brief,
    compile_work_brief_file,
)


def test_research_brief_deduplicates_and_keeps_ranked_evidence() -> None:
    records = [
        {
            "id": "one",
            "text": "Cursor repeatedly loses my project rules.",
            "topic": "context",
            "sentiment": "negative",
            "tool": "Cursor",
            "engagement": 4,
        },
        {
            "id": "duplicate",
            "text": "  CURSOR repeatedly loses my project rules. ",
            "topic": "context",
            "sentiment": "negative",
            "tool": "Cursor",
            "engagement": 100,
        },
        {
            "id": "two",
            "text": "Claude remembered the decision.",
            "topic": "memory",
            "sentiment": "positive",
            "tool": "Claude",
            "engagement": 10,
        },
    ]

    brief = compile_research_brief(records, evidence_limit=2)

    assert brief["summary"] == {
        "records_received": 3,
        "unique_records": 2,
        "duplicates_removed": 1,
        "topic_counts": {"context": 1, "memory": 1},
        "sentiment_counts": {"negative": 1, "positive": 1},
        "integration_counts": {"Claude": 1, "Cursor": 1},
    }
    assert [item["source_id"] for item in brief["representative_evidence"]] == [
        "two",
        "one",
    ]
    assert brief["guardrails"]["raw_records_stay_local"] is True
    assert brief["receipt"]["raw_record_count"] == 3


def test_browser_brief_keeps_latest_state_and_respects_action_guards() -> None:
    records = [
        {"surface_id": "a", "state": "candidate", "priority": 10},
        {"surface_id": "b", "state": "candidate", "priority": 9},
        {"surface_id": "c", "state": "candidate", "priority": 8},
        {"surface_id": "a", "state": "published", "priority": 10},
        {
            "surface_id": "b",
            "state": "candidate",
            "priority": 9,
            "approval_required": True,
        },
        {"surface_id": "d", "state": "ready", "priority": 7},
    ]

    brief = compile_browser_brief(records)

    assert brief["summary"]["events_received"] == 6
    assert brief["summary"]["surfaces_tracked"] == 4
    assert brief["summary"]["history_events_collapsed"] == 2
    assert brief["summary"]["latest_state_counts"] == {
        "candidate": 2,
        "published": 1,
        "ready": 1,
    }
    assert brief["summary"]["next_eligible_surfaces"] == ["c", "d"]


def test_brief_refuses_credential_like_records() -> None:
    with pytest.raises(WorkBriefError, match="credential-like"):
        compile_research_brief([{"text": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"}])


def test_json_lines_file_compiles_without_provider_call(tmp_path) -> None:
    source = tmp_path / "posts.jsonl"
    source.write_text(
        "\n".join(
            (
                json.dumps({"id": "1", "text": "First post"}),
                json.dumps({"id": "2", "text": "Second post"}),
            )
        ),
        encoding="utf-8",
    )

    brief = compile_work_brief_file("research", source)

    assert brief["summary"]["records_received"] == 2
    assert brief["summary"]["unique_records"] == 2


def test_brief_cli_writes_ai_ready_json(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "events.json"
    output = tmp_path / "brief.json"
    source.write_text(
        json.dumps([{"url": "https://example.test", "status": "candidate"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)

    cli.main(["brief", "browser", str(source), "--output", str(output)])

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["kind"] == "browser"
    assert written["summary"]["next_eligible_surfaces"] == ["https://example.test"]
    assert "Raw records were not sent" in capsys.readouterr().out
