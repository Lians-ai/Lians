from __future__ import annotations

from datetime import datetime, timezone

from lians_easy.lifeline import format_activity_time, format_count, lifeline_snapshot


def test_lifeline_snapshot_summarizes_local_receipts() -> None:
    class FakeStore:
        def stats(self):
            return {
                "current": 8,
                "efficiency": {
                    "context_events": 4,
                    "memories_reused": 11,
                    "context_tokens_sent_estimate": 900,
                    "available_memory_tokens_estimate": 5000,
                    "repeated_memory_tokens_avoided_estimate": 4100,
                    "clients_used": 2,
                },
            }

        def receipts(self, *, limit):
            assert limit == 3
            return [
                {
                    "created_at": "2026-08-16T14:30:00+00:00",
                    "client": "codex",
                    "project": {"name": "Market research"},
                    "memory_count": 3,
                    "token_estimate": 180,
                    "efficiency": {
                        "repeated_memory_tokens_avoided_estimate": 820,
                    },
                }
            ]

    snapshot = lifeline_snapshot(FakeStore(), limit=3)

    assert snapshot["saved_memories"] == 8
    assert snapshot["context_events"] == 4
    assert snapshot["memories_reused"] == 11
    assert snapshot["repeated_tokens_avoided_estimate"] == 4100
    assert snapshot["reduction_percent_estimate"] == 82
    assert snapshot["activity"][0]["title"] == "Codex · Market research"
    assert snapshot["activity"][0]["detail"] == (
        "3 memories reused · about 820 repeated tokens avoided · "
        "about 180 context tokens sent"
    )


def test_lifeline_snapshot_handles_empty_or_malformed_counters() -> None:
    class FakeStore:
        def stats(self):
            return {"current": None, "efficiency": {"context_events": "bad"}}

        def receipts(self, *, limit):
            return []

    snapshot = lifeline_snapshot(FakeStore())

    assert snapshot["saved_memories"] == 0
    assert snapshot["context_events"] == 0
    assert snapshot["reduction_percent_estimate"] == 0
    assert snapshot["activity"] == []
    assert format_count(-20) == "0"


def test_activity_time_uses_readable_relative_days() -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)  # noqa: UP017

    assert format_activity_time("2026-08-16T10:30:00", now=now) == "Today at 10:30 AM"
    assert format_activity_time("2026-08-15T20:00:00", now=now) == "Yesterday at 8:00 PM"
    assert format_activity_time("not-a-time", now=now) == "Recent"
