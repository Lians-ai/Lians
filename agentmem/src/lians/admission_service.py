"""
Service layer for memory admission control — the held-for-review queue and its
resolution. Every decision is written to the tamper-evident audit chain, so the
admission trail itself is examiner-grade.
"""
from __future__ import annotations

import base64
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from .admission import AdmissionDecision
from .audit_chain import chain_log
from .crypto import decrypt_content, encrypt_content
from .models import EventLog, PendingAdmission, SubjectKey
from .pii import (
    get_or_create_subject_key,
    lock_subject_key_for_update,
)
from .schemas import MemoryAdd
from .secret_storage import PENDING_CONTENT_PURPOSE, seal_text, unseal_text
from .subject_key_loader import load_subject_keys
from .subject_privacy import replace_subject_identifier, subject_reference

_SUBJECT_CONTENT_PREFIX = "lians-subject-content-v1:"
_PENDING_KEY_BIND_BATCH = 400
_PENDING_PAGE_RESPONSE_MULTIPLIER = 4
_PENDING_PAGE_ROW_OVERHEAD_BYTES = 4 * 1024


class PendingContentIntegrityError(RuntimeError):
    """A queued row cannot be decrypted without weakening its key boundary."""


class PendingContentPageCapacityExceeded(RuntimeError):
    """A complete review page cannot fit its plaintext response budget."""

    def __init__(self, *, estimated_bytes: int, byte_limit: int) -> None:
        super().__init__("Pending admission page exceeds its response byte budget")
        self.estimated_bytes = estimated_bytes
        self.byte_limit = byte_limit


async def materialize_pending_page(
    db: AsyncSession,
    pending_rows: Sequence[PendingAdmission],
) -> list[PendingAdmission]:
    """Preflight sealed/JSON bytes before hydrating one content-bearing page."""

    rows = list(pending_rows)
    if not rows:
        return []
    row_ids = [row.id for row in rows]
    serialized_characters = 0
    for start in range(0, len(row_ids), _PENDING_KEY_BIND_BATCH):
        chunk = row_ids[start : start + _PENDING_KEY_BIND_BATCH]
        serialized_characters += int(
            (
                await db.execute(
                    select(
                        func.coalesce(
                            func.sum(
                                func.length(PendingAdmission.content)
                                + func.coalesce(
                                    func.length(
                                        cast(PendingAdmission.risk_tags, Text)
                                    ),
                                    0,
                                )
                                + func.coalesce(
                                    func.length(cast(PendingAdmission.reasons, Text)),
                                    0,
                                )
                            ),
                            0,
                        )
                    ).where(PendingAdmission.id.in_(chunk))
                )
            ).scalar_one()
            or 0
        )
    estimated_bytes = (
        serialized_characters * _PENDING_PAGE_RESPONSE_MULTIPLIER
        + len(rows) * _PENDING_PAGE_ROW_OVERHEAD_BYTES
    )
    from .config import get_settings

    byte_limit = get_settings().content_export_page_bytes_limit
    if estimated_bytes > byte_limit:
        raise PendingContentPageCapacityExceeded(
            estimated_bytes=estimated_bytes,
            byte_limit=byte_limit,
        )

    hydrated_by_id: dict[UUID, PendingAdmission] = {}
    for start in range(0, len(row_ids), _PENDING_KEY_BIND_BATCH):
        chunk = row_ids[start : start + _PENDING_KEY_BIND_BATCH]
        hydrated = (
            (
                await db.execute(
                    select(PendingAdmission)
                    .options(
                        defer(PendingAdmission.metadata_, raiseload=True),
                        defer(PendingAdmission.resolver_note, raiseload=True),
                    )
                    .where(PendingAdmission.id.in_(chunk))
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        hydrated_by_id.update((row.id, row) for row in hydrated)
    if set(hydrated_by_id) != set(row_ids):
        raise PendingContentIntegrityError(
            "Pending admission page changed between inventory and hydration"
        )
    return [hydrated_by_id[row_id] for row_id in row_ids]


async def decrypt_pending_contents(
    db: AsyncSession,
    pending_rows: Sequence[PendingAdmission],
) -> list[str]:
    """Decrypt one bounded queue page without per-row I/O or key creation.

    A read must never mint a replacement key for a missing durable key row.  A
    destroyed key is the expected erasure state and returns the tombstone; a
    missing, corrupt, or otherwise unavailable active key is an integrity
    failure for the entire page.
    """

    rows = list(pending_rows)
    namespace = rows[0].namespace if rows else ""
    if any(row.namespace != namespace for row in rows):
        raise PendingContentIntegrityError(
            "Pending content page crosses a namespace boundary"
        )
    subject_ids = sorted(
        {
            str(row.subject_id)
            for row in rows
            if row.content.startswith(_SUBJECT_CONTENT_PREFIX) and row.subject_id
        }
    )
    key_destroyed: dict[str, bool] = {}
    for start in range(0, len(subject_ids), _PENDING_KEY_BIND_BATCH):
        chunk = subject_ids[start : start + _PENDING_KEY_BIND_BATCH]
        states = (
            await db.execute(
                select(SubjectKey.subject_id, SubjectKey.destroyed_at).where(
                    SubjectKey.namespace == namespace,
                    SubjectKey.subject_id.in_(chunk),
                )
            )
        ).all()
        key_destroyed.update(
            (str(subject_id), destroyed_at is not None)
            for subject_id, destroyed_at in states
        )
    active_keys = await load_subject_keys(
        db,
        namespace,
        subject_ids,
    )

    plaintexts: list[str] = []
    for pending in rows:
        try:
            if not pending.content.startswith(_SUBJECT_CONTENT_PREFIX):
                plaintexts.append(
                    unseal_text(
                        pending.content,
                        purpose=PENDING_CONTENT_PURPOSE,
                        context=pending.namespace,
                    )
                )
                continue
            if not pending.subject_id:
                raise PendingContentIntegrityError(
                    "Subject-encrypted pending content has no subject reference"
                )
            subject_id = str(pending.subject_id)
            if subject_id not in key_destroyed:
                raise PendingContentIntegrityError(
                    "Subject-encrypted pending content has no durable key"
                )
            if key_destroyed[subject_id]:
                plaintexts.append("[ERASED]")
                continue
            key = active_keys.get(subject_id)
            if key is None:
                raise PendingContentIntegrityError(
                    "Subject-encrypted pending content key is unavailable"
                )
            encoded = pending.content.removeprefix(_SUBJECT_CONTENT_PREFIX)
            plaintexts.append(
                decrypt_content(base64.b64decode(encoded, validate=True), key)
            )
        except PendingContentIntegrityError:
            raise
        except Exception as exc:
            raise PendingContentIntegrityError(
                "Pending content failed authenticated decryption"
            ) from exc
    return plaintexts


async def decrypt_pending_content(db: AsyncSession, pending: PendingAdmission) -> str:
    """Compatibility wrapper for a single queued row."""

    return (await decrypt_pending_contents(db, [pending]))[0]


async def record_rejection(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    decision: AdmissionDecision,
    *,
    commit: bool = True,
) -> EventLog:
    """Audit a write that admission control rejected outright (injection / blocked source)."""
    event = await chain_log(
        db, namespace=namespace, agent_id=agent_id, op="admission_rejected",
        payload={"risk_tags": decision.risk_tags, "reasons": decision.reasons},
    )
    if commit:
        await db.commit()
    return event


async def enqueue_pending(
    db: AsyncSession, namespace: str, req: MemoryAdd, decision: AdmissionDecision,
    barrier_override: Optional[str] = None,
    *,
    commit: bool = True,
) -> PendingAdmission:
    """Park a high-risk write for human review (enforce mode)."""
    from .memory_service import _acquire_pg_advisory_lock, _get_barrier_group

    raw_subject_id = req.subject_id
    persisted_subject_ref = subject_reference(namespace, raw_subject_id)
    metadata = req.metadata or {}
    source = req.source
    if raw_subject_id and persisted_subject_ref:
        metadata = replace_subject_identifier(
            metadata, raw_subject_id, persisted_subject_ref
        )
        if source == raw_subject_id:
            source = persisted_subject_ref
        subject_key = await get_or_create_subject_key(
            db,
            persisted_subject_ref,
            namespace,
            legacy_subject_id=raw_subject_id,
        )
        content = _SUBJECT_CONTENT_PREFIX + base64.b64encode(
            encrypt_content(req.content, subject_key)
        ).decode("ascii")
    else:
        content = seal_text(
            req.content,
            purpose=PENDING_CONTENT_PURPOSE,
            context=namespace,
        )
    await _acquire_pg_advisory_lock(db, namespace, req.agent_id)
    effective_barrier = await _get_barrier_group(
        db,
        namespace,
        req.agent_id,
        override=barrier_override,
    )
    pending = PendingAdmission(
        namespace=namespace,
        agent_id=req.agent_id,
        barrier_group=effective_barrier,
        content=content,
        event_time=req.event_time,
        source=source,
        subject_id=persisted_subject_ref,
        metadata_=metadata,
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
    if commit:
        await db.commit()
        await db.refresh(pending)
    else:
        await db.flush()
    return pending


# Callers naturally reach for the write-path vocabulary ("admit" is what the
# admission engine calls the action; the API's canonical resolve verb is
# "approve"). Accept both spellings everywhere rather than 422ing on the synonym.
_ACTION_ALIASES = {"admit": "approve"}
_STATUS_ALIASES = {"admitted": "approved"}


async def list_pending(
    db: AsyncSession, namespace: str, status: Optional[str] = "pending", limit: int = 50,
    barrier_override: Optional[str] = None,
    after_created_at: Optional[datetime] = None,
    after_id: Optional[UUID] = None,
) -> list[PendingAdmission]:
    conds = _pending_conditions(
        namespace,
        status=status,
        barrier_override=barrier_override,
    )
    if after_created_at is not None and after_id is not None:
        conds.append(
            or_(
                PendingAdmission.created_at < after_created_at,
                and_(
                    PendingAdmission.created_at == after_created_at,
                    PendingAdmission.id < after_id,
                ),
            )
        )
    stmt = (
        select(PendingAdmission)
        .options(
            defer(PendingAdmission.content, raiseload=True),
            defer(PendingAdmission.metadata_, raiseload=True),
            defer(PendingAdmission.resolver_note, raiseload=True),
        )
        .where(and_(*conds))
        .order_by(PendingAdmission.created_at.desc(), PendingAdmission.id.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


def _pending_conditions(
    namespace: str,
    *,
    status: Optional[str],
    barrier_override: Optional[str],
) -> list[Any]:
    conds: list[Any] = [PendingAdmission.namespace == namespace]
    if barrier_override is not None:
        conds.append(or_(
            PendingAdmission.barrier_group.is_(None),
            PendingAdmission.barrier_group == barrier_override,
        ))
    if status:
        conds.append(PendingAdmission.status == _STATUS_ALIASES.get(status, status))
    return conds


async def count_pending(
    db: AsyncSession,
    namespace: str,
    *,
    status: Optional[str] = "pending",
    barrier_override: Optional[str] = None,
) -> int:
    """Return the exact collection cardinality independent of page position."""

    conditions = _pending_conditions(
        namespace,
        status=status,
        barrier_override=barrier_override,
    )
    return int(
        (
            await db.execute(
                select(func.count(PendingAdmission.id)).where(and_(*conditions))
            )
        ).scalar_one()
    )


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

    conditions = [
        PendingAdmission.id == pending_id,
        PendingAdmission.namespace == namespace,
    ]
    if barrier_override is not None:
        conditions.append(
            or_(
                PendingAdmission.barrier_group.is_(None),
                PendingAdmission.barrier_group == barrier_override,
            )
        )
    observed = (
        await db.execute(select(PendingAdmission).where(*conditions))
    ).scalar_one_or_none()
    if observed is None:
        raise HTTPException(status_code=404, detail="Pending admission not found")
    # Approval decrypts and persists subject-bearing content. Take the subject
    # fence before the pending-row lock so it has the same lock order as erase.
    if action == "approve" and observed.subject_id:
        await lock_subject_key_for_update(db, observed.subject_id, namespace)
    pending = (
        await db.execute(
            select(PendingAdmission)
            .where(*conditions)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if pending is None:
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
        content=await decrypt_pending_content(db, pending),
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
        db, namespace, req, barrier_override=effective_barrier, commit=False
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
