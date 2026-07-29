import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

from lians import LiansMemoryHarness, LocalLiansClient


def test_helpful_feedback_is_persistent_and_promotes_importance():
    with LocalLiansClient() as client:
        harness = LiansMemoryHarness(client, agent_id="feedback-agent")
        harness.remember("The customer wants weekly summaries.", importance=0.5)
        prepared = harness.prepare("What reporting cadence does the customer want?")

        records = harness.feedback(prepared, "helpful", outcome="accepted")

        assert records
        assert records[0]["policy_action"] == "importance_promoted"
        assert records[0]["memory_importance"] == min(
            1.0, prepared.memories[0].importance + 0.05,
        )
        summary = client.learning_summary(agent_id="feedback-agent")
        assert summary["total_feedback"] == len(records)
        assert summary["helpful_rate"] == 1.0


def test_incorrect_feedback_flags_review_without_deleting_memory():
    with LocalLiansClient() as client:
        memory = client.add(
            agent_id="feedback-agent",
            content="The launch date is Monday.",
            event_time=datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
        )

        record = client.feedback(
            memory["id"],
            agent_id="feedback-agent",
            signal="incorrect",
            note="Customer corrected the date.",
        )

        assert record["policy_action"] == "flagged_for_review"
        recalled = client.recall(
            agent_id="feedback-agent", query="When is the launch date?"
        )
        assert recalled["memories"]  # feedback never silently erases evidence
        assert recalled["memories"][0]["metadata"]["_learning_review"]["status"] == "pending"
        summary = client.learning_summary(agent_id="feedback-agent")
        assert summary["incorrect"] == 1
        assert summary["memories_pending_review"] == 1

        resolution = client.resolve_memory_review(
            memory["id"],
            agent_id="feedback-agent",
            action="retire",
            reviewer="ops@example.com",
            note="Confirmed against the project plan.",
        )
        assert resolution["status"] == "retired"
        after = client.recall(
            agent_id="feedback-agent", query="When is the launch date?"
        )
        assert not after["memories"]
        summary = client.learning_summary(agent_id="feedback-agent")
        assert summary["memories_pending_review"] == 0


def test_review_can_keep_flagged_memory():
    with LocalLiansClient() as client:
        memory = client.add(
            agent_id="feedback-agent",
            content="The support tier is enterprise.",
            event_time=datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
        )
        client.feedback(
            memory["id"], agent_id="feedback-agent", signal="outdated",
        )

        resolution = client.resolve_memory_review(
            memory["id"],
            agent_id="feedback-agent",
            action="keep",
            reviewer="owner@example.com",
        )

        assert resolution["status"] == "kept"
        after = client.recall(
            agent_id="feedback-agent", query="What is the support tier?"
        )
        assert after["memories"]
        assert after["memories"][0]["metadata"]["_learning_review"]["status"] == "kept"
