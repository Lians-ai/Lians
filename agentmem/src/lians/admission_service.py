"""
Service layer for memory admission control — the held-for-review queue and its
resolution. Every decision is written to the tamper-evident audit chain, so the
admission trail itself is examiner-grade.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from .admission import AdmissionDecision
from .audit_chain import chain_log
from .models import PendingAdmission
from .schemas import MemoryAdd


async def record_rejection(
    db: AsyncSession, namespace: str, agent_id: str, decision: AdmissionDecision
) -> None:
    """Audit a write that admission control rejected outright (injection / blocked source)."""
    await chain_log(
        db, namespace=namespace, agent_id=agent_id, op="admission_rejected",
        payload={"risk_tags": decision.risk_tags, "reasons": decision.reasons},
    )
    await db.commit()


async def enqueue_pending(
    db: AsyncSession, namespace: str, req: MemoryAdd, decision: AdmissionDecision
) -> PendingAdmission:
    """Park a high-risk write for human review (enforce mode)."""
    pending = PendingAdmission(
        namespace=namespace,
        agent_id=req.agent_id,
        content=req.content,
        event_time=req.event_time,
        source=req.source,
        subject_id=req.subject_id,
        metadata_=req.metadata or {},
        importance=req.importance,
        risk_tags=decision.risk_tags,
        reasons=decision.reasons,
        status="pending",
    )
    db.add(pending)
    await chain_log(
        db, namespace=namespace, agent_id=req.agent_id, op="admission_held",
        payload={"risk_tags": decision.risk_tags, "reasons": decision.reasons},
    )
    await db.commit()
    await db.refresh(pending)
    return pending


async def list_pending(
    db: AsyncSession, namespace: str, status: Optional[str] = "pending", limit: int = 50
) -> list[PendingAdmission]:
    conds = [PendingAdmission.namespace == namespace]
    if status:
        conds.append(PendingAdmission.status == status)
    stmt = (
        select(PendingAdmission)
        .where(and_(*conds))
        .order_by(PendingAdmission.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def resolve_pending(
    db: AsyncSession, namespace: str, pending_id: UUID, action: str, note: Optional[str] = None
) -> dict[str, Any]:
    """
    Approve (→ the memory is created) or reject a held write. Records the decision
    on the audit chain either way.
    """
    from fastapi import HTTPException
    from .memory_service import add_memory

    if action not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="action must be 'approve' or 'reject'")

    pending = await db.get(PendingAdmission, pending_id)
    if pending is None or pending.namespace != namespace:
        raise HTTPException(status_code=404, detail="Pending admission not found")
    if pending.status != "pending":
        raise HTTPException(status_code=409, detail="Already resolved")

    now = datetime.now(timezone.utc)
    pending.resolved_at = now
    pending.resolver_note = note

    if action == "reject":
        pending.status = "rejected"
        await chain_log(
            db, namespace=namespace, agent_id=pending.agent_id,
            op="admission_review_rejected",
            payload={"pending_id": str(pending_id), "note": note},
        )
        await db.commit()
        return {"status": "rejected", "pending_id": str(pending_id)}

    # approve → admit the memory now
    req = MemoryAdd(
        agent_id=pending.agent_id,
        content=pending.content,
        event_time=pending.event_time,
        source=pending.source,
        subject_id=pending.subject_id,
        metadata={**dict(pending.metadata_ or {}),
                  "_admission": {"action": "approved", "risk_tags": list(pending.risk_tags or [])}},
        importance=pending.importance,
    )
    mem = await add_memory(db, namespace, req)
    pending.status = "approved"
    pending.memory_id = mem.id
    await chain_log(
        db, namespace=namespace, agent_id=pending.agent_id,
        op="admission_approved", memory_id=mem.id,
        payload={"pending_id": str(pending_id), "note": note},
    )
    await db.commit()
    return {"status": "approved", "pending_id": str(pending_id), "memory_id": str(mem.id)}
