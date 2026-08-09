"""Adversarial regressions for recall semantics, evidence, and cache safety."""
from __future__ import annotations

import asyncio
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.lians.api.routes_admin import assign_barrier_group
from src.lians.cache import get_agent_cache_generation
from src.lians.cache import RecallCacheInvalidationError
from src.lians.cache_invalidation import (
    flush_pending_recall_invalidations,
    pending_recall_invalidations,
)
from src.lians.memory_service import (
    IdempotencyMemoryErasedError,
    _recall_receipt,
    add_memory,
    add_memory_idempotent,
    apply_supersession_action,
    assemble_context,
    recall_memories,
)
from src.lians.models import AgentBarrierGroup, DurableJob, IdempotencyKey, Memory
from src.lians.schemas import (
    BarrierGroupAssign,
    ContextRequest,
    MemoryAdd,
    RecallRequest,
    SupersessionAction,
)
from src.lians.session_cache import (
    get_scoring_pack,
    get_working_set,
    set_scoring_pack,
    set_working_set,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_effective_legacy_barrier_is_resolved_before_all_caches(db):
    namespace = "hardening-barrier-cache"
    agent_id = "analyst"
    secret = await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id=agent_id,
            content="Desk B confidential lending exposure",
            event_time=_now() - timedelta(minutes=1),
        ),
        barrier_override="desk-b",
    )

    request = RecallRequest(
        agent_id=agent_id,
        query="confidential lending exposure",
        k=10,
        mode="fast",
    )
    unscoped = await recall_memories(db, namespace, request)
    assert secret.id in {memory.id for memory in unscoped.memories}

    # Simulate a legacy assignment arriving after an unscoped Redis and
    # in-process working-set hit already exist. Even without relying on the
    # admin invalidator, the restricted read must resolve its barrier first.
    db.add(AgentBarrierGroup(
        namespace=namespace,
        agent_id=agent_id,
        group_name="desk-a",
    ))
    await db.commit()
    restricted = await recall_memories(db, namespace, request)
    assert secret.id not in {memory.id for memory in restricted.memories}


@pytest.mark.asyncio
async def test_present_keyed_and_semantic_recall_exclude_future_events(db):
    namespace = "hardening-future"
    future = await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id="agent",
            content="ACME lending limit will become 50 million",
            event_time=_now() + timedelta(days=180),
            metadata={"entity": "ACME", "metric": "lending_limit"},
        ),
    )
    keyed = await recall_memories(
        db,
        namespace,
        RecallRequest(
            agent_id="agent",
            query="ACME lending limit",
            filters={"entity": "ACME", "metric": "lending_limit"},
            k=10,
        ),
    )
    semantic = await recall_memories(
        db,
        namespace,
        RecallRequest(agent_id="agent", query="ACME lending limit", k=10),
    )
    assert future.id not in {memory.id for memory in keyed.memories}
    assert future.id not in {memory.id for memory in semantic.memories}
    generation = await get_agent_cache_generation(namespace, "agent")
    assert generation is not None
    assert get_working_set(namespace, "agent", generation) is None


@pytest.mark.asyncio
async def test_scheduled_supersession_keeps_predecessor_visible_until_activation(db):
    namespace = "hardening-scheduled-transition"
    reference = _now()
    metadata = {"entity": "ACME", "metric": "lending_limit"}
    current = await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id="agent",
            content="ACME lending limit is 10 million",
            event_time=reference - timedelta(days=1),
            metadata=metadata,
        ),
    )
    future = await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id="agent",
            content="ACME lending limit will be 20 million",
            event_time=reference + timedelta(days=30),
            metadata=metadata,
        ),
    )

    keyed = await recall_memories(
        db,
        namespace,
        RecallRequest(
            agent_id="agent",
            query="ACME lending limit",
            filters=metadata,
            k=10,
        ),
    )
    semantic = await recall_memories(
        db,
        namespace,
        RecallRequest(agent_id="agent", query="ACME lending limit", k=10),
    )
    assert current.id in {memory.id for memory in keyed.memories}
    assert current.id in {memory.id for memory in semantic.memories}
    assert future.id not in {memory.id for memory in keyed.memories}
    assert future.id not in {memory.id for memory in semantic.memories}


@pytest.mark.asyncio
async def test_recall_receipt_changes_with_public_score_or_breakdown(db):
    namespace = "hardening-receipt"
    await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id="agent",
            content="The lending review requires two approvers",
            event_time=_now() - timedelta(days=1),
        ),
    )
    request = RecallRequest(agent_id="agent", query="lending approvers", k=5)
    result = await recall_memories(db, namespace, request)
    reference = datetime.fromisoformat(result.receipt["reference_time"])
    baseline, _coverage, _payload = _recall_receipt(
        request,
        dict(result.receipt["policy"]),
        deepcopy(result.memories),
        reference_time=reference,
        retrieval_degraded=result.retrieval_degraded,
    )
    assert baseline == result.receipt_sha256

    changed = deepcopy(result.memories)
    changed[0].score = round(max(0.0, float(changed[0].score or 0.0) - 0.1), 6)
    changed[0].score_breakdown = {
        **dict(changed[0].score_breakdown or {}),
        "final_score": changed[0].score,
        "ranking_stages": [{"stage": "adversarial-change"}],
    }
    changed_sha, _coverage, _payload = _recall_receipt(
        request,
        dict(result.receipt["policy"]),
        changed,
        reference_time=reference,
        retrieval_degraded=result.retrieval_degraded,
    )
    assert changed_sha != baseline


@pytest.mark.asyncio
async def test_context_neighbors_are_temporally_gated_and_receipt_bound(db):
    namespace = "hardening-context-temporal"
    agent_id = "agent"
    base = _now() - timedelta(minutes=20)
    cutoff = _now() + timedelta(minutes=1)
    rows = []
    for offset, content in (
        (0, "valid context before"),
        (1, "not yet valid context"),
        (2, "target lending decision"),
        (3, "late ingested context"),
        (4, "expired context"),
        (5, "valid context after"),
    ):
        rows.append(await add_memory(
            db,
            namespace,
            MemoryAdd(
                agent_id=agent_id,
                content=content,
                event_time=base + timedelta(minutes=offset),
            ),
        ))

    not_yet = await db.get(Memory, rows[1].id)
    late = await db.get(Memory, rows[3].id)
    expired = await db.get(Memory, rows[4].id)
    not_yet.valid_from = cutoff + timedelta(days=1)
    late.ingestion_time = cutoff + timedelta(days=1)
    expired.valid_to = cutoff - timedelta(seconds=1)
    await db.commit()

    request = RecallRequest(
        agent_id=agent_id,
        query="target lending decision",
        k=10,
        as_of=cutoff,
        include_context=True,
    )
    result = await recall_memories(db, namespace, request)
    target = next(memory for memory in result.memories if memory.id == rows[2].id)
    assert target.context_before_id == rows[0].id
    assert target.context_after_id == rows[5].id
    attached_ids = {
        target.context_before_id,
        target.context_before_2_id,
        target.context_after_id,
        target.context_after_2_id,
    }
    assert rows[1].id not in attached_ids
    assert rows[3].id not in attached_ids
    assert rows[4].id not in attached_ids
    assert result.receipt["results"][0].get("context_neighbors") is not None

    baseline = result.receipt_sha256
    changed = deepcopy(result.memories)
    changed_target = next(memory for memory in changed if memory.id == rows[2].id)
    changed_target.context_after = "tampered neighbor plaintext"
    changed_sha, _coverage, _payload = _recall_receipt(
        request,
        dict(result.receipt["policy"]),
        changed,
        reference_time=datetime.fromisoformat(result.receipt["reference_time"]),
        retrieval_degraded=result.retrieval_degraded,
    )
    assert changed_sha != baseline


@pytest.mark.asyncio
async def test_context_reassignment_rebuilds_neighbors_under_new_barrier(
    db,
    monkeypatch,
):
    namespace = "hardening-context-reassignment"
    agent_id = "agent"
    base = _now() - timedelta(minutes=10)
    await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id=agent_id,
            content="public lending target",
            event_time=base + timedelta(minutes=2),
        ),
    )
    await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id=agent_id,
            content="desk A secret neighbor plaintext",
            event_time=base + timedelta(minutes=1),
        ),
        barrier_override="desk-a",
    )
    db.add(AgentBarrierGroup(
        namespace=namespace,
        agent_id=agent_id,
        group_name="desk-a",
    ))
    await db.commit()

    import src.lians.memory_service as service
    original_recall = service.recall_memories

    async def recall_then_reassign(*args, **kwargs):
        recalled = await original_recall(*args, **kwargs)
        assignment = await db.get(AgentBarrierGroup, (namespace, agent_id))
        assignment.group_name = "desk-b"
        await db.commit()
        return recalled

    monkeypatch.setattr(service, "recall_memories", recall_then_reassign)
    result = await assemble_context(
        db,
        namespace,
        ContextRequest(
            agent_id=agent_id,
            query="public lending target",
            k=10,
            max_tokens=1000,
        ),
    )
    assert "desk A secret neighbor plaintext" not in result.context
    assert all(
        "desk A secret neighbor plaintext" not in (memory.context_before or "")
        and "desk A secret neighbor plaintext" not in (memory.context_after or "")
        for memory in result.memories
    )


def test_replacing_working_set_discards_derived_scoring_pack():
    namespace = "hardening-pack-lifecycle"
    agent_id = "agent"
    generation = "generation-1"
    set_working_set(namespace, agent_id, [SimpleNamespace(id="a")], generation)
    pack = SimpleNamespace(contents=["bounded plaintext"])
    set_scoring_pack(namespace, agent_id, pack)
    assert get_scoring_pack(namespace, agent_id) is pack
    set_working_set(namespace, agent_id, [SimpleNamespace(id="b")], generation)
    assert get_scoring_pack(namespace, agent_id) is None


@pytest.mark.asyncio
async def test_timed_out_reranker_cannot_mutate_evidence_or_queue_more_work(
    monkeypatch,
):
    import src.lians.ranking as ranking

    release = threading.Event()
    calls = 0

    class SlowReranker:
        def predict(self, pairs, show_progress_bar=False):
            nonlocal calls
            calls += 1
            release.wait(timeout=2)
            return list(reversed(range(len(pairs))))

    now = _now()
    scored = []
    for index in range(2):
        memory = SimpleNamespace(
            id=uuid4(),
            event_time=now - timedelta(minutes=index),
            ingestion_time=now,
            _score_breakdown={
                "final_score": 0.9 - index * 0.1,
                "ranking_stages": [],
                "reasons": [],
            },
        )
        scored.append((memory, 0.9 - index * 0.1, f"candidate {index}"))

    monkeypatch.setattr(ranking, "RERANKER_MODEL", "test/slow")
    monkeypatch.setattr(ranking, "RERANKER_TIMEOUT_MS", 10.0)
    monkeypatch.setattr(ranking, "_get_reranker", lambda: SlowReranker())
    monkeypatch.setattr(ranking, "_reranker_slots", asyncio.Semaphore(1))
    before = deepcopy([entry[0]._score_breakdown for entry in scored])
    first = await ranking.rerank_cross_encoder_async("query", scored, 2)
    second = await ranking.rerank_cross_encoder_async("query", scored, 2)
    assert [entry[0].id for entry in first] == [entry[0].id for entry in scored]
    assert [entry[0].id for entry in second] == [entry[0].id for entry in scored]
    assert calls == 1
    assert [entry[0]._score_breakdown for entry in scored] == before
    release.set()
    await asyncio.sleep(0.05)
    assert [entry[0]._score_breakdown for entry in scored] == before


@pytest.mark.asyncio
async def test_failed_add_invalidation_leaves_cross_worker_barrier(db, monkeypatch):
    namespace = "hardening-mutation-barrier"
    await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id="agent",
            content="Alpha lending baseline",
            event_time=_now() - timedelta(days=2),
        ),
    )
    request = RecallRequest(agent_id="agent", query="Alpha lending", k=10)
    cached = await recall_memories(db, namespace, request)
    assert any(memory.content == "Alpha lending baseline" for memory in cached.memories)

    async def fail_invalidation(*_args, **_kwargs):
        raise RecallCacheInvalidationError("shared cache unavailable")

    import src.lians.cache_invalidation as invalidation
    monkeypatch.setattr(invalidation, "invalidate_agent", fail_invalidation)
    with pytest.raises(RecallCacheInvalidationError):
        await add_memory(
            db,
            namespace,
            MemoryAdd(
                agent_id="agent",
                content="Alpha lending supplemental evidence",
                event_time=_now() - timedelta(days=1),
                metadata={"document": "supplement"},
            ),
        )

    pending = await pending_recall_invalidations(
        db, namespace, agent_id="agent", operation="memory.add"
    )
    assert pending
    assert pending[0].last_error == "RecallCacheInvalidationError"
    fresh = await recall_memories(db, namespace, request)
    assert any(
        memory.content == "Alpha lending supplemental evidence"
        for memory in fresh.memories
    )


@pytest.mark.asyncio
async def test_idempotent_add_repairs_post_commit_invalidation_without_duplicate(
    db,
    monkeypatch,
):
    namespace = "hardening-idempotent-invalidation"
    idempotency_key = "stable-client-retry-key"
    request = MemoryAdd(
        agent_id="agent",
        content="ACME lending package passed final review",
        event_time=_now() - timedelta(minutes=1),
    )

    async def fail_invalidation(*_args, **_kwargs):
        raise RecallCacheInvalidationError("shared cache unavailable")

    import src.lians.cache_invalidation as invalidation
    original = invalidation.invalidate_agent
    monkeypatch.setattr(invalidation, "invalidate_agent", fail_invalidation)
    with pytest.raises(RecallCacheInvalidationError):
        await add_memory_idempotent(
            db,
            namespace,
            request,
            idempotency_key,
        )

    first_rows = list((await db.execute(
        select(Memory).where(
            Memory.namespace == namespace,
            Memory.agent_id == "agent",
        )
    )).scalars().all())
    assert len(first_rows) == 1
    mapping = await db.get(IdempotencyKey, (idempotency_key, namespace))
    assert mapping is not None
    assert mapping.memory_id == first_rows[0].id
    assert await pending_recall_invalidations(
        db,
        namespace,
        agent_id="agent",
        operation="memory.add",
    )

    monkeypatch.setattr(invalidation, "invalidate_agent", original)
    retried = await add_memory_idempotent(
        db,
        namespace,
        request,
        idempotency_key,
    )
    assert retried.id == first_rows[0].id
    final_rows = list((await db.execute(
        select(Memory).where(
            Memory.namespace == namespace,
            Memory.agent_id == "agent",
        )
    )).scalars().all())
    assert [memory.id for memory in final_rows] == [first_rows[0].id]
    assert not await pending_recall_invalidations(
        db,
        namespace,
        agent_id="agent",
        operation="memory.add",
    )


@pytest.mark.asyncio
async def test_idempotent_add_fails_closed_when_original_memory_was_erased(db):
    namespace = "hardening-idempotent-erased"
    idempotency_key = "private-client-retry-key"
    content = "Confidential lending decision"
    request = MemoryAdd(
        agent_id="agent",
        content=content,
        event_time=_now() - timedelta(minutes=1),
    )
    created = await add_memory_idempotent(
        db,
        namespace,
        request,
        idempotency_key,
    )
    memory = await db.get(Memory, created.id)
    memory.content_encrypted = None
    memory.erased_at = _now()
    await db.commit()

    with pytest.raises(IdempotencyMemoryErasedError) as exc_info:
        await add_memory_idempotent(
            db,
            namespace,
            request,
            idempotency_key,
        )

    assert idempotency_key not in str(exc_info.value)
    assert content not in str(exc_info.value)


@pytest.mark.asyncio
async def test_idempotent_add_race_winner_fails_closed_when_erased(monkeypatch):
    erased_memory = SimpleNamespace(
        id=uuid4(),
        erased_at=_now(),
    )
    mapping = SimpleNamespace(memory_id=erased_memory.id)

    class RaceDatabase:
        def __init__(self):
            self.mapping_reads = 0
            self.rolled_back = False

        async def get(self, model, _key):
            if model is IdempotencyKey:
                self.mapping_reads += 1
                return None if self.mapping_reads == 1 else mapping
            if model is Memory:
                return erased_memory
            raise AssertionError(f"unexpected model: {model}")

        async def rollback(self):
            self.rolled_back = True

    async def lose_insert_race(*_args, **_kwargs):
        raise IntegrityError("INSERT", {}, RuntimeError("duplicate mapping"))

    database = RaceDatabase()
    monkeypatch.setattr("src.lians.memory_service.add_memory", lose_insert_race)

    with pytest.raises(IdempotencyMemoryErasedError):
        await add_memory_idempotent(
            database,
            "hardening-idempotent-race",
            MemoryAdd(
                agent_id="agent",
                content="A retry must not resurrect this record",
                event_time=_now(),
            ),
            "stable-race-key",
        )

    assert database.rolled_back is True


@pytest.mark.asyncio
async def test_working_set_is_bound_to_shared_cross_worker_generation(db):
    namespace = "hardening-working-set-generation"
    agent_id = "agent"
    await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id=agent_id,
            content="Lending control baseline",
            event_time=_now() - timedelta(days=2),
        ),
    )
    request = RecallRequest(agent_id=agent_id, query="lending control", k=10)
    await recall_memories(db, namespace, request)
    old_generation = await get_agent_cache_generation(namespace, agent_id)
    assert old_generation is not None
    stale_facts = get_working_set(namespace, agent_id, old_generation)
    assert stale_facts is not None

    await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id=agent_id,
            content="Lending control supplemental evidence",
            event_time=_now() - timedelta(days=1),
            metadata={"document": "supplement"},
        ),
    )
    new_generation = await get_agent_cache_generation(namespace, agent_id)
    assert new_generation is not None and new_generation != old_generation

    # Recreate what a different worker would still hold in process. The next
    # recall must reject it because its shared generation is old.
    set_working_set(namespace, agent_id, stale_facts, old_generation)
    fresh = await recall_memories(db, namespace, request)
    assert any(
        memory.content == "Lending control supplemental evidence"
        for memory in fresh.memories
    )


@pytest.mark.asyncio
async def test_supersession_rejection_uses_lifecycle_specific_invalidation(db):
    namespace = "hardening-supersession-lifecycle"
    agent_id = "agent"
    metadata = {"ticker": "ACME", "metric": "lending_limit"}
    old = await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id=agent_id,
            content="ACME lending limit is 10 million",
            event_time=_now() - timedelta(days=3),
            metadata=metadata,
        ),
    )
    await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id=agent_id,
            content="ACME lending limit is 20 million",
            event_time=_now() - timedelta(days=2),
            metadata=metadata,
        ),
    )
    await apply_supersession_action(
        db,
        namespace,
        old.id,
        SupersessionAction(action="reject"),
    )

    await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id=agent_id,
            content="ACME lending limit is 30 million",
            event_time=_now() - timedelta(days=1),
            metadata=metadata,
        ),
    )
    await apply_supersession_action(
        db,
        namespace,
        old.id,
        SupersessionAction(action="reject"),
    )

    jobs = list((await db.execute(
        select(DurableJob).where(
            DurableJob.namespace == namespace,
            DurableJob.kind == "recall_cache.invalidate",
        )
    )).scalars().all())
    reject_jobs = [
        job for job in jobs
        if (job.payload or {}).get("operation") == "supersession.reject"
    ]
    assert len(reject_jobs) == 2
    assert len({job.payload["operation_ref"] for job in reject_jobs}) == 2
    assert all(job.status == "completed" for job in reject_jobs)


@pytest.mark.asyncio
async def test_barrier_admin_mutation_is_durable_when_redis_fails(db, monkeypatch):
    namespace = "hardening-admin-barrier"

    async def fail_invalidation(*_args, **_kwargs):
        raise RecallCacheInvalidationError("shared cache unavailable")

    import src.lians.cache_invalidation as invalidation
    original = invalidation.invalidate_agent
    monkeypatch.setattr(invalidation, "invalidate_agent", fail_invalidation)
    with pytest.raises(RecallCacheInvalidationError):
        await assign_barrier_group(
            BarrierGroupAssign(agent_id="agent", group_name="desk-a"),
            namespace,
            None,
            db,
        )
    assert await db.get(AgentBarrierGroup, (namespace, "agent")) is not None
    assert await pending_recall_invalidations(
        db, namespace, agent_id="agent", operation="admin.barrier.assign"
    )

    monkeypatch.setattr(invalidation, "invalidate_agent", original)
    assert await flush_pending_recall_invalidations(
        db, namespace, agent_id="agent", operation="admin.barrier.assign"
    ) == 1
    assert not await pending_recall_invalidations(
        db, namespace, agent_id="agent", operation="admin.barrier.assign"
    )
