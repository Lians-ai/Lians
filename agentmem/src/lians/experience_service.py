"""Outcome-aware, review-gated learning owned by the Lians engine."""

from __future__ import annotations

import math
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import chain_log
from .models import AgentExperience, ReflectionProposal
from .schemas import (
    ExperienceCreate,
    ExperienceOutcome,
    MemoryAdd,
    ReflectionGenerateRequest,
    ReflectionReviewRequest,
)


def normalize_task(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())[:300]


async def create_experience(
    db: AsyncSession,
    namespace: str,
    req: ExperienceCreate,
) -> AgentExperience:
    row = AgentExperience(
        namespace=namespace,
        agent_id=req.agent_id,
        task=req.task,
        task_key=normalize_task(req.task),
        decision=dict(req.decision),
        context_memory_ids=[str(item) for item in req.context_memory_ids],
        metadata_=dict(req.metadata),
    )
    db.add(row)
    await db.flush()
    await chain_log(
        db,
        namespace=namespace,
        agent_id=req.agent_id,
        op="experience_created",
        payload={
            "experience_id": str(row.id),
            "context_memory_ids": row.context_memory_ids,
            "task_key": row.task_key,
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


async def record_experience_outcome(
    db: AsyncSession,
    namespace: str,
    experience_id: uuid.UUID,
    req: ExperienceOutcome,
) -> AgentExperience | None:
    row = (
        await db.execute(
            select(AgentExperience).where(
                AgentExperience.id == experience_id,
                AgentExperience.namespace == namespace,
                AgentExperience.status == "open",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.outcome = dict(req.outcome)
    row.reward = req.reward
    row.reviewer_feedback = req.reviewer_feedback
    row.status = "completed"
    row.completed_at = datetime.now(timezone.utc)
    await chain_log(
        db,
        namespace=namespace,
        agent_id=row.agent_id,
        op="experience_completed",
        payload={
            "experience_id": str(row.id),
            "reward": req.reward,
            "context_memory_ids": list(row.context_memory_ids or []),
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


async def list_experiences(
    db: AsyncSession,
    namespace: str,
    *,
    agent_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> tuple[list[AgentExperience], int]:
    conditions = [AgentExperience.namespace == namespace]
    if agent_id:
        conditions.append(AgentExperience.agent_id == agent_id)
    if status:
        conditions.append(AgentExperience.status == status)
    total = int(
        (
            await db.execute(
                select(func.count(AgentExperience.id)).where(*conditions)
            )
        ).scalar_one()
    )
    rows = list(
        (
            await db.execute(
                select(AgentExperience)
                .where(*conditions)
                .order_by(AgentExperience.created_at.desc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars()
    )
    return rows, total


async def learning_adjustments(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    memory_ids: list[uuid.UUID],
) -> dict[str, dict[str, float | int]]:
    """Aggregate completed outcomes for a bounded candidate set."""
    wanted = {str(item) for item in memory_ids}
    if not wanted:
        return {}
    experiences, _ = await list_experiences(
        db,
        namespace,
        agent_id=agent_id,
        status="completed",
        limit=500,
    )
    stats: dict[str, dict[str, float | int]] = {}
    for experience in experiences:
        reward = float(experience.reward or 0.0)
        for memory_id in set(experience.context_memory_ids or []) & wanted:
            current = stats.setdefault(
                memory_id,
                {"count": 0, "reward_sum": 0.0, "positive": 0, "negative": 0},
            )
            current["count"] = int(current["count"]) + 1
            current["reward_sum"] = float(current["reward_sum"]) + reward
            if reward > 0:
                current["positive"] = int(current["positive"]) + 1
            elif reward < 0:
                current["negative"] = int(current["negative"]) + 1
    for current in stats.values():
        count = int(current["count"])
        average = float(current["reward_sum"]) / count
        current["average_reward"] = average
        current["confidence"] = min(1.0, math.log2(count + 1) / 3)
    return stats


async def generate_reflections(
    db: AsyncSession,
    namespace: str,
    req: ReflectionGenerateRequest,
) -> list[ReflectionProposal]:
    experiences, _ = await list_experiences(
        db,
        namespace,
        agent_id=req.agent_id,
        status="completed",
        limit=500,
    )
    groups: dict[str, list[AgentExperience]] = defaultdict(list)
    for row in experiences:
        if float(row.reward or 0.0) >= req.minimum_reward:
            groups[row.task_key].append(row)
    created: list[ReflectionProposal] = []
    for task_key, support in groups.items():
        if len(support) < req.minimum_support:
            continue
        pending = (
            await db.execute(
                select(ReflectionProposal.id).where(
                    ReflectionProposal.namespace == namespace,
                    ReflectionProposal.agent_id == req.agent_id,
                    ReflectionProposal.task_key == task_key,
                    ReflectionProposal.status == "pending",
                )
            )
        ).scalar_one_or_none()
        if pending:
            continue
        confidence = sum(float(row.reward or 0.0) for row in support) / len(support)
        proposal = ReflectionProposal(
            namespace=namespace,
            agent_id=req.agent_id,
            task_key=task_key,
            content=(
                f'For "{support[0].task}", {len(support)} reviewed outcomes were '
                "positive. Prefer the validated context pattern while still "
                "checking current policy, validity, and provenance."
            ),
            supporting_experience_ids=[str(row.id) for row in support[:20]],
            confidence=max(0.0, min(confidence, 1.0)),
        )
        db.add(proposal)
        created.append(proposal)
    await db.flush()
    for proposal in created:
        await chain_log(
            db,
            namespace=namespace,
            agent_id=req.agent_id,
            op="reflection_proposed",
            payload={
                "proposal_id": str(proposal.id),
                "supporting_experience_ids": proposal.supporting_experience_ids,
                "confidence": proposal.confidence,
            },
        )
    await db.commit()
    for proposal in created:
        await db.refresh(proposal)
    return created


async def list_reflections(
    db: AsyncSession,
    namespace: str,
    status: str = "pending",
) -> list[ReflectionProposal]:
    return list(
        (
            await db.execute(
                select(ReflectionProposal)
                .where(
                    ReflectionProposal.namespace == namespace,
                    ReflectionProposal.status == status,
                )
                .order_by(ReflectionProposal.created_at.desc())
                .limit(200)
            )
        ).scalars()
    )


async def review_reflection(
    db: AsyncSession,
    namespace: str,
    proposal_id: uuid.UUID,
    req: ReflectionReviewRequest,
    *,
    barrier_override: str | None = None,
) -> ReflectionProposal | None:
    proposal = (
        await db.execute(
            select(ReflectionProposal).where(
                ReflectionProposal.id == proposal_id,
                ReflectionProposal.namespace == namespace,
                ReflectionProposal.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if proposal is None:
        return None
    promoted_id = None
    if req.action == "approve":
        from .admission import AdmissionDecision, detect_risk_tags
        from .memory_service import add_memory

        # Reflection approval is an explicit admin-controlled trust boundary.
        # Still classify and score the promoted text; unsafe content remains
        # ineligible even though the reviewer authorized its storage.
        risk_tags = detect_risk_tags(proposal.content)
        promoted = await add_memory(
            db,
            namespace,
            MemoryAdd(
                agent_id=proposal.agent_id,
                content=proposal.content,
                event_time=datetime.now(timezone.utc),
                source="governed_reflection",
                metadata={
                    "kind": "governed_reflection",
                    "reflection_proposal_id": str(proposal.id),
                    "supporting_experience_ids": proposal.supporting_experience_ids,
                    "confidence": proposal.confidence,
                },
            ),
            barrier_override=barrier_override,
            _trusted_admission=AdmissionDecision("admit", risk_tags, []),
        )
        promoted_id = promoted.id
    proposal.status = "approved" if req.action == "approve" else "rejected"
    proposal.reviewer_note = req.note
    proposal.promoted_memory_id = promoted_id
    proposal.reviewed_at = datetime.now(timezone.utc)
    await chain_log(
        db,
        namespace=namespace,
        agent_id=proposal.agent_id,
        op="reflection_reviewed",
        memory_id=promoted_id,
        payload={
            "proposal_id": str(proposal.id),
            "action": req.action,
            "reviewer": req.reviewer,
        },
    )
    await db.commit()
    await db.refresh(proposal)
    return proposal
