from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from src.lian.db import Base
from src.lian.memory_service import add_memory, recall_memories
from src.lian.schemas import MemoryAdd, RecallRequest


@dataclass(frozen=True)
class Case:
    query: str
    as_of: datetime
    expected_fragment: str
    description: str = ""


async def seed(db, ns: str, agent: str) -> None:
    """Load a realistic sequence of financial facts with supersessions."""
    t = lambda y, m, d: datetime(y, m, d, tzinfo=timezone.utc)

    facts = [
        # NVDA guidance: three revisions over the year
        MemoryAdd(agent_id=agent, content="NVDA FY guidance $32B",
                  event_time=t(2026, 1, 15),
                  metadata={"ticker": "NVDA", "metric": "guidance"}),
        MemoryAdd(agent_id=agent, content="NVDA FY guidance raised to $36B",
                  event_time=t(2026, 5, 10),
                  metadata={"ticker": "NVDA", "metric": "guidance"}),
        MemoryAdd(agent_id=agent, content="NVDA FY guidance raised to $40B",
                  event_time=t(2026, 9, 1),
                  metadata={"ticker": "NVDA", "metric": "guidance"}),

        # AAPL: separate metrics, no supersession between them
        MemoryAdd(agent_id=agent, content="AAPL gross margin 46%",
                  event_time=t(2026, 2, 1),
                  metadata={"ticker": "AAPL", "metric": "gross_margin"}),
        MemoryAdd(agent_id=agent, content="AAPL gross margin improved to 47.5%",
                  event_time=t(2026, 7, 1),
                  metadata={"ticker": "AAPL", "metric": "gross_margin"}),
        MemoryAdd(agent_id=agent, content="AAPL services revenue $26B",
                  event_time=t(2026, 2, 1),
                  metadata={"ticker": "AAPL", "metric": "services_revenue"}),

        # Credit rating â€” entity-keyed
        MemoryAdd(agent_id=agent, content="Moody's rates XYZ Corp Baa2",
                  event_time=t(2026, 3, 1),
                  metadata={"entity": "xyz_corp", "metric": "credit_rating"}),
        MemoryAdd(agent_id=agent, content="Moody's upgrades XYZ Corp to Baa1",
                  event_time=t(2026, 8, 15),
                  metadata={"entity": "xyz_corp", "metric": "credit_rating"}),
    ]

    for fact in facts:
        await add_memory(db, ns, fact)


CASES = [
    # --- NVDA guidance at each revision point ---
    Case("NVDA guidance", datetime(2026, 3, 1, tzinfo=timezone.utc),
         "$32B", "Before first revision"),
    Case("NVDA guidance", datetime(2026, 7, 1, tzinfo=timezone.utc),
         "$36B", "After first revision, before second"),
    Case("NVDA guidance", datetime(2026, 10, 1, tzinfo=timezone.utc),
         "$40B", "After final revision"),

    # --- AAPL metric isolation ---
    Case("AAPL gross margin", datetime(2026, 4, 1, tzinfo=timezone.utc),
         "46%", "AAPL margin before update"),
    Case("AAPL gross margin", datetime(2026, 8, 1, tzinfo=timezone.utc),
         "47.5%", "AAPL margin after update"),
    Case("AAPL services revenue", datetime(2026, 4, 1, tzinfo=timezone.utc),
         "$26B", "AAPL services â€” unaffected by margin update"),

    # --- Credit rating ---
    Case("XYZ Corp credit rating", datetime(2026, 5, 1, tzinfo=timezone.utc),
         "Baa2", "Before upgrade"),
    Case("XYZ Corp credit rating", datetime(2026, 10, 1, tzinfo=timezone.utc),
         "Baa1", "After upgrade"),
]


async def main() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Drop PG-only indexes so SQLite doesn't choke
    pg_indexes = [
        idx
        for table in Base.metadata.tables.values()
        for idx in table.indexes
        if idx.dialect_kwargs.get("postgresql_using") is not None
    ]
    for idx in pg_indexes:
        idx.table.indexes.discard(idx)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    ns, agent = "bench", "research"

    async with session_factory() as db:
        await seed(db, ns, agent)

    hits = 0
    print(f"{'as_of':<12} {'description':<42} {'expected':<12} {'top_content':<50} {'ok'}")
    print("-" * 130)
    async with session_factory() as db:
        for case in CASES:
            result = await recall_memories(
                db, ns,
                RecallRequest(agent_id=agent, query=case.query, as_of=case.as_of, k=1),
            )
            top = result.memories[0].content if result.memories else ""
            ok = case.expected_fragment in (top or "")
            hits += int(ok)
            marker = "OK" if ok else "FAIL"
            print(f"{marker} {case.as_of.date()!s:<12} {case.description:<42} "
                  f"{case.expected_fragment:<12} {(top or '')[:48]:<50} {ok}")

    total = len(CASES)
    print()
    print(f"point_in_time_accuracy = {hits / total:.2f}  ({hits}/{total})")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
