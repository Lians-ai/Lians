"""
Keyed fast-path relation classification.

The fast path used to label every later keyed write SUPERSEDES 1.0 - REFINES
and CONFIRMS were unreachable for keyed facts (and unkeyed writes rarely find
candidates without real embeddings, so they were unreachable, period).
These tests pin the deterministic relation split:

  identical value, later event  → CONFIRMS  (old window closes, honest label)
  narrowing (token superset)    → REFINES   (0.8 - reviewable, window closes)
  changed value, later event    → SUPERSEDES (1.0, unchanged behavior)
  different value, same instant → CONTRADICTS_SAME_TIME (conflict flag)
"""
import pytest
from datetime import datetime, timezone
from sqlalchemy import select, and_

from src.lians.schemas import MemoryAdd
from src.lians.memory_service import add_memory, get_pending_supersessions
from src.lians.models import Memory, EventLog, LiveFact

NS = "keyed-rel-ns"
AGENT = "keyed-rel-agent"
T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 5, 10, tzinfo=timezone.utc)


async def _supersede_events(db, namespace):
    result = await db.execute(select(EventLog).where(and_(
        EventLog.namespace == namespace, EventLog.op == "supersede")))
    return list(result.scalars().all())


async def _live_count(db, namespace):
    result = await db.execute(select(LiveFact).where(LiveFact.namespace == namespace))
    return len(list(result.scalars().all()))


@pytest.mark.asyncio
async def test_identical_later_value_is_confirms_not_supersedes(db):
    ns = NS + "-confirms"
    meta = {"ticker": "ZORG", "metric": "eps"}
    m1 = await add_memory(db, ns, MemoryAdd(
        agent_id=AGENT, content="ZORG Q2 EPS was 1.10", event_time=T0, metadata=meta))
    m2 = await add_memory(db, ns, MemoryAdd(
        agent_id=AGENT, content="ZORG Q2 EPS was 1.10", event_time=T1, metadata=meta))

    old = await db.get(Memory, m1.id)
    # SQLite hands back naive datetimes - normalize before comparing.
    closed_at = old.valid_to.replace(tzinfo=timezone.utc) if old.valid_to.tzinfo is None else old.valid_to
    assert closed_at == T1, "re-confirmation must close the old window"
    assert str(old.superseded_by) == str(m2.id)

    events = await _supersede_events(db, ns)
    assert len(events) == 1
    assert events[0].payload["relation"] == "CONFIRMS"
    assert events[0].payload["confidence"] == 1.0

    assert await _live_count(db, ns) == 1, "exactly one live copy of a confirmed fact"


@pytest.mark.asyncio
async def test_keyed_narrowing_is_refines_and_reviewable(db):
    ns = NS + "-refines"
    meta = {"ticker": "ZORG", "metric": "capex_guidance"}
    await add_memory(db, ns, MemoryAdd(
        agent_id=AGENT, content="Zorg FY26 capex guidance is 2 billion dollars",
        event_time=T0, metadata=meta))
    await add_memory(db, ns, MemoryAdd(
        agent_id=AGENT,
        content="Zorg FY26 capex guidance is 2 billion dollars primarily for datacenter buildout",
        event_time=T1, metadata=meta))

    events = await _supersede_events(db, ns)
    assert len(events) == 1
    assert events[0].payload["relation"] == "REFINES"
    assert events[0].payload["confidence"] == 0.8

    # 0.8 sits below a 0.85 review threshold → surfaces for human review
    review = await get_pending_supersessions(db, ns, confidence_threshold=0.85)
    assert review.total == 1
    assert review.items[0].relation == "REFINES"

    assert await _live_count(db, ns) == 1


@pytest.mark.asyncio
async def test_changed_value_still_supersedes_at_full_confidence(db):
    ns = NS + "-supersedes"
    meta = {"ticker": "ZORG", "metric": "revenue"}
    await add_memory(db, ns, MemoryAdd(
        agent_id=AGENT, content="ZORG Q2 revenue was $88 billion", event_time=T0, metadata=meta))
    await add_memory(db, ns, MemoryAdd(
        agent_id=AGENT, content="ZORG Q2 revenue revised to $91 billion", event_time=T1, metadata=meta))

    events = await _supersede_events(db, ns)
    assert len(events) == 1
    assert events[0].payload["relation"] == "SUPERSEDES"
    assert events[0].payload["confidence"] == 1.0
    assert await _live_count(db, ns) == 1


@pytest.mark.asyncio
async def test_same_time_different_value_flags_conflict_keeps_both(db):
    ns = NS + "-conflict"
    meta = {"ticker": "ZORG", "metric": "ebitda_margin"}
    m1 = await add_memory(db, ns, MemoryAdd(
        agent_id=AGENT, content="ZORG margin was 18 percent", event_time=T0, metadata=meta))
    await add_memory(db, ns, MemoryAdd(
        agent_id=AGENT, content="ZORG margin was 22 percent", event_time=T0, metadata=meta))

    old = await db.get(Memory, m1.id)
    assert old.valid_to is None, "contradiction must not silently supersede"
    assert await _live_count(db, ns) == 2, "both contradictory facts stay live"
    assert (await _supersede_events(db, ns)) == []
