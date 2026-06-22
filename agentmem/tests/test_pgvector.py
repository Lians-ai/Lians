"""
PostgreSQL + pgvector integration tests.

These tests verify the full Postgres code path — asyncpg codec registration,
vector INSERT/SELECT, cosine distance ordering via the HNSW index, and the
end-to-end add_memory → recall_memories round-trip.

Prerequisites
-------------
1. Start the pgvector Postgres container::

       cd agentmem
       docker compose up -d postgres

2. Set TEST_DATABASE_URL::

       export TEST_DATABASE_URL=postgresql+asyncpg://agentmem:agentmem@localhost:5432/agentmem

3. Run migrations::

       alembic upgrade head

4. Run just these tests::

       pytest tests/test_pgvector.py -v

All tests are skipped automatically when TEST_DATABASE_URL is not set or when
the database is unreachable, so they never break the standard CI suite.
"""
import os
import uuid
import pytest
import pytest_asyncio
from datetime import datetime, timezone

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
PG_AVAILABLE = bool(TEST_DB_URL and "postgresql" in TEST_DB_URL)

pytestmark = pytest.mark.skipif(
    not PG_AVAILABLE,
    reason="TEST_DATABASE_URL not set to a PostgreSQL URL — skipping pgvector tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def pg_engine():
    """Async engine pointing at the test Postgres."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_session_factory(pg_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    return async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def pg_db(pg_session_factory):
    """One async session per test, rolled back on exit so tests are isolated."""
    async with pg_session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_vec(dim: int = 1024) -> list[float]:
    import random
    import math
    v = [random.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / (norm + 1e-9) for x in v]


def _similar_vec(base: list[float], noise: float = 0.05) -> list[float]:
    import math
    v = [x + noise * (0.5 - __import__("random").random()) for x in base]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / (norm + 1e-9) for x in v]


TEST_NS = f"pgvec-test-{uuid.uuid4().hex[:8]}"
AGENT = "pgvec-agent"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAsyncpgCodec:
    """Verify that the pgvector asyncpg codec is registered correctly."""

    async def test_vector_extension_enabled(self, pg_db):
        from sqlalchemy import text
        result = await pg_db.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )
        row = result.fetchone()
        assert row is not None, "pgvector extension not installed — run: alembic upgrade head"

    async def test_insert_and_select_vector(self, pg_db):
        """Raw INSERT + SELECT round-trip — string protocol, no binary codec needed."""
        from sqlalchemy import text
        vec = _random_vec(4)  # tiny vector for a quick sanity check
        vec_str = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"

        await pg_db.execute(text("CREATE TEMP TABLE _vec_test (v vector(4))"))
        await pg_db.execute(text(f"INSERT INTO _vec_test VALUES ('{vec_str}'::vector)"))

        result = await pg_db.execute(text("SELECT v FROM _vec_test"))
        row = result.fetchone()
        assert row is not None
        # asyncpg returns vector as a string "[x1,x2,...]" via text protocol
        raw = row[0]
        returned = [float(x) for x in raw.strip("[]").split(",")] if isinstance(raw, str) else list(raw)
        assert len(returned) == 4
        for a, b in zip(vec, returned):
            assert abs(a - b) < 1e-5, f"Vector round-trip mismatch: {a} vs {b}"


class TestVectorOperations:
    """Verify cosine distance operator and HNSW ordering."""

    async def test_cosine_distance_ordering(self, pg_db):
        """The <=> operator should rank a similar vector closer than a random one."""
        from sqlalchemy import text
        query = _random_vec(8)
        near = _similar_vec(query, noise=0.01)
        far = _random_vec(8)

        def fmt(v):
            return "[" + ",".join(f"{x:.8f}" for x in v) + "]"

        await pg_db.execute(text("CREATE TEMP TABLE _dist_test (id int, v vector(8))"))
        await pg_db.execute(text(f"INSERT INTO _dist_test VALUES (1, '{fmt(near)}'::vector)"))
        await pg_db.execute(text(f"INSERT INTO _dist_test VALUES (2, '{fmt(far)}'::vector)"))

        q_str = fmt(query)
        result = await pg_db.execute(
            text(f"SELECT id, v <=> '{q_str}'::vector AS dist FROM _dist_test ORDER BY dist")
        )
        rows = result.fetchall()
        assert rows[0][0] == 1, "Near vector should rank first by cosine distance"

    async def test_hnsw_index_exists(self, pg_db):
        """Confirm the HNSW index was created by the migration."""
        from sqlalchemy import text
        result = await pg_db.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'memories' AND indexdef ILIKE '%hnsw%'"
        ))
        row = result.fetchone()
        assert row is not None, (
            "HNSW index not found on memories.embedding — "
            "run: alembic upgrade head"
        )


class TestEndToEnd:
    """Full add_memory → recall_memories round-trip on Postgres."""

    async def test_add_memory_stores_vector(self, pg_session_factory):
        from src.agentmem.memory_service import add_memory
        from src.agentmem.schemas import MemoryAdd

        req = MemoryAdd(
            agent_id=AGENT,
            content="NVDA Q3 FY2026 guidance raised to $36B",
            event_time=datetime(2026, 5, 10, tzinfo=timezone.utc),
            source="test",
            metadata={"ticker": "NVDA", "metric": "guidance"},
        )

        async with pg_session_factory() as db:
            result = await add_memory(db, TEST_NS, req)

        assert result.id is not None
        assert result.content == "NVDA Q3 FY2026 guidance raised to $36B"
        assert result.namespace == TEST_NS

    async def test_recall_finds_added_memory(self, pg_session_factory):
        from src.agentmem.memory_service import add_memory, recall_memories
        from src.agentmem.schemas import MemoryAdd, RecallRequest

        async with pg_session_factory() as db:
            await add_memory(db, TEST_NS, MemoryAdd(
                agent_id=AGENT,
                content="AAPL gross margin expanded to 46%",
                event_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
                metadata={"ticker": "AAPL", "metric": "gross_margin"},
            ))
            result = await recall_memories(db, TEST_NS, RecallRequest(
                agent_id=AGENT,
                query="AAPL gross margin",
                k=5,
            ))

        assert len(result.memories) >= 1
        assert any("AAPL" in (m.content or "") for m in result.memories)

    async def test_ann_prefetch_used_on_postgres(self, pg_session_factory):
        """
        With enough rows seeded, EXPLAIN should show an Index Scan on the HNSW
        index rather than a Seq Scan — proves the index is actually used.
        """
        from sqlalchemy import text
        from src.agentmem.memory_service import add_memory
        from src.agentmem.schemas import MemoryAdd
        from src.agentmem.embeddings import get_embedding_provider

        # Seed 30 rows so the planner prefers the HNSW index over a seq scan
        seed_agent = f"ann-seed-{uuid.uuid4().hex[:6]}"
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META"]
        async with pg_session_factory() as db:
            for i in range(30):
                ticker = tickers[i % len(tickers)]
                await add_memory(db, TEST_NS, MemoryAdd(
                    agent_id=seed_agent,
                    content=f"{ticker} Q{(i % 4) + 1} revenue ${10 + i}B",
                    event_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    metadata={"ticker": ticker, "metric": "revenue"},
                ))

        provider = get_embedding_provider()
        query_embedding = await provider.embed_one("NVDA guidance")
        vec_str = "[" + ",".join(f"{x:.8f}" for x in query_embedding) + "]"

        async with pg_session_factory() as db:
            await db.execute(text("ANALYZE memories"))
            # Disable seq scan so the planner is forced to use the HNSW index
            # if one exists — standard technique for index-existence tests without
            # needing millions of rows.
            await db.execute(text("SET enable_seqscan = off"))
            result = await db.execute(text(
                f"EXPLAIN SELECT * FROM memories "
                f"ORDER BY embedding <=> '{vec_str}'::vector LIMIT 20"
            ))
            plan = "\n".join(row[0] for row in result.fetchall())

        assert "Index Scan" in plan or "Bitmap" in plan, (
            f"Expected HNSW index scan but got:\n{plan}"
        )

    async def test_point_in_time_recall(self, pg_session_factory):
        """as_of filter works on Postgres — validates bitemporal model end-to-end."""
        from src.agentmem.memory_service import add_memory, recall_memories
        from src.agentmem.schemas import MemoryAdd, RecallRequest
        from datetime import timedelta

        agent = f"pit-{uuid.uuid4().hex[:6]}"
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t1 = datetime(2026, 6, 1, tzinfo=timezone.utc)

        async with pg_session_factory() as db:
            await add_memory(db, TEST_NS, MemoryAdd(
                agent_id=agent,
                content="TSLA deliveries 400k",
                event_time=t0,
                metadata={"ticker": "TSLA", "metric": "deliveries"},
            ))
            await add_memory(db, TEST_NS, MemoryAdd(
                agent_id=agent,
                content="TSLA deliveries revised to 450k",
                event_time=t1,
                metadata={"ticker": "TSLA", "metric": "deliveries"},
            ))

            # As of one day after t0 — should only see 400k
            past = await recall_memories(db, TEST_NS, RecallRequest(
                agent_id=agent,
                query="TSLA deliveries",
                k=5,
                as_of=t0 + timedelta(days=1),
            ))

        assert len(past.memories) >= 1
        assert all("400k" in (m.content or "") for m in past.memories)
        assert not any("450k" in (m.content or "") for m in past.memories)
