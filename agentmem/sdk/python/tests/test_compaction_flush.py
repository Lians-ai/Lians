"""
Pre-compaction memory flush.

Long-running agents lose granular facts at the context cliff: the host
framework summarizes old turns and whatever the summary drops is gone. The
harness flushes durable facts into governed memory BEFORE that happens, tagged
``_flush: "pre_compaction"`` so the audit chain shows when the agent
externalized what it knew.
"""
import asyncio

import pytest
from lians import CompactionGuard, LiansMemoryHarness, LocalLiansClient


def _harness(mem, **kw):
    return LiansMemoryHarness(mem, agent_id="flush-desk", source="test-agent", **kw)


MESSAGES = [
    {"role": "user", "content": "should we rebalance the growth book?"},
    {"role": "assistant", "content": "Client approved the Q3 rebalance on 2026-07-01"},
    {"role": "user", "content": "and the tobacco names?"},
    {"role": "assistant", "content": "Compliance: no tobacco exposure in any account"},
]


class TestFlushBeforeCompaction:
    def test_explicit_facts_are_persisted_and_tagged(self):
        with LocalLiansClient() as mem:
            h = _harness(mem)
            result = h.flush_before_compaction(facts=[
                "Client approved the Q3 rebalance",
                "  ",  # blank facts are skipped, not written
                "Compliance: no tobacco exposure",
            ])
            assert result == {"flushed": 2, "mode": "facts"}

            recalled = h.recall("tobacco compliance")
            assert any(
                m.metadata.get("_flush") == "pre_compaction" for m in recalled
            ), "flush writes must carry the pre_compaction audit tag"

    def test_messages_fallback_persists_assistant_turns(self):
        with LocalLiansClient() as mem:
            h = _harness(mem)
            result = h.flush_before_compaction(MESSAGES)
            assert result["mode"] == "messages"
            assert result["flushed"] == 2  # two assistant messages

            recalled = h.recall("Q3 rebalance approval")
            assert any("Q3 rebalance" in (m.content or "") for m in recalled)
            # User turns were not persisted
            all_facts = h.recall("tobacco rebalance growth book", k=20)
            assert not any("should we rebalance" in (m.content or "") for m in all_facts)

    def test_extractor_reproduces_the_silent_agentic_turn(self):
        seen: list[str] = []

        def extract(transcript: str):
            seen.append(transcript)
            return ["Distilled: client wants quarterly rebalancing only"]

        with LocalLiansClient() as mem:
            h = _harness(mem)
            result = h.flush_before_compaction(MESSAGES, extract=extract)
            assert result == {"flushed": 1, "mode": "extract"}
            assert "assistant: Client approved" in seen[0]

            recalled = h.recall("quarterly rebalancing")
            assert any("Distilled" in (m.content or "") for m in recalled)

    def test_requires_facts_or_messages(self):
        with LocalLiansClient() as mem:
            with pytest.raises(ValueError):
                _harness(mem).flush_before_compaction()


class TestCompactionGuard:
    def test_no_flush_under_threshold(self):
        with LocalLiansClient() as mem:
            guard = CompactionGuard(_harness(mem), context_limit_tokens=100_000)
            assert guard.observe_and_maybe_flush(MESSAGES) is None
            assert not guard.should_flush()

    def test_flushes_once_when_crossing_threshold(self):
        with LocalLiansClient() as mem:
            h = _harness(mem)
            # Tiny window: MESSAGES easily exceeds 80% of 40 tokens
            guard = CompactionGuard(h, context_limit_tokens=40)

            first = guard.observe_and_maybe_flush(MESSAGES)
            assert first is not None and first["flushed"] == 2

            # Same window: no double-flush while waiting for the host to compact
            assert guard.observe_and_maybe_flush(MESSAGES) is None

            # After the host compacts, reset opens a fresh window
            guard.reset()
            assert guard.used_tokens == 0
            again = guard.observe_and_maybe_flush(MESSAGES)
            assert again is not None

    def test_observe_accumulates_ad_hoc_text(self):
        with LocalLiansClient() as mem:
            guard = CompactionGuard(_harness(mem), context_limit_tokens=10, threshold=0.5)
            guard.observe("x" * 100)  # ~25 tokens > 5-token trigger
            assert guard.should_flush()

    def test_validates_configuration(self):
        with LocalLiansClient() as mem:
            h = _harness(mem)
            with pytest.raises(ValueError):
                CompactionGuard(h, context_limit_tokens=0)
            with pytest.raises(ValueError):
                CompactionGuard(h, context_limit_tokens=100, threshold=1.5)


class TestLangGraphFlushNode:
    def test_node_flushes_past_threshold_and_not_under(self):
        from lians.langgraph_integration import create_flush_node

        with LocalLiansClient() as mem:
            # Huge limit: nothing to do
            idle = create_flush_node(mem, "flush-desk", context_limit_tokens=1_000_000)
            out = asyncio.run(idle({"messages": MESSAGES}))
            assert out["compaction_flush"] == {"flushed": 0}

            # Tiny limit: assistant turns are persisted
            node = create_flush_node(mem, "flush-desk", context_limit_tokens=40)
            out = asyncio.run(node({"messages": MESSAGES}))
            assert out["compaction_flush"]["flushed"] == 2

            recalled = mem.recall(agent_id="flush-desk", query="tobacco compliance", k=5)
            mems = recalled.get("memories", [])
            assert any(
                (m.get("metadata") or {}).get("_flush") == "pre_compaction" for m in mems
            )
