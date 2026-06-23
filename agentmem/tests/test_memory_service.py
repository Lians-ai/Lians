"""
Happy-path integration tests for memory_service.add() + recall().
Uses local embedding provider (no API calls).
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

from src.lian.schemas import MemoryAdd, RecallRequest
from src.lian.memory_service import add_memory, recall_memories


NS = "test-tenant"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_add_and_recall(db):
    req = MemoryAdd(
        agent_id="agent-1",
        content="NVDA Q3 guidance raised to $36B",
        event_time=T1,
        source="analyst_day",
        subject_id=None,
        metadata={"ticker": "NVDA", "metric": "guidance"},
    )
    result = await add_memory(db, NS, req)
    assert result.id is not None
    assert result.content == "NVDA Q3 guidance raised to $36B"
    assert result.valid_to is None

    recall_req = RecallRequest(
        agent_id="agent-1",
        query="NVDA guidance",
        k=5,
    )
    recall_result = await recall_memories(db, NS, recall_req)
    assert len(recall_result.memories) >= 1
    assert recall_result.memories[0].content == "NVDA Q3 guidance raised to $36B"


@pytest.mark.asyncio
async def test_supersession_closes_old_memory(db):
    """Adding an updated fact supersedes the old one and closes its valid_to."""
    old_req = MemoryAdd(
        agent_id="agent-1",
        content="NVDA Q3 guidance $32B",
        event_time=T0,
        metadata={"ticker": "NVDA", "metric": "guidance"},
    )
    old = await add_memory(db, NS, old_req)

    new_req = MemoryAdd(
        agent_id="agent-1",
        content="NVDA Q3 guidance raised to $36B",
        event_time=T1,
        metadata={"ticker": "NVDA", "metric": "guidance"},
    )
    new = await add_memory(db, NS, new_req)

    # Refresh old from DB
    from src.lian.models import Memory
    refreshed_old = await db.get(Memory, old.id)
    assert refreshed_old.valid_to is not None, "Old memory must be closed after supersession"
    assert refreshed_old.superseded_by == new.id


@pytest.mark.asyncio
async def test_recall_as_of_past(db):
    """recall(as_of=T0) returns only the old memory, not the new one."""
    old_req = MemoryAdd(
        agent_id="agent-1",
        content="NVDA Q3 guidance $32B",
        event_time=T0,
        metadata={"ticker": "NVDA", "metric": "guidance"},
    )
    old = await add_memory(db, NS, old_req)

    new_req = MemoryAdd(
        agent_id="agent-1",
        content="NVDA Q3 guidance raised to $36B",
        event_time=T1,
        metadata={"ticker": "NVDA", "metric": "guidance"},
    )
    new = await add_memory(db, NS, new_req)

    recall_req = RecallRequest(
        agent_id="agent-1",
        query="NVDA guidance",
        k=10,
        as_of=T0 + timedelta(days=1),
    )
    result = await recall_memories(db, NS, recall_req)
    ids = [m.id for m in result.memories]
    assert old.id in ids, "Old memory should appear in past snapshot"
    assert new.id not in ids, "New memory must not appear before its event_time"


@pytest.mark.asyncio
async def test_namespace_isolation(db):
    """Memories from one namespace must not appear in another."""
    req = MemoryAdd(
        agent_id="agent-1",
        content="Tenant A secret guidance",
        event_time=T1,
        metadata={"ticker": "AAPL", "metric": "guidance"},
    )
    await add_memory(db, "tenant-a", req)

    recall_req = RecallRequest(agent_id="agent-1", query="secret guidance", k=5)
    result = await recall_memories(db, "tenant-b", recall_req)
    assert len(result.memories) == 0, "Tenant B must not see Tenant A's memories"
