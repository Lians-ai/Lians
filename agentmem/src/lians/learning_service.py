"""Outcome feedback loop with encrypted details and customer-approved proposals."""

from __future__ import annotations

import hashlib
import json
import statistics
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .improvement_models import AgentVersion
from .improvement_schemas import EvalCaseFromDecision
from .improvement_service import (
    barrier_scope,
    create_eval_case_from_decision,
    sha256_json,
    visible_by_id,
)
from .learning_models import DriftSignal, Feedback, LearningProposal, Outcome
from .learning_schemas import (
    DriftAnalyzeRequest,
    DriftSignalOut,
    FeedbackCreate,
    FeedbackOut,
    LearningProposalOut,
    OutcomeCreate,
    OutcomeOut,
)
from .release_models import Deployment
from .secret_storage import seal_text, unseal_text

_OUTCOME_PURPOSE = "improvement-outcome-payload"
_FEEDBACK_PURPOSE = "improvement-feedback-payload"


class LearningContractError(ValueError):
    """A learning record cannot be created without its evidence boundary."""


def _payload_context(kind: str, row_id: uuid.UUID, namespace: str, payload_hash: str) -> str:
    return f"{kind}:{namespace}:{row_id}:{payload_hash}"


def outcome_out(row: Outcome) -> OutcomeOut:
    payload = None
    if row.payload_encrypted:
        payload = json.loads(
            unseal_text(
                row.payload_encrypted,
                purpose=_OUTCOME_PURPOSE,
                context=_payload_context("outcome", row.id, row.namespace, row.payload_hash),
            )
        )
    return OutcomeOut(
        id=row.id,
        agent_version_id=row.agent_version_id,
        decision_id=row.decision_id,
        deployment_id=row.deployment_id,
        correlation_hash=row.correlation_hash,
        kind=row.kind,
        metrics=row.metrics,
        payload=payload,
        payload_hash=row.payload_hash,
        outcome_hash=row.outcome_hash,
        occurred_at=row.occurred_at,
        recorded_at=row.recorded_at,
        recorded_by_principal_ref=row.recorded_by_principal_ref,
    )


async def create_outcome(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: OutcomeCreate,
) -> Outcome:
    await visible_by_id(
        db,
        AgentVersion,
        body.agent_version_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    if body.deployment_id is not None:
        deployment = await visible_by_id(
            db,
            Deployment,
            body.deployment_id,
            namespace=namespace,
            barrier_group=barrier_group,
        )
        if deployment.status == "failed" and body.kind == "success":
            raise LearningContractError("a failed deployment cannot record a success outcome")
    row_id = uuid.uuid4()
    metrics = [metric.model_dump(mode="json") for metric in body.metrics]
    correlation_hash = hashlib.sha256(body.correlation_id.encode("utf-8")).hexdigest()
    payload_hash = sha256_json(body.payload) if body.payload is not None else None
    document = {
        "schema": "lians.outcome.v1",
        "id": str(row_id),
        "agent_version_id": str(body.agent_version_id),
        "decision_id": str(body.decision_id) if body.decision_id else None,
        "deployment_id": str(body.deployment_id) if body.deployment_id else None,
        "correlation_hash": correlation_hash,
        "kind": body.kind,
        "metrics": metrics,
        "payload_hash": payload_hash,
        "occurred_at": body.occurred_at,
    }
    row = Outcome(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        agent_version_id=body.agent_version_id,
        decision_id=body.decision_id,
        deployment_id=body.deployment_id,
        correlation_hash=correlation_hash,
        kind=body.kind,
        metrics=metrics,
        payload_encrypted=(
            seal_text(
                json.dumps(body.payload, sort_keys=True, separators=(",", ":")),
                purpose=_OUTCOME_PURPOSE,
                context=_payload_context("outcome", row_id, namespace, payload_hash),
            )
            if body.payload is not None
            else None
        ),
        payload_hash=payload_hash,
        outcome_hash=sha256_json(document),
        occurred_at=body.occurred_at,
        recorded_by_principal_ref=principal_ref,
    )
    db.add(row)
    await db.flush()
    return row


def feedback_out(row: Feedback) -> FeedbackOut:
    payload = json.loads(
        unseal_text(
            row.payload_encrypted,
            purpose=_FEEDBACK_PURPOSE,
            context=_payload_context("feedback", row.id, row.namespace, row.payload_hash),
        )
    )
    return FeedbackOut(
        id=row.id,
        agent_version_id=row.agent_version_id,
        outcome_id=row.outcome_id,
        decision_id=row.decision_id,
        decision_receipt_hash=row.decision_receipt_hash,
        kind=row.kind,
        payload=payload,
        payload_hash=row.payload_hash,
        generated_eval_case_id=row.generated_eval_case_id,
        feedback_hash=row.feedback_hash,
        authored_by_principal_ref=row.authored_by_principal_ref,
        authored_at=row.authored_at,
    )


def learning_proposal_out(row: LearningProposal) -> LearningProposalOut:
    return LearningProposalOut(
        id=row.id,
        agent_version_id=row.agent_version_id,
        source_feedback_id=row.source_feedback_id,
        source_drift_signal_id=row.source_drift_signal_id,
        eval_case_id=row.eval_case_id,
        proposal_type=row.proposal_type,
        recommendation=row.recommendation,
        priority=row.priority,
        status=row.status,
        proposal_hash=row.proposal_hash,
        created_by_principal_ref=row.created_by_principal_ref,
        created_at=row.created_at,
    )


async def create_feedback(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: FeedbackCreate,
) -> tuple[Feedback, LearningProposal]:
    await visible_by_id(
        db,
        AgentVersion,
        body.agent_version_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    if body.outcome_id is not None:
        outcome = await visible_by_id(
            db,
            Outcome,
            body.outcome_id,
            namespace=namespace,
            barrier_group=barrier_group,
        )
        if outcome.agent_version_id != body.agent_version_id:
            raise LearningContractError("feedback outcome belongs to a different agent version")
    row_id = uuid.uuid4()
    payload_hash = sha256_json(body.payload)
    generated_case = None
    if body.auto_create_eval_case and body.decision_id is not None:
        generated_case = await create_eval_case_from_decision(
            db,
            namespace=namespace,
            barrier_group=barrier_group,
            principal_ref=principal_ref,
            body=EvalCaseFromDecision(
                decision_id=body.decision_id,
                decision_receipt_hash=body.decision_receipt_hash,
                name=f"production-{body.kind}-{row_id}",
                scorer_context={
                    "feedback_payload_hash": payload_hash,
                    "feedback_kind": body.kind,
                },
                tags=["production-regression", body.kind],
                capture_limitations=[
                    "Feedback details remain encrypted; case input is reconstructed from the decision boundary."
                ],
            ),
        )
    document = {
        "schema": "lians.feedback.v1",
        "id": str(row_id),
        "agent_version_id": str(body.agent_version_id),
        "outcome_id": str(body.outcome_id) if body.outcome_id else None,
        "decision_id": str(body.decision_id) if body.decision_id else None,
        "decision_receipt_hash": body.decision_receipt_hash,
        "kind": body.kind,
        "payload_hash": payload_hash,
        "generated_eval_case_hash": generated_case.case_hash if generated_case else None,
    }
    feedback = Feedback(
        id=row_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        agent_version_id=body.agent_version_id,
        outcome_id=body.outcome_id,
        decision_id=body.decision_id,
        decision_receipt_hash=body.decision_receipt_hash,
        kind=body.kind,
        payload_encrypted=seal_text(
            json.dumps(body.payload, sort_keys=True, separators=(",", ":")),
            purpose=_FEEDBACK_PURPOSE,
            context=_payload_context("feedback", row_id, namespace, payload_hash),
        ),
        payload_hash=payload_hash,
        generated_eval_case_id=generated_case.id if generated_case else None,
        feedback_hash=sha256_json(document),
        authored_by_principal_ref=principal_ref,
    )
    db.add(feedback)
    await db.flush()
    priority_by_kind = {
        "incident": 1.0,
        "human_override": 0.9,
        "correction": 0.85,
        "dispute": 0.8,
        "rating": 0.4,
        "comment": 0.3,
    }
    proposal_id = uuid.uuid4()
    recommendation = {
        "action": "add_to_regression_suite" if generated_case else "investigate_feedback",
        "feedback_hash": feedback.feedback_hash,
        "eval_case_id": str(generated_case.id) if generated_case else None,
        "automatic_production_change_authorized": False,
    }
    proposal_document = {
        "schema": "lians.learning-proposal.v1",
        "id": str(proposal_id),
        "agent_version_id": str(body.agent_version_id),
        "source_feedback_hash": feedback.feedback_hash,
        "eval_case_hash": generated_case.case_hash if generated_case else None,
        "proposal_type": "regression_case" if generated_case else "investigate",
        "recommendation": recommendation,
        "priority": priority_by_kind[body.kind],
        "status": "awaiting_customer_approval",
    }
    proposal = LearningProposal(
        id=proposal_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        agent_version_id=body.agent_version_id,
        source_feedback_id=feedback.id,
        source_drift_signal_id=None,
        eval_case_id=generated_case.id if generated_case else None,
        proposal_type="regression_case" if generated_case else "investigate",
        recommendation=recommendation,
        priority=priority_by_kind[body.kind],
        status="awaiting_customer_approval",
        proposal_hash=sha256_json(proposal_document),
        created_by_principal_ref=principal_ref,
    )
    db.add(proposal)
    await db.flush()
    return feedback, proposal


def drift_signal_out(row: DriftSignal) -> DriftSignalOut:
    return DriftSignalOut(
        id=row.id,
        agent_version_id=row.agent_version_id,
        metric_name=row.metric_name,
        baseline=row.baseline,
        current=row.current,
        direction=row.direction,
        magnitude=row.magnitude,
        threshold=row.threshold,
        drifted=row.drifted,
        method=row.method,
        signal_hash=row.signal_hash,
        detected_by_principal_ref=row.detected_by_principal_ref,
        detected_at=row.detected_at,
    )


def _metric_values(rows: Sequence[Outcome], metric_name: str) -> list[float]:
    return [
        float(metric["value"])
        for row in rows
        for metric in row.metrics
        if metric.get("name") == metric_name
    ]


def _summary(values: list[float], *, start: Any, end: Any) -> dict[str, Any]:
    return {
        "start": _utc(start).isoformat() if isinstance(start, datetime) else start,
        "end": _utc(end).isoformat() if isinstance(end, datetime) else end,
        "sample_size": len(values),
        "mean": statistics.fmean(values),
        "variance": statistics.variance(values) if len(values) > 1 else 0.0,
        "minimum": min(values),
        "maximum": max(values),
    }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def analyze_drift(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: DriftAnalyzeRequest,
) -> tuple[DriftSignal, LearningProposal | None]:
    await visible_by_id(
        db,
        AgentVersion,
        body.agent_version_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    scope = [
        Outcome.namespace == namespace,
        Outcome.agent_version_id == body.agent_version_id,
        Outcome.occurred_at >= body.baseline_start,
        Outcome.occurred_at < body.current_end,
    ]
    if barrier_group is not None:
        scope.append(or_(Outcome.barrier_group.is_(None), Outcome.barrier_group == barrier_group))
    rows = list(
        (
            await db.execute(
                select(Outcome)
                .where(*scope)
                .order_by(Outcome.occurred_at.asc(), Outcome.id.asc())
                .limit((body.max_samples_per_window * 2) + 1)
            )
        )
        .scalars()
        .all()
    )
    if len(rows) > body.max_samples_per_window * 2:
        raise LearningContractError("drift analysis exceeds the declared bounded sample capacity")
    baseline_start = _utc(body.baseline_start)
    baseline_end = _utc(body.baseline_end)
    current_start = _utc(body.current_start)
    current_end = _utc(body.current_end)
    baseline_rows = [row for row in rows if baseline_start <= _utc(row.occurred_at) < baseline_end]
    current_rows = [row for row in rows if current_start <= _utc(row.occurred_at) < current_end]
    baseline_values = _metric_values(baseline_rows, body.metric_name)
    current_values = _metric_values(current_rows, body.metric_name)
    if len(baseline_values) < 2 or len(current_values) < 2:
        raise LearningContractError("each drift window requires at least two metric observations")
    baseline = _summary(baseline_values, start=body.baseline_start, end=body.baseline_end)
    current = _summary(current_values, start=body.current_start, end=body.current_end)
    raw_delta = float(current["mean"]) - float(baseline["mean"])
    if body.direction == "increase":
        magnitude = max(0.0, raw_delta)
    elif body.direction == "decrease":
        magnitude = max(0.0, -raw_delta)
    else:
        magnitude = abs(raw_delta)
    drifted = magnitude >= body.threshold
    signal_id = uuid.uuid4()
    document = {
        "schema": "lians.drift-signal.v1",
        "id": str(signal_id),
        "agent_version_id": str(body.agent_version_id),
        "metric_name": body.metric_name,
        "baseline": baseline,
        "current": current,
        "direction": body.direction,
        "magnitude": magnitude,
        "threshold": body.threshold,
        "drifted": drifted,
        "method": "two-window-mean-v1",
    }
    signal = DriftSignal(
        id=signal_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        agent_version_id=body.agent_version_id,
        metric_name=body.metric_name,
        baseline=baseline,
        current=current,
        direction=body.direction,
        magnitude=magnitude,
        threshold=body.threshold,
        drifted=drifted,
        method="two-window-mean-v1",
        signal_hash=sha256_json(document),
        detected_by_principal_ref=principal_ref,
    )
    db.add(signal)
    await db.flush()
    proposal = None
    if drifted:
        proposal_id = uuid.uuid4()
        priority = min(1.0, magnitude / body.threshold) if body.threshold > 0 else 1.0
        recommendation = {
            "action": "investigate_and_build_regression_cases",
            "metric_name": body.metric_name,
            "drift_signal_hash": signal.signal_hash,
            "automatic_production_change_authorized": False,
        }
        proposal_document = {
            "schema": "lians.learning-proposal.v1",
            "id": str(proposal_id),
            "agent_version_id": str(body.agent_version_id),
            "source_drift_signal_hash": signal.signal_hash,
            "proposal_type": "investigate",
            "recommendation": recommendation,
            "priority": priority,
            "status": "awaiting_customer_approval",
        }
        proposal = LearningProposal(
            id=proposal_id,
            namespace=namespace,
            barrier_group=barrier_group,
            barrier_scope=barrier_scope(barrier_group),
            agent_version_id=body.agent_version_id,
            source_feedback_id=None,
            source_drift_signal_id=signal.id,
            eval_case_id=None,
            proposal_type="investigate",
            recommendation=recommendation,
            priority=priority,
            status="awaiting_customer_approval",
            proposal_hash=sha256_json(proposal_document),
            created_by_principal_ref=principal_ref,
        )
        db.add(proposal)
        await db.flush()
    return signal, proposal


__all__ = [
    "LearningContractError",
    "analyze_drift",
    "create_feedback",
    "create_outcome",
    "drift_signal_out",
    "feedback_out",
    "learning_proposal_out",
    "outcome_out",
]
