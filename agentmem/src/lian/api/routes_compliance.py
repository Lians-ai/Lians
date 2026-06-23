"""
Compliance report endpoint.

GET /v1/compliance/report?from=<ISO>&to=<ISO>

Returns a structured JSON report covering the time window, suitable for
submission to regulators (SEC, FINRA, CFTC) or internal compliance teams.

Report sections:
  summary        — total memories, supersessions, conflicts, erasures
  audit_chain    — chain integrity status (ok / broken / unchecked)
  erasures       — GDPR Art. 17 / CCPA right-to-erasure events
  conflicts      — open and resolved conflict flags
  supersessions  — high-level supersession statistics
  retention      — namespace policy snapshot (TTL, legal hold, audit retention)

All timestamps are UTC ISO-8601.  All counts cover the requested window.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..api.deps import get_auth, AuthContext
from ..models import Memory, EventLog, ConflictFlag, NamespacePolicy
from ..audit_chain import verify_chain

router = APIRouter(prefix="/v1", tags=["compliance"])

_UTC = timezone.utc


# ── Response schema ────────────────────────────────────────────────────────────

class MemorySummary(BaseModel):
    total_memories: int
    active_memories: int         # valid_to IS NULL
    superseded_memories: int
    erased_memories: int
    new_in_window: int
    superseded_in_window: int


class AuditChainStatus(BaseModel):
    status: str                  # ok | broken | unchecked
    rows_checked: int
    violations: list[dict[str, Any]]


class ErasureSummary(BaseModel):
    total_requests: int
    total_records_erased: int
    subject_ids: list[str]       # anonymized subject IDs that had erasures


class ConflictSummary(BaseModel):
    open: int
    resolved_accept_a: int
    resolved_accept_b: int
    dismissed: int
    detected_in_window: int


class SupersessionSummary(BaseModel):
    total_supersessions: int
    confirmed_by_human: int
    rejected_by_human: int
    high_confidence: int         # confidence >= 0.9
    low_confidence: int          # confidence < 0.9 (review recommended)


class RetentionSnapshot(BaseModel):
    content_ttl_days: Optional[int]
    audit_retention_days: int
    legal_hold: bool
    stripe_customer_id: Optional[str]


class ComplianceReport(BaseModel):
    namespace: str
    generated_at: str
    window_from: Optional[str]
    window_to: Optional[str]
    summary: MemorySummary
    audit_chain: AuditChainStatus
    erasures: ErasureSummary
    conflicts: ConflictSummary
    supersessions: SupersessionSummary
    retention: Optional[RetentionSnapshot]


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/compliance/report", response_model=ComplianceReport)
async def compliance_report(
    from_: Optional[str] = Query(None, alias="from", description="ISO-8601 window start (UTC)"),
    to: Optional[str] = Query(None, description="ISO-8601 window end (UTC)"),
    verify: bool = Query(False, description="Run audit chain verification (adds latency)"),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    ns = auth.namespace

    # Parse window
    win_from: Optional[datetime] = None
    win_to: Optional[datetime] = None
    if from_:
        win_from = datetime.fromisoformat(from_.replace("Z", "+00:00"))
    if to:
        win_to = datetime.fromisoformat(to.replace("Z", "+00:00"))

    now = datetime.now(_UTC)

    # ── Memory counts ──────────────────────────────────────────────────────────

    total = (await db.execute(
        select(func.count()).where(Memory.namespace == ns)
    )).scalar_one()

    active = (await db.execute(
        select(func.count()).where(Memory.namespace == ns, Memory.valid_to.is_(None), Memory.erased_at.is_(None))
    )).scalar_one()

    superseded = (await db.execute(
        select(func.count()).where(Memory.namespace == ns, Memory.valid_to.isnot(None))
    )).scalar_one()

    erased = (await db.execute(
        select(func.count()).where(Memory.namespace == ns, Memory.erased_at.isnot(None))
    )).scalar_one()

    # Window-specific counts
    new_filter = [Memory.namespace == ns]
    sup_filter = [Memory.namespace == ns, Memory.valid_to.isnot(None)]
    if win_from:
        new_filter.append(Memory.ingestion_time >= win_from)
        sup_filter.append(Memory.valid_to >= win_from)
    if win_to:
        new_filter.append(Memory.ingestion_time <= win_to)
        sup_filter.append(Memory.valid_to <= win_to)

    new_in_window = (await db.execute(select(func.count()).where(*new_filter))).scalar_one()
    sup_in_window = (await db.execute(select(func.count()).where(*sup_filter))).scalar_one()

    # ── Audit chain ────────────────────────────────────────────────────────────

    if verify:
        chain_result = await verify_chain(db, ns)
        chain_status = AuditChainStatus(
            status=chain_result.get("status", "unchecked"),
            rows_checked=chain_result.get("rows_checked", 0),
            violations=chain_result.get("violations", []),
        )
    else:
        # Quick row count without verification
        chain_rows = (await db.execute(
            select(func.count()).where(EventLog.namespace == ns)
        )).scalar_one()
        chain_status = AuditChainStatus(status="unchecked", rows_checked=chain_rows, violations=[])

    # ── Erasures ───────────────────────────────────────────────────────────────

    erase_filter = [EventLog.namespace == ns, EventLog.op == "erase"]
    if win_from:
        erase_filter.append(EventLog.created_at >= win_from)
    if win_to:
        erase_filter.append(EventLog.created_at <= win_to)

    erase_rows = (await db.execute(select(EventLog).where(*erase_filter))).scalars().all()
    subject_ids_seen: set[str] = set()
    for row in erase_rows:
        payload = dict(row.payload or {})
        sid = payload.get("subject_id")
        if sid:
            subject_ids_seen.add(str(sid))

    total_erased_records = (await db.execute(
        select(func.count()).where(Memory.namespace == ns, Memory.erased_at.isnot(None))
    )).scalar_one()

    # ── Conflicts ─────────────────────────────────────────────────────────────

    def _conflict_count(status_val: str) -> "coroutine":
        filt = [ConflictFlag.namespace == ns, ConflictFlag.status == status_val]
        if win_from:
            filt.append(ConflictFlag.detected_at >= win_from)
        if win_to:
            filt.append(ConflictFlag.detected_at <= win_to)
        return db.execute(select(func.count()).where(*filt))

    open_cnt = (await _conflict_count("open")).scalar_one()
    accept_a_cnt = (await _conflict_count("accept_a")).scalar_one()
    accept_b_cnt = (await _conflict_count("accept_b")).scalar_one()
    dismissed_cnt = (await _conflict_count("dismissed")).scalar_one()

    detected_filt = [ConflictFlag.namespace == ns]
    if win_from:
        detected_filt.append(ConflictFlag.detected_at >= win_from)
    if win_to:
        detected_filt.append(ConflictFlag.detected_at <= win_to)
    detected_total = (await db.execute(select(func.count()).where(*detected_filt))).scalar_one()

    # ── Supersessions ─────────────────────────────────────────────────────────

    sup_event_filt = [EventLog.namespace == ns, EventLog.op == "supersede"]
    if win_from:
        sup_event_filt.append(EventLog.created_at >= win_from)
    if win_to:
        sup_event_filt.append(EventLog.created_at <= win_to)

    sup_events = (await db.execute(select(EventLog).where(*sup_event_filt))).scalars().all()
    total_sup_events = len(sup_events)

    # Count confirm/reject from review ops
    confirm_filt = [EventLog.namespace == ns, EventLog.op == "supersession_confirmed"]
    reject_filt  = [EventLog.namespace == ns, EventLog.op == "supersession_rejected"]
    if win_from:
        confirm_filt.append(EventLog.created_at >= win_from)
        reject_filt.append(EventLog.created_at >= win_from)
    if win_to:
        confirm_filt.append(EventLog.created_at <= win_to)
        reject_filt.append(EventLog.created_at <= win_to)

    confirmed = (await db.execute(select(func.count()).where(*confirm_filt))).scalar_one()
    rejected  = (await db.execute(select(func.count()).where(*reject_filt))).scalar_one()

    high_conf = sum(1 for e in sup_events if (dict(e.payload or {}).get("confidence") or 0.0) >= 0.9)
    low_conf  = total_sup_events - high_conf

    # ── Retention policy ───────────────────────────────────────────────────────

    policy = await db.get(NamespacePolicy, ns)
    retention = None
    if policy:
        retention = RetentionSnapshot(
            content_ttl_days=policy.content_ttl_days,
            audit_retention_days=policy.audit_retention_days,
            legal_hold=policy.legal_hold,
            stripe_customer_id=policy.stripe_customer_id,
        )

    return ComplianceReport(
        namespace=ns,
        generated_at=now.isoformat(),
        window_from=win_from.isoformat() if win_from else None,
        window_to=win_to.isoformat() if win_to else None,
        summary=MemorySummary(
            total_memories=total,
            active_memories=active,
            superseded_memories=superseded,
            erased_memories=erased,
            new_in_window=new_in_window,
            superseded_in_window=sup_in_window,
        ),
        audit_chain=chain_status,
        erasures=ErasureSummary(
            total_requests=len(erase_rows),
            total_records_erased=total_erased_records,
            subject_ids=sorted(subject_ids_seen),
        ),
        conflicts=ConflictSummary(
            open=open_cnt,
            resolved_accept_a=accept_a_cnt,
            resolved_accept_b=accept_b_cnt,
            dismissed=dismissed_cnt,
            detected_in_window=detected_total,
        ),
        supersessions=SupersessionSummary(
            total_supersessions=total_sup_events,
            confirmed_by_human=confirmed,
            rejected_by_human=rejected,
            high_confidence=high_conf,
            low_confidence=low_conf,
        ),
        retention=retention,
    )
