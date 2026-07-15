"""Cross-industry decision ledger and evidence-pack API."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log, verify_chain
from ..db import get_db
from ..memory_service import get_knowledge_snapshot
from ..models import DecisionRecord, LedgerEvent, Memory, NamespacePolicy
from ..schemas import DecisionCreate, DecisionOut, DecisionReview, LedgerEventCreate, LedgerEventOut
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/decisions", tags=["decisions"])
records_router = APIRouter(prefix="/v1/records", tags=["records"])


def _canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def _out(row: DecisionRecord) -> DecisionOut:
    return DecisionOut(
        id=row.id, namespace=row.namespace, agent_id=row.agent_id,
        decision_type=row.decision_type, outcome=row.outcome,
        reason_codes=list(row.reason_codes or []), regime=row.regime,
        subject_id=row.subject_id, session_id=row.session_id, model_id=row.model_id,
        model_version=row.model_version, policy_version=row.policy_version,
        decided_at=row.decided_at, recorded_at=row.recorded_at,
        knowledge_as_of=row.knowledge_as_of,
        evidence_memory_ids=[UUID(str(x)) for x in (row.evidence_memory_ids or [])],
        input_hash=row.input_hash, output_hash=row.output_hash,
        human_review_status=row.human_review_status,
        human_reviewer=row.human_reviewer, human_reviewed_at=row.human_reviewed_at,
        supersedes_id=row.supersedes_id, metadata=dict(row.metadata_ or {}),
        record_hash=row.record_hash,
    )


def _event_out(row: LedgerEvent) -> LedgerEventOut:
    return LedgerEventOut(id=row.id, namespace=row.namespace, event_type=row.event_type,
        agent_id=row.agent_id, occurred_at=row.occurred_at, recorded_at=row.recorded_at,
        subject_id=row.subject_id, session_id=row.session_id, decision_id=row.decision_id,
        model_id=row.model_id, model_version=row.model_version, payload=dict(row.payload or {}),
        artifact_hash=row.artifact_hash, event_hash=row.event_hash)


@records_router.post("/events", response_model=LedgerEventOut)
async def record_event(req: LedgerEventCreate, auth: AuthContext = Depends(get_auth), db: AsyncSession = Depends(get_db)):
    """Append an inference, oversight, change, subject, incident, or memory event."""
    auth.require("write")
    if req.decision_id:
        decision = await db.get(DecisionRecord, req.decision_id)
        if decision is None or decision.namespace != auth.namespace:
            raise HTTPException(422, "decision_id does not belong to this namespace")
    recorded_at = datetime.now(timezone.utc)
    body = req.model_dump(mode="json") | {"namespace": auth.namespace, "recorded_at": recorded_at.isoformat()}
    event_hash = hashlib.sha256(_canonical(body).encode()).hexdigest()
    row = LedgerEvent(namespace=auth.namespace, event_type=req.event_type, agent_id=req.agent_id,
        occurred_at=req.occurred_at, recorded_at=recorded_at, subject_id=req.subject_id,
        session_id=req.session_id, decision_id=req.decision_id, model_id=req.model_id,
        model_version=req.model_version, payload=req.payload, artifact_hash=req.artifact_hash,
        event_hash=event_hash)
    db.add(row); await db.flush()
    await chain_log(db, auth.namespace, req.agent_id, f"record_{req.event_type}",
        content_hash=event_hash, payload={"record_id": str(row.id), "event_type": req.event_type})
    await db.commit(); await db.refresh(row)
    return _event_out(row)


@records_router.get("/events", response_model=list[LedgerEventOut])
async def list_events(event_type: str | None = None, agent_id: str | None = None,
                      decision_id: UUID | None = None, limit: int = Query(100, ge=1, le=1000),
                      auth: AuthContext = Depends(get_auth), db: AsyncSession = Depends(get_db)):
    auth.require("read")
    filters = [LedgerEvent.namespace == auth.namespace]
    if event_type: filters.append(LedgerEvent.event_type == event_type)
    if agent_id: filters.append(LedgerEvent.agent_id == agent_id)
    if decision_id: filters.append(LedgerEvent.decision_id == decision_id)
    rows = (await db.execute(select(LedgerEvent).where(*filters).order_by(LedgerEvent.occurred_at.desc()).limit(limit))).scalars().all()
    return [_event_out(row) for row in rows]


@router.post("", response_model=DecisionOut)
async def create_decision(req: DecisionCreate, auth: AuthContext = Depends(get_auth), db: AsyncSession = Depends(get_db)):
    """Append an authoritative record of a consequential agent decision."""
    auth.require("write")
    as_of = req.knowledge_as_of or req.decided_at
    ids = [str(x) for x in req.evidence_memory_ids]
    if ids:
        found = (await db.execute(select(Memory.id).where(
            Memory.namespace == auth.namespace, Memory.id.in_(req.evidence_memory_ids)
        ))).scalars().all()
        if len(set(found)) != len(set(req.evidence_memory_ids)):
            raise HTTPException(422, "One or more evidence_memory_ids do not belong to this namespace")
    if req.supersedes_id:
        prior = await db.get(DecisionRecord, req.supersedes_id)
        if prior is None or prior.namespace != auth.namespace:
            raise HTTPException(422, "supersedes_id does not belong to this namespace")

    recorded_at = datetime.now(timezone.utc)
    body = req.model_dump(mode="json") | {"namespace": auth.namespace, "knowledge_as_of": as_of.isoformat(), "recorded_at": recorded_at.isoformat()}
    record_hash = hashlib.sha256(_canonical(body).encode()).hexdigest()
    row = DecisionRecord(
        namespace=auth.namespace, agent_id=req.agent_id, decision_type=req.decision_type,
        outcome=req.outcome, reason_codes=req.reason_codes, regime=req.regime,
        subject_id=req.subject_id, session_id=req.session_id, model_id=req.model_id,
        model_version=req.model_version, policy_version=req.policy_version,
        decided_at=req.decided_at, recorded_at=recorded_at, knowledge_as_of=as_of,
        evidence_memory_ids=ids, input_hash=req.input_hash, output_hash=req.output_hash,
        supersedes_id=req.supersedes_id, metadata_=req.metadata, record_hash=record_hash,
    )
    db.add(row)
    await db.flush()
    event = await chain_log(db, auth.namespace, req.agent_id, "decision_recorded", content_hash=record_hash,
        payload={"decision_id": str(row.id), "decision_type": req.decision_type, "regime": req.regime})
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.get("", response_model=list[DecisionOut])
async def list_decisions(agent_id: str | None = None, subject_id: str | None = None,
                         regime: str | None = None, limit: int = Query(100, ge=1, le=1000),
                         auth: AuthContext = Depends(get_auth), db: AsyncSession = Depends(get_db)):
    auth.require("read")
    filters = [DecisionRecord.namespace == auth.namespace]
    if agent_id: filters.append(DecisionRecord.agent_id == agent_id)
    if subject_id: filters.append(DecisionRecord.subject_id == subject_id)
    if regime: filters.append(DecisionRecord.regime == regime)
    rows = (await db.execute(select(DecisionRecord).where(*filters).order_by(DecisionRecord.decided_at.desc()).limit(limit))).scalars().all()
    return [_out(r) for r in rows]


@router.get("/{decision_id}", response_model=DecisionOut)
async def get_decision(decision_id: UUID, auth: AuthContext = Depends(get_auth), db: AsyncSession = Depends(get_db)):
    auth.require("read")
    row = await db.get(DecisionRecord, decision_id)
    if row is None or row.namespace != auth.namespace: raise HTTPException(404, "Decision not found")
    return _out(row)


@router.post("/{decision_id}/review", response_model=DecisionOut)
async def review_decision(decision_id: UUID, req: DecisionReview, auth: AuthContext = Depends(get_auth), db: AsyncSession = Depends(get_db)):
    auth.require("admin")
    row = await db.get(DecisionRecord, decision_id)
    if row is None or row.namespace != auth.namespace: raise HTTPException(404, "Decision not found")
    now = datetime.now(timezone.utc)
    row.human_review_status, row.human_reviewer, row.human_reviewed_at = req.status, req.reviewer, now
    await chain_log(db, auth.namespace, row.agent_id, "decision_reviewed", content_hash=row.record_hash,
        payload={"decision_id": str(row.id), "status": req.status, "reviewer": req.reviewer, "note": req.note})
    await db.commit(); await db.refresh(row)
    return _out(row)


@router.get("/{decision_id}/evidence-pack")
async def evidence_pack(decision_id: UUID, verify: bool = True, auth: AuthContext = Depends(get_auth), db: AsyncSession = Depends(get_db)):
    """Produce a portable, point-in-time evidence pack for a dispute or audit."""
    auth.require("read")
    row = await db.get(DecisionRecord, decision_id)
    if row is None or row.namespace != auth.namespace: raise HTTPException(404, "Decision not found")
    snapshot = await get_knowledge_snapshot(db, auth.namespace, row.agent_id, row.knowledge_as_of, 10000)
    evidence_ids = {str(x) for x in (row.evidence_memory_ids or [])}
    cited = [m.model_dump(mode="json") for m in snapshot if str(m.id) in evidence_ids] if evidence_ids else []
    policy = await db.get(NamespacePolicy, auth.namespace)
    chain = await verify_chain(db, auth.namespace) if verify else {"status": "unchecked", "rows_checked": 0, "violations": []}
    pack = {
        "schema": "https://lians.ai/schemas/evidence-pack/v1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": _out(row).model_dump(mode="json"), "knowledge_snapshot": [m.model_dump(mode="json") for m in snapshot],
        "cited_evidence": cited, "audit_chain": chain,
        "retention": None if policy is None else {"content_ttl_days": policy.content_ttl_days, "audit_retention_days": policy.audit_retention_days, "legal_hold": policy.legal_hold},
    }
    pack["pack_hash"] = hashlib.sha256(_canonical(pack).encode()).hexdigest()
    await chain_log(db, auth.namespace, row.agent_id, "evidence_pack_exported", content_hash=pack["pack_hash"], payload={"decision_id": str(row.id), "schema": pack["schema"]})
    await db.commit()
    return pack
