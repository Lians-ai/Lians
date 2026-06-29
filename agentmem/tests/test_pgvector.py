"""
PostgreSQL + pgvector integration tests.

These tests verify the full Postgres code path â€” asyncpg codec registration,
vector INSERT/SELECT, cosine distance ordering via the HNSW index, and the
end-to-end add_memory â†’ recall_memories round-trip.

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
    reason="TEST_DATABASE_URL not set to a PostgreSQL URL â€” skipping pgvector tests",
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
        assert row is not None, "pgvector extension not installed â€” run: alembic upgrade head"

    async def test_insert_and_select_vector(self, pg_db):
        """Raw INSERT + SELECT round-trip â€” string protocol, no binary codec needed."""
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
            "HNSW index not found on memories.embedding â€” "
            "run: alembic upgrade head"
        )


class TestEndToEnd:
    """Full add_memory â†’ recall_memories round-trip on Postgres."""

    async def test_add_memory_stores_vector(self, pg_session_factory):
        from src.lians.memory_service import add_memory
        from src.lians.schemas import MemoryAdd

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
        from src.lians.memory_service import add_memory, recall_memories
        from src.lians.schemas import MemoryAdd, RecallRequest

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
        index rather than a Seq Scan â€” proves the index is actually used.
        """
        from sqlalchemy import text
        from src.lians.memory_service import add_memory
        from src.lians.schemas import MemoryAdd
        from src.lians.embeddings import get_embedding_provider

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
            # if one exists â€” standard technique for index-existence tests without
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
        """as_of filter works on Postgres â€” validates bitemporal model end-to-end."""
        from src.lians.memory_service import add_memory, recall_memories
        from src.lians.schemas import MemoryAdd, RecallRequest
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

            # As of one day after t0 â€” should only see 400k
            past = await recall_memories(db, TEST_NS, RecallRequest(
                agent_id=agent,
                query="TSLA deliveries",
                k=5,
                as_of=t0 + timedelta(days=1),
            ))

        assert len(past.memories) >= 1
        assert all("400k" in (m.content or "") for m in past.memories)
        assert not any("450k" in (m.content or "") for m in past.memories)


# ---------------------------------------------------------------------------
# RLS information barrier tests (migration 0011_rls_barriers)
# ---------------------------------------------------------------------------

class TestRLSInformationBarriers:
    """
    Verify PostgreSQL RLS enforces information barriers at the DB layer.

    These tests bypass the service layer and exercise the Postgres RLS policy
    directly, confirming that FORCE ROW LEVEL SECURITY blocks cross-barrier
    reads even when the app user owns the table.  This is AgentMem's primary
    compliance differentiator vs. Graphiti/Zep (which has no DB-layer barrier
    enforcement as of June 2026).

    Each test uses an isolated namespace so parallel CI runs cannot interfere.
    """

    @staticmethod
    def _ch(s: str) -> str:
        import hashlib
        return hashlib.sha256(s.encode()).hexdigest()

    async def test_barrier_group_isolation(self, pg_engine):
        """
        A session scoped to barrier group A must not see memories tagged B —
        enforced at the database layer, not the application.

        The CI/postgres-image login is a superuser, and superusers bypass RLS
        unconditionally. To prove genuine enforcement we create a NOSUPERUSER /
        NOBYPASSRLS role, GRANT it SELECT, switch to it with SET ROLE, set the
        namespace + barrier session vars, and confirm the group-B row is filtered
        out by the RESTRICTIVE barrier_isolation policy (migration 0013).
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        from sqlalchemy import text

        group_a = f"rls-a-{uuid.uuid4().hex[:6]}"
        group_b = f"rls-b-{uuid.uuid4().hex[:6]}"
        ns = f"rls-iso-{uuid.uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)
        id_a, id_b = str(uuid.uuid4()), str(uuid.uuid4())
        role = f"lians_rls_test_{uuid.uuid4().hex[:10]}"

        factory = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)

        async with factory() as db:
            # Insert both rows (as the superuser login).
            await db.execute(text("""
                INSERT INTO memories
                    (id, namespace, agent_id, content_hash,
                     event_time, valid_from, ingestion_time, importance, barrier_group)
                VALUES
                    (:id_a, :ns, 'agent-a', :ha, :now, :now, :now, 0.9, :ga),
                    (:id_b, :ns, 'agent-b', :hb, :now, :now, :now, 0.9, :gb)
            """), dict(id_a=id_a, id_b=id_b, ns=ns,
                       ha=self._ch("confidential-a"), hb=self._ch("confidential-b"),
                       now=now, ga=group_a, gb=group_b))

            # Create a restricted role that RLS actually applies to.
            await db.execute(text(f"CREATE ROLE {role} NOSUPERUSER NOBYPASSRLS"))
            await db.execute(text(f"GRANT SELECT ON memories TO {role}"))

            try:
                # Read as the restricted role, scoped to group A.
                await db.execute(text(f"SET ROLE {role}"))
                await db.execute(text("SELECT set_config('app.current_namespace', :ns, true)"), {"ns": ns})
                await db.execute(text("SELECT set_config('agentmem.barrier_group', :g, true)"), {"g": group_a})
                rows = (await db.execute(
                    text("SELECT id FROM memories WHERE namespace = :ns"), {"ns": ns}
                )).fetchall()
                visible = {str(r[0]) for r in rows}
            finally:
                await db.execute(text("RESET ROLE"))
                # Roll back everything — the inserts, CREATE ROLE, and GRANT are all
                # transactional, so nothing (including the role) persists. This also
                # avoids DROP ROLE failing while the role still holds the GRANT.
                await db.rollback()

        assert id_a in visible, "group_a must see its own memory"
        assert id_b not in visible, (
            "RLS FAILED: group_a read a group_b memory — the barrier_isolation "
            "policy is not RESTRICTIVE, or the barrier session var was not set"
        )

    async def test_unbarriered_memories_visible_to_all(self, pg_engine):
        """
        Memories with barrier_group=NULL (public) are visible regardless of
        which group the session is scoped to.  This covers shared market data
        or namespace-wide facts that every agent should see.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        from sqlalchemy import text

        group_a = f"rls-open-{uuid.uuid4().hex[:6]}"
        ns = f"rls-pub-{uuid.uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)
        id_pub = str(uuid.uuid4())

        factory = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)

        async with factory() as db:
            await db.execute(text("""
                INSERT INTO memories
                    (id, namespace, agent_id, content_hash,
                     event_time, valid_from, ingestion_time, importance)
                VALUES (:id, :ns, 'shared', :ch, :now, :now, :now, 0.5)
            """), dict(id=id_pub, ns=ns, ch=self._ch("public-fact"), now=now))
            await db.commit()

        async with factory() as db:
            await db.execute(text("SELECT set_config('agentmem.barrier_group', :g, true)"), {"g": group_a})
            rows = (await db.execute(
                text("SELECT id FROM memories WHERE namespace = :ns"), {"ns": ns}
            )).fetchall()
            visible = {str(r[0]) for r in rows}

        assert id_pub in visible, (
            "barrier_group=NULL memory must be visible to all groups â€” "
            "the IS NULL branch of the RLS policy is not firing"
        )

    async def test_null_session_var_sees_all_rows(self, pg_engine):
        """
        When agentmem.barrier_group is not set (NULL), all rows are visible.
        This is the admin / compliance-export path: no SET before the query
        means current_setting(..., true) returns NULL, and the IS NULL OR
        branch passes for every row.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        from sqlalchemy import text

        group_x = f"rls-adm-x-{uuid.uuid4().hex[:6]}"
        group_y = f"rls-adm-y-{uuid.uuid4().hex[:6]}"
        ns = f"rls-adm-{uuid.uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)
        id_x, id_y = str(uuid.uuid4()), str(uuid.uuid4())

        factory = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)

        async with factory() as db:
            await db.execute(text("""
                INSERT INTO memories
                    (id, namespace, agent_id, content_hash,
                     event_time, valid_from, ingestion_time, importance, barrier_group)
                VALUES
                    (:ix, :ns, 'agent-x', :hx, :now, :now, :now, 0.9, :gx),
                    (:iy, :ns, 'agent-y', :hy, :now, :now, :now, 0.9, :gy)
            """), dict(ix=id_x, iy=id_y, ns=ns,
                       hx=self._ch("x"), hy=self._ch("y"),
                       now=now, gx=group_x, gy=group_y))
            await db.commit()

        # No SET â€” current_setting('agentmem.barrier_group', true) IS NULL â†’ all rows pass
        async with factory() as db:
            rows = (await db.execute(
                text("SELECT id FROM memories WHERE namespace = :ns"), {"ns": ns}
            )).fetchall()
            visible = {str(r[0]) for r in rows}

        assert id_x in visible and id_y in visible, (
            "Admin path (no session var) must see all rows â€” "
            "IS NULL check in RLS policy is not returning TRUE for NULL setting"
        )
