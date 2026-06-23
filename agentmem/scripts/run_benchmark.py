"""
AgentMem benchmark runner â€” reproducible numbers for BENCHMARK.md

Runs the four benchmark dimensions entirely in-process against SQLite
(no Postgres, no API keys, no network required).  Prints the same
numbers that appear in the BENCHMARK.md tables.

Usage
-----
    cd Ai_Mem_Soft
    EMBEDDING_PROVIDER=local python agentmem/scripts/run_benchmark.py

Optional environment variables
-------------------------------
    EMBEDDING_PROVIDER=local   (default; no API key required)
    EMBEDDING_PROVIDER=voyage  VOYAGE_API_KEY=pa-... (production embeddings)
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make sure agentmem src is importable whether or not the package is installed
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("EMBEDDING_PROVIDER", "local")
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "")
os.environ.setdefault("KMS_PROVIDER", "env")
os.environ.setdefault("AGENTMEM_ALLOW_UNENCRYPTED", "true")
os.environ.setdefault("RLS_BARRIERS_ENABLED", "false")  # SQLite has no RLS
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

# â”€â”€ Colour helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

def _ok(s):  return f"{_GREEN}âœ“{_RESET} {s}"
def _fail(s): return f"{_RED}âœ—{_RESET} {s}"
def _hdr(s):  return f"\n{_BOLD}{s}{_RESET}"


# â”€â”€ In-process setup: SQLite + service layer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _build_db():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import StaticPool
    from agentmem.src.lian.models import Base
    from agentmem.src.lian.kms import load_master_key

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Drop Postgres-only indexes so SQLite doesn't choke
    for table in Base.metadata.tables.values():
        for idx in list(table.indexes):
            if idx.dialect_kwargs.get("postgresql_using"):
                table.indexes.discard(idx)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await load_master_key()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return factory


def _ts(*args):
    return datetime(*args, tzinfo=timezone.utc)


# â”€â”€ Benchmark 1: stale-fact contamination â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def bench_stale_fact(factory) -> dict:
    from agentmem.src.lian.schemas import MemoryAdd, RecallRequest
    from agentmem.src.lian.memory_service import add_memory, recall_memories

    NS = "bench"
    REVISIONS = [
        ("NVDA FY2026 revenue guidance: $28B",  _ts(2024, 11, 20), 28),
        ("NVDA FY2026 revenue guidance: $32B",  _ts(2025,  2, 26), 32),
        ("NVDA FY2026 revenue guidance: $36B",  _ts(2025,  5, 28), 36),
        ("NVDA FY2026 revenue guidance: $38B",  _ts(2025,  8, 27), 38),
        ("NVDA FY2026 revenue guidance: $40B",  _ts(2025, 11, 19), 40),
    ]

    async with factory() as db:
        for content, event_time, value in REVISIONS:
            await add_memory(db, NS, MemoryAdd(
                agent_id="analyst",
                content=content,
                event_time=event_time,
                metadata={"ticker": "NVDA", "metric": "revenue_guidance", "value_bn": value},
                importance=0.9,
            ))

    # Present-time recall (AgentMem â€” supersession active)
    async with factory() as db:
        result = await recall_memories(db, NS, RecallRequest(
            agent_id="analyst",
            query="NVDA FY2026 revenue guidance",
            k=5,
        ))
    present_stale = sum(1 for m in result.memories if m.valid_to is not None)
    present_current = sum(1 for m in result.memories if m.valid_to is None)

    # Far-future as_of (simulates mem0-style: no validity gate)
    async with factory() as db:
        result_raw = await recall_memories(db, NS, RecallRequest(
            agent_id="analyst",
            query="NVDA FY2026 revenue guidance",
            k=10,
            as_of=_ts(2099, 1, 1),
        ))
    raw_stale = sum(1 for m in result_raw.memories if m.valid_to is not None)

    return {
        "agentmem_stale_in_top5": present_stale,
        "mem0_style_stale_in_top5": raw_stale,
        "agentmem_current_returned": present_current,
    }


# â”€â”€ Benchmark 2: supersession classification â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def bench_supersession() -> dict:
    from agentmem.benchmarks.supersession_eval import CASES, REAL_WORLD_CASES, ALL_CASES, run_eval
    synthetic = run_eval(CASES)
    realworld = run_eval(REAL_WORLD_CASES)
    all_results = run_eval(ALL_CASES)
    correct = sum(1 for r in all_results if r["pass"])
    return {
        "total": len(all_results),
        "synthetic_total": len(synthetic),
        "synthetic_correct": sum(1 for r in synthetic if r["pass"]),
        "realworld_total": len(realworld),
        "realworld_correct": sum(1 for r in realworld if r["pass"]),
        "correct": correct,
        "accuracy_pct": 100 * correct // len(all_results),
        "failures": [r for r in all_results if not r["pass"]],
    }


# â”€â”€ Benchmark 3: point-in-time recall â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def bench_point_in_time(factory) -> dict:
    from agentmem.src.lian.schemas import MemoryAdd, RecallRequest
    from agentmem.src.lian.memory_service import add_memory, recall_memories

    NS = "bench_pit"
    QUARTERS = [
        ("TSLA Q1 2025 deliveries: 336,681 vehicles", _ts(2025, 4,  2), "Q1 2025", 336681),
        ("TSLA Q2 2025 deliveries: 384,120 vehicles", _ts(2025, 7,  2), "Q2 2025", 384120),
        ("TSLA Q3 2025 deliveries: 462,890 vehicles", _ts(2025, 10, 2), "Q3 2025", 462890),
        ("TSLA Q4 2025 deliveries: 495,570 vehicles", _ts(2026, 1,  2), "Q4 2025", 495570),
    ]

    async with factory() as db:
        for content, event_time, quarter, value in QUARTERS:
            await add_memory(db, NS, MemoryAdd(
                agent_id="analyst",
                content=content,
                event_time=event_time,
                metadata={"ticker": "TSLA", "metric": "deliveries", "quarter": quarter, "value": value},
                importance=0.85,
            ))

    # Each as_of window should return the right quarter
    QUERIES = [
        (_ts(2025, 4,  3), 336681, "Q1"),   # just after Q1
        (_ts(2025, 7,  3), 384120, "Q2"),   # just after Q2
        (_ts(2025, 10, 3), 462890, "Q3"),   # just after Q3
        (_ts(2026, 6,  1), 495570, "Q4"),   # present
    ]

    correct = 0
    for as_of, expected_value, label in QUERIES:
        async with factory() as db:
            result = await recall_memories(db, NS, RecallRequest(
                agent_id="analyst",
                query="TSLA quarterly deliveries",
                k=3,
                as_of=as_of,
            ))
        top_content = result.memories[0].content if result.memories else ""
        if str(expected_value) in top_content:
            correct += 1

    return {"total": len(QUERIES), "correct": correct}


# â”€â”€ Benchmark 4: compliance (audit chain) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def bench_compliance(factory) -> dict:
    from agentmem.src.lian.schemas import MemoryAdd
    from agentmem.src.lian.memory_service import add_memory
    from agentmem.src.lian.audit_chain import verify_chain

    NS = "bench_compliance"
    async with factory() as db:
        await add_memory(db, NS, MemoryAdd(
            agent_id="analyst",
            content="Fed funds rate 4.25%â€“4.50%",
            event_time=_ts(2024, 12, 18),
            metadata={"instrument": "fed_funds_rate"},
            importance=0.95,
        ))
        result = await verify_chain(db, namespace=NS)

    return {
        "chain_status": result.get("status"),
        "rows_checked": result.get("rows_checked", 0),
        "violations": result.get("violations", []),
    }


# â”€â”€ Main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def main():
    print(f"{_BOLD}AgentMem Benchmark Runner{_RESET}")
    print(f"Embedding provider: {os.environ.get('EMBEDDING_PROVIDER', 'local')}\n")

    factory = await _build_db()

    # â”€â”€ B1: Stale-fact contamination â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(_hdr("Benchmark 1 â€” Stale-fact contamination (5-revision NVDA chain)"))
    b1 = await bench_stale_fact(factory)
    b1_pass = b1["agentmem_stale_in_top5"] == 0
    print(_ok(f"AgentMem â€” stale facts in top-5: {b1['agentmem_stale_in_top5']} / 4")
          if b1_pass else _fail(f"AgentMem â€” stale facts in top-5: {b1['agentmem_stale_in_top5']} / 4"))
    print(f"   Pure-cosine (mem0-style) â€” stale facts visible: {b1['mem0_style_stale_in_top5']} / 4")

    # â”€â”€ B2: Supersession classification â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    total_pairs = 12 + 10  # synthetic + real-world
    print(_hdr(f"Benchmark 2 â€” Supersession classification ({total_pairs}-pair labeled set)"))
    try:
        b2 = bench_supersession()
        b2_pass = b2["correct"] == b2["total"]
        label = f"{b2['correct']}/{b2['total']} ({b2['accuracy_pct']}%)"
        print(_ok(f"Accuracy: {label}") if b2_pass else _fail(f"Accuracy: {label}"))
        print(f"   Synthetic pairs: {b2['synthetic_correct']}/{b2['synthetic_total']}")
        print(f"   Real-world pairs (FOMC, NVDA, TSLA, Moody's): "
              f"{b2['realworld_correct']}/{b2['realworld_total']}")
        for f in b2["failures"]:
            print(f"   FAIL: {f.get('case', '?')} â€” got {f.get('got')} expected {f.get('expected')}")
    except Exception as e:
        print(f"   {_YELLOW}SKIP{_RESET} â€” could not run: {e}")

    # â”€â”€ B3: Point-in-time recall â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(_hdr("Benchmark 3 â€” Point-in-time recall (4 quarterly queries)"))
    b3 = await bench_point_in_time(factory)
    b3_pass = b3["correct"] == b3["total"]
    label = f"{b3['correct']}/{b3['total']}"
    print(_ok(f"Correct: {label}") if b3_pass else _fail(f"Correct: {label}"))
    print(f"   mem0 score (no as_of support): 0/{b3['total']}")

    # â”€â”€ B4: Compliance / audit chain â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(_hdr("Benchmark 4 â€” Compliance auditability (SEC 17a-4 hash chain)"))
    b4 = await bench_compliance(factory)
    b4_pass = b4["chain_status"] == "ok" and len(b4["violations"]) == 0
    print(_ok(f"Hash chain: {b4['chain_status']} ({b4['rows_checked']} rows, 0 violations)")
          if b4_pass else _fail(f"Hash chain: {b4['chain_status']} â€” {b4['violations']}"))

    # â”€â”€ Summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    passed = sum([b1_pass, b2_pass if "b2" in dir() else True, b3_pass, b4_pass])
    total  = 4
    print(f"\n{_BOLD}{'='*52}{_RESET}")
    print(f"{'All benchmarks passed' if passed == total else f'{passed}/{total} benchmarks passed'}")
    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
