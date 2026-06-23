"""
Concurrency and write-serialisation tests.

The core invariant: for any (namespace, agent_id, structured-key-set), there
must be at most ONE memory with valid_to IS NULL at any point in time.
Violating this produces "ghost current facts" â€” the agent sees two conflicting
values for the same metric.

On PostgreSQL, this is enforced by a transaction-level advisory lock acquired
in add_memory before run_supersession.  On SQLite (asyncio unit tests), the
cooperative single-thread model means awaits don't truly interleave at the DB
level, but the state invariant must still hold after concurrent asyncio tasks.

Each concurrent call gets its own session (mirroring production where each HTTP
request gets its own session from get_db()).  With StaticPool the underlying
SQLite connection is shared, so operations serialise at the connection level â€”
this validates the invariant without truly exercising the advisory lock.
The advisory lock is exercised against PostgreSQL in test_pgvector.py.
"""
from __future__ import annotations
import asyncio
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.lian.schemas import MemoryAdd, RecallRequest
from src.lian.memory_service import add_memory, recall_memories, _write_lock_keys

NS    = "concurrency-ns"
AGENT = "concurrency-agent"


# ---------------------------------------------------------------------------
# Session-factory fixture â€” each call returns a fresh session on the same DB
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def session_factory(test_settings):
    """
    In-memory SQLite engine shared across sessions in a single test.
    Mirrors production: each add_memory call uses its own session,
    just as each HTTP request uses its own get_db() session.
    """
    from src.lian.models import Base as AppBase

    # Use SQLite shared-cache URI so each session gets its own connection
    # but all sessions share the same in-memory database â€” avoids StaticPool's
    # single-connection limit when multiple sessions run concurrently.
    engine = create_async_engine(
        "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
        connect_args={"check_same_thread": False},
    )

    pg_indexes = [
        idx for table in AppBase.metadata.tables.values()
        for idx in table.indexes
        if idx.dialect_kwargs.get("postgresql_using") is not None
    ]
    for idx in pg_indexes:
        idx.table.indexes.discard(idx)

    async with engine.begin() as conn:
        await conn.run_sync(AppBase.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


def _t(month: int) -> datetime:
    return datetime(2026, month, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Advisory lock key helpers
# ---------------------------------------------------------------------------

class TestWriteLockKeys:

    def test_keys_are_stable(self):
        """Same input always produces the same keys â€” no PYTHONHASHSEED dependence."""
        k1a, k2a = _write_lock_keys("tenant-x", "agent-1")
        k1b, k2b = _write_lock_keys("tenant-x", "agent-1")
        assert (k1a, k2a) == (k1b, k2b)

    def test_different_agents_get_different_keys(self):
        """Two agents in the same namespace must not share a lock."""
        k_a = _write_lock_keys("ns", "agent-a")
        k_b = _write_lock_keys("ns", "agent-b")
        assert k_a != k_b

    def test_different_namespaces_get_different_keys(self):
        """Same agent_id in different namespaces must not share a lock."""
        k1 = _write_lock_keys("tenant-a", "agent-1")
        k2 = _write_lock_keys("tenant-b", "agent-1")
        assert k1 != k2

    def test_keys_are_valid_int4_range(self):
        """PostgreSQL int4 is signed 32-bit: -(2^31) .. 2^31-1."""
        INT4_MIN, INT4_MAX = -(2**31), 2**31 - 1
        for ns, agent in [("ns", "a"), ("long-namespace", "complex-agent-id-123")]:
            k1, k2 = _write_lock_keys(ns, agent)
            assert INT4_MIN <= k1 <= INT4_MAX, f"k1={k1} out of int4 range"
            assert INT4_MIN <= k2 <= INT4_MAX, f"k2={k2} out of int4 range"

    def test_null_byte_separator_prevents_collisions(self):
        """
        'abcd' + 'efgh' must differ from 'abc' + 'defgh'.
        The \\x00 separator in the hash input prevents prefix-collision attacks.
        """
        k_ab = _write_lock_keys("abcd", "efgh")
        k_cd = _write_lock_keys("abc", "defgh")
        assert k_ab != k_cd


# ---------------------------------------------------------------------------
# State invariant after sequential writes (foundation for the concurrent case)
# ---------------------------------------------------------------------------

class TestSequentialConsistency:

    @pytest.mark.asyncio
    async def test_sequential_updates_single_open_memory(self, db):
        """
        Three sequential updates to the same metric produce a chain where
        only the last memory is open.  This is the baseline the concurrent
        case must preserve.
        """
        meta = {"ticker": "NVDA", "metric": "guidance"}

        m0 = await add_memory(db, NS, MemoryAdd(
            agent_id=AGENT, content="NVDA guidance $32B",
            event_time=_t(1), metadata=meta,
        ))
        m1 = await add_memory(db, NS, MemoryAdd(
            agent_id=AGENT, content="NVDA guidance $36B",
            event_time=_t(4), metadata=meta,
        ))
        m2 = await add_memory(db, NS, MemoryAdd(
            agent_id=AGENT, content="NVDA guidance $40B",
            event_time=_t(7), metadata=meta,
        ))

        from sqlalchemy import select
        from src.lian.models import Memory as MemModel

        open_mems = (await db.execute(
            select(MemModel).where(
                MemModel.namespace == NS,
                MemModel.agent_id == AGENT,
                MemModel.valid_to.is_(None),
                MemModel.erased_at.is_(None),
            )
        )).scalars().all()

        assert len(open_mems) == 1, (
            f"Expected 1 open memory after 3 sequential updates; got {len(open_mems)}"
        )
        assert open_mems[0].id == m2.id, "The last update must be the only open memory"

    @pytest.mark.asyncio
    async def test_supersession_chain_integrity(self, db):
        """
        Each memory in the chain points to its successor via superseded_by.
        The chain must be acyclic and terminate at the current memory.
        """
        from src.lian.models import Memory as MemModel

        meta = {"ticker": "AAPL", "metric": "revenue"}
        mems = []
        for i, t in enumerate([_t(1), _t(4), _t(7)]):
            m = await add_memory(db, NS, MemoryAdd(
                agent_id=AGENT, content=f"AAPL revenue step {i}",
                event_time=t, metadata=meta,
            ))
            mems.append(m)

        # Walk the chain forward
        db_m0 = await db.get(MemModel, mems[0].id)
        db_m1 = await db.get(MemModel, mems[1].id)
        db_m2 = await db.get(MemModel, mems[2].id)

        assert db_m0.superseded_by == mems[1].id, "m0 must point to m1"
        assert db_m1.superseded_by == mems[2].id, "m1 must point to m2"
        assert db_m2.superseded_by is None,       "m2 (current) has no successor"
        assert db_m2.valid_to is None,             "m2 must be open"


# ---------------------------------------------------------------------------
# Concurrent asyncio writes
# ---------------------------------------------------------------------------

class TestConcurrentAsyncioWrites:
    """
    asyncio.gather fires concurrent add_memory coroutines, each with its own
    session â€” mirroring production where each HTTP request gets its own
    get_db() session.

    On SQLite + StaticPool, the underlying connection is shared, so operations
    serialise at the aiosqlite level.  This validates the STATE invariant
    without truly racing.  The advisory lock is exercised against a live
    PostgreSQL instance in test_pgvector.py.
    """

    @pytest.mark.asyncio
    async def test_two_concurrent_adds_leave_one_open_memory(self, session_factory):
        """
        Two concurrent adds for the same metric must leave exactly one open
        (valid_to=None) memory â€” no ghost current facts.
        """
        from sqlalchemy import select
        from src.lian.models import Memory as MemModel

        meta = {"ticker": "TSLA", "metric": "deliveries"}

        # Seed
        async with session_factory() as db:
            await add_memory(db, NS, MemoryAdd(
                agent_id=AGENT, content="TSLA deliveries 400k",
                event_time=_t(1), metadata=meta,
            ))

        async def _add(content, t):
            async with session_factory() as session:
                return await add_memory(session, NS, MemoryAdd(
                    agent_id=AGENT, content=content, event_time=t, metadata=meta,
                ))

        await asyncio.gather(
            _add("TSLA deliveries 430k", _t(4)),
            _add("TSLA deliveries 460k", _t(7)),
        )

        async with session_factory() as db:
            open_mems = (await db.execute(
                select(MemModel).where(
                    MemModel.namespace == NS,
                    MemModel.agent_id == AGENT,
                    MemModel.valid_to.is_(None),
                    MemModel.erased_at.is_(None),
                )
            )).scalars().all()

        assert len(open_mems) == 1, (
            f"Race condition: expected 1 open memory, got {len(open_mems)}"
        )

    @pytest.mark.asyncio
    async def test_concurrent_adds_different_metrics_independent(self, session_factory):
        """
        Concurrent adds for DIFFERENT metrics on the same ticker must not
        interfere â€” both end up as open memories.
        """
        from sqlalchemy import select
        from src.lian.models import Memory as MemModel

        agent = f"{AGENT}-diff"

        async def _add(content, meta):
            async with session_factory() as session:
                return await add_memory(session, NS, MemoryAdd(
                    agent_id=agent, content=content, event_time=_t(4), metadata=meta,
                ))

        r_rev, r_gm = await asyncio.gather(
            _add("AAPL Q3 revenue $95B",     {"ticker": "AAPL", "metric": "revenue"}),
            _add("AAPL Q3 gross margin 46%", {"ticker": "AAPL", "metric": "gross_margin"}),
        )

        async with session_factory() as db:
            open_mems = (await db.execute(
                select(MemModel).where(
                    MemModel.namespace == NS,
                    MemModel.agent_id == agent,
                    MemModel.valid_to.is_(None),
                    MemModel.erased_at.is_(None),
                )
            )).scalars().all()

        open_ids = {m.id for m in open_mems}
        assert r_rev.id in open_ids, "Revenue memory must remain open"
        assert r_gm.id  in open_ids, "Gross margin memory must remain open"

    @pytest.mark.asyncio
    async def test_five_concurrent_adds_single_winner(self, session_factory):
        """
        Five concurrent adds for the same metric â€” exactly one must be open.
        """
        from sqlalchemy import select
        from src.lian.models import Memory as MemModel

        meta  = {"ticker": "NVDA", "metric": "guidance"}
        agent = f"{AGENT}-five"

        async def _add(i):
            async with session_factory() as session:
                return await add_memory(session, NS, MemoryAdd(
                    agent_id=agent,
                    content=f"NVDA guidance update #{i}",
                    event_time=_t(i + 1),
                    metadata=meta,
                ))

        await asyncio.gather(*[_add(i) for i in range(5)])

        async with session_factory() as db:
            open_mems = (await db.execute(
                select(MemModel).where(
                    MemModel.namespace == NS,
                    MemModel.agent_id == agent,
                    MemModel.valid_to.is_(None),
                    MemModel.erased_at.is_(None),
                )
            )).scalars().all()

        assert len(open_mems) == 1, (
            f"5 concurrent adds left {len(open_mems)} open memories; expected 1"
        )

    @pytest.mark.asyncio
    async def test_sequential_adds_different_agents_isolated(self, session_factory):
        """
        Writes by two different agents in the same namespace must not interfere.
        The advisory lock is keyed on (namespace, agent_id), so different agents
        run independently without blocking each other on PostgreSQL.
        This test validates the isolation invariant using separate sessions.
        """
        from sqlalchemy import select
        from src.lian.models import Memory as MemModel

        meta = {"ticker": "MSFT", "metric": "cloud_revenue"}

        async with session_factory() as session:
            r_a = await add_memory(session, NS, MemoryAdd(
                agent_id="agent-iso-a", content="MSFT azure revenue $28B",
                event_time=_t(4), metadata=meta,
            ))

        async with session_factory() as session:
            r_b = await add_memory(session, NS, MemoryAdd(
                agent_id="agent-iso-b", content="MSFT azure revenue $29B",
                event_time=_t(4), metadata=meta,
            ))

        async with session_factory() as db:
            for agent_id, own_id, other_id in [
                ("agent-iso-a", r_a.id, r_b.id),
                ("agent-iso-b", r_b.id, r_a.id),
            ]:
                open_mems = (await db.execute(
                    select(MemModel).where(
                        MemModel.namespace == NS,
                        MemModel.agent_id == agent_id,
                        MemModel.valid_to.is_(None),
                        MemModel.erased_at.is_(None),
                    )
                )).scalars().all()
                open_ids = {m.id for m in open_mems}
                assert own_id   in open_ids, f"{agent_id} must see its own memory"
                assert other_id not in open_ids, f"{agent_id} must not see the other agent's memory"
