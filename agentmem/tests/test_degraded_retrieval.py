"""
Degraded-retrieval mode.

An unavailable embedding provider must not take recall down with it: the
query proceeds lexical-only (BM25 + recency + importance) and the degradation
is explicit — on the result (``retrieval_degraded``), on the /v1/context
block, and in the recall audit event — so a decision made under degraded
recall is reconstructable as such.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from sqlalchemy import select

from src.lians.models import EventLog
from src.lians.schemas import MemoryAdd, RecallRequest, ContextRequest
from src.lians.memory_service import add_memory, recall_memories, assemble_context

NS = "degraded-ns"
AGENT = "degraded-agent"


class _BrokenProvider:
    """Simulates an embedding provider outage."""

    async def embed_one(self, text: str) -> list[float]:
        raise ConnectionError("embedding endpoint unreachable")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise ConnectionError("embedding endpoint unreachable")


@pytest.fixture
def break_embeddings(monkeypatch):
    """Returns a callable that breaks the provider — call it AFTER seeding."""
    def _break():
        import src.lians.memory_service as ms
        monkeypatch.setattr(ms, "get_embedding_provider", lambda: _BrokenProvider())
    return _break


async def _seed(db):
    now = datetime.now(timezone.utc)
    return await add_memory(db, NS, MemoryAdd(
        agent_id=AGENT,
        content="client mandate restricts leverage to 2x on the growth book",
        event_time=now,
    ))


@pytest.mark.asyncio
async def test_recall_survives_embedding_outage(db, break_embeddings):
    mem = await _seed(db)
    break_embeddings()

    result = await recall_memories(db, NS, RecallRequest(
        agent_id=AGENT, query="leverage mandate growth book", k=5,
    ))

    assert result.retrieval_degraded is True
    # BM25 still surfaces the fact — the outage degrades quality, not availability.
    assert [m.id for m in result.memories] == [mem.id]


@pytest.mark.asyncio
async def test_degradation_is_written_to_the_audit_chain(db, break_embeddings):
    await _seed(db)
    break_embeddings()

    await recall_memories(db, NS, RecallRequest(
        agent_id=AGENT, query="leverage mandate", k=5,
    ))

    rows = (await db.execute(
        select(EventLog).where(EventLog.namespace == NS, EventLog.op == "recall")
    )).scalars().all()
    assert rows, "recall must always be audited"
    assert rows[-1].payload.get("retrieval_degraded") is True


@pytest.mark.asyncio
async def test_healthy_recall_is_not_flagged_and_not_audited_as_degraded(db):
    await _seed(db)

    result = await recall_memories(db, NS, RecallRequest(
        agent_id=AGENT, query="leverage mandate", k=5,
    ))

    assert result.retrieval_degraded is False
    rows = (await db.execute(
        select(EventLog).where(EventLog.namespace == NS, EventLog.op == "recall")
    )).scalars().all()
    # The flag is only present in the payload when degradation actually happened,
    # keeping healthy audit payloads byte-stable for existing consumers.
    assert "retrieval_degraded" not in rows[-1].payload


@pytest.mark.asyncio
async def test_context_block_carries_the_degradation_flag(db, break_embeddings):
    await _seed(db)
    break_embeddings()

    ctx = await assemble_context(db, NS, ContextRequest(
        agent_id=AGENT, query="leverage mandate growth book",
    ))

    assert ctx.retrieval_degraded is True
    assert "leverage" in ctx.context
