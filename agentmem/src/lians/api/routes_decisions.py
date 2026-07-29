"""Cross-industry decision ledger and evidence-pack API."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log, verify_chain
from ..db import get_db
from ..decision_evidence import (
    add_evidence,
    assess_completeness,
    create_envelope,
    decision_out,
    envelope_out,
    evidence_out,
    get_envelope,
    ledger_event_out,
    list_evidence,
    seal_envelope,
)
from ..evidence_signing import sign_evidence_manifest
from ..memory_service import get_knowledge_snapshot
from ..models import DecisionRecord, LedgerEvent, NamespacePolicy
from ..schemas import (
    DecisionCreate,
    DecisionEnvelopeOpen,
    DecisionEnvelopeSeal,
    DecisionEvidenceCreate,
    DecisionOut,
    DecisionReview,
    LedgerEventCreate,
    LedgerEventOut,
)
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/decisions", tags=["decisions"])
records_router = APIRouter(prefix="/v1/records", tags=["records"])


def _barrier_visible(row, auth: AuthContext) -> bool:
    return (
        auth.barrier_group is None
        or row.barrier_group is None
        or row.barrier_group == auth.barrier_group
    )


def _apply_barrier_filter(filters: list, column, auth: AuthContext) -> None:
    if auth.barrier_group is not None:
        filters.append(or_(column.is_(None), column == auth.barrier_group))


def _canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def _out(row: DecisionRecord) -> DecisionOut:
    return decision_out(row)


def _event_out(row: LedgerEvent) -> LedgerEventOut:
    return ledger_event_out(row)


@records_router.post("/events", response_model=LedgerEventOut)
async def record_event(
    req: LedgerEventCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Append an inference, oversight, change, subject, incident, or memory event."""
    auth.require("write")
    envelope = None
    decision = None
    if req.decision_id:
        decision = await db.get(DecisionRecord, req.decision_id)
        if (
            decision is None
            or decision.namespace != auth.namespace
            or not _barrier_visible(decision, auth)
        ):
            raise HTTPException(422, "decision_id does not belong to this namespace")
    envelope_id = req.decision_envelope_id or (
        decision.envelope_id if decision is not None else None
    )
    if envelope_id:
        envelope = await get_envelope(
            db, auth.namespace, envelope_id, auth.barrier_group
        )
        if envelope is None:
            raise HTTPException(422, "decision_envelope_id does not belong to this namespace")
        if decision is not None and decision.envelope_id not in (None, envelope.id):
            raise HTTPException(422, "decision_id and decision_envelope_id do not match")
        if decision is None:
            decision = (
                await db.execute(
                    select(DecisionRecord).where(
                        DecisionRecord.envelope_id == envelope.id
                    )
                )
            ).scalar_one_or_none()
    recorded_at = datetime.now(timezone.utc)
    body = req.model_dump(mode="json") | {
        "namespace": auth.namespace,
        "decision_id": str(decision.id) if decision is not None else None,
        "decision_envelope_id": str(envelope.id) if envelope is not None else None,
        "recorded_at": recorded_at.isoformat(),
    }
    event_hash = hashlib.sha256(_canonical(body).encode()).hexdigest()
    row = LedgerEvent(
        namespace=auth.namespace,
        event_type=req.event_type,
        agent_id=req.agent_id,
        barrier_group=auth.barrier_group,
        occurred_at=req.occurred_at,
        recorded_at=recorded_at,
        subject_id=req.subject_id,
        session_id=req.session_id,
        decision_id=decision.id if decision is not None else None,
        model_id=req.model_id,
        model_version=req.model_version,
        payload=req.payload,
        artifact_hash=req.artifact_hash,
        event_hash=event_hash,
    )
    db.add(row)
    await db.flush()
    if envelope is not None:
        evidence_type = {
            "policy_decision": "policy_decision",
            "tool_call": "tool_call",
            "tool_result": "tool_result",
            "human_oversight": "human_review",
            "memory": "memory",
            "inference": "model",
        }.get(req.event_type, "external")
        role = {
            "policy_decision": "governed",
            "tool_call": "executed",
            "tool_result": "used",
            "human_oversight": "reviewed",
            "memory": "used",
            "inference": "executed",
        }.get(req.event_type, "used")
        await add_evidence(
            db,
            envelope,
            [
                DecisionEvidenceCreate(
                    evidence_type=evidence_type,
                    role=role,
                    source_id=str(row.id),
                    source_version=req.model_version,
                    artifact_hash=req.artifact_hash or event_hash,
                    occurred_at=req.occurred_at,
                    metadata={
                        "ledger_event_type": req.event_type,
                        "ledger_event_id": str(row.id),
                        "model_id": req.model_id,
                        "model_version": req.model_version,
                        "payload": req.payload,
                    },
                )
            ],
            actor_id=req.agent_id,
            audit=False,
        )
    await chain_log(
        db,
        auth.namespace,
        req.agent_id,
        f"record_{req.event_type}",
        content_hash=event_hash,
        payload={"record_id": str(row.id), "event_type": req.event_type},
    )
    await db.commit()
    await db.refresh(row)
    return _event_out(row)


@records_router.get("/events", response_model=list[LedgerEventOut])
async def list_events(
    event_type: str | None = None,
    agent_id: str | None = None,
    decision_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    filters = [LedgerEvent.namespace == auth.namespace]
    _apply_barrier_filter(filters, LedgerEvent.barrier_group, auth)
    if event_type:
        filters.append(LedgerEvent.event_type == event_type)
    if agent_id:
        filters.append(LedgerEvent.agent_id == agent_id)
    if decision_id:
        filters.append(LedgerEvent.decision_id == decision_id)
    rows = (
        (
            await db.execute(
                select(LedgerEvent)
                .where(*filters)
                .order_by(LedgerEvent.occurred_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [_event_out(row) for row in rows]


@router.post("", response_model=DecisionOut)
async def create_decision(
    req: DecisionCreate, auth: AuthContext = Depends(get_auth), db: AsyncSession = Depends(get_db)
):
    """Compatibility path that opens and seals a Decision Envelope atomically."""
    auth.require("write")
    try:
        envelope = await create_envelope(
            db,
            auth.namespace,
            auth.barrier_group,
            DecisionEnvelopeOpen(
                agent_id=req.agent_id,
                decision_type=req.decision_type,
                regime=req.regime,
                subject_id=req.subject_id,
                session_id=req.session_id,
                trace_id=req.trace_id,
                run_id=req.run_id,
                knowledge_as_of=req.knowledge_as_of,
                completeness_profile=req.completeness_profile,
                required_checks=req.required_checks,
                metadata=req.metadata,
            ),
        )
        row, _, _ = await seal_envelope(
            db,
            envelope,
            DecisionEnvelopeSeal(
                outcome=req.outcome,
                reason_codes=req.reason_codes,
                decided_at=req.decided_at,
                knowledge_as_of=req.knowledge_as_of,
                model_id=req.model_id,
                model_version=req.model_version,
                model_artifact_hash=req.model_artifact_hash,
                policy_id=req.policy_id,
                policy_version=req.policy_version,
                policy_artifact_hash=req.policy_artifact_hash,
                prompt_id=req.prompt_id,
                prompt_version=req.prompt_version,
                prompt_artifact_hash=req.prompt_artifact_hash,
                runtime_version=req.runtime_version,
                evidence_memory_ids=req.evidence_memory_ids,
                input_hash=req.input_hash,
                output_hash=req.output_hash,
                replay_manifest_hash=req.replay_manifest_hash,
                supersedes_id=req.supersedes_id,
                metadata=req.metadata,
            ),
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _out(row)


@router.get("", response_model=list[DecisionOut])
async def list_decisions(
    agent_id: str | None = None,
    subject_id: str | None = None,
    regime: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    filters = [DecisionRecord.namespace == auth.namespace]
    _apply_barrier_filter(filters, DecisionRecord.barrier_group, auth)
    if agent_id:
        filters.append(DecisionRecord.agent_id == agent_id)
    if subject_id:
        filters.append(DecisionRecord.subject_id == subject_id)
    if regime:
        filters.append(DecisionRecord.regime == regime)
    rows = (
        (
            await db.execute(
                select(DecisionRecord)
                .where(*filters)
                .order_by(DecisionRecord.decided_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [_out(r) for r in rows]


@router.get("/{decision_id}", response_model=DecisionOut)
async def get_decision(
    decision_id: UUID, auth: AuthContext = Depends(get_auth), db: AsyncSession = Depends(get_db)
):
    auth.require("read")
    row = await db.get(DecisionRecord, decision_id)
    if row is None or row.namespace != auth.namespace or not _barrier_visible(row, auth):
        raise HTTPException(404, "Decision not found")
    return _out(row)


@router.post("/{decision_id}/review", response_model=DecisionOut)
async def review_decision(
    decision_id: UUID,
    req: DecisionReview,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    row = await db.get(DecisionRecord, decision_id)
    if row is None or row.namespace != auth.namespace or not _barrier_visible(row, auth):
        raise HTTPException(404, "Decision not found")
    now = datetime.now(timezone.utc)
    row.human_review_status, row.human_reviewer, row.human_reviewed_at = (
        req.status,
        req.reviewer,
        now,
    )
    if row.envelope_id is not None:
        envelope = await get_envelope(
            db, auth.namespace, row.envelope_id, auth.barrier_group
        )
        if envelope is not None:
            review_payload = {
                "decision_id": str(row.id),
                "status": req.status,
                "reviewer": req.reviewer,
                "note": req.note,
                "reviewed_at": now.isoformat(),
            }
            await add_evidence(
                db,
                envelope,
                [
                    DecisionEvidenceCreate(
                        evidence_type="human_review",
                        role="reviewed",
                        source_id=req.reviewer,
                        source_version=req.status,
                        artifact_hash=hashlib.sha256(
                            _canonical(review_payload).encode()
                        ).hexdigest(),
                        occurred_at=now,
                        metadata=review_payload,
                    )
                ],
                actor_id=req.reviewer,
                audit=False,
            )
    await chain_log(
        db,
        auth.namespace,
        row.agent_id,
        "decision_reviewed",
        content_hash=row.record_hash,
        payload={
            "decision_id": str(row.id),
            "status": req.status,
            "reviewer": req.reviewer,
            "note": req.note,
        },
    )
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.get("/{decision_id}/evidence-pack")
async def evidence_pack(
    decision_id: UUID,
    verify: bool = True,
    version: str = Query("v1", pattern=r"^v[12]$"),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Produce a portable, point-in-time evidence pack for a dispute or audit."""
    auth.require("read")
    row = await db.get(DecisionRecord, decision_id)
    if row is None or row.namespace != auth.namespace or not _barrier_visible(row, auth):
        raise HTTPException(404, "Decision not found")
    snapshot = await get_knowledge_snapshot(
        db,
        auth.namespace,
        row.agent_id,
        row.knowledge_as_of,
        10000,
        barrier_override=auth.barrier_group,
    )
    evidence_ids = {str(x) for x in (row.evidence_memory_ids or [])}
    cited = (
        [m.model_dump(mode="json") for m in snapshot if str(m.id) in evidence_ids]
        if evidence_ids
        else []
    )
    policy = await db.get(NamespacePolicy, auth.namespace)
    if verify and auth.barrier_group is None:
        chain = await verify_chain(db, auth.namespace)
    elif verify:
        chain = {
            "status": "unavailable_for_barrier_scoped_export",
            "rows_checked": 0,
            "violations": [],
        }
    else:
        chain = {"status": "unchecked", "rows_checked": 0, "violations": []}
    generated_at = datetime.now(timezone.utc).isoformat()
    retention = None
    if policy is not None:
        retention = {
            "content_ttl_days": policy.content_ttl_days,
            "audit_retention_days": policy.audit_retention_days,
            "legal_hold": policy.legal_hold,
        }
    if version == "v2":
        if row.envelope_id is None:
            raise HTTPException(
                status_code=409,
                detail="This legacy decision predates Decision Envelopes",
            )
        envelope = await get_envelope(
            db, auth.namespace, row.envelope_id, auth.barrier_group
        )
        if envelope is None:
            raise HTTPException(status_code=409, detail="Decision envelope is unavailable")
        evidence = await list_evidence(
            db, auth.namespace, envelope.id, auth.barrier_group
        )
        completeness = assess_completeness(envelope, row, evidence)
        envelope_payload = await envelope_out(
            db, envelope, decision=row, evidence=evidence
        )
        manifest = {
            "schema": "https://lians.ai/schemas/evidence-pack/v2",
            "generated_at": generated_at,
            "decision": _out(row).model_dump(mode="json"),
            "envelope": envelope_payload.model_dump(mode="json"),
            "completeness": completeness.model_dump(mode="json"),
            "knowledge_snapshot": [m.model_dump(mode="json") for m in snapshot],
            "cited_evidence": cited,
            "evidence_graph": [evidence_out(item).model_dump(mode="json") for item in evidence],
            "audit_chain": chain,
            "retention": retention,
            "verification_policy": {
                "incomplete_records_are_never_labeled_verified": True,
                "grade": completeness.grade,
                "gaps": [gap.model_dump(mode="json") for gap in completeness.gaps],
            },
        }
        pack = sign_evidence_manifest(manifest)
        await chain_log(
            db,
            auth.namespace,
            row.agent_id,
            "evidence_pack_exported",
            content_hash=pack["pack_hash"],
            payload={
                "decision_id": str(row.id),
                "schema": pack["schema"],
                "signature_status": pack["signature"]["status"],
                "completeness_grade": completeness.grade,
            },
        )
        await db.commit()
        return pack

    pack = {
        "schema": "https://lians.ai/schemas/evidence-pack/v1",
        "generated_at": generated_at,
        "decision": _out(row).model_dump(mode="json"),
        "knowledge_snapshot": [m.model_dump(mode="json") for m in snapshot],
        "cited_evidence": cited,
        "audit_chain": chain,
        "retention": retention,
    }
    pack["pack_hash"] = hashlib.sha256(_canonical(pack).encode()).hexdigest()
    await chain_log(
        db,
        auth.namespace,
        row.agent_id,
        "evidence_pack_exported",
        content_hash=pack["pack_hash"],
        payload={"decision_id": str(row.id), "schema": pack["schema"]},
    )
    await db.commit()
    return pack
