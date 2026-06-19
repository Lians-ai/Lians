"""
Core memory service: add, recall, recall(as_of), used by API routes.
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

from .models import Memory, EventLog, SubjectKey, AgentBarrierGroup, NamespacePolicy
from .audit_chain import chain_log
from .telemetry import tracer
from .schemas import (
    MemoryAdd, MemoryOut, RecallRequest, RecallResult,
    MemoryBatchAdd, MemoryBatchResult,
    SupersessionReviewItem, SupersessionReviewResult,
    SupersessionAction, SupersessionActionResult,
    RetentionPolicyIn, RetentionPolicyOut, RetentionPruneResult,
)
from .embeddings import get_embedding_provider
from .crypto import encrypt_content, decrypt_content, unwrap_subject_key
from .pii import get_or_create_subject_key, destroy_subject_key
from .supersession import run_supersession
from .ranking import hybrid_recall
from .cache import get_cached_recall, set_cached_recall, invalidate_agent
from .config import get_settings

_IMPORTANCE_RECENCY_HALF_LIFE_DAYS = 90.0


def _write_lock_keys(namespace: str, agent_id: str) -> tuple[int, int]:
    """Two stable int4 values for pg_advisory_xact_lock(int, int)."""
    h = hashlib.sha256(f"{namespace}\x00{agent_id}".encode()).digest()
    return int.from_bytes(h[:4], "big"), int.from_bytes(h[4:8], "big")


# ── In-process lock registry ────────────────────────────────────────────────
# One asyncio.Lock per (event_loop, namespace, agent_id).
# Keyed by loop identity so test-generated loops each get fresh locks.
# No mutex needed: asyncio is cooperative — no interleaving between the
# `if key not in` check and the `_write_locks[key] = ...` assignment.
_write_locks: dict[tuple[int, str, str], asyncio.Lock] = {}


async def _get_in_process_lock(namespace: str, agent_id: str) -> asyncio.Lock:
    """Return (creating if needed) the asyncio.Lock for this (namespace, agent_id)."""
    loop = asyncio.get_running_loop()
    key = (id(loop), namespace, agent_id)
    if key not in _write_locks:
        _write_locks[key] = asyncio.Lock()
    return _write_locks[key]


async def _acquire_pg_advisory_lock(db: AsyncSession, namespace: str, agent_id: str) -> None:
    """
    Layer 2: PostgreSQL transaction-level advisory lock — cross-process guard.

    Released automatically on commit or rollback.  A second writer (in a different
    worker process) with the same namespace+agent_id blocks here until the first
    writer's transaction commits, then re-reads candidates with updated valid_to.

    No-op on SQLite (unit tests) — Layer 1 (asyncio.Lock) covers in-process races.
    """
    try:
        engine = db.sync_session.get_bind()
        if engine.dialect.name != "postgresql":
            return
    except Exception:
        return
    k1, k2 = _write_lock_keys(namespace, agent_id)
    await db.execute(text("SELECT pg_advisory_xact_lock(:k1, :k2)"), {"k1": k1, "k2": k2})


def _compute_importance(event_time: datetime, caller_salience: float) -> float:
    """Blend caller-provided salience with event-time recency."""
    now = datetime.now(timezone.utc)
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    age_days = (now - event_time).total_seconds() / 86400
    recency = math.exp(-math.log(2) * age_days / _IMPORTANCE_RECENCY_HALF_LIFE_DAYS)
    return round(0.4 * recency + 0.6 * caller_salience, 4)


async def _get_barrier_group(db: AsyncSession, namespace: str, agent_id: str) -> Optional[str]:
    """Return the barrier group for an agent, or None if unassigned (sees all)."""
    stmt = select(AgentBarrierGroup).where(
        and_(AgentBarrierGroup.namespace == namespace, AgentBarrierGroup.agent_id == agent_id)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    return row.group_name if row else None


async def _load_namespace_subject_keys(db: AsyncSession, namespace: str) -> dict[str, bytes]:
    """Load all active subject keys for a namespace (for decrypting recalled memories)."""
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
        try:
            keys[row.subject_id] = unwrap_subject_key(bytes(row.enc_key))
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
    with tracer.start_as_current_span("memory.add") as span:
        span.set_attribute("namespace", namespace)
        span.set_attribute("agent_id", req.agent_id)
        span.set_attribute("has_subject", bool(req.subject_id))

        provider = get_embedding_provider()
        embedding = await provider.embed_one(req.content)

        # Resolve subject key if PII
        subject_key: Optional[bytes] = None
        if req.subject_id:
            subject_key = await get_or_create_subject_key(db, req.subject_id, namespace)

        # Encrypt content outside the lock (pure CPU, no DB I/O)
        stored_bytes = (
            encrypt_content(req.content, subject_key) if subject_key else req.content.encode()
        )

        # ── Critical section ────────────────────────────────────────────────────
        # Two-layer write serialisation for (namespace, agent_id):
        #   Layer 1 — asyncio.Lock: in-process (single worker or test)
        #   Layer 2 — pg_advisory_xact_lock: cross-process (multi-worker)
        # Both are acquired before reading supersession candidates; Layer 2 is
        # released automatically on db.commit() / rollback.
        #
        # barrier_group is looked up INSIDE the lock so that it reads within the
        # same transaction as supersession candidates.  If it were fetched outside,
        # on SQLite the autobegin transaction would snapshot the DB before any
        # concurrent write commits, causing concurrent adds to miss each other's
        # superseded memories (tested in test_concurrency.py).
        in_process_lock = await _get_in_process_lock(namespace, req.agent_id)
        async with in_process_lock:
            await _acquire_pg_advisory_lock(db, namespace, req.agent_id)

            # Tag memory with agent's barrier group so that other barrier groups
            # cannot recall it.  Looked up here so the read is within the locked
            # transaction (consistent with supersession candidate reads below).
            barrier_group = await _get_barrier_group(db, namespace, req.agent_id)

            supersession = await run_supersession(
                db=db,
                namespace=namespace,
                agent_id=req.agent_id,
                new_content=req.content,
                new_meta=req.metadata,
                new_embedding=embedding,
                new_event_time=req.event_time,
                subject_key=subject_key,
            )

            now = datetime.now(timezone.utc)
            mem = Memory(
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
            await db.flush()  # get mem.id

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
        # ── End critical section ────────────────────────────────────────────────

        await db.refresh(mem)
        # Invalidate all cached recall results for this agent so the next recall
        # sees the new memory immediately rather than a stale hot-cache response.
        await invalidate_agent(namespace, req.agent_id)
        span.set_attribute("memory_id", str(mem.id))
        span.set_attribute("supersession_relation", supersession.relation)
        return _memory_to_out(mem, req.content)


async def recall_memories(
    db: AsyncSession,
    namespace: str,
    req: RecallRequest,
) -> RecallResult:
    with tracer.start_as_current_span("memory.recall") as span:
        span.set_attribute("namespace", namespace)
        span.set_attribute("agent_id", req.agent_id)
        span.set_attribute("k", req.k)
        span.set_attribute("has_as_of", bool(req.as_of))

        settings = get_settings()

        # ── Hot cache (Redis) ───────────────────────────────────────────────────
        if settings.recall_cache_enabled:
            cached = await get_cached_recall(
                namespace, req.agent_id, req.query, req.as_of, req.k, req.filters
            )
            if cached is not None:
                span.set_attribute("cache_hit", True)
                return RecallResult.model_validate_json(cached)

        span.set_attribute("cache_hit", False)
        provider = get_embedding_provider()
        query_embedding = await provider.embed_one(req.query)
        subject_keys = await _load_namespace_subject_keys(db, namespace)
        barrier_group = await _get_barrier_group(db, namespace, req.agent_id)

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
        )

        span.set_attribute("result_count", len(results))

        # Audit log the recall
        await chain_log(
            db, namespace=namespace, agent_id=req.agent_id,
            op="recall",
            payload={
                "query_hash": _content_hash(req.query),
                "k": req.k,
                "as_of": req.as_of.isoformat() if req.as_of else None,
                "filters": req.filters,
                "result_ids": [str(m.id) for m, _, _ in results],
                "scores": [round(float(s), 4) for _, s, _ in results],
            },
        )
        await db.commit()

        memories_out = [_memory_to_out(mem, content) for mem, _, content in results]
        result = RecallResult(
            memories=memories_out,
            as_of=req.as_of,
            total_candidates=len(results),
        )

        # ── Hot cache (Redis) write ─────────────────────────────────────────────
        if settings.recall_cache_enabled:
            await set_cached_recall(
                namespace, req.agent_id, req.query, req.as_of, req.k, req.filters,
                result.model_dump_json(),
                settings.recall_cache_ttl_seconds,
            )

        return result


async def batch_add_memories(
    db: AsyncSession,
    namespace: str,
    reqs: list[MemoryAdd],
) -> MemoryBatchResult:
    """
    Add multiple memories sequentially in a single request.

    Each memory runs the full supersession funnel, advisory lock, and event-log
    write independently.  Sequential (not parallel) so that later items in the
    batch can supersede earlier ones in the same batch — matching the behaviour
    a caller would get by calling /v1/memories once per item.
    """
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
    """
    Return supersession events whose confidence is below the configured threshold.

    These are the cases the Stage 2/3 engine was uncertain about and a human
    reviewer should inspect before treating the old fact as definitively stale.
    """
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
        .limit(limit * 4)  # over-fetch; confidence filter is in Python for SQLite compat
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
    """
    Confirm or reject a supersession event from the review queue.

    confirm — the supersession was correct; write an audit event so a reviewer's
              name/note is on record. No data changes (old memory stays superseded).

    reject  — the supersession was wrong; restore the old memory as currently valid
              (valid_to = NULL, superseded_by = NULL).  Both old and new memory are
              now valid — treated as additive facts, each standing on their own.
              Write an immutable audit event logging the rejection.
    """
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
    return SupersessionActionResult(
        memory_id=memory_id,
        action=action.action,
        applied_at=now,
    )


async def get_retention_policy(
    db: AsyncSession,
    namespace: str,
) -> RetentionPolicyOut:
    """Return the namespace's retention policy, creating a default one if absent."""
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
) -> RetentionPolicyOut:
    """Upsert the retention policy for a namespace."""
    pol = await db.get(NamespacePolicy, namespace)
    if pol is None:
        pol = NamespacePolicy(namespace=namespace)
        db.add(pol)
    pol.content_ttl_days = data.content_ttl_days
    pol.audit_retention_days = data.audit_retention_days
    pol.legal_hold = data.legal_hold
    pol.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(pol)
    return RetentionPolicyOut.model_validate(pol)


async def prune_expired_content(
    db: AsyncSession,
    namespace: str,
) -> RetentionPruneResult:
    """
    Null out content_encrypted on memories whose ingestion_time exceeds content_ttl_days.

    Never runs when legal_hold is True or when content_ttl_days is NULL.
    The content_hash is preserved so the audit trail remains intact after pruning
    (SEC 17a-4 requires that audit records outlive the underlying content).
    """
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
            payload={
                "cutoff_date": cutoff.isoformat(),
                "content_ttl_days": pol.content_ttl_days,
            },
        )

    await db.commit()
    return RetentionPruneResult(
        namespace=namespace,
        memories_pruned=len(memories),
        cutoff_date=cutoff,
    )


async def erase_subject(
    db: AsyncSession,
    namespace: str,
    subject_id: str,
    request_ref: str,
) -> int:
    """
    Crypto-shred a data subject: null out content, destroy their key, write
    an immutable erase event to the audit log.  Returns count of erased rows.
    """
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
    for mem in memories:
        mem.content_encrypted = None
        mem.erased_at = now
        await chain_log(
            db, namespace=namespace, agent_id=mem.agent_id,
            op="erase", memory_id=mem.id,
            content_hash=mem.content_hash,
            payload={"subject_id": subject_id, "request_ref": request_ref},
        )

    await destroy_subject_key(db, subject_id)
    await db.commit()
    return len(memories)
