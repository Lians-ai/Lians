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
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, and_, or_, update, text, cast, Float
from sqlalchemy.ext.asyncio import AsyncSession

import time as _time

from .models import Memory, EventLog, SubjectKey, AgentBarrierGroup, NamespacePolicy, ConflictFlag, IdempotencyKey
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
    ConflictFlagOut, ConflictListResult, ConflictResolveRequest, ConflictResolveResult,
)
from .embeddings import get_embedding_provider
from .crypto import encrypt_content, decrypt_content, unwrap_subject_key
from .pii import get_or_create_subject_key, destroy_subject_key
from .supersession import run_supersession, _utc
from .ranking import hybrid_recall
from .cache import get_cached_recall, set_cached_recall, invalidate_agent
from .config import get_settings
from .current_facts import compute_predicate_key, upsert_live_fact, remove_live_facts, keyed_lookup
from .dek_cache import get_cached_dek, cache_dek, evict_dek
from .session_cache import get_working_set, set_working_set, invalidate_working_set

logger = logging.getLogger("agentmem.memory_service")

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


async def _get_barrier_group(
    db: AsyncSession, namespace: str, agent_id: str, override: Optional[str] = None
) -> Optional[str]:
    if override is not None:
        # The calling API key is barrier-scoped (SSO gateway picked it from the
        # caller's IdP group) — the key's barrier is authoritative, no lookup.
        group: Optional[str] = override
    else:
        stmt = select(AgentBarrierGroup).where(
            and_(AgentBarrierGroup.namespace == namespace, AgentBarrierGroup.agent_id == agent_id)
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        group = row.group_name if row else None

    # Engage the PostgreSQL RLS barrier policy by setting the session variable the
    # RESTRICTIVE barrier_isolation policy reads (migration 0013). An unbarriered
    # agent sets '' and sees every row in its namespace (compliance-officer view);
    # a group-scoped agent sees only NULL-barrier (shared) and same-group rows.
    # No-op on SQLite (no set_config) — those tests rely on app-layer filtering.
    try:
        await db.execute(
            text("SELECT set_config('agentmem.barrier_group', :bg, true)"),
            {"bg": group or ""},
        )
    except Exception:
        pass

    return group


async def _resolve_subject_key(
    db: AsyncSession,
    subject_id: str,
    namespace: str,
) -> bytes:
    """Return plaintext DEK for subject, using cache (Change 6)."""
    cached = get_cached_dek(namespace, subject_id)
    if cached is not None:
        return cached
    key = await get_or_create_subject_key(db, subject_id, namespace)
    cache_dek(namespace, subject_id, key)
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
        cached = get_cached_dek(namespace, row.subject_id)
        if cached is not None:
            keys[row.subject_id] = cached
            continue
        try:
            plaintext = unwrap_subject_key(bytes(row.enc_key))
            cache_dek(namespace, row.subject_id, plaintext)
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


async def _mark_parent_stale(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    closed_mem: Memory,
    closure_time: datetime,
) -> None:
    """When a derived clause closes, its parent turn still contains the stale
    text. Record the closure on the parent (metadata._stale_clauses, a list of
    closure timestamps — never clause text, which may be subject-encrypted) so
    ranking can demote it; the raw content is untouched."""
    parent_ref = (dict(closed_mem.metadata_ or {})).get("_parent")
    if not parent_ref:
        return
    try:
        parent = await db.get(Memory, UUID(str(parent_ref)))
    except (ValueError, TypeError):
        return
    if parent is None or parent.erased_at is not None:
        return
    meta = dict(parent.metadata_ or {})
    marks = list(meta.get("_stale_clauses") or [])
    marks.append(_utc(closure_time).isoformat())
    meta["_stale_clauses"] = marks
    parent.metadata_ = meta
    from .models import LiveFact
    await db.execute(
        update(LiveFact).where(LiveFact.memory_id == parent.id).values(metadata_=meta)
    )
    await chain_log(
        db, namespace=namespace, agent_id=agent_id,
        op="derived_stale_mark", memory_id=parent.id,
        content_hash=parent.content_hash,
        payload={"closed_clause": str(closed_mem.id), "closure_time": _utc(closure_time).isoformat()},
    )


async def _ingest_derived_clause(
    db: AsyncSession,
    namespace: str,
    req: MemoryAdd,
    parent: Memory,
    clause: str,
    embedding: list[float],
    subject_key: Optional[bytes],
) -> None:
    """Store one extracted interjection clause as a derived memory.

    Same event_time/subject/barrier as the parent; structured keys are dropped
    so a clause can never trip keyed supersession against its own parent. Runs
    the full supersession funnel — this is where a cued revision clause closes
    its predecessor clause. Caller holds the agent write lock.
    """
    from .adapters import get_adapter
    sk = get_adapter().structured_keys
    meta = {
        k: v for k, v in (req.metadata or {}).items()
        if k not in sk and k not in ("_auto_meta", "_stale_clauses")
    }
    meta["_derived"] = "interjection"
    meta["_parent"] = str(parent.id)

    import uuid as _uuid
    new_id = _uuid.uuid4()
    # The clause inherits the parent turn's revision-cue status: the cue words
    # ("wait —", "actually", "now") usually stay in the surrounding chatter
    # while the extracted clause is the revision payload itself.
    from .supersession import _REVISION_CUE_RE
    parent_cued = bool(_REVISION_CUE_RE.search(req.content or ""))
    supersession = await run_supersession(
        db=db, namespace=namespace, agent_id=req.agent_id,
        new_content=clause, new_meta=meta, new_embedding=embedding,
        new_event_time=req.event_time, subject_key=subject_key,
        new_memory_id=new_id, cue_hint=parent_cued,
    )

    dmem = Memory(
        id=new_id,
        namespace=namespace,
        agent_id=req.agent_id,
        content_encrypted=encrypt_content(clause, subject_key) if subject_key else clause.encode(),
        subject_id=req.subject_id,
        embedding=embedding,
        metadata_=meta,
        event_time=req.event_time,
        ingestion_time=datetime.now(timezone.utc),
        valid_from=req.event_time,
        valid_to=None,
        importance=parent.importance,
        source=req.source,
        content_hash=_content_hash(clause),
        barrier_group=parent.barrier_group,
    )
    db.add(dmem)
    await db.flush()

    for old_id in supersession.superseded_ids:
        old = await db.get(Memory, old_id)
        if old:
            old.valid_to = req.event_time
            old.superseded_by = dmem.id
            old.supersession_confidence = supersession.confidence
            await chain_log(
                db, namespace=namespace, agent_id=req.agent_id,
                op="supersede", memory_id=old.id,
                content_hash=old.content_hash,
                payload={
                    "superseded_by": str(dmem.id),
                    "confidence": supersession.confidence,
                    "relation": supersession.relation,
                    "derived": True,
                },
            )
            await _mark_parent_stale(db, namespace, req.agent_id, old, req.event_time)

    # Backdated arrival: a live later revision of this clause already exists.
    arrived_closed = False
    if supersession.superseded_by_id is not None:
        newer = await db.get(Memory, supersession.superseded_by_id)
        if newer is not None and _utc(newer.event_time) > _utc(req.event_time):
            dmem.valid_to = newer.event_time
            dmem.superseded_by = newer.id
            dmem.supersession_confidence = supersession.confidence
            arrived_closed = True

    await remove_live_facts(db, supersession.superseded_ids)
    if not arrived_closed:
        await upsert_live_fact(db, dmem, compute_predicate_key(meta))

    await chain_log(
        db, namespace=namespace, agent_id=req.agent_id,
        op="add", memory_id=dmem.id,
        content_hash=dmem.content_hash,
        payload={
            "source": req.source,
            "event_time": req.event_time.isoformat(),
            "derived_from": str(parent.id),
            "kind": "interjection",
            "supersession_relation": supersession.relation,
            "supersession_confidence": supersession.confidence,
        },
    )


async def add_memory(
    db: AsyncSession,
    namespace: str,
    req: MemoryAdd,
    *,
    barrier_override: Optional[str] = None,
    precomputed_embedding: Optional[list[float]] = None,
) -> MemoryOut:
    """``precomputed_embedding`` lets batch writers embed many contents in one
    model call (10-20x faster on local models) and pass each vector through;
    it must come from the same provider/model the store was built with."""
    _add_t0 = _time.perf_counter()
    with tracer.start_as_current_span("memory.add") as span:
        span.set_attribute("namespace", namespace)
        span.set_attribute("agent_id", req.agent_id)
        span.set_attribute("has_subject", bool(req.subject_id))

        if precomputed_embedding is not None:
            embedding = precomputed_embedding
        else:
            provider = get_embedding_provider()
            embedding = await provider.embed_one(req.content)

        # Auto-metadata (auto-supersession parity): when the caller supplied no
        # structured keys, derive them from the content so the deterministic
        # keyed-supersession fast path can fire on a plain-text write. Opt-in
        # (auto_metadata_enabled); caller keys are never overridden; provenance
        # is tagged under metadata._auto_meta. Fail-open — never blocks the write.
        settings = get_settings()
        if settings.auto_metadata_enabled:
            try:
                from .auto_metadata import enrich_metadata
                from .adapters import get_adapter
                enriched_meta, auto_prov = await enrich_metadata(
                    req.content, req.metadata or {}, adapter=get_adapter(), settings=settings,
                )
                if auto_prov is not None:
                    req.metadata = enriched_meta
                    span.set_attribute("auto_metadata_keys", ",".join(auto_prov["keys"]))
            except Exception:
                pass  # fail-open: enrichment must never break ingestion

        # Interjection extraction (see interjection.py): durable-fact clauses
        # buried in a conversational turn become derived memories beside the
        # raw turn. Extraction + embedding happen before the write lock; the
        # derived rows are ingested inside it. Fail-open, like auto-metadata.
        derived_clauses: list[tuple[str, list[float]]] = []
        if settings.interjection_extraction_enabled and not (req.metadata or {}).get("_derived"):
            try:
                from .interjection import extract_interjections
                clauses = extract_interjections(req.content)
                if clauses:
                    vectors = await get_embedding_provider().embed(clauses)
                    derived_clauses = list(zip(clauses, vectors))
            except Exception:
                logger.warning("interjection extraction failed — storing raw turn only", exc_info=True)

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

            barrier_group = await _get_barrier_group(db, namespace, req.agent_id, override=barrier_override)

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
                    await _mark_parent_stale(db, namespace, req.agent_id, old, req.event_time)

            # Out-of-order ingestion: a live fact with a LATER event_time already
            # covers this key/topic, so the incoming memory arrives historical —
            # its validity window closes at the successor's event_time. It stays
            # queryable via as_of/snapshot for its own era but never pollutes the
            # current view.
            arrived_closed = False
            if supersession.superseded_by_id is not None:
                newer = await db.get(Memory, supersession.superseded_by_id)
                if newer is not None and _utc(newer.event_time) > _utc(req.event_time):
                    mem.valid_to = newer.event_time
                    mem.superseded_by = newer.id
                    mem.supersession_confidence = supersession.confidence
                    arrived_closed = True
                    await chain_log(
                        db, namespace=namespace, agent_id=req.agent_id,
                        op="supersede", memory_id=mem.id,
                        content_hash=mem.content_hash,
                        payload={
                            "superseded_by": str(newer.id),
                            "confidence": supersession.confidence,
                            "relation": supersession.relation,
                            "backdated_arrival": True,
                        },
                    )

            # Same-time contradiction: persist a ConflictFlag for human review.
            # Both memories stay live (neither superseded) until someone resolves it.
            for conflict_old_id in supersession.conflict_ids:
                flag = ConflictFlag(
                    namespace=namespace,
                    agent_id=req.agent_id,
                    memory_a_id=conflict_old_id,   # pre-existing memory
                    memory_b_id=mem.id,            # newly ingested memory
                    confidence=supersession.confidence,
                    status="open",
                )
                db.add(flag)
                await chain_log(
                    db, namespace=namespace, agent_id=req.agent_id,
                    op="conflict_detected", memory_id=mem.id,
                    content_hash=mem.content_hash,
                    payload={
                        "memory_a_id": str(conflict_old_id),
                        "memory_b_id": str(mem.id),
                        "confidence": supersession.confidence,
                        "relation": supersession.relation,
                    },
                )

            # Change 1: maintain live_facts projection. A memory that arrived
            # already superseded (backdated) is never live.
            await remove_live_facts(db, supersession.superseded_ids)
            if not arrived_closed:
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

            # Ingest extracted interjection clauses as derived memories. Runs
            # inside the same lock/transaction as the parent; each clause goes
            # through the full supersession funnel so a cued revision clause
            # closes its predecessor clause. Fail-open per clause.
            for clause_text, clause_vec in derived_clauses:
                try:
                    await _ingest_derived_clause(
                        db, namespace, req, mem, clause_text, clause_vec, subject_key,
                    )
                except Exception:
                    logger.warning("derived-clause ingest failed — raw turn unaffected", exc_info=True)

            # Fan out webhook events for the write outcome. dispatch_event is a
            # no-op when no endpoint subscribes, so this is safe on every write.
            from .webhook_service import dispatch_event, MEMORY_SUPERSEDED, MEMORY_CONFLICT
            if supersession.superseded_ids:
                await dispatch_event(db, namespace, MEMORY_SUPERSEDED, {
                    "agent_id": req.agent_id,
                    "new_memory_id": str(mem.id),
                    "superseded_ids": [str(i) for i in supersession.superseded_ids],
                    "relation": supersession.relation,
                    "confidence": supersession.confidence,
                })
            if supersession.conflict_ids:
                await dispatch_event(db, namespace, MEMORY_CONFLICT, {
                    "agent_id": req.agent_id,
                    "new_memory_id": str(mem.id),
                    "conflict_ids": [str(i) for i in supersession.conflict_ids],
                    "confidence": supersession.confidence,
                })

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


async def add_memory_idempotent(
    db: AsyncSession,
    namespace: str,
    req: MemoryAdd,
    idempotency_key: Optional[str],
    *,
    barrier_override: Optional[str] = None,
) -> MemoryOut:
    """
    Idempotent wrapper around :func:`add_memory`.

    When ``idempotency_key`` is supplied, a previously-seen key (in this
    namespace) returns the original memory instead of inserting a duplicate —
    giving exactly-once semantics for a retried write. Without a key, behaves
    exactly like ``add_memory``.

    The SDKs send a stable key on automatic retries, so a write that succeeded
    server-side but whose response was lost to a network blip is not duplicated.
    """
    if not idempotency_key:
        return await add_memory(db, namespace, req, barrier_override=barrier_override)

    existing = await db.get(IdempotencyKey, (idempotency_key, namespace))
    if existing is not None:
        mem = await db.get(Memory, existing.memory_id)
        if mem is not None:
            subject_keys = await _load_namespace_subject_keys(db, namespace)
            from .ranking import _decrypt
            return _memory_to_out(mem, _decrypt(mem, subject_keys))

    result = await add_memory(db, namespace, req, barrier_override=barrier_override)
    db.add(IdempotencyKey(key=idempotency_key, namespace=namespace, memory_id=result.id))
    try:
        await db.commit()
    except Exception:
        # Lost a race with a concurrent identical request — return the winner's row.
        await db.rollback()
        existing = await db.get(IdempotencyKey, (idempotency_key, namespace))
        if existing is not None:
            mem = await db.get(Memory, existing.memory_id)
            if mem is not None:
                subject_keys = await _load_namespace_subject_keys(db, namespace)
                from .ranking import _decrypt
                return _memory_to_out(mem, _decrypt(mem, subject_keys))
    return result


def _estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) — good enough for budgeting."""
    return max(1, len(text) // 4)


# Neighbors farther apart than this are different episodes, not context.
CONTEXT_GAP_S = 3600.0


async def _attach_context(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    memories_out: list[MemoryOut],
    subject_keys: dict[str, bytes],
    *,
    as_of: Optional[datetime] = None,
) -> None:
    """Populate ``context_before``/``context_after`` on recall hits.

    For each hit, the nearest same-agent memory strictly before/after it in
    event time (within CONTEXT_GAP_S) — the other half of a dialogue exchange
    or event burst. Two indexed LIMIT-1 queries per hit on
    ix_memories_ns_agent_event; erased rows never surface, and under as_of
    only rows already knowable at the pinned time qualify.
    """
    from .ranking import _decrypt

    gap = timedelta(seconds=CONTEXT_GAP_S)
    for out in memories_out:
        base = [
            Memory.namespace == namespace,
            Memory.agent_id == agent_id,
            Memory.erased_at.is_(None),
            Memory.id != out.id,
        ]
        if as_of is not None:
            base.append(Memory.event_time <= as_of)
        before_stmt = (
            select(Memory)
            .where(and_(*base, Memory.event_time < out.event_time,
                        Memory.event_time >= out.event_time - gap))
            .order_by(Memory.event_time.desc())
            .limit(1)
        )
        after_stmt = (
            select(Memory)
            .where(and_(*base, Memory.event_time > out.event_time,
                        Memory.event_time <= out.event_time + gap))
            .order_by(Memory.event_time.asc())
            .limit(1)
        )
        before = (await db.execute(before_stmt)).scalars().first()
        after = (await db.execute(after_stmt)).scalars().first()
        if before is not None:
            out.context_before = _decrypt(before, subject_keys)
        if after is not None:
            out.context_after = _decrypt(after, subject_keys)


async def _agent_open_conflicts(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    limit: int,
) -> tuple[list[ConflictFlagOut], int]:
    """
    Open conflicts for one agent, oldest first (the longest-unresolved conflict
    is the most overdue), plus the total open count. Backs the active-resurfacing
    section of ``assemble_context``.
    """
    from sqlalchemy import func

    conds = and_(
        ConflictFlag.namespace == namespace,
        ConflictFlag.agent_id == agent_id,
        ConflictFlag.status == "open",
    )
    total = (await db.execute(select(func.count()).select_from(ConflictFlag).where(conds))).scalar() or 0
    if total == 0:
        return [], 0

    flags = (await db.execute(
        select(ConflictFlag).where(conds).order_by(ConflictFlag.detected_at.asc()).limit(limit)
    )).scalars().all()

    subject_keys = await _load_namespace_subject_keys(db, namespace)
    from .ranking import _decrypt

    out: list[ConflictFlagOut] = []
    for flag in flags:
        mem_a = await db.get(Memory, flag.memory_a_id)
        mem_b = await db.get(Memory, flag.memory_b_id)
        out.append(ConflictFlagOut(
            id=flag.id,
            namespace=flag.namespace,
            agent_id=flag.agent_id,
            memory_a_id=flag.memory_a_id,
            memory_b_id=flag.memory_b_id,
            memory_a_content=_decrypt(mem_a, subject_keys) if mem_a else None,
            memory_b_content=_decrypt(mem_b, subject_keys) if mem_b else None,
            memory_a_source=mem_a.source if mem_a else None,
            memory_b_source=mem_b.source if mem_b else None,
            memory_a_event_time=mem_a.event_time if mem_a else flag.detected_at,
            memory_b_event_time=mem_b.event_time if mem_b else flag.detected_at,
            confidence=flag.confidence,
            detected_at=flag.detected_at,
            status=flag.status,
            resolved_at=flag.resolved_at,
            resolver_note=flag.resolver_note,
        ))
    return out, int(total)


async def assemble_context(
    db: AsyncSession,
    namespace: str,
    req: "ContextRequest",
    *,
    barrier_override: Optional[str] = None,
) -> "ContextResult":
    """
    Recall the relevant facts and assemble them into a token-budgeted, ready-to-
    inject context block — the one-call "memory context" surface (Zep parity),
    backed by Lians' bitemporal recall so the block never contains stale facts.

    Facts are included in relevance order until ``max_tokens`` is reached; each
    line carries event-time and source so the model can reason about recency and
    provenance. Erased (crypto-shredded) facts are skipped.

    Active resurfacing: open conflicts for this agent push to the top of the
    block (oldest first — they cannot silently age out) until a human
    adjudicates them, so the model treats contested facts as contested rather
    than confidently using whichever version recall happened to rank higher.
    """
    from .schemas import ContextResult
    filters: dict[str, Any] = {}
    if req.mmr:
        filters["_rerank"] = "mmr"
    recall_req = RecallRequest(
        agent_id=req.agent_id, query=req.query, k=req.k, as_of=req.as_of, filters=filters,
    )
    result = await recall_memories(db, namespace, recall_req, barrier_override=barrier_override)

    lines = [req.header]
    used = _estimate_tokens(req.header)

    open_conflicts: list[ConflictFlagOut] = []
    open_conflicts_total = 0
    if req.surface_conflicts and req.max_conflicts > 0:
        open_conflicts, open_conflicts_total = await _agent_open_conflicts(
            db, namespace, req.agent_id, req.max_conflicts
        )
    if open_conflicts:
        banner = "⚠ UNRESOLVED MEMORY CONFLICTS — contested facts, pending adjudication:"
        lines.append(banner)
        used += _estimate_tokens(banner)
        for c in open_conflicts:
            a_stamp = c.memory_a_event_time.isoformat()[:16].replace("T", " ")
            b_stamp = c.memory_b_event_time.isoformat()[:16].replace("T", " ")
            a_src = f" [{c.memory_a_source}]" if c.memory_a_source else ""
            b_src = f" [{c.memory_b_source}]" if c.memory_b_source else ""
            line = (
                f"- ({a_stamp}){a_src} \"{c.memory_a_content}\" DISAGREES WITH "
                f"({b_stamp}){b_src} \"{c.memory_b_content}\""
            )
            lines.append(line)
            used += _estimate_tokens(line)
        if open_conflicts_total > len(open_conflicts):
            more = f"  (+{open_conflicts_total - len(open_conflicts)} more open conflicts not shown)"
            lines.append(more)
            used += _estimate_tokens(more)
    included: list = []
    truncated = False
    for m in result.memories:
        if not m.content:
            continue  # erased — content unrecoverable
        stamp = m.event_time.isoformat()[:16].replace("T", " ") if m.event_time else "undated"
        prov = f" [{m.source}]" if m.source else ""
        line = f"- ({stamp}){prov} {m.content}"
        t = _estimate_tokens(line)
        if used + t > req.max_tokens:
            truncated = True
            break
        lines.append(line)
        used += t
        included.append(m)

    return ContextResult(
        context="\n".join(lines),
        memories=included,
        token_estimate=used,
        truncated=truncated,
        retrieval_degraded=result.retrieval_degraded,
        open_conflicts=open_conflicts,
        open_conflicts_total=open_conflicts_total,
    )


async def recall_memories(
    db: AsyncSession,
    namespace: str,
    req: RecallRequest,
    *,
    barrier_override: Optional[str] = None,
) -> RecallResult:
    _recall_t0 = _time.perf_counter()
    with tracer.start_as_current_span("memory.recall") as span:
        span.set_attribute("namespace", namespace)
        span.set_attribute("agent_id", req.agent_id)
        span.set_attribute("k", req.k)
        span.set_attribute("has_as_of", bool(req.as_of))

        settings = get_settings()

        # Graph-proximity reranking (opt-in via filters). Pull the anchor params
        # out of `filters` BEFORE they reach the metadata matcher, and bypass the
        # recall cache when present (results depend on the live graph).
        near_entity: Optional[str] = None
        near_key = "ticker"
        rerank: Optional[str] = None
        mmr_lambda = 0.5
        if req.filters:
            near_entity = req.filters.pop("_near_entity", None)
            near_key = req.filters.pop("_near_key", "ticker")
            rerank = req.filters.pop("_rerank", None)
            try:
                mmr_lambda = float(req.filters.pop("_mmr_lambda", 0.5))
            except (TypeError, ValueError):
                mmr_lambda = 0.5

        # Hot cache (Redis)
        if settings.recall_cache_enabled and not req.as_of and not near_entity and not rerank and barrier_override is None:
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
                    barrier_group = await _get_barrier_group(db, namespace, req.agent_id, override=barrier_override)
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
                            mem_out.score = 1.0  # exact keyed match
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
        #
        # Degraded-retrieval mode: an unavailable embedding provider must not
        # take recall down with it. On embed failure the query proceeds
        # lexical-only (BM25 + recency + importance — semantic weight scores 0)
        # and the degradation is carried on the result AND into the audit
        # chain: a decision made under degraded recall is a fact an examiner
        # needs, not something to silently absorb. Keyed lookups above never
        # embed, so they never degrade.
        retrieval_degraded = False
        with tracer.start_as_current_span("recall.embed") as embed_span:
            provider = get_embedding_provider()
            try:
                # Custom providers written against the pre-asymmetric
                # interface may only implement embed_one; treat that as the
                # query embedding rather than a degradation event.
                embed_fn = getattr(provider, "embed_query", None) or provider.embed_one
                query_embedding = await embed_fn(req.query)
            except Exception as exc:
                query_embedding = []
                retrieval_degraded = True
                embed_span.set_attribute("retrieval_degraded", True)
                logger.warning(
                    "embedding provider failed (%s: %s) — recall degrading to lexical-only",
                    type(exc).__name__, exc,
                )
        span.set_attribute("retrieval_degraded", retrieval_degraded)

        with tracer.start_as_current_span("recall.load_keys"):
            subject_keys = await _load_namespace_subject_keys(db, namespace)
            barrier_group = await _get_barrier_group(db, namespace, req.agent_id, override=barrier_override)

        # Change 7: in-process working-set cache (present-time only). The cache is
        # keyed by (namespace, agent_id); a key-level barrier (SSO) can vary the
        # barrier for the same agent, so bypass the cache when an override is in
        # play to avoid serving one barrier's working set to another.
        live_facts_cache: Optional[list] = None
        if not req.as_of:
            from .current_facts import fetch_working_set
            if barrier_override is not None:
                with tracer.start_as_current_span("recall.prefetch_working_set"):
                    live_facts_cache = await fetch_working_set(
                        db, namespace, req.agent_id, barrier_group
                    )
            else:
                live_facts_cache = get_working_set(namespace, req.agent_id)
                if live_facts_cache is None:
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

        # MMR reranking (opt-in via filters {"_rerank": "mmr"}): reorder the
        # candidate set to balance relevance against diversity, so the top-k isn't
        # dominated by near-duplicate restatements of the same fact.
        if rerank == "mmr" and len(results) > 1:
            from .ranking import mmr_rerank
            results = mmr_rerank(results, lambda_=mmr_lambda)
            span.set_attribute("mmr_rerank", True)

        # Graph-proximity reranking: boost results whose entity sits near the
        # anchor entity in the relationship graph (Graphiti-style node-distance).
        if near_entity and results:
            results = await _rerank_by_proximity(
                db, namespace, req.agent_id, near_entity, near_key, results, req.as_of
            )
            span.set_attribute("graph_rerank", True)

        # hybrid_recall always returns Memory objects (Change 1 fetch-back ensures this)
        with tracer.start_as_current_span("recall.assemble"):
            memories_out: list[MemoryOut] = []
            for mem, _score, content in results:
                mem_out = _memory_to_out(mem, content)
                mem_out.score = _score
                memories_out.append(mem_out)

        if req.include_context and memories_out:
            with tracer.start_as_current_span("recall.context"):
                await _attach_context(
                    db, namespace, req.agent_id, memories_out, subject_keys,
                    as_of=req.as_of,
                )

        audit_payload = {
            "query_hash": _content_hash(req.query),
            "k": req.k,
            "as_of": req.as_of.isoformat() if req.as_of else None,
            "filters": req.filters,
            "result_ids": [str(m.id) for m in memories_out],
        }
        if retrieval_degraded:
            audit_payload["retrieval_degraded"] = True
        recall_log = await chain_log(
            db, namespace=namespace, agent_id=req.agent_id,
            op="recall",
            payload=audit_payload,
        )
        await db.commit()

        result = RecallResult(
            memories=memories_out,
            as_of=req.as_of,
            total_candidates=len(results),
            retrieval_degraded=retrieval_degraded,
            token_estimate=sum(
                _estimate_tokens(m.content) for m in memories_out if m.content),
        )

        from .metering import get_customer_id, queue_usage_event
        customer_id = await get_customer_id(db, namespace)
        if customer_id:
            queue_usage_event(
                settings.stripe_meter_recall_event,
                customer_id, 1, f"r:{recall_log.id}",
            )

        # Never cache a degraded result — it would keep serving lexical-only
        # recall after the embedding provider recovers.
        if (
            settings.recall_cache_enabled and not req.as_of and not near_entity
            and barrier_override is None and not retrieval_degraded
        ):
            await set_cached_recall(
                namespace, req.agent_id, req.query, req.as_of, req.k, req.filters,
                result.model_dump_json(),
                settings.recall_cache_ttl_seconds,
            )

        record_recall(
            namespace,
            router="semantic_degraded" if retrieval_degraded else "semantic",
            cache_hit=False,
        )
        observe_recall(namespace, _time.perf_counter() - _recall_t0)
        return result


async def _rerank_by_proximity(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    anchor: str,
    near_key: str,
    results: list,
    as_of: Optional[datetime],
) -> list:
    """
    Reorder recall results by graph proximity to ``anchor``.

    Each result's entity is read from metadata[``near_key``]; its hop-distance to
    the anchor in the relationship graph yields an additive proximity bonus
    (1/(1+distance)), so closely-connected facts rise without displacing strong
    semantic matches. Unreachable entities get no bonus — pure semantic order.
    """
    from .graph_service import entity_distances, canon_entity

    candidates: set[str] = set()
    for mem, _score, _content in results:
        val = (mem.metadata_ or {}).get(near_key)
        if val:
            candidates.add(str(val))
    if not candidates:
        return results

    distances = await entity_distances(
        db, namespace, agent_id, anchor, candidates, as_of=as_of
    )

    def _key(item):
        mem, score, _content = item
        val = (mem.metadata_ or {}).get(near_key)
        dist = distances.get(canon_entity(str(val))) if val else None
        bonus = 1.0 / (1.0 + dist) if dist is not None else 0.0
        return score + bonus

    return sorted(results, key=_key, reverse=True)


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

    pruned_agents: set[str] = set()
    for mem in memories:
        mem.content_encrypted = None
        mem.embedding = None
        mem.erased_at = now
        pruned_agents.add(mem.agent_id)
        await chain_log(
            db, namespace=namespace, agent_id=mem.agent_id,
            op="retention_prune", memory_id=mem.id,
            content_hash=mem.content_hash,
            payload={"cutoff_date": cutoff.isoformat(), "content_ttl_days": pol.content_ttl_days},
        )

    # Same tombstone hazard as erase_subject: pruned content must leave the
    # present-time read model and caches, or recall returns empty husks.
    await remove_live_facts(db, [mem.id for mem in memories])

    await db.commit()

    for aid in pruned_agents:
        invalidate_working_set(namespace, aid)
        await invalidate_agent(namespace, aid)

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
        # The embedding is derived from the content (inversion attacks can
        # approximate the original text) and metadata routinely carries
        # personal identifiers — GDPR erasure must shred both, not just the
        # ciphertext.
        mem.embedding = None
        mem.metadata_ = {}
        mem.erased_at = now
        agent_ids.add(mem.agent_id)
        await chain_log(
            db, namespace=namespace, agent_id=mem.agent_id,
            op="erase", memory_id=mem.id,
            content_hash=mem.content_hash,
            payload={"subject_id": subject_id, "request_ref": request_ref},
        )

    # Purge the denormalized present-time read model. Without this the next
    # working-set fill resurrects the erased fact as a null-content tombstone
    # in recall results (with its own denormalized embedding copy).
    await remove_live_facts(db, [mem.id for mem in memories])

    await destroy_subject_key(db, subject_id, namespace)

    if memories:
        from .webhook_service import dispatch_event, MEMORY_ERASED
        await dispatch_event(db, namespace, MEMORY_ERASED, {
            "subject_id": subject_id,
            "request_ref": request_ref,
            "memories_erased": len(memories),
        })

    await db.commit()

    # Change 6: evict destroyed key from DEK cache
    evict_dek(namespace, subject_id)
    # Change 7: invalidate session caches for all agents that had this subject's data
    for aid in agent_ids:
        invalidate_working_set(namespace, aid)
        await invalidate_agent(namespace, aid)

    record_erase(namespace, len(memories))
    return len(memories)


async def get_knowledge_snapshot(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    as_of: datetime,
    limit: int = 1000,
) -> list[MemoryOut]:
    """
    Exhaustive point-in-time knowledge state — every memory valid at *as_of*.

    Unlike :func:`recall_memories` (vector search → top-k), this returns *all*
    memories whose validity window contains ``as_of``
    (``valid_from <= as_of < valid_to``) and whose ``event_time <= as_of``,
    ordered by ``event_time`` ascending. No relevance filter is applied —
    regulators want the complete state, not the most relevant slice.

    Content is decrypted where the per-subject key is still live; memories whose
    subject key was crypto-shredded return ``content=None`` (existence and
    metadata preserved, content unrecoverable). This is the read side of the
    GDPR/HIPAA erasure guarantee.
    """
    stmt = (
        select(Memory)
        .where(
            and_(
                Memory.namespace == namespace,
                Memory.agent_id == agent_id,
                Memory.valid_from <= as_of,
                or_(Memory.valid_to.is_(None), Memory.valid_to > as_of),
                Memory.event_time <= as_of,
                # No erased_at filter: crypto-shredded memories appear as
                # tombstones (content=None, existence + hash preserved) — an
                # examiner must see that a fact existed even after erasure.
            )
        )
        .order_by(Memory.event_time.asc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    mems = result.scalars().all()

    # Decrypt content using the namespace's live subject keys.
    from .ranking import _decrypt

    subject_keys = await _load_namespace_subject_keys(db, namespace)
    return [_memory_to_out(m, _decrypt(m, subject_keys)) for m in mems]


def _lineage_node(mem: Memory, content: Optional[str]) -> LineageNode:
    return LineageNode(
        id=mem.id,
        content=content,
        content_hash=mem.content_hash,
        event_time=mem.event_time,
        ingestion_time=mem.ingestion_time,
        valid_from=mem.valid_from,
        valid_to=mem.valid_to,
        source=mem.source,
        importance=mem.importance,
        supersession_confidence=mem.supersession_confidence,
        erased_at=mem.erased_at,
        metadata=dict(mem.metadata_ or {}),
        # The live tip of the chain: nothing supersedes it and it is still valid.
        is_current=(mem.superseded_by is None and mem.valid_to is None),
    )


async def get_memory_lineage(
    db: AsyncSession,
    namespace: str,
    memory_id: UUID,
) -> MemoryLineageResult:
    """
    Reconstruct the full belief-provenance chain a memory belongs to.

    Walks the ``superseded_by`` pointers forward (to the current tip) and backward
    (to the oldest ancestor), then returns every version oldest-first with the
    supersession edges connecting them. The queried memory may sit anywhere in the
    chain — root, tip, or middle.

    Edge metadata (relation, confidence, rationale, adjudication stage) is read
    from the tamper-evident ``supersede`` event-log rows, so the lineage is
    backed by the same audit trail an examiner would inspect.
    """
    from fastapi import HTTPException

    queried = await db.get(Memory, memory_id)
    if queried is None or queried.namespace != namespace:
        raise HTTPException(status_code=404, detail="Memory not found")

    # Walk forward: follow superseded_by until the live tip (superseded_by IS NULL).
    forward: list[Memory] = []
    cursor: Optional[Memory] = queried
    seen: set = set()
    while cursor is not None and cursor.id not in seen:
        forward.append(cursor)
        seen.add(cursor.id)
        if cursor.superseded_by is None:
            break
        cursor = await db.get(Memory, cursor.superseded_by)

    # Walk backward: find the memory whose superseded_by points AT the current root.
    backward: list[Memory] = []
    current_root = queried
    while True:
        stmt = select(Memory).where(
            and_(
                Memory.namespace == namespace,
                Memory.superseded_by == current_root.id,
            )
        )
        older = (await db.execute(stmt)).scalars().first()
        if older is None or older.id in seen:
            break
        backward.append(older)
        seen.add(older.id)
        current_root = older

    # Oldest-first: reversed backward ancestors, then the forward chain.
    ordered = list(reversed(backward)) + forward

    # Decrypt content for every node in one pass.
    subject_keys = await _load_namespace_subject_keys(db, namespace)
    from .ranking import _decrypt
    nodes = [_lineage_node(m, _decrypt(m, subject_keys)) for m in ordered]

    # Build edges from the supersede event-log rows (older -> newer).
    edges: list[LineageEdge] = []
    for older, newer in zip(ordered, ordered[1:]):
        log_stmt = (
            select(EventLog)
            .where(
                and_(
                    EventLog.namespace == namespace,
                    EventLog.op == "supersede",
                    EventLog.memory_id == older.id,
                )
            )
            .order_by(EventLog.created_at.desc())
        )
        row = (await db.execute(log_stmt)).scalars().first()
        payload = dict(row.payload) if row and row.payload else {}
        edges.append(LineageEdge(
            from_id=older.id,
            to_id=newer.id,
            relation=payload.get("relation", "SUPERSEDES"),
            confidence=float(
                payload.get("confidence", older.supersession_confidence or 1.0)
            ),
            rationale=payload.get("rationale"),
            adjudication_stage=int(payload.get("adjudication_stage", 2)),
            superseded_at=row.created_at if row else older.valid_to or older.ingestion_time,
        ))

    root = ordered[0]
    tip = ordered[-1]
    return MemoryLineageResult(
        agent_id=queried.agent_id,
        namespace=namespace,
        queried_id=memory_id,
        root_id=root.id,
        tip_id=tip.id,
        depth=len(nodes),
        nodes=nodes,
        edges=edges,
    )


async def get_structured_fact_history(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    key_values: dict[str, str],
    adapter,
    limit: int = 100,
) -> list[MemoryOut]:
    """
    Return every recorded version of a structured fact, ordered by event_time asc.

    ``key_values`` is an already-normalized structured-key map (e.g.
    ``{"ticker": "AAPL", "metric": "eps"}`` for finance, ``{"patient_id": ...,
    "condition": ...}`` for healthcare, ``{"matter_id": ..., "claim_type": ...}``
    for legal). Superseded versions are included so analysts can see how the fact
    evolved. Entity normalization is applied through the domain ``adapter`` so
    'Apple Inc.', 'AAPL', and ISIN 'US0378331005' all collapse to one series.
    """
    stmt = (
        select(Memory)
        .where(
            and_(
                Memory.namespace == namespace,
                Memory.agent_id == agent_id,
                Memory.erased_at.is_(None),
            )
        )
        .order_by(Memory.event_time.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    # For each requested (canonical) key, accept any of its metadata aliases.
    # e.g. for finance, 'ticker' is satisfied by metadata 'ticker' | 'entity' |
    # 'isin' | 'cusip' — all normalized to the same canonical value.
    alias_map = {c: adapter.key_aliases(c) for c in key_values}

    matched: list[Memory] = []
    for mem in rows:
        meta = dict(mem.metadata_ or {})
        ok = True
        for canonical, want in key_values.items():
            found = None
            for alias in alias_map[canonical]:
                if alias in meta:
                    found = adapter.normalize(canonical, str(meta[alias]))
                    break
            if found != want:
                ok = False
                break
        if ok:
            matched.append(mem)
            if len(matched) >= limit:
                break

    subject_keys = await _load_namespace_subject_keys(db, namespace)
    from .ranking import _decrypt
    return [_memory_to_out(m, _decrypt(m, subject_keys)) for m in matched]


# ── Conflicts ──────────────────────────────────────────────────────────────────


async def list_conflicts(
    db: AsyncSession,
    namespace: str,
    status: Optional[str] = "open",
    limit: int = 50,
) -> ConflictListResult:
    """
    List conflict flags for a namespace, newest first.

    Each conflict carries the decrypted content, source, and event-time of *both*
    disagreeing memories so a reviewer can decide which source to trust. Pass
    ``status`` to filter (``open`` | ``accept_a`` | ``accept_b`` | ``dismissed``),
    or ``None`` for all statuses.
    """
    conds = [ConflictFlag.namespace == namespace]
    if status:
        conds.append(ConflictFlag.status == status)
    stmt = (
        select(ConflictFlag)
        .where(and_(*conds))
        .order_by(ConflictFlag.detected_at.desc())
        .limit(limit)
    )
    flags = (await db.execute(stmt)).scalars().all()

    subject_keys = await _load_namespace_subject_keys(db, namespace)
    from .ranking import _decrypt

    conflicts: list[ConflictFlagOut] = []
    for flag in flags:
        mem_a = await db.get(Memory, flag.memory_a_id)
        mem_b = await db.get(Memory, flag.memory_b_id)
        conflicts.append(ConflictFlagOut(
            id=flag.id,
            namespace=flag.namespace,
            agent_id=flag.agent_id,
            memory_a_id=flag.memory_a_id,
            memory_b_id=flag.memory_b_id,
            memory_a_content=_decrypt(mem_a, subject_keys) if mem_a else None,
            memory_b_content=_decrypt(mem_b, subject_keys) if mem_b else None,
            memory_a_source=mem_a.source if mem_a else None,
            memory_b_source=mem_b.source if mem_b else None,
            memory_a_event_time=mem_a.event_time if mem_a else flag.detected_at,
            memory_b_event_time=mem_b.event_time if mem_b else flag.detected_at,
            confidence=flag.confidence,
            detected_at=flag.detected_at,
            status=flag.status,
            resolved_at=flag.resolved_at,
            resolver_note=flag.resolver_note,
        ))

    return ConflictListResult(
        conflicts=conflicts,
        total=len(conflicts),
        status_filter=status,
    )


async def resolve_conflict(
    db: AsyncSession,
    namespace: str,
    conflict_id: UUID,
    req: ConflictResolveRequest,
) -> ConflictResolveResult:
    """
    Resolve a conflict flag and append a tamper-evident ``conflict_resolved`` event.

    ``accept_a`` invalidates memory_b; ``accept_b`` invalidates memory_a;
    ``dismiss`` leaves both live. Resolving a non-existent / cross-namespace
    conflict raises 404; resolving an already-resolved one raises 409; an unknown
    resolution raises 422.
    """
    from fastapi import HTTPException

    if req.resolution not in ("accept_a", "accept_b", "dismiss"):
        raise HTTPException(status_code=422, detail="resolution must be accept_a, accept_b, or dismiss")

    flag = await db.get(ConflictFlag, conflict_id)
    if flag is None or flag.namespace != namespace:
        raise HTTPException(status_code=404, detail="Conflict not found")
    if flag.status != "open":
        raise HTTPException(status_code=409, detail="Conflict already resolved")

    now = datetime.now(timezone.utc)
    invalidated: Optional[UUID] = None

    if req.resolution == "accept_a":
        invalidated = flag.memory_b_id
    elif req.resolution == "accept_b":
        invalidated = flag.memory_a_id

    if invalidated is not None:
        loser = await db.get(Memory, invalidated)
        if loser is not None:
            loser.valid_to = now
            await remove_live_facts(db, [invalidated])

    flag.status = "dismissed" if req.resolution == "dismiss" else req.resolution
    flag.resolved_at = now
    flag.resolver_note = req.note

    await chain_log(
        db, namespace=namespace, agent_id=flag.agent_id,
        op="conflict_resolved", memory_id=flag.memory_b_id,
        content_hash=None,
        payload={
            "conflict_id": str(conflict_id),
            "resolution": req.resolution,
            "memory_invalidated": str(invalidated) if invalidated else None,
            "note": req.note,
            "resolved_at": now.isoformat(),
        },
    )
    await db.commit()
    invalidate_working_set(namespace, flag.agent_id)
    await invalidate_agent(namespace, flag.agent_id)

    return ConflictResolveResult(
        conflict_id=conflict_id,
        resolution=req.resolution,
        resolved_at=now,
        memory_invalidated=invalidated,
    )


# ── Erasure certificate ────────────────────────────────────────────────────────


async def get_erasure_certificate(
    db: AsyncSession,
    namespace: str,
    subject_id: str,
) -> Optional[dict]:
    """
    Build a proof-of-erasure certificate for a crypto-shredded data subject.

    Reads the ``erase`` event-log rows for the subject — their content was
    destroyed but the SHA-256 ``content_hash`` of each survives — and reports the
    preserved hashes plus the current audit-chain status. Returns ``None`` when no
    erasure has been recorded for the subject (the route turns that into a 404).
    """
    import uuid as _uuid

    stmt = (
        select(EventLog)
        .where(
            and_(
                EventLog.namespace == namespace,
                EventLog.op == "erase",
            )
        )
        .order_by(EventLog.created_at.asc())
    )
    rows = [
        r for r in (await db.execute(stmt)).scalars().all()
        if dict(r.payload or {}).get("subject_id") == subject_id
    ]
    if not rows:
        return None

    content_hashes = [r.content_hash for r in rows if r.content_hash]
    erased_at = max(r.created_at for r in rows)
    request_ref = next(
        (dict(r.payload or {}).get("request_ref") for r in rows
         if dict(r.payload or {}).get("request_ref")),
        None,
    )

    from .audit_chain import verify_chain as _verify_chain
    try:
        chain = await _verify_chain(db, namespace=namespace)
        chain_status = chain.get("status", "unchecked")
    except Exception:
        chain_status = "unchecked"

    certificate_id = str(_uuid.uuid5(
        _uuid.NAMESPACE_URL,
        f"lians-erasure:{namespace}:{subject_id}:{erased_at.isoformat()}",
    ))

    return {
        "certificate_id": certificate_id,
        "subject_id": subject_id,
        "namespace": namespace,
        "request_ref": request_ref,
        "erased_at": erased_at,
        "memories_erased": len(rows),
        "content_hashes": content_hashes,
        "chain_status": chain_status,
        "generated_at": datetime.now(timezone.utc),
    }
