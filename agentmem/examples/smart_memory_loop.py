"""Presentation-ready Lians memory intelligence loop.

Run from the agentmem directory:
    python examples/smart_memory_loop.py
"""
from __future__ import annotations

from datetime import datetime, timezone

from lians import LiansMemoryHarness, LocalLiansClient


def main() -> None:
    with LocalLiansClient(embedding_provider="local") as client:
        memory = LiansMemoryHarness(
            client,
            agent_id="customer-success-copilot",
            source="demo",
        )

        # Information learned in earlier tools, conversations, or workflows.
        memory.remember(
            "Northstar's renewal is October 15 and procurement needs the "
            "security questionnaire two weeks beforehand.",
            event_time=datetime.now(timezone.utc),
            metadata={"account": "Northstar", "type": "renewal"},
            importance=0.9,
        )
        memory.remember(
            "Northstar prefers concise emails and Tuesday morning meetings.",
            event_time=datetime.now(timezone.utc),
            metadata={"account": "Northstar", "type": "preference"},
            importance=0.8,
        )

        result = memory.smart_turn(
            "How should I prepare for Northstar's renewal?",
            generate=lambda context, query: (
                "Send a concise security-questionnaire email by October 1, "
                "then propose a Tuesday-morning review."
                if "security questionnaire" in context
                else "I need more account context."
            ),
            learned_memories=lambda response: [
                "The Northstar renewal plan is to send the security "
                "questionnaire by October 1 and schedule a Tuesday review."
            ],
            outcome="plan_created",
        )

        print(result.response)
        print()
        print("Memory intelligence telemetry")
        print(f"  strategy: {result.prepared.strategy}")
        print(f"  retrieval confidence: {result.prepared.retrieval_confidence:.2f}")
        print(f"  context tokens: {result.prepared.token_estimate}")
        print(f"  recall latency: {result.prepared.latency_ms:.1f} ms")
        print(f"  durable learnings stored: {len(result.learned)}")


if __name__ == "__main__":
    main()
