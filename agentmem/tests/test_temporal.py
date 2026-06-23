"""
Bitemporal correctness tests â€” THE critical test suite.
Property: recall(as_of=t) NEVER returns a fact outside its validity window.
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from src.lian.models import Memory
from src.lian.ranking import hybrid_recall
from src.lian.embeddings import get_embedding_provider


NS = "test-ns"
AGENT = "test-agent"

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 5, 1, tzinfo=timezone.utc)
T3 = datetime(2026, 7, 1, tzinfo=timezone.utc)


async def _add_raw_memory(db, content, event_time, valid_from, valid_to=None, meta=None):
    provider = get_embedding_provider()
    emb = await provider.embed_one(content)
    mem = Memory(
        namespace=NS,
        agent_id=AGENT,
        content_encrypted=content.encode(),
        subject_id=None,
        embedding=emb,
        metadata_=meta or {},
        event_time=event_time,
        ingestion_time=datetime.now(timezone.utc),
        valid_from=valid_from,
        valid_to=valid_to,
        importance=0.5,
        source="test",
        content_hash=f"hash-{content[:8]}",
    )
    db.add(mem)
    await db.flush()

    # Maintain live_facts for present-time recall (Change 1).
    # Only live memories (valid_to is None) are projected into live_facts;
    # superseded ones are intentionally omitted.
    if valid_to is None:
        from src.lian.current_facts import upsert_live_fact, compute_predicate_key
        predicate_key = compute_predicate_key(meta or {})
        await upsert_live_fact(db, mem, predicate_key)

    await db.commit()
    return mem


@pytest.mark.asyncio
async def test_as_of_returns_correct_snapshot(db):
    """recall(as_of=T1) returns only memories valid at T1."""
    # Memory valid T0â€“T2 (superseded at T2)
    old = await _add_raw_memory(db, "NVDA guidance $32B", T0, valid_from=T0, valid_to=T2)
    # Memory valid T2+ (the superseding memory)
    new = await _add_raw_memory(db, "NVDA guidance raised to $36B", T2, valid_from=T2)

    provider = get_embedding_provider()
    q_emb = await provider.embed_one("NVDA guidance")

    # At T1 (between T0 and T2), only the old memory should be visible
    results_t1 = await hybrid_recall(db, NS, AGENT, "NVDA guidance", q_emb, k=10, as_of=T1)
    ids_t1 = [m.id for m, _, _ in results_t1]
    assert old.id in ids_t1, "Old memory should be visible at T1"
    assert new.id not in ids_t1, "New memory must NOT be visible before T2"


@pytest.mark.asyncio
async def test_as_of_after_supersession(db):
    """recall(as_of=T3) returns only the new memory, not the superseded one."""
    old = await _add_raw_memory(db, "NVDA guidance $32B", T0, valid_from=T0, valid_to=T2)
    new = await _add_raw_memory(db, "NVDA guidance $36B", T2, valid_from=T2)

    provider = get_embedding_provider()
    q_emb = await provider.embed_one("NVDA guidance")

    results_t3 = await hybrid_recall(db, NS, AGENT, "NVDA guidance", q_emb, k=10, as_of=T3)
    ids_t3 = [m.id for m, _, _ in results_t3]
    assert new.id in ids_t3
    assert old.id not in ids_t3, "Superseded memory must NOT appear after its valid_to"


@pytest.mark.asyncio
async def test_present_time_favors_valid_memories(db):
    """Without as_of, currently-valid memories rank above superseded ones."""
    old = await _add_raw_memory(db, "NVDA guidance $32B", T0, valid_from=T0, valid_to=T2)
    new = await _add_raw_memory(db, "NVDA guidance $36B", T2, valid_from=T2)

    provider = get_embedding_provider()
    q_emb = await provider.embed_one("NVDA guidance")

    results = await hybrid_recall(db, NS, AGENT, "NVDA guidance", q_emb, k=5, as_of=None)
    assert results, "Should return results"
    top_id = results[0][0].id
    assert top_id == new.id, "Currently valid memory should rank first in present-time recall"


@pytest.mark.asyncio
async def test_event_time_boundary(db):
    """Memory with event_time > as_of must NOT appear."""
    future_mem = await _add_raw_memory(db, "NVDA guidance $40B", T3, valid_from=T3)

    provider = get_embedding_provider()
    q_emb = await provider.embed_one("NVDA guidance")

    results = await hybrid_recall(db, NS, AGENT, "NVDA guidance", q_emb, k=10, as_of=T2)
    ids = [m.id for m, _, _ in results]
    assert future_mem.id not in ids, "Future event must not appear in past recall"
