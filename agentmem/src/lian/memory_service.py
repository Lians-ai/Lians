"""
Core memory service: add, recall, recall(as_of) — used by API routes.

Performance roadmap changes wired here:
  Change 1  — recall queries live_facts (compact read model), not memories.
  Change 2  — keyed-vs-semantic router: keyed queries skip embed + ANN entirely.
  Change 3  — supersession fast path (keyed deterministic); async LLM worker.
  Change 6  — DEK cache: subject keys unwrapped once, cached in-process.
  Change 7  — session cache: working set prefetched and served from memory.
  Change 10 — recall instrumented as sub-spans: embed/search/decrypt/assemble.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, and_, update, text, cast, Float
from sqlalchemy.ext.asyncio import AsyncSession

import time as _time

from .models import Memory, EventLog, SubjectKey, AgentBarrierGroup, NamespacePolicy
from .audit_chain import chain_log
from .telemetry import tracer
from .metrics import record_write, observe_add, record_recall, observe_recall, record_erase
from .schemas import (
    MemoryAdd, MemoryOut, RecallRequest, RecallResult,
    MemoryBatchAdd, MemoryBatchResult,
    SupersessionReviewItem, SupersessionReviewResult,
    SupersessionAction, SupersessionActionResult,
    RetentionPolicyIn, RetentionPolicyOut, RetentionPruneResult,
    LineageNode, LineageEdge, MemoryLineageResult,
)
from .embeddings import get_embedding_provider
from .crypto import encrypt_content, decrypt_content, unwrap_subject_key
from .pii import get_or_create_subject_key, destroy_subject_key
from .supersession import run_supersession
from .ranking import hybrid_recall
from .cache import get_cached_recall, set_cached_recall, invalidate_agent
from .config import get_settings
from .current_facts import compute_predicate_key, upsert_live_fact, remove_live_facts, keyed_lookup
from .dek_cache import get_cached_dek, cache_dek, evict_dek
from .session_cache import get_working_set, set_working_set, invalidate_working_set

_IMPORTANCE_RECENCY_HALF_LIFE_DAYS = 90.0


def _write_lock_keys(namespace: str, agent_id: str) -> tuple[int, int]:
    h = hashlib.sha256(f"{namespace}\x00{agent_id}".encode()).digest()
    return (
        int.from_bytes(h[:4], "big", signed=True),
        int.from_bytes(h[4:8], "big", signed=True),
    )


_write_locks: dict[tuple[int, str, str], asyncio.Lock] = {}


async def _get_in_process_lock(namespace: str, agent_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (id(loop), namespace, agent_id)
    if key not in _write_locks:
        _write_locks[key] = asyncio.Lock()
    return _write_locks[key]


async def _acquire_pg_advisory_lock(db: AsyncSession, namespace: str, agent_id: str) -> None:
    try:
        engine = db.sync_session.get_bind()
        if engine.dialect.name != "postgresql":
            return
    except Exception:
        return
    k1, k2 = _write_lock_keys(namespace, agent_id)
    await db.execute(text("SELECT pg_advisory_xact_lock(:k1, :k2)"), {"k1": k1, "k2": k2})


def _compute_importance(event_time: datetime, caller_salience: float) -> float:
    now = datetime.now(timezone.utc)
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    age_days = (now - event_time).total_seconds() / 86400
    recency = math.exp(-math.log(2) * age_days / _IMPORTANCE_RECENCY_HALF_LIFE_DAYS)
    return round(0.4 * recency + 0.6 * caller_salience, 4)


async def _get_barrier_group(db: AsyncSession, namespace: str, agent_id: str) -> Optional[str]:
    stmt = select(AgentBarrierGroup).where(
        and_(AgentBarrierGroup.namespace == namespace, AgentBarrierGroup.agent_id == agent_id)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    return row.group_name if row else None


async def _resolve_subject_key(
    db: AsyncSession,
    subject_id: str,
    namespace: str,
) -> bytes:
    """Return plaintext DEK for subject, using cache (Change 6)."""
    cached = get_cached_dek(subject_id)
    if cached is not None:
        return cached
    key = await get_or_create_subject_key(db, subject_id, namespace)
    cache_dek(subject_id, key)
    return key


async def _load_namespace_subject_keys(db: AsyncSession, namespace: str) -> dict[str, bytes]:
    """Load all active subject keys for a namespace, using DEK cache (Change 6)."""
    stmt = select(SubjectKey).where(
        and_(
            SubjectKey.namespace == namespace,
            SubjectKey.destroyed_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    keys: dict[str, bytes] = {}
    for row in rows:
        cached = get_cached_dek(row.subject_id)
        if cached is not None:
            keys[row.subject_id] = cached
            continue
        try:
            plaintext = unwrap_subject_key(bytes(row.enc_key))
            cache_dek(row.subject_id, plaintext)
            keys[row.subject_id] = plaintext
        except Exception:
            pass
    return keys


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _memory_to_out(mem: Memory, content: Optional[str]) -> MemoryOut:
    return MemoryOut(
        id=mem.id,
        namespace=mem.namespace,
        agent_id=mem.agent_id,
        content=content,
        subject_id=mem.subject_id,
        event_time=mem.event_time,
        ingestion_time=mem.ingestion_time,
        valid_from=mem.valid_from,
        valid_to=mem.valid_to,
        superseded_by=mem.superseded_by,
        supersession_confidence=mem.supersession_confidence,
        barrier_group=mem.barrier_group,
        importance=mem.importance,
        source=mem.source,
        content_hash=mem.content_hash,
        erased_at=mem.erased_at,
        metadata=dict(mem.metadata_ or {}),
    )


async def add_memory(
    db: AsyncSession,
    namespace: str,
    req: MemoryAdd,
) -> MemoryOut:
    _add_t0 = _time.perf_counter()
    with tracer.start_as_current_span("memory.add") as span:
        span.set_attribute("namespace", namespace)
        span.set_attribute("agent_id", req.agent_id)
        span.set_attribute("has_subject", bool(req.subject_id))

        provider = get_embedding_provider()
        embedding = await provider.embed_one(req.content)

        # Change 6: DEK resolved through cache
        subject_key: Optional[bytes] = None
        if req.subject_id:
            subject_key = await _resolve_subject_key(db, req.subject_id, namespace)

        stored_bytes = (
            encrypt_content(req.content, subject_key) if subject_key else req.content.encode()
        )

        predicate_key = compute_predicate_key(req.metadata or {})

        in_process_lock = await _get_in_process_lock(namespace, req.agent_id)
        async with in_process_lock:
            await _acquire_pg_advisory_lock(db, namespace, req.agent_id)

            barrier_group = await _get_barrier_group(db, namespace, req.agent_id)

            # Change 3: pass a pre-generated UUID so the async LLM worker can
            # reference the new memory before flush assigns the DB id.
            import uuid as _uuid
            new_id = _uuid.uuid4()

            supersession = await run_supersession(
                db=db,
                namespace=namespace,
                agent_id=req.agent_id,
                new_content=req.content,
                new_meta=req.metadata or {},
                new_embedding=embedding,
                new_event_time=req.event_time,
                subject_key=subject_key,
                new_memory_id=new_id,
            )

            now = datetime.now(timezone.utc)
            mem = Memory(
                id=new_id,
                namespace=namespace,
                agent_id=req.agent_id,
                content_encrypted=stored_bytes,
                subject_id=req.subject_id,
                embedding=embedding,
                metadata_=req.metadata,
                event_time=req.event_time,
                ingestion_time=now,
                valid_from=req.event_time,
                valid_to=None,
                importance=_compute_importance(req.event_time, req.importance),
                source=req.source,
                content_hash=_content_hash(req.content),
                barrier_group=barrier_group,
            )
            db.add(mem)
            await db.flush()

            for old_id in supersession.superseded_ids:
                old = await db.get(Memory, old_id)
                if old:
                    old.valid_to = req.event_time
                    old.superseded_by = mem.id
                    old.supersession_confidence = supersession.confidence
                    await chain_log(
                        db, namespace=namespace, agent_id=req.agent_id,
                        op="supersede", memory_id=old.id,
                        content_hash=old.content_hash,
                        payload={
                            "superseded_by": str(mem.id),
                            "confidence": supersession.confidence,
                            "relation": supersession.relation,
                            "rationale": supersession.rationale,
                            "adjudication_stage": 3 if supersession.rationale else 2,
                        },
                    )

            # Change 1: maintain live_facts projection
            await remove_live_facts(db, supersession.superseded_ids)
            await upsert_live_fact(db, mem, predicate_key)

            await chain_log(
                db, namespace=namespace, agent_id=req.agent_id,
                op="add", memory_id=mem.id,
                content_hash=mem.content_hash,
                payload={
                    "source": req.source,
                    "event_time": req.event_time.isoformat(),
                    "metadata": req.metadata,
                    "supersession_relation": supersession.relation,
                    "supersession_confidence": supersession.confidence,
                },
            )

            await db.commit()

        await db.refresh(mem)

        # Change 7: invalidate in-process session cache on write
        invalidate_working_set(namespace, req.agent_id)
        await invalidate_agent(namespace, req.agent_id)

        span.set_attribute("memory_id", str(mem.id))
        span.set_attribute("supersession_relation", supersession.relation)
        span.set_attribute("predicate_key", predicate_key or "")

        from .metering import get_customer_id, queue_usage_event
        customer_id = await get_customer_id(db, namespace)
        if customer_id:
            settings = get_settings()
            queue_usage_event(settings.stripe_meter_write_event, customer_id, 1, f"w:{mem.id}")

        record_write(namespace, supersession.relation)
        observe_add(namespace, _time.perf_counter() - _add_t0)

        return _memory_to_out(mem, req.content)


async def recall_memories(
    db: AsyncSession,
    namespace: str,
    req: RecallRequest,
) -> RecallResult:
    _recall_t0 = _time.perf_counter()
    with tracer.start_as_current_span("memory.recall") as span:
        span.set_attribute("namespace", namespace)
        span.set_attribute("agent_id", req.agent_id)
        span.set_attribute("k", req.k)
        span.set_attribute("has_as_of", bool(req.as_of))

        settings = get_settings()

        # Hot cache (Redis)
        if settings.recall_cache_enabled and not req.as_of:
            cached = await get_cached_recall(
                namespace, req.agent_id, req.query, req.as_of, req.k, req.filters
            )
            if cached is not None:
                span.set_attribute("cache_hit", True)
                record_recall(namespace, router="cache", cache_hit=True)
                observe_recall(namespace, _time.perf_counter() - _recall_t0)
                return RecallResult.model_validate_json(cached)
        span.set_attribute("cache_hit", False)

        # Change 2: keyed router — exact lookup if filters resolve to a known predicate
        if not req.as_of and req.filters:
            predicate_key = compute_predicate_key(req.filters)
            if predicate_key:
                with tracer.start_as_current_span("recall.keyed_lookup") as ks:
                    barrier_group = await _get_barrier_group(db, namespace, req.agent_id)
                    live_fact = await keyed_lookup(
                        db, namespace, req.agent_id, predicate_key, barrier_group
                    )
                    if live_fact is not None:
                        subject_keys = await _load_namespace_subject_keys(db, namespace)
                        from .ranking import _decrypt
                        content = _decrypt(live_fact, subject_keys)
                        # Build a synthetic Memory-like result for the schema
                        mem = await db.get(Memory, live_fact.memory_id)
                        if mem is not None:
                            ks.set_attribute("keyed_hit", True)
                            span.set_attribute("router", "keyed")
                            mem_out = _memory_to_out(mem, content)
                            result = RecallResult(
                                memories=[mem_out],
                                as_of=None,
                                total_candidates=1,
                            )
                            _fire_recall_audit(db, namespace, req, [mem_out])
                            record_recall(namespace, router="keyed", cache_hit=False)
                            observe_recall(namespace, _time.perf_counter() - _recall_t0)
                            return result

        span.set_attribute("router", "semantic")

        # Change 10: sub-spans for each recall stage
        with tracer.start_as_current_span("recall.embed"):
            provider = get_embedding_provider()
            query_embedding = await provider.embed_one(req.query)

        with tracer.start_as_current_span("recall.load_keys"):
            subject_keys = await _load_namespace_subject_keys(db, namespace)
            barrier_group = await _get_barrier_group(db, namespace, req.agent_id)

        # Change 7: in-process working-set cache (present-time only)
        live_facts_cache: Optional[list] = None
        if not req.as_of:
            live_facts_cache = get_working_set(namespace, req.agent_id)
            if live_facts_cache is None:
                from .current_facts import fetch_working_set
                with tracer.start_as_current_span("recall.prefetch_working_set"):
                    live_facts_cache = await fetch_working_set(
                        db, namespace, req.agent_id, barrier_group
                    )
                set_working_set(namespace, req.agent_id, live_facts_cache)
                span.set_attribute("working_set_cold", True)
            else:
                span.set_attribute("working_set_cold", False)

        with tracer.start_as_current_span("recall.search"):
            results = await hybrid_recall(
                db=db,
                namespace=namespace,
                agent_id=req.agent_id,
                query=req.query,
                query_embedding=query_embedding,
                k=req.k,
                as_of=req.as_of,
                filters=req.filters,
                subject_keys=subject_keys,
                barrier_group=barrier_group,
                live_facts_override=live_facts_cache,
            )

        span.set_attribute("result_count", len(results))

        # hybrid_recall always returns Memory objects (Change 1 fetch-back ensures this)
        with tracer.start_as_current_span("recall.assemble"):
            memories_out: list[MemoryOut] = [
                _memory_to_out(mem, content) for mem, _score, content in results
            ]

        recall_log = await chain_log(
            db, namespace=namespace, agent_id=req.agent_id,
            op="recall",
            payload={
                "query_hash": _content_hash(req.query),
                "k": req.k,
                "as_of": req.as_of.isoformat() if req.as_of else None,
                "filters": req.filters,
                "result_ids": [str(m.id) for m in memories_out],
            },
        )
        await db.commit()

        result = RecallResult(
            memories=memories_out,
            as_of=req.as_of,
            total_candidates=len(results),
        )

        from .metering import get_customer_id, queue_usage_event
        customer_id = await get_customer_id(db, namespace)
        if customer_id:
            queue_usage_event(
                settings.stripe_meter_recall_event,
                customer_id, 1, f"r:{recall_log.id}",
            )

        if settings.recall_cache_enabled and not req.as_of:
            await set_cached_recall(
                namespace, req.agent_id, req.query, req.as_of, req.k, req.filters,
                result.model_dump_json(),
                settings.recall_cache_ttl_seconds,
            )

        record_recall(namespace, router="semantic", cache_hit=False)
        observe_recall(namespace, _time.perf_counter() - _recall_t0)
        return result


def _fire_recall_audit(db: AsyncSession, namespace: str, req: RecallRequest, memories: list) -> None:
    """Fire-and-forget recall audit log for keyed-router fast exits."""
    async def _log():
        try:
            await chain_log(
                db, namespace=namespace, agent_id=req.agent_id,
                op="recall",
                payload={
                    "query_hash": _content_hash(req.query),
                    "k": req.k,
                    "as_of": None,
                    "filters": req.filters,
                    "result_ids": [str(m.id) for m in memories],
                    "router": "keyed",
                },
            )
            await db.commit()
        except Exception:
            pass
    asyncio.create_task(_log())


async def batch_add_memories(
    db: AsyncSession,
    namespace: str,
    reqs: list[MemoryAdd],
) -> MemoryBatchResult:
    """Add multiple memories sequentially — later items can supersede earlier ones."""
    out: list[MemoryOut] = []
    for req in reqs:
        out.append(await add_memory(db, namespace, req))
    return MemoryBatchResult(added=len(out), memories=out)


async def get_pending_supersessions(
    db: AsyncSession,
    namespace: str,
    confidence_threshold: Optional[float] = None,
    limit: int = 50,
) -> SupersessionReviewResult:
    settings = get_settings()
    threshold = confidence_threshold if confidence_threshold is not None else settings.supersession_review_threshold

    stmt = (
        select(EventLog)
        .where(
            and_(
                EventLog.namespace == namespace,
                EventLog.op == "supersede",
            )
        )
        .order_by(EventLog.created_at.desc())
        .limit(limit * 4)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    items: list[SupersessionReviewItem] = []
    for row in rows:
        payload = dict(row.payload or {})
        confidence = float(payload.get("confidence", 1.0))
        if confidence >= threshold:
            continue
        items.append(SupersessionReviewItem(
            event_id=row.id,
            memory_id=row.memory_id,
            superseded_by=payload.get("superseded_by"),
            confidence=confidence,
            relation=payload.get("relation", "SUPERSEDES"),
            rationale=payload.get("rationale"),
            adjudication_stage=payload.get("adjudication_stage", 2),
            created_at=row.created_at,
            content_hash=row.content_hash,
        ))
        if len(items) >= limit:
            break

    return SupersessionReviewResult(
        items=items,
        total=len(items),
        confidence_threshold=threshold,
    )


async def apply_supersession_action(
    db: AsyncSession,
    namespace: str,
    memory_id: UUID,
    action: SupersessionAction,
) -> SupersessionActionResult:
    mem = await db.get(Memory, memory_id)
    if mem is None or mem.namespace != namespace:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Memory not found")
    if action.action not in ("confirm", "reject"):
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="action must be 'confirm' or 'reject'")

    now = datetime.now(timezone.utc)

    if action.action == "reject":
        mem.valid_to = None
        mem.superseded_by = None
        mem.supersession_confidence = None
        # Change 1: restore to live_facts when supersession is rejected
        predicate_key = compute_predicate_key(dict(mem.metadata_ or {}))
        await upsert_live_fact(db, mem, predicate_key)
        op = "supersession_rejected"
    else:
        op = "supersession_confirmed"

    await chain_log(
        db, namespace=namespace, agent_id=mem.agent_id,
        op=op, memory_id=mem.id,
        content_hash=mem.content_hash,
        payload={
            "reviewer_note": action.reviewer_note,
            "action": action.action,
            "actioned_at": now.isoformat(),
        },
    )
    await db.commit()
    invalidate_working_set(namespace, mem.agent_id)
    return SupersessionActionResult(memory_id=memory_id, action=action.action, applied_at=now)


async def get_retention_policy(db: AsyncSession, namespace: str) -> RetentionPolicyOut:
    pol = await db.get(NamespacePolicy, namespace)
    if pol is None:
        pol = NamespacePolicy(namespace=namespace)
        db.add(pol)
        await db.commit()
        await db.refresh(pol)
    return RetentionPolicyOut.model_validate(pol)


async def set_retention_policy(
    db: AsyncSession,
    namespace: str,
    data: RetentionPolicyIn,
    actor_id: str = "__admin__",
) -> RetentionPolicyOut:
    pol = await db.get(NamespacePolicy, namespace)
    if pol is None:
        pol = NamespacePolicy(namespace=namespace)
        db.add(pol)
    pol.content_ttl_days = data.content_ttl_days
    pol.audit_retention_days = data.audit_retention_days
    pol.legal_hold = data.legal_hold
    pol.updated_at = datetime.now(timezone.utc)
    await chain_log(
        db, namespace=namespace, agent_id=actor_id,
        op="admin.retention_set",
        payload={
            "content_ttl_days": data.content_ttl_days,
            "audit_retention_days": data.audit_retention_days,
            "legal_hold": data.legal_hold,
        },
    )
    await db.commit()
    await db.refresh(pol)
    return RetentionPolicyOut.model_validate(pol)


async def prune_expired_content(db: AsyncSession, namespace: str) -> RetentionPruneResult:
    pol = await db.get(NamespacePolicy, namespace)
    if pol is None or pol.content_ttl_days is None:
        cutoff = datetime.min.replace(tzinfo=timezone.utc)
        return RetentionPruneResult(namespace=namespace, memories_pruned=0, cutoff_date=cutoff)

    if pol.legal_hold:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=409,
            detail=f"Namespace '{namespace}' is under legal hold — pruning is blocked.",
        )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=pol.content_ttl_days)

    stmt = select(Memory).where(
        and_(
            Memory.namespace == namespace,
            Memory.ingestion_time < cutoff,
            Memory.content_encrypted.is_not(None),
            Memory.erased_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    memories = result.scalars().all()

    for mem in memories:
        mem.content_encrypted = None
        mem.erased_at = now
        await chain_log(
            db, namespace=namespace, agent_id=mem.agent_id,
            op="retention_prune", memory_id=mem.id,
            content_hash=mem.content_hash,
            payload={"cutoff_date": cutoff.isoformat(), "content_ttl_days": pol.content_ttl_days},
        )

    await db.commit()
    return RetentionPruneResult(namespace=namespace, memories_pruned=len(memories), cutoff_date=cutoff)


async def erase_subject(
    db: AsyncSession,
    namespace: str,
    subject_id: str,
    request_ref: str,
) -> int:
    stmt = select(Memory).where(
        and_(
            Memory.namespace == namespace,
            Memory.subject_id == subject_id,
            Memory.erased_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    memories = result.scalars().all()

    now = datetime.now(timezone.utc)
    agent_ids: set[str] = set()
    for mem in memories:
        mem.content_encrypted = None
        mem.erased_at = now
        agent_ids.add(mem.agent_id)
        await chain_log(
            db, namespace=namespace, agent_id=mem.agent_id,
            op="erase", memory_id=mem.id,
            content_hash=mem.content_hash,
            payload={"subject_id": subject_id, "request_ref": request_ref},
        )

    await destroy_subject_key(db, subject_id)
    await db.commit()

    # Change 6: evict destroyed key from DEK cache
    evict_dek(subject_id)
    # Change 7: invalidate session caches for all agents that had this subject's data
    for aid in agent_ids:
        invalidate_working_set(namespace, aid)

    record_erase(namespace, len(memories))
    return len(memories)
