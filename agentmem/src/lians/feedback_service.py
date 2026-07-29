"""Persistent, policy-gated learning signals for memory quality."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import chain_log
from .models import LiveFact, Memory, MemoryFeedback
from .schemas import MemoryFeedbackCreate, MemoryFeedbackOut, MemoryLearningSummary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def record_memory_feedback(
    db: AsyncSession,
    namespace: str,
    memory_id: UUID,
    req: MemoryFeedbackCreate,
) -> MemoryFeedbackOut:
    memory = await db.get(Memory, memory_id)
    if memory is None or memory.namespace != namespace or memory.agent_id != req.agent_id:
        raise LookupError("memory not found")

    action = "record_only"
    importance = float(memory.importance)
    metadata = dict(memory.metadata_ or {})
    if req.signal == "helpful":
        importance = min(1.0, importance + 0.05 * req.weight)
        action = "importance_promoted"
    elif req.signal == "duplicate":
        importance = max(0.0, importance - 0.05 * req.weight)
        action = "importance_demoted"
    elif req.signal in {"incorrect", "outdated"}:
        # Negative model/user feedback never silently deletes or supersedes a
        # fact. It creates a durable review flag for human or trusted-policy
        # correction.
        metadata["_learning_review"] = {
            "status": "pending",
            "signal": req.signal,
            "at": _utcnow().isoformat(),
        }
        action = "flagged_for_review"

    memory.importance = importance
    memory.metadata_ = metadata
    await db.execute(
        update(LiveFact)
        .where(LiveFact.memory_id == memory_id)
        .values(importance=importance, metadata_=metadata)
    )
    row = MemoryFeedback(
        namespace=namespace,
        agent_id=req.agent_id,
        memory_id=memory_id,
        signal=req.signal,
        weight=req.weight,
        outcome=req.outcome,
        query_hash=hashlib.sha256(req.query.encode()).hexdigest() if req.query else None,
        source=req.source,
        note=req.note,
        policy_action=action,
    )
    db.add(row)
    await db.flush()
    await chain_log(
        db,
        namespace=namespace,
        agent_id=req.agent_id,
        op="memory_feedback",
        memory_id=memory_id,
        content_hash=memory.content_hash,
        payload={
            "feedback_id": str(row.id),
            "signal": req.signal,
            "weight": req.weight,
            "outcome": req.outcome,
            "policy_action": action,
        },
    )
    await db.commit()
    from .cache import invalidate_agent
    from .session_cache import invalidate_working_set
    await invalidate_agent(namespace, req.agent_id)
    invalidate_working_set(namespace, req.agent_id)
    return MemoryFeedbackOut(
        id=row.id,
        memory_id=memory_id,
        agent_id=req.agent_id,
        signal=req.signal,
        weight=req.weight,
        outcome=req.outcome,
        policy_action=action,
        memory_importance=importance,
        created_at=row.created_at,
    )


async def memory_learning_summary(
    db: AsyncSession,
    namespace: str,
    agent_id: Optional[str] = None,
) -> MemoryLearningSummary:
    conditions = [MemoryFeedback.namespace == namespace]
    if agent_id:
        conditions.append(MemoryFeedback.agent_id == agent_id)
    rows = (await db.execute(
        select(MemoryFeedback.signal, func.count())
        .where(*conditions)
        .group_by(MemoryFeedback.signal)
    )).all()
    counts = {str(signal): int(count) for signal, count in rows}
    total = sum(counts.values())
    review_conditions = [Memory.namespace == namespace]
    if agent_id:
        review_conditions.append(Memory.agent_id == agent_id)
    metadata_rows = (await db.execute(
        select(Memory.metadata_).where(*review_conditions)
    )).scalars().all()
    pending = sum(
        1 for metadata in metadata_rows
        if (dict(metadata or {}).get("_learning_review") or {}).get("status") == "pending"
    )
    helpful = counts.get("helpful", 0)
    return MemoryLearningSummary(
        agent_id=agent_id,
        total_feedback=total,
        helpful=helpful,
        incorrect=counts.get("incorrect", 0),
        outdated=counts.get("outdated", 0),
        duplicate=counts.get("duplicate", 0),
        ignored=counts.get("ignored", 0),
        helpful_rate=round(helpful / total, 4) if total else 0.0,
        memories_pending_review=pending,
    )
