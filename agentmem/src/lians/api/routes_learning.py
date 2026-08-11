"""Governed outcome learning and review-gated reflections."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..experience_service import (
    create_experience,
    generate_reflections,
    list_experiences,
    list_reflections,
    record_experience_outcome,
    review_reflection,
)
from ..schemas import (
    ExperienceCreate,
    ExperienceListResult,
    ExperienceOutcome,
    ExperienceOut,
    ReflectionGenerateRequest,
    ReflectionListResult,
    ReflectionProposalOut,
    ReflectionReviewRequest,
)
from .deps import AuthContext, get_auth


router = APIRouter(prefix="/v1", tags=["Learning"])


@router.post("/experiences", response_model=ExperienceOut, status_code=201)
async def add_experience(
    req: ExperienceCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_all("write", "learning")
    return await create_experience(db, auth.namespace, req)


@router.get("/experiences", response_model=ExperienceListResult)
async def get_experiences(
    agent_id: str | None = None,
    status: str | None = Query(default=None, pattern="^(open|completed)$"),
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_all("read", "learning")
    rows, total = await list_experiences(
        db,
        auth.namespace,
        agent_id=agent_id,
        status=status,
        limit=limit,
    )
    return ExperienceListResult(experiences=rows, total=total)


@router.patch("/experiences/{experience_id}/outcome", response_model=ExperienceOut)
async def add_experience_outcome(
    experience_id: UUID,
    req: ExperienceOutcome,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_all("write", "learning")
    row = await record_experience_outcome(db, auth.namespace, experience_id, req)
    if row is None:
        raise HTTPException(status_code=404, detail="Open experience not found")
    return row


@router.post("/reflections/generate", response_model=ReflectionListResult, status_code=201)
async def create_reflections(
    req: ReflectionGenerateRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_all("write", "learning")
    rows = await generate_reflections(db, auth.namespace, req)
    return ReflectionListResult(proposals=rows, total=len(rows))


@router.get("/reflections", response_model=ReflectionListResult)
async def get_reflections(
    status: str = Query(default="pending", pattern="^(pending|approved|rejected)$"),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_all("read", "learning")
    rows = await list_reflections(db, auth.namespace, status)
    return ReflectionListResult(proposals=rows, total=len(rows))


@router.patch("/reflections/{proposal_id}", response_model=ReflectionProposalOut)
async def resolve_reflection(
    proposal_id: UUID,
    req: ReflectionReviewRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require_all("admin", "learning")
    row = await review_reflection(
        db,
        auth.namespace,
        proposal_id,
        req,
        barrier_override=auth.barrier_group,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Pending reflection proposal not found")
    return row
