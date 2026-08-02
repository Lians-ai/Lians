"""
Service layer for memory admission control — the held-for-review queue and its
resolution. Every decision is written to the tamper-evident audit chain, so the
admission trail itself is examiner-grade.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .admission import AdmissionDecision, evaluate
from .audit_chain import chain_log
from .models import PendingAdmission
from .scoring import score_memory
from .schemas import MemoryAdd
from .secret_storage import PENDING_CONTENT_PURPOSE, seal_text, unseal_text


class MemoryAdmissionRejected(ValueError):
    """A candidate was rejected by the configured admission policy."""

    def __init__(self, decision: AdmissionDecision):
        self.decision = decision
        super().__init__("; ".join(decision.reasons) or "memory admission rejected")


class MemoryAdmissionReviewRequired(ValueError):
    """A candidate was durably parked for admission review."""

    def __init__(self, decision: AdmissionDecision, pending_id: UUID):
        self.decision = decision
        self.pending_id = pending_id
        super().__init__(f"memory admission held for review: pending_id={pending_id}")


def attach_memory_admission(
    req: MemoryAdd,
    decision: AdmissionDecision,
    *,
    action: Optional[str] = None,
    safety_status: Optional[str] = None,
) -> None:
    """Replace caller-controlled engine metadata with one evaluated decision."""
    recorded_action = action or decision.action
    status = safety_status or {
        "admit": "safe",
        "review": "review_needed",
        "reject": "rejected",
    }.get(decision.action, "review_needed")
    caller_metadata = dict(req.metadata or {})
    caller_metadata.pop("_admission", None)
    caller_metadata.pop("_score", None)
    breakdown = score_memory(
        content=req.content,
        reference_time=req.event_time,
        event_time=req.event_time,
        valid_from=req.event_time,
        metadata=caller_metadata,
        importance=req.importance,
        source=req.source,
        safety_status=status,
        risk_tags=decision.risk_tags,
        purpose="admission",
    )
    req.metadata = {
        **caller_metadata,
        "_admission": {
            "action": recorded_action,
            "risk_tags": list(decision.risk_tags),
        },
        "_score": breakdown,
    }


def evaluate_memory_admission(
    req: MemoryAdd,
    *,
    mode: str,
    blocked_sources: str | Iterable[str] | None = None,
) -> AdmissionDecision:
    """Evaluate a write and replace caller-controlled admission metadata.

    ``_admission`` and ``_score`` are reserved engine fields. Keeping their
    normalization beside admission evaluation gives HTTP and embedded clients
    one canonical safety boundary, so callers cannot forge an eligible score
    or a prior approval through any published write path.
    """
    if isinstance(blocked_sources, str):
        source_values: Iterable[str] = blocked_sources.split(",")
    else:
        source_values = blocked_sources or ()
    normalized_blocked_sources = {
        str(source).strip().lower()
        for source in source_values
        if str(source).strip()
    }

    decision = evaluate(
        req.content,
        req.source,
        mode=mode,
        blocked_sources=normalized_blocked_sources,
    )
    attach_memory_admission(req, decision)
    return decision


async def enforce_memory_admission(
    db: AsyncSession,
    namespace: str,
    req: MemoryAdd,
    *,
    barrier_override: Optional[str] = None,
) -> AdmissionDecision:
    """Canonical storage-boundary admission check for every untrusted write."""
    from .config import get_settings

    settings = get_settings()
    decision = evaluate_memory_admission(
        req,
        mode=settings.admission_mode,
        blocked_sources=settings.admission_blocked_sources,
    )
    if decision.action == "reject":
        await record_rejection(db, namespace, req.agent_id, decision)
        raise MemoryAdmissionRejected(decision)
    if decision.action == "review":
        pending = await enqueue_pending(
            db,
            namespace,
            req,
            decision,
            barrier_override=barrier_override,
        )
        raise MemoryAdmissionReviewRequired(decision, pending.id)
    return decision


def decrypt_pending_content(pending: PendingAdmission) -> str:
    return unseal_text(
        pending.content,
        purpose=PENDING_CONTENT_PURPOSE,
        context=pending.namespace,
    )


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
    db: AsyncSession, namespace: str, req: MemoryAdd, decision: AdmissionDecision,
    barrier_override: Optional[str] = None,
) -> PendingAdmission:
    """Park a high-risk write for human review (enforce mode)."""
    pending = PendingAdmission(
        namespace=namespace,
        agent_id=req.agent_id,
        barrier_group=barrier_override,
        content=seal_text(
            req.content,
            purpose=PENDING_CONTENT_PURPOSE,
            context=namespace,
        ),
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


# Callers naturally reach for the write-path vocabulary ("admit" is what the
# admission engine calls the action; the API's canonical resolve verb is
# "approve"). Accept both spellings everywhere rather than 422ing on the synonym.
_ACTION_ALIASES = {"admit": "approve"}
_STATUS_ALIASES = {"admitted": "approved"}


async def list_pending(
    db: AsyncSession, namespace: str, status: Optional[str] = "pending", limit: int = 50,
    barrier_override: Optional[str] = None,
) -> list[PendingAdmission]:
    conds = [PendingAdmission.namespace == namespace]
    if barrier_override is not None:
        conds.append(or_(
            PendingAdmission.barrier_group.is_(None),
            PendingAdmission.barrier_group == barrier_override,
        ))
    if status:
        conds.append(PendingAdmission.status == _STATUS_ALIASES.get(status, status))
    stmt = (
        select(PendingAdmission)
        .where(and_(*conds))
        .order_by(PendingAdmission.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def resolve_pending(
    db: AsyncSession, namespace: str, pending_id: UUID, action: str, note: Optional[str] = None,
    barrier_override: Optional[str] = None,
) -> dict[str, Any]:
    """
    Approve (→ the memory is created) or reject a held write. Records the decision
    on the audit chain either way.
    """
    from fastapi import HTTPException
    from .memory_service import add_memory

    action = _ACTION_ALIASES.get(action, action)
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="action must be 'approve' (alias: 'admit') or 'reject'")

    pending = await db.get(PendingAdmission, pending_id)
    if (
        pending is None
        or pending.namespace != namespace
        or (
            barrier_override is not None
            and pending.barrier_group not in (None, barrier_override)
        )
    ):
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
        content=decrypt_pending_content(pending),
        event_time=pending.event_time,
        source=pending.source,
        subject_id=pending.subject_id,
        metadata={**dict(pending.metadata_ or {}),
                  "_admission": {"action": "approved", "risk_tags": list(pending.risk_tags or [])}},
        importance=pending.importance,
    )
    # Preserve the barrier attached when the content entered the queue. An
    # unbarriered compliance reviewer may approve another desk's item, but that
    # must never turn the resulting memory into an unbarriered/shared record.
    effective_barrier = pending.barrier_group or barrier_override
    mem = await add_memory(
        db,
        namespace,
        req,
        barrier_override=effective_barrier,
        _trusted_admission=AdmissionDecision(
            "admit",
            list(pending.risk_tags or []),
            list(pending.reasons or []),
        ),
    )
    pending.status = "approved"
    pending.memory_id = mem.id
    await chain_log(
        db, namespace=namespace, agent_id=pending.agent_id,
        op="admission_approved", memory_id=mem.id,
        payload={"pending_id": str(pending_id), "note": note},
    )
    await db.commit()
    return {"status": "approved", "pending_id": str(pending_id), "memory_id": str(mem.id)}
