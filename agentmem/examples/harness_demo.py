"""
Agent memory harness demo — a complete, runnable memory-augmented agent loop.

Run it::

    pip install lians-sdk[local]
    python examples/harness_demo.py

This uses LocalLiansClient (in-memory SQLite, zero setup, no API key) and a
trivial rule-based "model" so the example runs offline. Swap ``fake_model`` for
a real LLM call (Claude, GPT, etc.) and ``LocalLiansClient`` for ``LiansClient``
pointed at your server to take it to production unchanged.

What it demonstrates
--------------------
1. recall-before / remember-after on every turn (``run_turn``)
2. supersession: a revised guidance figure replaces the stale one in context
3. point-in-time recall: "what did we know in September?"
4. backtest-contamination check: proof the agent held no future knowledge
"""
from datetime import datetime, timezone

from lians import LocalLiansClient, LiansMemoryHarness


def fake_model(context: str, query: str) -> str:
    """Stand-in for a real LLM. Echoes the recalled context to show injection."""
    print("\n--- context injected into the model ---")
    print(context)
    print("--- end context ---")
    # A real agent would reason here; we just acknowledge the turn.
    if "guidance" in query.lower():
        return "Acknowledged the latest NVDA guidance; updating the desk note."
    return "Noted."


def main() -> None:
    with LocalLiansClient() as mem:
        harness = LiansMemoryHarness(
            mem,
            agent_id="research-desk",
            source="analyst-agent",
            domain="finance",
        )

        # Seed two facts about the same metric at different business times.
        harness.remember(
            "NVDA FY2026 revenue guidance is $36B",
            event_time=datetime(2025, 8, 1, tzinfo=timezone.utc),
            metadata={"ticker": "NVDA", "metric": "revenue_guidance"},
        )
        harness.remember(
            "NVDA FY2026 revenue guidance raised to $40B",
            event_time=datetime(2025, 11, 19, tzinfo=timezone.utc),
            metadata={"ticker": "NVDA", "metric": "revenue_guidance"},
        )

        # A full harnessed turn: recall → model → remember.
        print("\n========== TURN 1 ==========")
        answer = harness.run_turn(
            "What is NVDA's current revenue guidance?",
            generate=fake_model,
        )
        print(f"\nmodel -> {answer}")

        # Present recall shows only the current ($40B) figure — the $36B fact was
        # superseded and is excluded at the database layer.
        print("\n========== PRESENT RECALL ==========")
        for m in harness.recall("NVDA revenue guidance"):
            print(f"  now: {m.content}")

        # Point-in-time recall reconstructs what was true on Sept 1 ($36B).
        print("\n========== POINT-IN-TIME (Sept 1, 2025) ==========")
        for m in harness.recall(
            "NVDA revenue guidance",
            as_of=datetime(2025, 9, 1, tzinfo=timezone.utc),
        ):
            print(f"  then: {m.content}")

        # Backtest-contamination check: if we simulate as of July 2025, both the
        # August and November facts are "future knowledge" and get flagged.
        print("\n========== BACKTEST CHECK (sim as of July 1, 2025) ==========")
        report = harness.backtest_check(
            simulation_as_of=datetime(2025, 7, 1, tzinfo=timezone.utc)
        )
        print(f"  is_clean: {report['is_clean']}  flags: {len(report['flags'])}")
        for f in report["flags"]:
            print(f"    [!] {f['contamination_type']}: {f['content_preview']}")


if __name__ == "__main__":
    main()
