"""Construction, aggregation, signing, and verification for improvement evidence."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import statistics
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .decision_record_integrity import assert_decision_record_integrity
from .improvement_models import (
    AgentDefinition,
    AgentVersion,
    AgentVersionComponent,
    Candidate,
    Comparison,
    ComponentArtifact,
    EvalCase,
    EvalRun,
    EvalSuite,
    EvalSuiteCase,
    EvaluationAttestation,
    MetricResult,
    OptimizationStudy,
    Recommendation,
    Trial,
)
from .improvement_schemas import (
    AgentDefinitionCreate,
    AgentDefinitionOut,
    AgentVersionCreate,
    AgentVersionOut,
    ComparisonOut,
    ComponentArtifactCreate,
    ComponentArtifactOut,
    EvalCaseFromDecision,
    EvalCaseOut,
    EvalRunCreate,
    EvalRunOut,
    EvalSuiteCreate,
    EvalSuiteOut,
    EvaluationAttestationCreate,
    EvaluationAttestationOut,
    EvaluationAttestationVerification,
    MetricAggregate,
    MetricResultOut,
    OptimizationCandidateOut,
    OptimizationStudyCreate,
    OptimizationStudyOut,
    ProtectedMetricResult,
    RecommendationOut,
    TrialOut,
)
from .models import DecisionRecord, EventLog
from .receipt_signer import ReceiptSigner, ReceiptSigningUnavailable


class ImprovementContractError(ValueError):
    """A requested record would make an unsupported or unpinned claim."""


class ImprovementNotFound(LookupError):
    """A scoped improvement-plane record is not visible to the caller."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=lambda item: _utc(item).isoformat() if isinstance(item, datetime) else str(item),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def barrier_scope(barrier_group: str | None) -> str:
    if barrier_group is None:
        return "unbarriered"
    digest = hashlib.sha256(
        b"lians/improvement-barrier/v1\0" + barrier_group.encode("utf-8")
    ).hexdigest()
    return f"barrier:{digest[:56]}"


def _visible_conditions(model, record_id: UUID, namespace: str, barrier_group: str | None):
    conditions = [model.id == record_id, model.namespace == namespace]
    if barrier_group is not None:
        conditions.append(or_(model.barrier_group.is_(None), model.barrier_group == barrier_group))
    return conditions


async def visible_by_id(
    db: AsyncSession,
    model,
    record_id: UUID,
    *,
    namespace: str,
    barrier_group: str | None,
):
    row = (
        await db.execute(
            select(model).where(*_visible_conditions(model, record_id, namespace, barrier_group))
        )
    ).scalar_one_or_none()
    if row is None:
        raise ImprovementNotFound(f"{model.__name__} not found")
    return row


def _same_or_shared(row, barrier_group: str | None) -> bool:
    return barrier_group is None or row.barrier_group is None or row.barrier_group == barrier_group


def agent_definition_out(row: AgentDefinition) -> AgentDefinitionOut:
    return AgentDefinitionOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        key=row.key,
        name=row.name,
        description=row.description,
        metadata=dict(row.metadata_ or {}),
        definition_hash=row.definition_hash,
        created_by_principal_ref=row.created_by_principal_ref,
        created_at=row.created_at,
    )


def component_artifact_out(row: ComponentArtifact) -> ComponentArtifactOut:
    return ComponentArtifactOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        kind=row.kind,
        name=row.name,
        version=row.version,
        uri=row.uri,
        digest_algorithm=row.digest_algorithm,
        digest=row.digest,
        metadata=dict(row.metadata_ or {}),
        artifact_hash=row.artifact_hash,
        created_by_principal_ref=row.created_by_principal_ref,
        created_at=row.created_at,
    )


async def create_agent_definition(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: AgentDefinitionCreate,
) -> AgentDefinition:
    created_at = utc_now()
    document = {
        "schema": "lians.agent-definition.v1",
        "namespace": namespace,
        "barrier_scope": barrier_scope(barrier_group),
        "key": body.key,
        "name": body.name,
        "description": body.description,
        "metadata": body.metadata,
    }
    row = AgentDefinition(
        id=uuid.uuid4(),
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        key=body.key,
        name=body.name,
        description=body.description,
        metadata_=body.metadata,
        definition_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
        created_at=created_at,
    )
    db.add(row)
    await db.flush()
    return row


async def _get_or_create_artifact(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: ComponentArtifactCreate,
) -> ComponentArtifact:
    scope = barrier_scope(barrier_group)
    existing = (
        await db.execute(
            select(ComponentArtifact).where(
                ComponentArtifact.namespace == namespace,
                ComponentArtifact.barrier_scope == scope,
                ComponentArtifact.kind == body.kind,
                ComponentArtifact.digest == body.digest,
            )
        )
    ).scalar_one_or_none()
    document = {
        "schema": "lians.component-artifact.v1",
        "namespace": namespace,
        "barrier_scope": scope,
        **body.model_dump(mode="json"),
    }
    artifact_hash = sha256_json(document)
    if existing is not None:
        if existing.artifact_hash != artifact_hash:
            raise ImprovementContractError(
                "An artifact digest is already bound to different immutable metadata"
            )
        return existing
    row = ComponentArtifact(
        id=uuid.uuid4(),
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=scope,
        kind=body.kind,
        name=body.name,
        version=body.version,
        uri=body.uri,
        digest_algorithm=body.digest_algorithm,
        digest=body.digest,
        metadata_=body.metadata,
        artifact_hash=artifact_hash,
        created_by_principal_ref=principal_ref,
        created_at=utc_now(),
    )
    db.add(row)
    await db.flush()
    return row


async def create_agent_version(
    db: AsyncSession,
    *,
    agent_definition: AgentDefinition,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: AgentVersionCreate,
) -> AgentVersion:
    if not _same_or_shared(agent_definition, barrier_group):
        raise ImprovementNotFound("AgentDefinition not found")
    artifacts: list[tuple[str, ComponentArtifact]] = []
    for component in body.components:
        if component.artifact is not None:
            artifact = await _get_or_create_artifact(
                db,
                namespace=namespace,
                barrier_group=barrier_group,
                principal_ref=principal_ref,
                body=component.artifact,
            )
        else:
            artifact = await visible_by_id(
                db,
                ComponentArtifact,
                component.artifact_id,
                namespace=namespace,
                barrier_group=barrier_group,
            )
        artifacts.append((component.role, artifact))

    component_document = [
        {
            "role": role,
            "position": position,
            "artifact_id": str(artifact.id),
            "artifact_hash": artifact.artifact_hash,
            "digest": artifact.digest,
        }
        for position, (role, artifact) in enumerate(artifacts)
    ]
    document = {
        "schema": "lians.agent-version.v1",
        "agent_definition_id": str(agent_definition.id),
        "definition_hash": agent_definition.definition_hash,
        "version": body.version,
        "manifest": body.manifest,
        "components": component_document,
    }
    row = AgentVersion(
        id=uuid.uuid4(),
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        agent_definition_id=agent_definition.id,
        version=body.version,
        manifest=body.manifest,
        manifest_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
        created_at=utc_now(),
    )
    db.add(row)
    await db.flush()
    for position, (role, artifact) in enumerate(artifacts):
        binding_document = component_document[position]
        db.add(
            AgentVersionComponent(
                id=uuid.uuid4(),
                namespace=namespace,
                barrier_group=barrier_group,
                barrier_scope=barrier_scope(barrier_group),
                agent_version_id=row.id,
                component_artifact_id=artifact.id,
                role=role,
                position=position,
                binding_hash=sha256_json(binding_document),
            )
        )
    await db.flush()
    return row


async def agent_version_out(db: AsyncSession, row: AgentVersion) -> AgentVersionOut:
    bindings = (
        await db.execute(
            select(AgentVersionComponent, ComponentArtifact)
            .join(
                ComponentArtifact,
                ComponentArtifact.id == AgentVersionComponent.component_artifact_id,
            )
            .where(
                AgentVersionComponent.namespace == row.namespace,
                AgentVersionComponent.agent_version_id == row.id,
            )
            .order_by(AgentVersionComponent.position.asc())
        )
    ).all()
    return AgentVersionOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        agent_definition_id=row.agent_definition_id,
        version=row.version,
        manifest=dict(row.manifest or {}),
        manifest_hash=row.manifest_hash,
        components=[
            {
                "role": binding.role,
                "position": binding.position,
                "binding_hash": binding.binding_hash,
                "artifact": component_artifact_out(artifact),
            }
            for binding, artifact in bindings
        ],
        created_by_principal_ref=row.created_by_principal_ref,
        created_at=row.created_at,
    )


def eval_case_out(row: EvalCase) -> EvalCaseOut:
    return EvalCaseOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        decision_id=row.decision_id,
        decision_record_hash=row.decision_record_hash,
        decision_receipt_hash=row.decision_receipt_hash,
        name=row.name,
        input=dict(row.input or {}),
        expected=dict(row.expected or {}),
        scorer_context=dict(row.scorer_context or {}),
        tags=list(row.tags or []),
        capture_limitations=list(row.capture_limitations or []),
        case_hash=row.case_hash,
        created_by_principal_ref=row.created_by_principal_ref,
        created_at=row.created_at,
    )


async def create_eval_case_from_decision(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: EvalCaseFromDecision,
) -> EvalCase:
    decision = await visible_by_id(
        db,
        DecisionRecord,
        body.decision_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    await assert_decision_record_integrity(db, decision)
    receipt_events = (
        (
            await db.execute(
                select(EventLog).where(
                    EventLog.namespace == namespace,
                    EventLog.op == "decision_receipt_exported",
                    EventLog.content_hash == body.decision_receipt_hash,
                )
            )
        )
        .scalars()
        .all()
    )
    if not any(
        str((event.payload or {}).get("decision_id")) == str(decision.id)
        for event in receipt_events
    ):
        raise ImprovementContractError(
            "decision_receipt_hash is not a recorded export for the selected decision"
        )

    case_input = body.input or {
        "decision_type": decision.decision_type,
        "agent_label": decision.agent_id,
        "model_id": decision.model_id,
        "model_version": decision.model_version,
        "policy_version": decision.policy_version,
        "input_hash": decision.input_hash,
        "knowledge_as_of": _utc(decision.knowledge_as_of).isoformat(),
    }
    expected = body.expected or {
        "recorded_outcome": decision.outcome,
        "output_hash": decision.output_hash,
        "reason_codes": list(decision.reason_codes or []),
    }
    document = {
        "schema": "lians.eval-case.v1",
        "decision_id": str(decision.id),
        "decision_record_hash": decision.record_hash,
        "decision_receipt_hash": body.decision_receipt_hash,
        "name": body.name,
        "input": case_input,
        "expected": expected,
        "scorer_context": body.scorer_context,
        "tags": body.tags,
        "capture_limitations": body.capture_limitations,
    }
    row = EvalCase(
        id=uuid.uuid4(),
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        decision_id=decision.id,
        decision_record_hash=decision.record_hash,
        decision_receipt_hash=body.decision_receipt_hash,
        name=body.name,
        input=case_input,
        expected=expected,
        scorer_context=body.scorer_context,
        tags=body.tags,
        capture_limitations=body.capture_limitations,
        case_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
        created_at=utc_now(),
    )
    db.add(row)
    await db.flush()
    return row


async def create_eval_suite(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: EvalSuiteCreate,
) -> EvalSuite:
    cases: list[EvalCase] = []
    for case_id in body.case_ids:
        cases.append(
            await visible_by_id(
                db, EvalCase, case_id, namespace=namespace, barrier_group=barrier_group
            )
        )
    document = {
        "schema": "lians.eval-suite.v1",
        "name": body.name,
        "version": body.version,
        "description": body.description,
        "case_hashes": [case.case_hash for case in cases],
        "improvement_contract": body.improvement_contract.model_dump(mode="json"),
        "repetitions": body.repetitions,
    }
    row = EvalSuite(
        id=uuid.uuid4(),
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        name=body.name,
        version=body.version,
        description=body.description,
        improvement_contract=body.improvement_contract.model_dump(mode="json"),
        repetitions=body.repetitions,
        suite_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
        created_at=utc_now(),
    )
    db.add(row)
    await db.flush()
    for position, case in enumerate(cases):
        db.add(
            EvalSuiteCase(
                id=uuid.uuid4(),
                namespace=namespace,
                barrier_group=barrier_group,
                barrier_scope=barrier_scope(barrier_group),
                suite_id=row.id,
                case_id=case.id,
                position=position,
            )
        )
    await db.flush()
    return row


async def _suite_case_ids(db: AsyncSession, suite_id: UUID, namespace: str) -> list[UUID]:
    return list(
        (
            await db.execute(
                select(EvalSuiteCase.case_id)
                .where(
                    EvalSuiteCase.namespace == namespace,
                    EvalSuiteCase.suite_id == suite_id,
                )
                .order_by(EvalSuiteCase.position.asc())
            )
        )
        .scalars()
        .all()
    )


async def eval_suite_out(db: AsyncSession, row: EvalSuite) -> EvalSuiteOut:
    return EvalSuiteOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        name=row.name,
        version=row.version,
        description=row.description,
        case_ids=await _suite_case_ids(db, row.id, row.namespace),
        improvement_contract=row.improvement_contract,
        repetitions=row.repetitions,
        suite_hash=row.suite_hash,
        created_by_principal_ref=row.created_by_principal_ref,
        created_at=row.created_at,
    )


def _metric_result_out(row: MetricResult) -> MetricResultOut:
    return MetricResultOut(
        id=row.id,
        name=row.name,
        metric_type=row.metric_type,
        value=row.value,
        unit=row.unit,
        provenance=row.provenance,
        scorer_id=row.scorer_id,
        scorer_version=row.scorer_version,
        scorer_config_hash=row.scorer_config_hash,
        evidence_refs=list(row.evidence_refs or []),
        limitations=list(row.limitations or []),
        result_hash=row.result_hash,
    )


async def eval_run_out(db: AsyncSession, row: EvalRun) -> EvalRunOut:
    trial_rows = list(
        (
            await db.execute(
                select(Trial)
                .where(Trial.namespace == row.namespace, Trial.run_id == row.id)
                .order_by(Trial.case_id.asc(), Trial.repetition.asc())
            )
        )
        .scalars()
        .all()
    )
    metrics_by_trial: dict[UUID, list[MetricResult]] = defaultdict(list)
    if trial_rows:
        metric_rows = list(
            (
                await db.execute(
                    select(MetricResult)
                    .where(
                        MetricResult.namespace == row.namespace,
                        MetricResult.trial_id.in_([trial.id for trial in trial_rows]),
                    )
                    .order_by(MetricResult.name.asc())
                )
            )
            .scalars()
            .all()
        )
        for metric in metric_rows:
            metrics_by_trial[metric.trial_id].append(metric)
    return EvalRunOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        suite_id=row.suite_id,
        agent_version_id=row.agent_version_id,
        environment=dict(row.environment or {}),
        capture_limitations=list(row.capture_limitations or []),
        trial_count=row.trial_count,
        run_hash=row.run_hash,
        created_by_principal_ref=row.created_by_principal_ref,
        completed_at=row.completed_at,
        trials=[
            TrialOut(
                id=trial.id,
                case_id=trial.case_id,
                repetition=trial.repetition,
                seed=trial.seed,
                input_hash=trial.input_hash,
                output_hash=trial.output_hash,
                configuration_hash=trial.configuration_hash,
                latency_ms=trial.latency_ms,
                input_tokens=trial.input_tokens,
                output_tokens=trial.output_tokens,
                cost=trial.cost,
                cost_currency=trial.cost_currency,
                started_at=trial.started_at,
                completed_at=trial.completed_at,
                trial_hash=trial.trial_hash,
                metrics=[_metric_result_out(metric) for metric in metrics_by_trial[trial.id]],
            )
            for trial in trial_rows
        ],
    )


async def create_eval_run(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: EvalRunCreate,
) -> EvalRun:
    suite = await visible_by_id(
        db, EvalSuite, body.suite_id, namespace=namespace, barrier_group=barrier_group
    )
    version = await visible_by_id(
        db, AgentVersion, body.agent_version_id, namespace=namespace, barrier_group=barrier_group
    )
    case_ids = await _suite_case_ids(db, suite.id, namespace)
    expected_pairs = {
        (case_id, repetition) for case_id in case_ids for repetition in range(suite.repetitions)
    }
    supplied_pairs = {(trial.case_id, trial.repetition) for trial in body.trials}
    if supplied_pairs != expected_pairs:
        missing = len(expected_pairs - supplied_pairs)
        extra = len(supplied_pairs - expected_pairs)
        raise ImprovementContractError(
            f"run must contain exactly every suite case/repetition pair (missing={missing}, extra={extra})"
        )
    required_metrics = {
        suite.improvement_contract["primary_metric"]["name"],
        *[metric["name"] for metric in suite.improvement_contract.get("protected_metrics", [])],
    }
    case_by_id = {
        case.id: case
        for case in (
            await db.execute(
                select(EvalCase).where(EvalCase.namespace == namespace, EvalCase.id.in_(case_ids))
            )
        )
        .scalars()
        .all()
    }
    trial_documents: list[dict[str, Any]] = []
    for trial in body.trials:
        if trial.configuration_hash != version.manifest_hash:
            raise ImprovementContractError(
                "every trial configuration_hash must equal the selected immutable agent manifest hash"
            )
        if trial.input_hash != sha256_json(case_by_id[trial.case_id].input):
            raise ImprovementContractError(
                "trial input_hash does not match the immutable evaluation case input"
            )
        names = {metric.name for metric in trial.metrics}
        if not required_metrics.issubset(names):
            missing = sorted(required_metrics - names)
            raise ImprovementContractError(
                f"trial is missing required contract metrics: {', '.join(missing)}"
            )
        trial_documents.append(trial.model_dump(mode="json"))

    run_id = uuid.uuid4()
    completed_at = max(_utc(trial.completed_at) for trial in body.trials)
    trial_hashes: list[str] = []
    trial_rows: list[tuple[Trial, Any]] = []
    for trial_body, document in zip(body.trials, trial_documents, strict=True):
        trial_id = uuid.uuid4()
        trial_document = {
            "schema": "lians.eval-trial.v1",
            "id": str(trial_id),
            "run_id": str(run_id),
            **document,
        }
        trial_hash = sha256_json(trial_document)
        trial_hashes.append(trial_hash)
        row = Trial(
            id=trial_id,
            namespace=namespace,
            barrier_group=barrier_group,
            barrier_scope=barrier_scope(barrier_group),
            run_id=run_id,
            case_id=trial_body.case_id,
            repetition=trial_body.repetition,
            seed=trial_body.seed,
            input_hash=trial_body.input_hash,
            output_hash=trial_body.output_hash,
            configuration_hash=trial_body.configuration_hash,
            latency_ms=trial_body.latency_ms,
            input_tokens=trial_body.input_tokens,
            output_tokens=trial_body.output_tokens,
            cost=trial_body.cost,
            cost_currency=trial_body.cost_currency,
            started_at=_utc(trial_body.started_at),
            completed_at=_utc(trial_body.completed_at),
            trial_hash=trial_hash,
        )
        trial_rows.append((row, trial_body))

    run_document = {
        "schema": "lians.eval-run.v1",
        "id": str(run_id),
        "suite_id": str(suite.id),
        "suite_hash": suite.suite_hash,
        "agent_version_id": str(version.id),
        "manifest_hash": version.manifest_hash,
        "environment": body.environment,
        "capture_limitations": body.capture_limitations,
        "trial_hashes": sorted(trial_hashes),
    }
    run = EvalRun(
        id=run_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        suite_id=suite.id,
        agent_version_id=version.id,
        environment=body.environment,
        capture_limitations=body.capture_limitations,
        trial_count=len(body.trials),
        run_hash=sha256_json(run_document),
        created_by_principal_ref=principal_ref,
        completed_at=completed_at,
    )
    db.add(run)
    await db.flush()
    for trial, trial_body in trial_rows:
        db.add(trial)
        await db.flush()
        for metric_body in trial_body.metrics:
            metric_id = uuid.uuid4()
            result_document = {
                "schema": "lians.metric-result.v1",
                "id": str(metric_id),
                "trial_id": str(trial.id),
                **metric_body.model_dump(mode="json"),
            }
            db.add(
                MetricResult(
                    id=metric_id,
                    namespace=namespace,
                    barrier_group=barrier_group,
                    barrier_scope=barrier_scope(barrier_group),
                    trial_id=trial.id,
                    name=metric_body.name,
                    metric_type=metric_body.metric_type,
                    value=metric_body.value,
                    unit=metric_body.unit,
                    provenance=metric_body.provenance,
                    scorer_id=metric_body.scorer_id,
                    scorer_version=metric_body.scorer_version,
                    scorer_config_hash=metric_body.scorer_config_hash,
                    evidence_refs=metric_body.evidence_refs,
                    limitations=metric_body.limitations,
                    result_hash=sha256_json(result_document),
                )
            )
    await db.flush()
    return run


async def _metric_values(db: AsyncSession, run: EvalRun) -> dict[str, list[float]]:
    rows = (
        await db.execute(
            select(MetricResult.name, MetricResult.value)
            .join(Trial, Trial.id == MetricResult.trial_id)
            .where(Trial.namespace == run.namespace, Trial.run_id == run.id)
        )
    ).all()
    values: dict[str, list[float]] = defaultdict(list)
    for name, value in rows:
        values[str(name)].append(float(value))
    return values


def _aggregate(
    name: str,
    direction: str,
    baseline: list[float],
    candidate: list[float],
) -> MetricAggregate:
    if not baseline or not candidate:
        raise ImprovementContractError(f"comparison metric {name} has no observations")

    def summarize(values: list[float]) -> tuple[float, float, list[float]]:
        mean = statistics.fmean(values)
        variance = statistics.variance(values) if len(values) > 1 else 0.0
        half_width = 1.96 * math.sqrt(variance / len(values)) if values else 0.0
        return mean, variance, [mean - half_width, mean + half_width]

    baseline_mean, baseline_variance, baseline_ci = summarize(baseline)
    candidate_mean, candidate_variance, candidate_ci = summarize(candidate)
    raw_delta = candidate_mean - baseline_mean
    improvement = raw_delta if direction == "maximize" else -raw_delta
    return MetricAggregate(
        name=name,
        direction=direction,
        baseline_mean=baseline_mean,
        candidate_mean=candidate_mean,
        baseline_variance=baseline_variance,
        candidate_variance=candidate_variance,
        baseline_ci95=baseline_ci,
        candidate_ci95=candidate_ci,
        raw_delta=raw_delta,
        improvement=improvement,
        sample_size_baseline=len(baseline),
        sample_size_candidate=len(candidate),
    )


def comparison_out(row: Comparison) -> ComparisonOut:
    return ComparisonOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        suite_id=row.suite_id,
        baseline_run_id=row.baseline_run_id,
        candidate_run_id=row.candidate_run_id,
        primary_metric=row.primary_metric,
        primary_improvement=row.primary_improvement,
        aggregates=row.aggregates,
        protected_results=row.protected_results,
        critical_invariants_passed=row.critical_invariants_passed,
        verdict=row.verdict,
        comparison_hash=row.comparison_hash,
        created_by_principal_ref=row.created_by_principal_ref,
        created_at=row.created_at,
    )


async def create_comparison(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    baseline_run_id: UUID,
    candidate_run_id: UUID,
) -> Comparison:
    baseline = await visible_by_id(
        db, EvalRun, baseline_run_id, namespace=namespace, barrier_group=barrier_group
    )
    candidate = await visible_by_id(
        db, EvalRun, candidate_run_id, namespace=namespace, barrier_group=barrier_group
    )
    if baseline.suite_id != candidate.suite_id:
        raise ImprovementContractError("baseline and candidate runs must use the same suite")
    if baseline.agent_version_id == candidate.agent_version_id:
        raise ImprovementContractError("baseline and candidate must use different agent versions")
    if baseline.trial_count != candidate.trial_count:
        raise ImprovementContractError("baseline and candidate trial inventories must match")
    suite = await visible_by_id(
        db, EvalSuite, baseline.suite_id, namespace=namespace, barrier_group=barrier_group
    )
    baseline_values = await _metric_values(db, baseline)
    candidate_values = await _metric_values(db, candidate)
    contract = suite.improvement_contract
    primary = contract["primary_metric"]
    contract_metrics = [primary, *contract.get("protected_metrics", [])]
    aggregates = [
        _aggregate(
            item["name"],
            item["direction"],
            baseline_values.get(item["name"], []),
            candidate_values.get(item["name"], []),
        )
        for item in contract_metrics
    ]
    aggregate_by_name = {aggregate.name: aggregate for aggregate in aggregates}
    protected_results: list[ProtectedMetricResult] = []
    for protected in contract.get("protected_metrics", []):
        aggregate = aggregate_by_name[protected["name"]]
        degradation = max(0.0, -aggregate.improvement)
        candidate_samples = candidate_values[protected["name"]]
        minimum = protected.get("minimum")
        maximum = protected.get("maximum")
        minimum_passed = minimum is None or all(value >= minimum for value in candidate_samples)
        maximum_passed = maximum is None or all(value <= maximum for value in candidate_samples)
        passed = (
            degradation <= float(protected.get("maximum_degradation", 0.0))
            and minimum_passed
            and maximum_passed
        )
        protected_results.append(
            ProtectedMetricResult(
                name=protected["name"],
                passed=passed,
                critical=bool(protected.get("critical", False)),
                degradation=degradation,
                maximum_degradation=float(protected.get("maximum_degradation", 0.0)),
                minimum_passed=minimum_passed,
                maximum_passed=maximum_passed,
            )
        )
    all_protected_passed = all(result.passed for result in protected_results)
    critical_passed = all(result.passed for result in protected_results if result.critical)
    primary_aggregate = aggregate_by_name[primary["name"]]
    primary_passed = primary_aggregate.improvement > float(primary.get("minimum_improvement", 0.0))
    if not all_protected_passed:
        verdict = "protected_regression"
    elif primary_passed:
        verdict = "eligible_for_review"
    else:
        verdict = "no_verified_improvement"
    created_at = utc_now()
    document = {
        "schema": "lians.eval-comparison.v1",
        "suite_id": str(suite.id),
        "suite_hash": suite.suite_hash,
        "baseline_run_id": str(baseline.id),
        "baseline_run_hash": baseline.run_hash,
        "candidate_run_id": str(candidate.id),
        "candidate_run_hash": candidate.run_hash,
        "aggregates": [item.model_dump(mode="json") for item in aggregates],
        "protected_results": [item.model_dump(mode="json") for item in protected_results],
        "critical_invariants_passed": critical_passed,
        "verdict": verdict,
        "created_at": created_at,
    }
    row = Comparison(
        id=uuid.uuid4(),
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        suite_id=suite.id,
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        primary_metric=primary["name"],
        primary_improvement=primary_aggregate.improvement,
        aggregates=[item.model_dump(mode="json") for item in aggregates],
        protected_results=[item.model_dump(mode="json") for item in protected_results],
        critical_invariants_passed=critical_passed,
        verdict=verdict,
        comparison_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
        created_at=created_at,
    )
    db.add(row)
    await db.flush()
    return row


def evaluation_attestation_out(row: EvaluationAttestation) -> EvaluationAttestationOut:
    return EvaluationAttestationOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        schema_version=row.schema_version,
        comparison_id=row.comparison_id,
        payload=dict(row.payload),
        payload_hash=row.payload_hash,
        signature_algorithm=row.signature_algorithm,
        signing_key_id=row.signing_key_id,
        signing_public_key=row.signing_public_key,
        signature=row.signature,
        created_by_principal_ref=row.created_by_principal_ref,
        attested_at=row.attested_at,
    )


async def create_evaluation_attestation(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: EvaluationAttestationCreate,
    signer: ReceiptSigner | None,
) -> EvaluationAttestation:
    if signer is None:
        raise ReceiptSigningUnavailable(
            "A configured Ed25519 signer is required for Evaluation Attestations"
        )
    comparison = await visible_by_id(
        db, Comparison, body.comparison_id, namespace=namespace, barrier_group=barrier_group
    )
    suite = await visible_by_id(
        db, EvalSuite, comparison.suite_id, namespace=namespace, barrier_group=barrier_group
    )
    baseline = await visible_by_id(
        db, EvalRun, comparison.baseline_run_id, namespace=namespace, barrier_group=barrier_group
    )
    candidate = await visible_by_id(
        db, EvalRun, comparison.candidate_run_id, namespace=namespace, barrier_group=barrier_group
    )
    baseline_version = await visible_by_id(
        db,
        AgentVersion,
        baseline.agent_version_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    candidate_version = await visible_by_id(
        db,
        AgentVersion,
        candidate.agent_version_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    case_ids = await _suite_case_ids(db, suite.id, namespace)
    receipt_hashes = sorted(
        (
            await db.execute(
                select(EvalCase.decision_receipt_hash).where(
                    EvalCase.namespace == namespace, EvalCase.id.in_(case_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    attestation_id = uuid.uuid4()
    attested_at = utc_now()
    payload = {
        "schema": "lians.evaluation-attestation.v0.1",
        "id": str(attestation_id),
        "namespace": namespace,
        "barrier_scope": barrier_scope(barrier_group),
        "comparison": {
            "id": str(comparison.id),
            "hash": comparison.comparison_hash,
            "verdict": comparison.verdict,
            "primary_metric": comparison.primary_metric,
            "primary_improvement": comparison.primary_improvement,
            "critical_invariants_passed": comparison.critical_invariants_passed,
        },
        "suite": {"id": str(suite.id), "hash": suite.suite_hash},
        "baseline": {
            "run_id": str(baseline.id),
            "run_hash": baseline.run_hash,
            "agent_version_id": str(baseline_version.id),
            "manifest_hash": baseline_version.manifest_hash,
        },
        "candidate": {
            "run_id": str(candidate.id),
            "run_hash": candidate.run_hash,
            "agent_version_id": str(candidate_version.id),
            "manifest_hash": candidate_version.manifest_hash,
        },
        "decision_receipt_hashes": receipt_hashes,
        "claims": body.claims,
        "limitations": sorted(
            {
                *body.limitations,
                *list(baseline.capture_limitations or []),
                *list(candidate.capture_limitations or []),
            }
        ),
        "attested_at": _utc(attested_at).isoformat(),
    }
    payload_hash = sha256_json(payload)
    signature = await signer.sign_digest(bytes.fromhex(payload_hash))
    if (
        signature.algorithm != "ed25519"
        or signature.key_id != signer.key_id
        or signature.public_key != signer.public_key
    ):
        raise ReceiptSigningUnavailable("Evaluation signer returned inconsistent trust metadata")
    row = EvaluationAttestation(
        id=attestation_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        schema_version="0.1",
        comparison_id=comparison.id,
        payload=payload,
        payload_hash=payload_hash,
        signature_algorithm=signature.algorithm,
        signing_key_id=signature.key_id,
        signing_public_key=signature.public_key,
        signature=signature.value,
        created_by_principal_ref=principal_ref,
        attested_at=attested_at,
    )
    db.add(row)
    await db.flush()
    return row


def verify_evaluation_attestation(
    attestation: EvaluationAttestationOut,
    *,
    trusted_public_key: str | None = None,
) -> EvaluationAttestationVerification:
    errors: list[str] = []
    expected_hash = sha256_json(attestation.payload)
    payload_hash_valid = expected_hash == attestation.payload_hash
    if not payload_hash_valid:
        errors.append("payload_hash does not match the canonical attestation payload")
    public_key_text = trusted_public_key or attestation.signing_public_key
    if trusted_public_key is not None and trusted_public_key != attestation.signing_public_key:
        errors.append("embedded signing key does not match the trusted public key")
    signature_valid = False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_text, validate=True)
        )
        signature_bytes = base64.b64decode(attestation.signature, validate=True)
        public_key.verify(signature_bytes, bytes.fromhex(attestation.payload_hash))
        signature_valid = True
    except (ValueError, InvalidSignature):
        errors.append("Ed25519 signature is invalid")
    return EvaluationAttestationVerification(
        valid=payload_hash_valid and signature_valid and not errors,
        payload_hash_valid=payload_hash_valid,
        signature_valid=signature_valid,
        errors=errors,
    )


async def optimization_study_out(db: AsyncSession, row: OptimizationStudy) -> OptimizationStudyOut:
    candidates = list(
        (
            await db.execute(
                select(Candidate)
                .where(Candidate.namespace == row.namespace, Candidate.study_id == row.id)
                .order_by(Candidate.rank.asc())
            )
        )
        .scalars()
        .all()
    )
    recommendations = list(
        (
            await db.execute(
                select(Recommendation)
                .where(
                    Recommendation.namespace == row.namespace,
                    Recommendation.study_id == row.id,
                )
                .order_by(Recommendation.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return OptimizationStudyOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        name=row.name,
        suite_id=row.suite_id,
        baseline_agent_version_id=row.baseline_agent_version_id,
        objective=dict(row.objective or {}),
        status=row.status,
        study_hash=row.study_hash,
        created_by_principal_ref=row.created_by_principal_ref,
        created_at=row.created_at,
        candidates=[
            OptimizationCandidateOut(
                id=item.id,
                agent_version_id=item.agent_version_id,
                comparison_id=item.comparison_id,
                rank=item.rank,
                eligible=item.eligible,
                score=item.score,
                candidate_hash=item.candidate_hash,
            )
            for item in candidates
        ],
        recommendations=[
            RecommendationOut(
                id=item.id,
                candidate_id=item.candidate_id,
                disposition=item.disposition,
                rationale=dict(item.rationale or {}),
                requires_human_approval=True,
                recommendation_hash=item.recommendation_hash,
                created_at=item.created_at,
            )
            for item in recommendations
        ],
    )


async def create_optimization_study(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    body: OptimizationStudyCreate,
) -> OptimizationStudy:
    suite = await visible_by_id(
        db, EvalSuite, body.suite_id, namespace=namespace, barrier_group=barrier_group
    )
    baseline_version = await visible_by_id(
        db,
        AgentVersion,
        body.baseline_agent_version_id,
        namespace=namespace,
        barrier_group=barrier_group,
    )
    comparisons: list[tuple[Comparison, EvalRun, EvalRun]] = []
    for comparison_id in body.comparison_ids:
        comparison = await visible_by_id(
            db, Comparison, comparison_id, namespace=namespace, barrier_group=barrier_group
        )
        if comparison.suite_id != suite.id:
            raise ImprovementContractError("all study comparisons must use the selected suite")
        baseline_run = await visible_by_id(
            db,
            EvalRun,
            comparison.baseline_run_id,
            namespace=namespace,
            barrier_group=barrier_group,
        )
        candidate_run = await visible_by_id(
            db,
            EvalRun,
            comparison.candidate_run_id,
            namespace=namespace,
            barrier_group=barrier_group,
        )
        if baseline_run.agent_version_id != baseline_version.id:
            raise ImprovementContractError(
                "every study comparison must use the selected baseline agent version"
            )
        comparisons.append((comparison, baseline_run, candidate_run))
    if len({candidate.agent_version_id for _, _, candidate in comparisons}) != len(comparisons):
        raise ImprovementContractError("study candidate agent versions must be unique")

    ranked = sorted(
        comparisons,
        key=lambda item: (
            item[0].verdict == "eligible_for_review",
            item[0].primary_improvement,
            item[0].comparison_hash,
        ),
        reverse=True,
    )
    study_id = uuid.uuid4()
    created_at = utc_now()
    document = {
        "schema": "lians.optimization-study.v1",
        "id": str(study_id),
        "name": body.name,
        "suite_id": str(suite.id),
        "suite_hash": suite.suite_hash,
        "baseline_agent_version_id": str(baseline_version.id),
        "baseline_manifest_hash": baseline_version.manifest_hash,
        "objective": body.objective,
        "comparison_hashes": [comparison.comparison_hash for comparison, _, _ in ranked],
        "mode": "advisory",
    }
    study = OptimizationStudy(
        id=study_id,
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=barrier_scope(barrier_group),
        name=body.name,
        suite_id=suite.id,
        baseline_agent_version_id=baseline_version.id,
        objective=body.objective,
        status="advisory",
        study_hash=sha256_json(document),
        created_by_principal_ref=principal_ref,
        created_at=created_at,
    )
    db.add(study)
    await db.flush()
    for rank, (comparison, _, candidate_run) in enumerate(ranked, start=1):
        candidate_id = uuid.uuid4()
        eligible = comparison.verdict == "eligible_for_review"
        candidate_document = {
            "schema": "lians.optimization-candidate.v1",
            "id": str(candidate_id),
            "study_hash": study.study_hash,
            "agent_version_id": str(candidate_run.agent_version_id),
            "comparison_hash": comparison.comparison_hash,
            "rank": rank,
            "eligible": eligible,
            "score": comparison.primary_improvement,
        }
        candidate = Candidate(
            id=candidate_id,
            namespace=namespace,
            barrier_group=barrier_group,
            barrier_scope=barrier_scope(barrier_group),
            study_id=study.id,
            agent_version_id=candidate_run.agent_version_id,
            comparison_id=comparison.id,
            rank=rank,
            eligible=eligible,
            score=comparison.primary_improvement,
            candidate_hash=sha256_json(candidate_document),
        )
        db.add(candidate)
        await db.flush()
        disposition = "recommend_for_human_review" if eligible else "do_not_recommend"
        recommendation_id = uuid.uuid4()
        rationale = {
            "comparison_id": str(comparison.id),
            "comparison_hash": comparison.comparison_hash,
            "verdict": comparison.verdict,
            "primary_metric": comparison.primary_metric,
            "primary_improvement": comparison.primary_improvement,
            "critical_invariants_passed": comparison.critical_invariants_passed,
            "automatic_deployment_authorized": False,
        }
        recommendation_document = {
            "schema": "lians.optimization-recommendation.v1",
            "id": str(recommendation_id),
            "candidate_hash": candidate.candidate_hash,
            "disposition": disposition,
            "rationale": rationale,
            "requires_human_approval": True,
        }
        db.add(
            Recommendation(
                id=recommendation_id,
                namespace=namespace,
                barrier_group=barrier_group,
                barrier_scope=barrier_scope(barrier_group),
                study_id=study.id,
                candidate_id=candidate.id,
                disposition=disposition,
                rationale=rationale,
                requires_human_approval=True,
                recommendation_hash=sha256_json(recommendation_document),
                created_at=created_at,
            )
        )
    await db.flush()
    return study


__all__ = [
    "ImprovementContractError",
    "ImprovementNotFound",
    "agent_definition_out",
    "agent_version_out",
    "barrier_scope",
    "canonical_json",
    "comparison_out",
    "component_artifact_out",
    "create_agent_definition",
    "create_agent_version",
    "create_comparison",
    "create_eval_case_from_decision",
    "create_eval_run",
    "create_eval_suite",
    "create_evaluation_attestation",
    "create_optimization_study",
    "eval_case_out",
    "eval_run_out",
    "eval_suite_out",
    "evaluation_attestation_out",
    "optimization_study_out",
    "sha256_json",
    "verify_evaluation_attestation",
    "visible_by_id",
]
