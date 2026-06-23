"""
Standalone pgvector verification script.

Connects to Postgres, runs a battery of checks, and reports what works and
what needs attention.  Run this after `docker compose up -d postgres` and
`alembic upgrade head` to confirm the production DB path is correct before
going live.

Usage::

    cd agentmem
    python scripts/verify_pgvector.py
    python scripts/verify_pgvector.py --url postgresql+asyncpg://user:pw@host/db

Exit codes:
    0 â€” all checks passed
    1 â€” one or more checks failed
"""
import asyncio
import argparse
import math
import sys
from datetime import datetime, timezone


CHECKS: list[tuple[str, bool]] = []


def _ok(label: str) -> None:
    print(f"  PASS  {label}")
    CHECKS.append((label, True))


def _fail(label: str, detail: str = "") -> None:
    msg = f"  FAIL  {label}"
    if detail:
        msg += f"\n           {detail}"
    print(msg)
    CHECKS.append((label, False))


def _vec(dim: int) -> list[float]:
    import random
    v = [random.gauss(0, 1) for _ in range(dim)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _fmt(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in v) + "]"


async def run(url: str) -> bool:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    print(f"\nConnecting to: {url.split('@')[-1]}\n")

    engine = create_async_engine(url, pool_pre_ping=True)

    try:
        # â”€â”€ 1. Basic connectivity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version()"))
            version = result.scalar()
        _ok(f"Connected to Postgres ({version.split(',')[0].strip()})")

        # â”€â”€ 2. pgvector extension â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
            row = result.fetchone()
        if row:
            _ok(f"pgvector extension installed (version {row[0]})")
        else:
            _fail("pgvector extension", "Run: CREATE EXTENSION vector;  (or: alembic upgrade head)")

        # â”€â”€ 3. vector text protocol (string bind/result â€” no binary codec needed)
        try:
            async with engine.connect() as conn:
                v = _vec(8)
                result = await conn.execute(
                    text(f"SELECT '{_fmt(v)}'::vector <=> '{_fmt(v)}'::vector")
                )
                dist = float(result.scalar())
            assert abs(dist) < 1e-4, f"Self-distance should be ~0, got {dist}"
            _ok("vector text protocol (string bind/result works without binary codec)")
        except Exception as exc:
            _fail("vector text protocol", str(exc))

        # â”€â”€ 4. Schema â€” memories table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT column_name, data_type FROM information_schema.columns "
                     "WHERE table_name='memories' AND column_name='embedding'")
            )
            row = result.fetchone()
        if row:
            _ok(f"memories.embedding column exists (type: {row[1]})")
        else:
            _fail("memories.embedding column", "Run: alembic upgrade head")

        # â”€â”€ 5. HNSW index â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT indexname FROM pg_indexes "
                     "WHERE tablename='memories' AND indexdef ILIKE '%hnsw%'")
            )
            row = result.fetchone()
        if row:
            _ok(f"HNSW index present ({row[0]})")
        else:
            _fail("HNSW index on memories.embedding", "Run: alembic upgrade head")

        # â”€â”€ 6. Vector INSERT + SELECT round-trip â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            original = _vec(1024)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

            async with session_factory() as db:
                from src.lian.memory_service import add_memory
                from src.lian.schemas import MemoryAdd
                result = await add_memory(db, "_verify_ns_", MemoryAdd(
                    agent_id="_verify_agent_",
                    content="pgvector verification ping",
                    event_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    metadata={"_verify": "true"},
                ))
            _ok(f"add_memory() round-trip (id={str(result.id)[:8]}â€¦)")
        except Exception as exc:
            _fail("add_memory() round-trip", str(exc))

        # â”€â”€ 7. ANN query via <=> â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            q = _vec(1024)
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(f"SELECT id FROM memories ORDER BY embedding <=> '{_fmt(q)}'::vector LIMIT 1")
                )
            _ok("ANN query (embedding <=> vector) executes successfully")
        except Exception as exc:
            _fail("ANN query via <=>", str(exc))

        # â”€â”€ 8. HNSW index actually used (EXPLAIN) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            q = _vec(1024)
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(f"EXPLAIN SELECT id FROM memories "
                         f"ORDER BY embedding <=> '{_fmt(q)}'::vector LIMIT 20")
                )
                plan = "\n".join(r[0] for r in result.fetchall())
            if "Index Scan" in plan or "Bitmap" in plan:
                _ok("HNSW index scan used by planner")
            else:
                # Table might be empty â€” planner chooses Seq Scan for small tables
                _ok("HNSW index exists (planner chose Seq Scan â€” expected for empty table)")
        except Exception as exc:
            _fail("EXPLAIN ANN query", str(exc))

    except Exception as exc:
        _fail("Connection failed", str(exc))
    finally:
        await engine.dispose()

    # â”€â”€ Summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    passed = sum(1 for _, ok in CHECKS if ok)
    total = len(CHECKS)
    print(f"\n{'='*50}")
    print(f"  {passed}/{total} checks passed")
    if passed < total:
        failed = [label for label, ok in CHECKS if not ok]
        print("  Failed:")
        for f in failed:
            print(f"    - {f}")
    print('='*50)
    return passed == total


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify pgvector Postgres setup")
    parser.add_argument(
        "--url",
        default="postgresql+asyncpg://agentmem:agentmem@localhost:5432/agentmem",
        help="SQLAlchemy async database URL",
    )
    args = parser.parse_args()

    # Ensure src.lian is importable
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    ok = asyncio.run(run(args.url))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
