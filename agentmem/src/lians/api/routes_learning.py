"""Outcome, feedback, drift, and customer-approval learning APIs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log
from ..db import get_db
from ..improvement_service import ImprovementNotFound, visible_by_id
from ..learning_models import DriftSignal, Feedback, LearningProposal, Outcome
from ..learning_schemas import (
    DriftAnalysisOut,
    DriftAnalyzeRequest,
    DriftSignalOut,
    FeedbackCreate,
    FeedbackCreateOut,
    FeedbackOut,
    LearningProposalOut,
    OutcomeCreate,
    OutcomeOut,
)
from ..learning_service import (
    LearningContractError,
    analyze_drift,
    create_feedback,
    create_outcome,
    drift_signal_out,
    feedback_out,
    learning_proposal_out,
    outcome_out,
)
from ..mutation_safety import reject_non_replayable_idempotency_key
from .deps import AuthContext, get_auth

outcomes_router = APIRouter(prefix="/v1/outcomes", tags=["outcomes"])
feedback_router = APIRouter(prefix="/v1/feedback", tags=["learning"])
drift_router = APIRouter(prefix="/v1/drift", tags=["learning"])
learning_router = APIRouter(prefix="/v1/learning", tags=["learning"])


def _principal(auth: AuthContext) -> str:
    if not auth.principal_id:
        raise HTTPException(status_code=401, detail="Authenticated principal identity required")
    return auth.principal_id


def _scope(model, auth: AuthContext):
    filters = [model.namespace == auth.namespace]
    if auth.barrier_group is not None:
        filters.append(
            or_(model.barrier_group.is_(None), model.barrier_group == auth.barrier_group)
        )
    return filters


async def _audit_commit(
    db: AsyncSession,
    *,
    auth: AuthContext,
    operation: str,
    resource_type: str,
    resource_id: UUID,
    content_hash: str,
) -> None:
    await chain_log(
        db,
        namespace=auth.namespace,
        agent_id=_principal(auth),
        op=operation,
        content_hash=content_hash,
        payload={"resource_type": resource_type, "resource_id": str(resource_id)},
    )
    await db.commit()


@outcomes_router.post(
    "",
    response_model=OutcomeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_outcome(
    body: OutcomeCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> OutcomeOut:
    auth.require("write")
    try:
        row = await create_outcome(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="production_outcome_recorded",
            resource_type="improvement_outcome",
            resource_id=row.id,
            content_hash=row.outcome_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Outcome dependency not found") from exc
    except LearningContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Outcome already recorded") from exc
    return outcome_out(row)


@outcomes_router.get("/{outcome_id}", response_model=OutcomeOut)
async def get_outcome(
    outcome_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> OutcomeOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db, Outcome, outcome_id, namespace=auth.namespace, barrier_group=auth.barrier_group
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Outcome not found") from exc
    return outcome_out(row)


@feedback_router.post(
    "",
    response_model=FeedbackCreateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_feedback(
    body: FeedbackCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> FeedbackCreateOut:
    auth.require("write")
    try:
        row, proposal = await create_feedback(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="production_feedback_recorded",
            resource_type="improvement_feedback",
            resource_id=row.id,
            content_hash=row.feedback_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Feedback dependency not found") from exc
    except LearningContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Feedback already recorded") from exc
    return FeedbackCreateOut(
        feedback=feedback_out(row), learning_proposal=learning_proposal_out(proposal)
    )


@feedback_router.get("/{feedback_id}", response_model=FeedbackOut)
async def get_feedback(
    feedback_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> FeedbackOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db, Feedback, feedback_id, namespace=auth.namespace, barrier_group=auth.barrier_group
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Feedback not found") from exc
    return feedback_out(row)


@drift_router.post(
    "/analyze",
    response_model=DriftAnalysisOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_drift_analysis(
    body: DriftAnalyzeRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DriftAnalysisOut:
    auth.require("write")
    try:
        signal, proposal = await analyze_drift(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _audit_commit(
            db,
            auth=auth,
            operation="outcome_drift_analyzed",
            resource_type="drift_signal",
            resource_id=signal.id,
            content_hash=signal.signal_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Agent version not found") from exc
    except LearningContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Drift analysis already recorded") from exc
    return DriftAnalysisOut(
        signal=drift_signal_out(signal),
        learning_proposal=learning_proposal_out(proposal) if proposal else None,
    )


@drift_router.get("/{signal_id}", response_model=DriftSignalOut)
async def get_drift_signal(
    signal_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DriftSignalOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db, DriftSignal, signal_id, namespace=auth.namespace, barrier_group=auth.barrier_group
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Drift signal not found") from exc
    return drift_signal_out(row)


@learning_router.get("/proposals", response_model=list[LearningProposalOut])
async def get_learning_proposals(
    limit: int = Query(default=100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[LearningProposalOut]:
    auth.require("read")
    rows = list(
        (
            await db.execute(
                select(LearningProposal)
                .where(*_scope(LearningProposal, auth))
                .order_by(
                    LearningProposal.priority.desc(),
                    LearningProposal.created_at.desc(),
                    LearningProposal.id.desc(),
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [learning_proposal_out(row) for row in rows]


@learning_router.get("/proposals/{proposal_id}", response_model=LearningProposalOut)
async def get_learning_proposal(
    proposal_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> LearningProposalOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            LearningProposal,
            proposal_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Learning proposal not found") from exc
    return learning_proposal_out(row)


__all__ = ["drift_router", "feedback_router", "learning_router", "outcomes_router"]
