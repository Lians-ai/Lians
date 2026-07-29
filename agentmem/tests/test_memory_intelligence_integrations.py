import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

from lians import LocalLiansClient
from lians.langgraph_integration import (
    create_memory_feedback_node,
    create_memory_intelligence_node,
)


def test_langgraph_intelligence_and_feedback_nodes_close_the_loop():
    with LocalLiansClient() as client:
        memory = client.add(
            agent_id="graph-agent",
            content="The customer prefers Tuesday meetings.",
            event_time=datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
        )
        prepare = create_memory_intelligence_node(client, "graph-agent")
        prepared = asyncio.run(prepare({"query": "When should we meet the customer?"}))

        assert "Tuesday" in prepared["memory_context"]
        assert prepared["memory_telemetry"]["strategy"] == "adaptive"
        assert prepared["memories"]

        feedback = create_memory_feedback_node(client, "graph-agent")
        result = asyncio.run(feedback({
            "query": "When should we meet the customer?",
            "memories": prepared["memories"],
            "memory_feedback_signal": "helpful",
            "outcome": "meeting_booked",
        }))
        assert result["memory_feedback"]
        assert result["memory_feedback"][0]["memory_id"] == memory["id"]
