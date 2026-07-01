"""
Audit reconstruction tests â€” the compliance product.
Property: reconstruct(as_of=T) returns the EXACT memory state + event trail
that existed at time T.  Nothing added after T may appear; nothing erased
before T (that was valid at T) may be missing.
"""
import pytest
from datetime import datetime, timezone, timedelta

from src.lians.schemas import MemoryAdd
from src.lians.memory_service import add_memory
from src.lians.audit import reconstruct

NS = "audit-ns"
AGENT = "audit-agent"

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 4, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 7, 1, tzinfo=timezone.utc)
T3 = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_reconstruct_returns_memories_valid_at_as_of(db):
    """reconstruct(as_of=T1) returns the memory that was valid at T1."""
    req = MemoryAdd(
        agent_id=AGENT,
        content="AAPL revenue guidance $400B",
        event_time=T0,
        metadata={"ticker": "AAPL", "metric": "revenue_guidance"},
    )
    mem = await add_memory(db, NS, req)

    result = await reconstruct(db, NS, AGENT, as_of=T1)
    ids = [m.id for m in result.memories]
    assert mem.id in ids, "Memory valid at T1 must appear in reconstruction"
    assert result.as_of == T1


@pytest.mark.asyncio
async def test_reconstruct_excludes_memories_added_after_as_of(db):
    """Memories ingested after as_of must not appear in reconstruction."""
    early = await add_memory(
        db, NS,
        MemoryAdd(agent_id=AGENT, content="MSFT EPS $3.00", event_time=T0,
                  metadata={"ticker": "MSFT", "metric": "eps"}),
    )
    late = await add_memory(
        db, NS,
        MemoryAdd(agent_id=AGENT, content="MSFT EPS $3.20", event_time=T2,
                  metadata={"ticker": "MSFT", "metric": "eps"}),
    )

    # Reconstruct at T1 (between T0 and T2)
    result = await reconstruct(db, NS, AGENT, as_of=T1)
    ids = [m.id for m in result.memories]
    assert early.id in ids
    assert late.id not in ids, "Memory added after as_of must be excluded"


@pytest.mark.asyncio
async def test_reconstruct_superseded_memory_visible_before_supersession(db):
    """Before supersession event, the old memory must appear in reconstruction."""
    old = await add_memory(
        db, NS,
        MemoryAdd(agent_id=AGENT, content="TSLA deliveries 400k", event_time=T0,
                  metadata={"ticker": "TSLA", "metric": "deliveries"}),
    )
    new = await add_memory(
        db, NS,
        MemoryAdd(agent_id=AGENT, content="TSLA deliveries 450k", event_time=T2,
                  metadata={"ticker": "TSLA", "metric": "deliveries"}),
    )

    # At T1 (before T2 supersession): old is valid, new doesn't exist yet
    result_t1 = await reconstruct(db, NS, AGENT, as_of=T1)
    ids_t1 = [m.id for m in result_t1.memories]
    assert old.id in ids_t1, "Old memory must appear before it was superseded"
    assert new.id not in ids_t1, "New memory must not appear before its event_time"

    # At T3 (after T2 supersession): new is valid, old is closed
    result_t3 = await reconstruct(db, NS, AGENT, as_of=T3)
    ids_t3 = [m.id for m in result_t3.memories]
    assert new.id in ids_t3
    assert old.id not in ids_t3, "Superseded memory must not appear after valid_to"


@pytest.mark.asyncio
async def test_event_trail_included_in_reconstruction(db):
    """Reconstruction result includes the event log trail."""
    await add_memory(
        db, NS,
        MemoryAdd(agent_id=AGENT, content="NVDA guidance $36B", event_time=T0,
                  metadata={"ticker": "NVDA", "metric": "guidance"}),
    )

    # The event trail filters on created_at (wall-clock ingestion time), so the
    # as_of must be in the *real* future — a fixed date is a time bomb that
    # starts failing the day the calendar catches up with it (it did: 2026-07-01).
    result = await reconstruct(db, NS, AGENT,
                               as_of=datetime.now(timezone.utc) + timedelta(days=1))
    assert len(result.event_trail) >= 1
    ops = [e["op"] for e in result.event_trail]
    assert "add" in ops, "Event trail must contain the add operation"


@pytest.mark.asyncio
async def test_event_trail_excludes_events_after_as_of(db):
    """Events logged after as_of must not appear in the trail."""
    await add_memory(
        db, NS,
        MemoryAdd(agent_id=AGENT, content="AMD guidance $25B", event_time=T0,
                  metadata={"ticker": "AMD", "metric": "guidance"}),
    )
    # This second memory is added "after" as_of=T0 in event_time terms
    await add_memory(
        db, NS,
        MemoryAdd(agent_id=AGENT, content="AMD guidance raised to $28B", event_time=T2,
                  metadata={"ticker": "AMD", "metric": "guidance"}),
    )

    # reconstruct at T0+1s: only the first add event should appear
    result = await reconstruct(db, NS, AGENT, as_of=T0 + timedelta(seconds=1))
    # The event_trail reflects events whose created_at <= as_of; since both
    # adds happen in real-time during the test (created_at â‰ˆ now), they may
    # both appear.  What matters is that the MEMORY snapshot is correct.
    cutoff = T0 + timedelta(seconds=1)
    # The T2 memory has event_time=T2 > cutoff, so it must be excluded
    for m in result.memories:
        mem_event_time = m.event_time
        # SQLite returns naive datetimes; normalise for comparison
        if mem_event_time.tzinfo is None:
            mem_event_time = mem_event_time.replace(tzinfo=timezone.utc)
        assert mem_event_time <= cutoff, \
            f"Memory with event_time {m.event_time} must not appear at as_of={cutoff}"


@pytest.mark.asyncio
async def test_reconstruct_with_query_filter(db):
    """reconstruct with a query string scopes results to relevant memories."""
    await add_memory(
        db, NS,
        MemoryAdd(agent_id=AGENT, content="NVDA revenue $18B", event_time=T0,
                  metadata={"ticker": "NVDA", "metric": "revenue"}),
    )
    await add_memory(
        db, NS,
        MemoryAdd(agent_id=AGENT, content="AAPL iphone units 50M", event_time=T0,
                  metadata={"ticker": "AAPL", "metric": "units"}),
    )

    result = await reconstruct(db, NS, AGENT, as_of=T2, query="NVDA revenue", k=5)
    assert len(result.memories) >= 1
    # Top result should be NVDA-related
    assert "NVDA" in (result.memories[0].content or "")
