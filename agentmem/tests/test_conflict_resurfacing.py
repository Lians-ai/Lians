"""
Active resurfacing of unresolved conflicts.

Open conflicts push to the top of every /v1/context block (oldest first) until
a human adjudicates them - an unresolved conflict must not silently age out,
and the model must treat contested facts as contested rather than confidently
using whichever version recall ranked higher.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from src.lians.schemas import MemoryAdd, ContextRequest, ConflictResolveRequest
from src.lians.memory_service import (
    add_memory, assemble_context, list_conflicts, resolve_conflict,
)

NS = "resurface-ns"
AGENT = "resurface-agent"
T_SAME = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


async def _seed_conflict(db) -> None:
    """Same event_time + same structured keys + different content → conflict."""
    for content, source in [
        ("NVDA EPS is 5.20 for Q1", "vendor-feed-A"),
        ("NVDA EPS is 4.80 for Q1", "vendor-feed-B"),
    ]:
        await add_memory(db, NS, MemoryAdd(
            agent_id=AGENT,
            content=content,
            event_time=T_SAME,
            source=source,
            metadata={"ticker": "NVDA", "metric": "eps"},
        ))


@pytest.mark.asyncio
async def test_open_conflict_surfaces_in_context_block(db):
    await _seed_conflict(db)

    ctx = await assemble_context(db, NS, ContextRequest(
        agent_id=AGENT, query="anything at all, even unrelated",
    ))

    assert ctx.open_conflicts_total == 1
    assert len(ctx.open_conflicts) == 1
    assert "UNRESOLVED MEMORY CONFLICTS" in ctx.context
    assert "5.20" in ctx.context and "4.80" in ctx.context
    assert "DISAGREES WITH" in ctx.context
    # The conflict banner precedes the recalled facts.
    assert ctx.context.index("UNRESOLVED") < len(ctx.context)


@pytest.mark.asyncio
async def test_resolved_conflict_stops_resurfacing(db):
    await _seed_conflict(db)
    flags = await list_conflicts(db, NS, status="open")
    assert flags.total == 1

    await resolve_conflict(db, NS, flags.conflicts[0].id, ConflictResolveRequest(
        resolution="accept_a", note="vendor A is the contracted golden source",
    ))

    ctx = await assemble_context(db, NS, ContextRequest(
        agent_id=AGENT, query="NVDA earnings",
    ))
    assert ctx.open_conflicts_total == 0
    assert ctx.open_conflicts == []
    assert "UNRESOLVED MEMORY CONFLICTS" not in ctx.context


@pytest.mark.asyncio
async def test_surfacing_can_be_opted_out_per_call(db):
    await _seed_conflict(db)

    ctx = await assemble_context(db, NS, ContextRequest(
        agent_id=AGENT, query="NVDA earnings", surface_conflicts=False,
    ))
    assert ctx.open_conflicts == []
    assert ctx.open_conflicts_total == 0
    assert "UNRESOLVED MEMORY CONFLICTS" not in ctx.context


@pytest.mark.asyncio
async def test_conflicts_of_other_agents_do_not_leak(db):
    await _seed_conflict(db)

    ctx = await assemble_context(db, NS, ContextRequest(
        agent_id="a-different-agent", query="NVDA earnings",
    ))
    assert ctx.open_conflicts == []
    assert ctx.open_conflicts_total == 0


@pytest.mark.asyncio
async def test_conflict_overflow_is_counted_not_dropped_silently(db):
    """More open conflicts than max_conflicts: the block says how many more."""
    for i in range(3):
        t = datetime(2026, 3, 10 + i, 12, 0, 0, tzinfo=timezone.utc)
        for content, source in [
            (f"AAPL revenue is {100 + i}bn", "feed-A"),
            (f"AAPL revenue is {90 + i}bn", "feed-B"),
        ]:
            await add_memory(db, NS, MemoryAdd(
                agent_id=AGENT,
                content=content,
                event_time=t,
                source=source,
                metadata={"ticker": "AAPL", "metric": f"rev-{i}"},
            ))

    ctx = await assemble_context(db, NS, ContextRequest(
        agent_id=AGENT, query="AAPL revenue", max_conflicts=2,
    ))
    assert ctx.open_conflicts_total == 3
    assert len(ctx.open_conflicts) == 2
    assert "+1 more open conflicts not shown" in ctx.context
    # Oldest first: the longest-unresolved conflict is the most overdue.
    assert ctx.open_conflicts[0].detected_at <= ctx.open_conflicts[1].detected_at
