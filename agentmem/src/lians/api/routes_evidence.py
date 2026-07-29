"""Decision Envelope, completeness, reconstruction, and blast-radius APIs."""

# FastAPI declares dependency providers in endpoint defaults by design.
# ruff: noqa: B008

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log
from ..db import get_db
from ..decision_evidence import (
    add_evidence,
    assess_completeness,
    blast_radius,
    canonical_sha256,
    create_envelope,
    decision_out,
    envelope_out,
    evidence_out,
    get_envelope,
    ledger_event_out,
    list_evidence,
    reconstruct_decision,
    seal_envelope,
)
from ..models import DecisionEnvelope, DecisionRecord, LedgerEvent
from ..schemas import (
    BlastRadiusResult,
    DecisionCompleteness,
    DecisionDetailOut,
    DecisionEnvelopeOpen,
    DecisionEnvelopeOut,
    DecisionEnvelopeSeal,
    DecisionEvidenceBatch,
    DecisionEvidenceOut,
    DecisionReconstructionOut,
    EvidenceChangeCreate,
    EvidenceChangeResult,
)
from ..webhook_service import EVIDENCE_BLAST_RADIUS, dispatch_event
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/decision-envelopes", tags=["decision-envelopes"])
decision_router = APIRouter(prefix="/v1/decisions", tags=["decision-evidence"])
evidence_router = APIRouter(prefix="/v1/evidence", tags=["evidence-impact"])


def _visible(row, auth: AuthContext) -> bool:
    return (
        auth.barrier_group is None
        or row.barrier_group is None
        or row.barrier_group == auth.barrier_group
    )


@router.post("", response_model=DecisionEnvelopeOut)
async def open_decision_envelope(
    req: DecisionEnvelopeOpen,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Open a correlation boundary before an agent starts a consequential action."""
    auth.require("write")
    try:
        row = await create_envelope(db, auth.namespace, auth.barrier_group, req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(row)
    return await envelope_out(db, row)


@router.get("", response_model=list[DecisionEnvelopeOut])
async def list_decision_envelopes(
    status: str | None = Query(None, pattern=r"^(open|sealed|abandoned)$"),
    agent_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    filters = [DecisionEnvelope.namespace == auth.namespace]
    if auth.barrier_group is not None:
        filters.append(
            or_(
                DecisionEnvelope.barrier_group.is_(None),
                DecisionEnvelope.barrier_group == auth.barrier_group,
            )
        )
    if status:
        filters.append(DecisionEnvelope.status == status)
    if agent_id:
        filters.append(DecisionEnvelope.agent_id == agent_id)
    rows = list(
        (
            await db.execute(
                select(DecisionEnvelope)
                .where(*filters)
                .order_by(DecisionEnvelope.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [await envelope_out(db, row) for row in rows]


@router.get("/{envelope_id}", response_model=DecisionEnvelopeOut)
async def get_decision_envelope(
    envelope_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    row = await get_envelope(db, auth.namespace, envelope_id, auth.barrier_group)
    if row is None:
        raise HTTPException(status_code=404, detail="Decision envelope not found")
    return await envelope_out(db, row)


@router.get("/{envelope_id}/evidence", response_model=list[DecisionEvidenceOut])
async def get_decision_envelope_evidence(
    envelope_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    row = await get_envelope(db, auth.namespace, envelope_id, auth.barrier_group)
    if row is None:
        raise HTTPException(status_code=404, detail="Decision envelope not found")
    links = await list_evidence(db, auth.namespace, envelope_id, auth.barrier_group)
    return [evidence_out(link) for link in links]


@router.post("/{envelope_id}/evidence", response_model=list[DecisionEvidenceOut])
async def add_decision_envelope_evidence(
    envelope_id: UUID,
    req: DecisionEvidenceBatch,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Append evidence before or after sealing without rewriting the decision."""
    auth.require("write")
    row = await get_envelope(db, auth.namespace, envelope_id, auth.barrier_group)
    if row is None:
        raise HTTPException(status_code=404, detail="Decision envelope not found")
    links = await add_evidence(db, row, req.evidence, actor_id=row.agent_id)
    await db.commit()
    for link in links:
        await db.refresh(link)
    return [evidence_out(link) for link in links]


@router.post("/{envelope_id}/seal", response_model=DecisionDetailOut)
async def seal_decision_envelope(
    envelope_id: UUID,
    req: DecisionEnvelopeSeal,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Seal an envelope into an append-only decision and compute its honest grade."""
    auth.require("write")
    row = await get_envelope(db, auth.namespace, envelope_id, auth.barrier_group)
    if row is None:
        raise HTTPException(status_code=404, detail="Decision envelope not found")
    try:
        decision, completeness, links = await seal_envelope(db, row, req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DecisionDetailOut(
        decision=decision_out(decision),
        completeness=completeness,
        evidence=[evidence_out(link) for link in links],
    )


@decision_router.get(
    "/{decision_id}/completeness",
    response_model=DecisionCompleteness,
)
async def decision_completeness(
    decision_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    decision = await db.get(DecisionRecord, decision_id)
    if (
        decision is None
        or decision.namespace != auth.namespace
        or not _visible(decision, auth)
    ):
        raise HTTPException(status_code=404, detail="Decision not found")
    if decision.envelope_id is None:
        raise HTTPException(
            status_code=409,
            detail="This legacy decision predates Decision Envelopes",
        )
    envelope = await get_envelope(
        db, auth.namespace, decision.envelope_id, auth.barrier_group
    )
    if envelope is None:
        raise HTTPException(status_code=409, detail="Decision envelope is unavailable")
    links = await list_evidence(
        db, auth.namespace, envelope.id, auth.barrier_group
    )
    return assess_completeness(envelope, decision, links)


@decision_router.get(
    "/{decision_id}/reconstruction",
    response_model=DecisionReconstructionOut,
)
async def get_decision_reconstruction(
    decision_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    decision = await db.get(DecisionRecord, decision_id)
    if (
        decision is None
        or decision.namespace != auth.namespace
        or not _visible(decision, auth)
    ):
        raise HTTPException(status_code=404, detail="Decision not found")
    if decision.envelope_id is None:
        raise HTTPException(
            status_code=409,
            detail="This legacy decision predates Decision Envelopes",
        )
    envelope = await get_envelope(
        db, auth.namespace, decision.envelope_id, auth.barrier_group
    )
    if envelope is None:
        raise HTTPException(status_code=409, detail="Decision envelope is unavailable")
    return await reconstruct_decision(db, envelope, decision)


@evidence_router.get("/blast-radius", response_model=BlastRadiusResult)
async def get_blast_radius(
    evidence_type: str = Query(min_length=1, max_length=100),
    source_id: str = Query(min_length=1, max_length=512),
    source_version: str | None = Query(None, max_length=255),
    artifact_hash: str | None = Query(None, pattern=r"^[0-9a-fA-F]{64}$"),
    limit: int = Query(100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return every decision connected to the exact changed source or artifact."""
    auth.require("read")
    return await blast_radius(
        db,
        auth.namespace,
        evidence_type=evidence_type,
        source_id=source_id,
        source_version=source_version,
        artifact_hash=artifact_hash,
        barrier_group=auth.barrier_group,
        limit=limit,
    )


@evidence_router.post("/changes", response_model=EvidenceChangeResult)
async def record_evidence_change(
    req: EvidenceChangeCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Record a source revision and emit an immediate blast-radius alert."""
    auth.require("write")
    impact = await blast_radius(
        db,
        auth.namespace,
        evidence_type=req.evidence_type,
        source_id=req.source_id,
        source_version=req.source_version,
        artifact_hash=req.artifact_hash,
        barrier_group=auth.barrier_group,
        limit=1000,
    )
    recorded_at = datetime.now(UTC)
    payload = {
        **req.model_dump(mode="json"),
        "impacted_decisions": impact.impacted_decisions,
        "impacted_open_envelopes": impact.impacted_open_envelopes,
        "matching_links": impact.matching_links,
    }
    event_hash = canonical_sha256(
        {
            "namespace": auth.namespace,
            "recorded_at": recorded_at,
            "payload": payload,
        }
    )
    event = LedgerEvent(
        namespace=auth.namespace,
        event_type="source_change",
        agent_id=req.actor_id,
        barrier_group=auth.barrier_group,
        occurred_at=req.changed_at,
        recorded_at=recorded_at,
        payload=payload,
        artifact_hash=req.new_artifact_hash,
        event_hash=event_hash,
    )
    db.add(event)
    await db.flush()
    await chain_log(
        db,
        auth.namespace,
        req.actor_id,
        "evidence_change_recorded",
        content_hash=event_hash,
        payload={
            "record_id": str(event.id),
            "evidence_type": req.evidence_type,
            "source_id": req.source_id,
            "impacted_decisions": impact.impacted_decisions,
        },
    )
    await dispatch_event(
        db,
        auth.namespace,
        EVIDENCE_BLAST_RADIUS,
        {
            "change_event_id": str(event.id),
            "evidence_type": req.evidence_type,
            "source_id": req.source_id,
            "source_version": req.source_version,
            "change_kind": req.change_kind,
            "severity": req.severity,
            "impacted_decisions": impact.impacted_decisions,
            "impacted_open_envelopes": impact.impacted_open_envelopes,
            "decision_ids": [str(item.decision.id) for item in impact.decisions],
        },
        barrier_group=auth.barrier_group,
    )
    await db.commit()
    await db.refresh(event)
    return EvidenceChangeResult(
        change_event=ledger_event_out(event),
        blast_radius=impact,
    )
