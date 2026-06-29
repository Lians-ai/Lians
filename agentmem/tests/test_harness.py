"""
Tests for the Lians agent memory harness.

Covers the recall-before / remember-after loop, compliance scoping, point-in-time
recall, and the combined turn helpers — exercised against the real
LocalLiansClient (in-memory SQLite) so the full service path runs.
"""
import sys
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Make the SDK importable from the test runner's working directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

from lians import LocalLiansClient, LiansMemoryHarness, RecalledMemory, TurnResult
from lians.harness import MemoryClient


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


class TestConstruction:
    def test_requires_agent_id(self):
        with LocalLiansClient() as mem:
            with pytest.raises(ValueError):
                LiansMemoryHarness(mem, agent_id="")

    def test_rejects_client_without_surface(self):
        class Bad:
            pass
        with pytest.raises(TypeError):
            LiansMemoryHarness(Bad(), agent_id="a")

    def test_local_client_satisfies_protocol(self):
        with LocalLiansClient() as mem:
            assert isinstance(mem, MemoryClient)


class TestRecall:
    def test_recall_returns_normalized_memories(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            h.remember("NVDA FY26 revenue guidance is $40B", event_time=_dt(2025, 11, 19))
            out = h.recall("NVDA revenue guidance")
            assert out and isinstance(out[0], RecalledMemory)
            assert "NVDA" in out[0].content

    def test_recall_context_renders_block(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            h.remember("AAPL Q1 EPS was $1.52", event_time=_dt(2026, 1, 28))
            ctx = h.recall_context("Apple earnings")
            assert "AAPL Q1 EPS" in ctx
            assert ctx.startswith("Relevant facts")

    def test_recall_context_empty(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            ctx = h.recall_context("nothing stored")
            assert "no relevant facts" in ctx

    def test_point_in_time_recall(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            h.remember("NVDA guidance $36B", event_time=_dt(2025, 8, 1),
                       metadata={"ticker": "NVDA", "metric": "guidance"})
            h.remember("NVDA guidance raised to $40B", event_time=_dt(2025, 11, 19),
                       metadata={"ticker": "NVDA", "metric": "guidance"})
            # Present recall: only the current (superseding) fact
            now = h.recall("NVDA guidance")
            assert any("$40B" in m.content for m in now)
            # As of September: the old figure was current then
            past = h.recall("NVDA guidance", as_of=_dt(2025, 9, 1))
            assert any("$36B" in m.content for m in past)


class TestRemember:
    def test_remember_applies_scoping(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(
                mem, agent_id="care", subject_id="MRN-1",
                barrier_group="oncology", domain="healthcare", source="ehr",
            )
            out = h.remember("Patient started metformin 500mg", event_time=_dt(2026, 3, 1))
            assert out["subject_id"] == "MRN-1"
            assert out["source"] == "ehr"
            assert out["metadata"]["_barrier"] == "oncology"
            assert out["metadata"]["_domain"] == "healthcare"

    def test_remember_messages(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            res = h.remember_messages([
                {"role": "user", "content": "What about TSLA?"},
                {"role": "assistant", "content": "TSLA deliveries hit 495k in Q4"},
            ])
            assert res["added"] >= 1
            out = h.recall("TSLA deliveries")
            assert any("495k" in m.content for m in out)


class TestTurn:
    def test_run_turn_recalls_and_remembers(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            h.remember("MSFT FY25 cloud revenue $110B", event_time=_dt(2025, 7, 1))

            seen = {}

            def generate(context, query):
                seen["context"] = context
                return "Recorded: MSFT cloud revenue is on track."

            answer = h.run_turn("How is MSFT cloud doing?", generate)
            assert "MSFT FY25 cloud revenue" in seen["context"]
            assert answer.startswith("Recorded")
            # The response was persisted and is now recallable
            out = h.recall("MSFT cloud on track")
            assert any("on track" in m.content for m in out)

    def test_turn_returns_full_result(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            res = h.turn("hello?", lambda ctx, q: "world")
            assert isinstance(res, TurnResult)
            assert res.response == "world"
            assert res.remembered is not None

    def test_turn_can_skip_remember(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            res = h.turn("q", lambda ctx, q: "answer", remember_response=False)
            assert res.remembered is None

    def test_turn_skips_empty_response(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            res = h.turn("q", lambda ctx, q: "   ")
            assert res.remembered is None


class TestCompliancePassthroughs:
    def test_backtest_check(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            h.remember("Future leak: earnings beat", event_time=_dt(2026, 6, 1))
            report = h.backtest_check(simulation_as_of=_dt(2026, 1, 1))
            assert len(report["flags"]) >= 1   # the June fact is unknowable on Jan 1
            assert report["is_clean"] is False

    def test_snapshot(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            h.remember("Fact A", event_time=_dt(2026, 1, 1))
            snap = h.snapshot(as_of=_dt(2026, 2, 1))
            assert snap["total"] == 1
            assert snap["items"][0]["content"] == "Fact A"

    def test_erase_uses_default_subject(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk", subject_id="user-9")
            h.remember("PII fact", event_time=_dt(2026, 1, 1))
            res = h.erase(request_ref="GDPR-1")
            assert res["subject_id"] == "user-9"
            assert res["memories_erased"] >= 1

    def test_erase_requires_subject(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="desk")
            with pytest.raises(ValueError):
                h.erase(request_ref="GDPR-1")
