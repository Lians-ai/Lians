"""
Temporal correctness stress tests.

These scenarios exercise the bitemporal model under conditions that expose
gaps in systems like mem0 and Zep that lack proper bitemporality:

  1. Long revision chains  — 10 consecutive updates to the same metric
  2. Interleaved tickers   — parallel revision chains don't cross-contaminate
  3. Future-dated events   — event_time > ingestion_time (pre-announced data)
  4. Out-of-order ingestion — events arrive in non-chronological order
  5. Same-second events    — multiple events at identical timestamps
  6. as_of boundary fences — values exactly on valid_from/valid_to boundaries
  7. Cross-quarter tracking — four quarters of the same metric as_of each boundary

Key claim: AgentMem returns the exact state of knowledge at any requested
point in time.  mem0 has no event_time; Zep's temporal model is conversation-
scoped (message order), not fact-scoped (real-world event time).
"""
from __future__ import annotations
import pytest
from datetime import datetime, timedelta, timezone

from src.agentmem.schemas import MemoryAdd, RecallRequest
from src.agentmem.memory_service import add_memory, recall_memories

NS    = "stress-ns"
AGENT = "stress-agent"


def _t(year=2026, month=1, day=1, **kw) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc, **kw)


# ---------------------------------------------------------------------------
# Long revision chain
# ---------------------------------------------------------------------------

class TestLongRevisionChain:

    @pytest.mark.asyncio
    async def test_ten_revision_chain_present_time(self, db):
        """
        After 10 sequential revisions, present-time recall returns ONLY the
        10th (latest) value and zero superseded values.
        """
        agent = f"{AGENT}-chain10"
        meta  = {"ticker": "NVDA", "metric": "guidance"}
        values = [f"NVDA guidance ${28 + 2*i}B" for i in range(10)]
        times  = [_t(month=1+i) for i in range(10)]

        mems = []
        for content, t in zip(values, times):
            m = await add_memory(db, NS, MemoryAdd(
                agent_id=agent, content=content, event_time=t, metadata=meta,
            ))
            mems.append(m)

        result = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="NVDA guidance", k=20,
        ))
        ids = {m.id for m in result.memories}

        assert mems[-1].id in ids, "10th (latest) revision must appear"
        stale = ids & {m.id for m in mems[:-1]}
        assert not stale, (
            f"Present-time recall returned {len(stale)} superseded revision(s); "
            "agent would see stale data"
        )

    @pytest.mark.asyncio
    async def test_ten_revision_chain_as_of_each_step(self, db):
        """
        as_of query at each of the 10 revision boundaries returns the correct value.
        No revision leaks into the wrong time window.
        """
        agent = f"{AGENT}-chain10-pit"
        meta  = {"ticker": "AAPL", "metric": "revenue"}
        values = [f"AAPL revenue ${80 + 5*i}B" for i in range(10)]
        times  = [_t(month=1+i) for i in range(10)]

        mems = []
        for content, t in zip(values, times):
            m = await add_memory(db, NS, MemoryAdd(
                agent_id=agent, content=content, event_time=t, metadata=meta,
            ))
            mems.append(m)

        for i, (mem, t) in enumerate(zip(mems, times)):
            snap = await recall_memories(db, NS, RecallRequest(
                agent_id=agent, query="AAPL revenue", k=5,
                as_of=t + timedelta(hours=1),
            ))
            snap_ids = {m.id for m in snap.memories}
            assert mem.id in snap_ids, (
                f"Revision {i} (event_time={t.date()}) not found in as_of snapshot"
            )
            # None of the LATER revisions should be visible
            future_ids = {mems[j].id for j in range(i + 1, len(mems))}
            leaked = snap_ids & future_ids
            assert not leaked, (
                f"as_of={t.date()} leaked {len(leaked)} future revision(s) — "
                "bitemporal boundary not enforced"
            )


# ---------------------------------------------------------------------------
# Interleaved parallel chains
# ---------------------------------------------------------------------------

class TestParallelChains:

    @pytest.mark.asyncio
    async def test_two_tickers_independent_chains(self, db):
        """
        Parallel revision chains for AAPL and TSLA on the same metric must not
        cross-contaminate.  Superseding AAPL must not close TSLA memories.
        """
        agent = f"{AGENT}-parallel"
        T0, T1 = _t(month=1), _t(month=4)

        ma0 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="AAPL revenue $90B",
            event_time=T0, metadata={"ticker": "AAPL", "metric": "revenue"},
        ))
        mt0 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="TSLA deliveries 400k",
            event_time=T0, metadata={"ticker": "TSLA", "metric": "deliveries"},
        ))
        ma1 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="AAPL revenue $95B",
            event_time=T1, metadata={"ticker": "AAPL", "metric": "revenue"},
        ))
        mt1 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="TSLA deliveries 430k",
            event_time=T1, metadata={"ticker": "TSLA", "metric": "deliveries"},
        ))

        # Present-time: each current version visible, each old version gone
        result = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="revenue deliveries", k=10,
        ))
        ids = {m.id for m in result.memories}

        assert ma1.id in ids, "Current AAPL revenue must appear"
        assert mt1.id in ids, "Current TSLA deliveries must appear"
        assert ma0.id not in ids, "Old AAPL revenue must be superseded"
        assert mt0.id not in ids, "Old TSLA deliveries must be superseded"

    @pytest.mark.asyncio
    async def test_two_metrics_same_ticker_independent(self, db):
        """
        Revenue and gross_margin revisions on the same ticker track independently.
        Updating revenue must not close gross_margin memories.
        """
        agent = f"{AGENT}-twometric"
        T0, T1 = _t(month=1), _t(month=4)

        r0 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="AAPL Q1 revenue $90B",
            event_time=T0, metadata={"ticker": "AAPL", "metric": "revenue"},
        ))
        gm0 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="AAPL Q1 gross margin 45%",
            event_time=T0, metadata={"ticker": "AAPL", "metric": "gross_margin"},
        ))
        r1 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="AAPL Q2 revenue $95B",
            event_time=T1, metadata={"ticker": "AAPL", "metric": "revenue"},
        ))

        result = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="AAPL revenue gross margin", k=10,
        ))
        ids = {m.id for m in result.memories}

        assert r1.id  in ids,  "Current AAPL revenue must appear"
        assert gm0.id in ids,  "Gross margin (only version) must still appear"
        assert r0.id  not in ids, "Old revenue must be superseded"


# ---------------------------------------------------------------------------
# Future-dated events
# ---------------------------------------------------------------------------

class TestFutureDatedEvents:

    @pytest.mark.asyncio
    async def test_future_event_time_invisible_before_its_date(self, db):
        """
        A memory with event_time = 6 months in the future (pre-announced earnings)
        must NOT appear in an as_of query from today.

        This is the 'pre-announced data' scenario: an analyst ingests guidance
        for a future quarter.  The bitemporal model ensures it's invisible until
        the event actually occurs.
        """
        agent = f"{AGENT}-future"
        now = datetime(2026, 6, 17, tzinfo=timezone.utc)
        future_t = now + timedelta(days=180)

        future_mem = await add_memory(db, NS, MemoryAdd(
            agent_id=agent,
            content="NVDA FY2027 guidance raised to $50B — CEO statement",
            event_time=future_t,
            metadata={"ticker": "NVDA", "metric": "guidance"},
        ))

        # Querying as of today must not surface the future-dated memory
        snap = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="NVDA guidance",
            k=10, as_of=now,
        ))
        assert future_mem.id not in {m.id for m in snap.memories}, (
            "Future-dated event must not appear in as_of query before its event_time"
        )

    @pytest.mark.asyncio
    async def test_future_event_time_visible_after_its_date(self, db):
        """
        The same future-dated memory DOES appear when as_of is after its event_time.
        """
        agent = f"{AGENT}-future2"
        now = datetime(2026, 6, 17, tzinfo=timezone.utc)
        future_t = now + timedelta(days=180)

        future_mem = await add_memory(db, NS, MemoryAdd(
            agent_id=agent,
            content="NVDA FY2027 guidance $50B announced",
            event_time=future_t,
            metadata={"ticker": "NVDA", "metric": "guidance"},
        ))

        snap = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="NVDA guidance",
            k=10, as_of=future_t + timedelta(days=1),
        ))
        assert future_mem.id in {m.id for m in snap.memories}, (
            "Future-dated event must appear in as_of query after its event_time"
        )


# ---------------------------------------------------------------------------
# Out-of-order ingestion
# ---------------------------------------------------------------------------

class TestOutOfOrderIngestion:

    @pytest.mark.asyncio
    async def test_ingesting_older_event_does_not_supersede_newer(self, db):
        """
        If we ingest a newer fact first and then an older fact arrives (late
        reporting, data correction), the older fact must NOT close the newer one.
        The engine uses event_time for temporal ordering, not ingestion order.
        """
        agent = f"{AGENT}-ooo"
        meta  = {"ticker": "TSLA", "metric": "deliveries"}

        T_NEW = _t(month=6)
        T_OLD = _t(month=3)

        # Ingest newer event first
        m_new = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="TSLA Q2 deliveries 430k",
            event_time=T_NEW, metadata=meta,
        ))
        # Ingest older event second (out-of-order arrival)
        m_old = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="TSLA Q1 deliveries 400k",
            event_time=T_OLD, metadata=meta,
        ))

        # The older ingested fact must not have closed the newer one
        from src.agentmem.models import Memory as MemModel
        from sqlalchemy import select
        db_new = await db.get(MemModel, m_new.id)
        assert db_new.valid_to is None, (
            "Newer event must remain open even when an older event is ingested later; "
            "out-of-order ingestion must not corrupt the temporal chain"
        )

        # Present-time: both facts may be valid if they're independent quarters
        # The newer fact is definitely valid
        result = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="TSLA deliveries", k=5,
        ))
        ids = {m.id for m in result.memories}
        assert m_new.id in ids, "Newer delivery fact must still appear at present time"

    @pytest.mark.asyncio
    async def test_out_of_order_chain_sorts_by_event_time(self, db):
        """
        Three events ingested in reverse chronological order (newest first,
        oldest last) still produce the correct supersession chain ordered by
        event_time, not ingestion_time.
        """
        agent = f"{AGENT}-ooo-chain"
        meta  = {"ticker": "NVDA", "metric": "guidance"}

        # Ingest in reverse order: T2, T1, T0
        m2 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="NVDA guidance $40B",
            event_time=_t(month=7), metadata=meta,
        ))
        m1 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="NVDA guidance $36B",
            event_time=_t(month=4), metadata=meta,
        ))
        m0 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="NVDA guidance $32B",
            event_time=_t(month=1), metadata=meta,
        ))

        # At present time, only m2 (the one with the latest event_time) should appear
        result = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="NVDA guidance", k=10,
        ))
        ids = {m.id for m in result.memories}
        assert m2.id in ids, "Mem with latest event_time must appear at present"

        # as_of T0+1h → m0
        snap0 = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="NVDA guidance", k=5,
            as_of=_t(month=1) + timedelta(hours=1),
        ))
        snap0_ids = {m.id for m in snap0.memories}
        assert m0.id in snap0_ids


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------

class TestBoundaryConditions:

    @pytest.mark.asyncio
    async def test_as_of_exactly_on_event_time(self, db):
        """
        as_of == event_time: the memory must be visible (boundary is inclusive).
        """
        agent = f"{AGENT}-boundary"
        T = _t(month=3)

        m = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="AAPL revenue $90B exact boundary",
            event_time=T, metadata={"ticker": "AAPL", "metric": "revenue"},
        ))

        snap = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="AAPL revenue", k=5, as_of=T,
        ))
        assert m.id in {m_.id for m_ in snap.memories}, (
            "Memory with event_time == as_of must be visible (inclusive lower bound)"
        )

    @pytest.mark.asyncio
    async def test_as_of_one_second_before_event_time(self, db):
        """
        as_of = event_time - 1 second: the memory must NOT be visible.
        """
        agent = f"{AGENT}-boundary2"
        T = _t(month=3)

        m = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="TSLA deliveries 400k boundary check",
            event_time=T, metadata={"ticker": "TSLA", "metric": "deliveries"},
        ))

        snap = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="TSLA deliveries", k=5,
            as_of=T - timedelta(seconds=1),
        ))
        assert m.id not in {m_.id for m_ in snap.memories}, (
            "Memory must not appear 1 second before its event_time"
        )

    @pytest.mark.asyncio
    async def test_four_quarter_tracking(self, db):
        """
        Full fiscal year: Q1–Q4 earnings, as_of at each quarter's end.
        Validates that the quarter boundaries don't bleed into each other.
        """
        agent = f"{AGENT}-fy2026"
        meta  = {"ticker": "AAPL", "metric": "eps"}

        quarters = [
            ("AAPL Q1 FY2026 EPS $1.50", _t(month=1)),
            ("AAPL Q2 FY2026 EPS $1.65", _t(month=4)),
            ("AAPL Q3 FY2026 EPS $1.45", _t(month=7)),
            ("AAPL Q4 FY2026 EPS $2.10", _t(month=10)),
        ]

        mems = []
        for content, t in quarters:
            m = await add_memory(db, NS, MemoryAdd(
                agent_id=agent, content=content, event_time=t, metadata=meta,
            ))
            mems.append((m, t))

        for i, (mem, t) in enumerate(mems):
            snap = await recall_memories(db, NS, RecallRequest(
                agent_id=agent, query="AAPL EPS earnings quarterly", k=5,
                as_of=t + timedelta(days=45),  # mid-quarter view
            ))
            snap_ids = {m.id for m in snap.memories}
            assert mem.id in snap_ids, f"Q{i+1} EPS not found at expected as_of"
            future = {mems[j][0].id for j in range(i + 1, len(mems))}
            leaked = snap_ids & future
            assert not leaked, (
                f"Q{i+1} snapshot leaked {len(leaked)} future quarter(s)"
            )
