"""
Materiality-weighted retrieval decay.

A fact's retrieval half-life scales with ``metadata.materiality`` — a client
instruction or compliance flag stays retrievable long after a passing
preference has faded. Ranking-only: storage never decays, the tag is
deterministic caller metadata, and untagged facts keep the default half-life
(fully backwards compatible).
"""
from __future__ import annotations

import math
import pytest
from datetime import datetime, timedelta, timezone

from lians.ranking import (
    RECENCY_HALF_LIFE_DAYS,
    MATERIALITY_HALF_LIFE_DAYS,
    _materiality_half_life,
    _recency_decay,
)
from lians.schemas import MemoryAdd, RecallRequest
from lians.memory_service import add_memory, recall_memories

NS = "materiality-ns"
AGENT = "materiality-agent"


# ---------------------------------------------------------------------------
# Unit: half-life resolution
# ---------------------------------------------------------------------------

def test_untagged_metadata_keeps_default_half_life():
    assert _materiality_half_life(None) == RECENCY_HALF_LIFE_DAYS
    assert _materiality_half_life({}) == RECENCY_HALF_LIFE_DAYS
    assert _materiality_half_life({"ticker": "AAPL"}) == RECENCY_HALF_LIFE_DAYS


def test_unknown_or_malformed_tag_falls_back_to_default():
    assert _materiality_half_life({"materiality": "urgent"}) == RECENCY_HALF_LIFE_DAYS
    assert _materiality_half_life({"materiality": 3}) == RECENCY_HALF_LIFE_DAYS
    assert _materiality_half_life({"materiality": None}) == RECENCY_HALF_LIFE_DAYS


def test_tag_is_case_and_whitespace_insensitive():
    assert _materiality_half_life({"materiality": "CRITICAL"}) == MATERIALITY_HALF_LIFE_DAYS["critical"]
    assert _materiality_half_life({"materiality": " high "}) == MATERIALITY_HALF_LIFE_DAYS["high"]


def test_materiality_levels_order_half_lives():
    assert (
        MATERIALITY_HALF_LIFE_DAYS["low"]
        < MATERIALITY_HALF_LIFE_DAYS["standard"]
        < MATERIALITY_HALF_LIFE_DAYS["high"]
        < MATERIALITY_HALF_LIFE_DAYS["critical"]
    )
    assert MATERIALITY_HALF_LIFE_DAYS["standard"] == RECENCY_HALF_LIFE_DAYS


def test_decay_halves_at_half_life():
    for half_life in (7.0, 30.0, 365.0):
        t = datetime.now(timezone.utc) - timedelta(days=half_life)
        assert _recency_decay(t, half_life) == pytest.approx(0.5, abs=1e-3)


def test_critical_fact_decays_slower_than_low():
    t = datetime.now(timezone.utc) - timedelta(days=90)
    low = _recency_decay(t, MATERIALITY_HALF_LIFE_DAYS["low"])
    critical = _recency_decay(t, MATERIALITY_HALF_LIFE_DAYS["critical"])
    assert critical > low
    assert critical == pytest.approx(math.exp(-math.log(2) * 90 / 365), abs=1e-6)


# ---------------------------------------------------------------------------
# Integration: recall ranking through the full service path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_critical_fact_outranks_equally_old_low_fact(db):
    """Two equally old, equally relevant facts: the critical one ranks first."""
    old = datetime.now(timezone.utc) - timedelta(days=180)

    low = await add_memory(db, NS, MemoryAdd(
        agent_id=AGENT,
        content="client prefers morning meetings about portfolio review",
        event_time=old,
        metadata={"materiality": "low"},
    ))
    critical = await add_memory(db, NS, MemoryAdd(
        agent_id=AGENT,
        content="client prefers morning meetings about portfolio review",
        event_time=old,
        metadata={"materiality": "critical"},
    ))

    result = await recall_memories(db, NS, RecallRequest(
        agent_id=AGENT, query="portfolio review meetings", k=2,
    ))

    assert [m.id for m in result.memories] == [critical.id, low.id]
    assert result.memories[0].score > result.memories[1].score


@pytest.mark.asyncio
async def test_untagged_facts_rank_identically_to_standard(db):
    """Backwards compatibility: no tag scores exactly like materiality=standard."""
    old = datetime.now(timezone.utc) - timedelta(days=60)

    await add_memory(db, NS, MemoryAdd(
        agent_id=AGENT,
        content="quarterly rebalancing thresholds were reviewed",
        event_time=old,
        metadata={},
    ))
    await add_memory(db, NS, MemoryAdd(
        agent_id=AGENT,
        content="quarterly rebalancing thresholds were reviewed",
        event_time=old,
        metadata={"materiality": "standard"},
    ))

    result = await recall_memories(db, NS, RecallRequest(
        agent_id=AGENT, query="rebalancing thresholds", k=2,
    ))

    assert len(result.memories) == 2
    assert result.memories[0].score == pytest.approx(result.memories[1].score, abs=1e-9)


@pytest.mark.asyncio
async def test_point_in_time_recall_applies_materiality(db):
    """as_of recall (bitemporal log path) honors the same half-life policy."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=180)

    low = await add_memory(db, NS, MemoryAdd(
        agent_id=AGENT,
        content="compliance restriction on energy sector trades",
        event_time=old,
        metadata={"materiality": "low"},
    ))
    critical = await add_memory(db, NS, MemoryAdd(
        agent_id=AGENT,
        content="compliance restriction on energy sector trades",
        event_time=old,
        metadata={"materiality": "critical"},
    ))

    result = await recall_memories(db, NS, RecallRequest(
        agent_id=AGENT, query="energy sector restriction", k=2, as_of=now,
    ))

    assert [m.id for m in result.memories] == [critical.id, low.id]
