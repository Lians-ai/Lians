"""Agent-version, evaluation, attestation, and advisory optimization APIs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log
from ..db import get_db
from ..improvement_models import (
    AgentDefinition,
    AgentVersion,
    Comparison,
    EvalCase,
    EvalRun,
    EvalSuite,
    EvaluationAttestation,
    OptimizationStudy,
)
from ..improvement_schemas import (
    AgentDefinitionCreate,
    AgentDefinitionOut,
    AgentVersionCreate,
    AgentVersionOut,
    ComparisonCreate,
    ComparisonOut,
    EvalCaseFromDecision,
    EvalCaseOut,
    EvalRunCreate,
    EvalRunOut,
    EvalSuiteCreate,
    EvalSuiteOut,
    EvaluationAttestationCreate,
    EvaluationAttestationOut,
    EvaluationAttestationVerification,
    EvaluationAttestationVerifyRequest,
    OptimizationStudyCreate,
    OptimizationStudyOut,
)
from ..improvement_service import (
    ImprovementContractError,
    ImprovementNotFound,
    agent_definition_out,
    agent_version_out,
    comparison_out,
    create_agent_definition,
    create_agent_version,
    create_comparison,
    create_eval_case_from_decision,
    create_eval_run,
    create_eval_suite,
    create_evaluation_attestation,
    create_optimization_study,
    eval_case_out,
    eval_run_out,
    eval_suite_out,
    evaluation_attestation_out,
    optimization_study_out,
    verify_evaluation_attestation,
    visible_by_id,
)
from ..mutation_safety import reject_non_replayable_idempotency_key
from ..receipt_signer import (
    ReceiptSignerConfigurationError,
    ReceiptSigningUnavailable,
    get_receipt_signer,
)
from .deps import AuthContext, get_auth

agents_router = APIRouter(prefix="/v1/agents", tags=["agent-versions"])
eval_router = APIRouter(prefix="/v1/eval", tags=["evaluation"])
optimization_router = APIRouter(prefix="/v1/optimization", tags=["optimization"])


def _principal(auth: AuthContext) -> str:
    if not auth.principal_id:
        raise HTTPException(status_code=401, detail="Authenticated principal identity required")
    return auth.principal_id


def _scope_filter(model, auth: AuthContext):
    filters = [model.namespace == auth.namespace]
    if auth.barrier_group is not None:
        filters.append(
            or_(model.barrier_group.is_(None), model.barrier_group == auth.barrier_group)
        )
    return filters


async def _commit_evidence(
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
        payload={
            "resource_type": resource_type,
            "resource_id": str(resource_id),
            "barrier_scoped": auth.barrier_group is not None,
        },
    )
    await db.commit()


async def _safe_write(call, *, conflict: str):
    try:
        return await call
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ImprovementContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=conflict) from exc


@agents_router.post(
    "",
    response_model=AgentDefinitionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_agent_definition(
    body: AgentDefinitionCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> AgentDefinitionOut:
    auth.require("write")
    try:
        row = await create_agent_definition(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _commit_evidence(
            db,
            auth=auth,
            operation="agent_definition_created",
            resource_type="agent_definition",
            resource_id=row.id,
            content_hash=row.definition_hash,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Agent key already exists") from exc
    return agent_definition_out(row)


@agents_router.get("", response_model=list[AgentDefinitionOut])
async def get_agent_definitions(
    limit: int = Query(default=100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[AgentDefinitionOut]:
    auth.require("read")
    rows = list(
        (
            await db.execute(
                select(AgentDefinition)
                .where(*_scope_filter(AgentDefinition, auth))
                .order_by(AgentDefinition.created_at.desc(), AgentDefinition.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [agent_definition_out(row) for row in rows]


@agents_router.get("/{agent_id}", response_model=AgentDefinitionOut)
async def get_agent_definition(
    agent_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> AgentDefinitionOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            AgentDefinition,
            agent_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    return agent_definition_out(row)


@agents_router.post(
    "/{agent_id}/versions",
    response_model=AgentVersionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_agent_version(
    agent_id: UUID,
    body: AgentVersionCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> AgentVersionOut:
    auth.require("write")
    try:
        definition = await visible_by_id(
            db,
            AgentDefinition,
            agent_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
        row = await create_agent_version(
            db,
            agent_definition=definition,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _commit_evidence(
            db,
            auth=auth,
            operation="agent_version_created",
            resource_type="agent_version",
            resource_id=row.id,
            content_hash=row.manifest_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Agent or component not found") from exc
    except ImprovementContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Agent version label or manifest already exists"
        ) from exc
    return await agent_version_out(db, row)


@agents_router.get("/{agent_id}/versions", response_model=list[AgentVersionOut])
async def get_agent_versions(
    agent_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[AgentVersionOut]:
    auth.require("read")
    try:
        await visible_by_id(
            db,
            AgentDefinition,
            agent_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    rows = list(
        (
            await db.execute(
                select(AgentVersion)
                .where(
                    *_scope_filter(AgentVersion, auth),
                    AgentVersion.agent_definition_id == agent_id,
                )
                .order_by(AgentVersion.created_at.desc(), AgentVersion.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [await agent_version_out(db, row) for row in rows]


@agents_router.get("/{agent_id}/versions/{version_id}", response_model=AgentVersionOut)
async def get_agent_version(
    agent_id: UUID,
    version_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> AgentVersionOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            AgentVersion,
            version_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Agent version not found") from exc
    if row.agent_definition_id != agent_id:
        raise HTTPException(status_code=404, detail="Agent version not found")
    return await agent_version_out(db, row)


@eval_router.post(
    "/cases/from-decision",
    response_model=EvalCaseOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_eval_case_from_decision(
    body: EvalCaseFromDecision,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> EvalCaseOut:
    auth.require("write")
    try:
        row = await create_eval_case_from_decision(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _commit_evidence(
            db,
            auth=auth,
            operation="eval_case_created",
            resource_type="eval_case",
            resource_id=row.id,
            content_hash=row.case_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Decision not found") from exc
    except ImprovementContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Evaluation case already exists") from exc
    return eval_case_out(row)


@eval_router.get("/cases/{case_id}", response_model=EvalCaseOut)
async def get_eval_case(
    case_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> EvalCaseOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db, EvalCase, case_id, namespace=auth.namespace, barrier_group=auth.barrier_group
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Evaluation case not found") from exc
    return eval_case_out(row)


@eval_router.post(
    "/suites",
    response_model=EvalSuiteOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_eval_suite(
    body: EvalSuiteCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> EvalSuiteOut:
    auth.require("write")
    try:
        row = await create_eval_suite(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _commit_evidence(
            db,
            auth=auth,
            operation="eval_suite_created",
            resource_type="eval_suite",
            resource_id=row.id,
            content_hash=row.suite_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Evaluation case not found") from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Evaluation suite already exists") from exc
    return await eval_suite_out(db, row)


@eval_router.get("/suites/{suite_id}", response_model=EvalSuiteOut)
async def get_eval_suite(
    suite_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> EvalSuiteOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db, EvalSuite, suite_id, namespace=auth.namespace, barrier_group=auth.barrier_group
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Evaluation suite not found") from exc
    return await eval_suite_out(db, row)


@eval_router.post(
    "/runs",
    response_model=EvalRunOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_eval_run(
    body: EvalRunCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> EvalRunOut:
    auth.require("write")
    try:
        row = await create_eval_run(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _commit_evidence(
            db,
            auth=auth,
            operation="eval_run_recorded",
            resource_type="eval_run",
            resource_id=row.id,
            content_hash=row.run_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Suite or agent version not found") from exc
    except ImprovementContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Evaluation run already exists") from exc
    return await eval_run_out(db, row)


@eval_router.get("/runs/{run_id}", response_model=EvalRunOut)
async def get_eval_run(
    run_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> EvalRunOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db, EvalRun, run_id, namespace=auth.namespace, barrier_group=auth.barrier_group
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Evaluation run not found") from exc
    return await eval_run_out(db, row)


@eval_router.post(
    "/comparisons",
    response_model=ComparisonOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_eval_comparison(
    body: ComparisonCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ComparisonOut:
    auth.require("write")
    try:
        row = await create_comparison(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            baseline_run_id=body.baseline_run_id,
            candidate_run_id=body.candidate_run_id,
        )
        await _commit_evidence(
            db,
            auth=auth,
            operation="eval_comparison_created",
            resource_type="eval_comparison",
            resource_id=row.id,
            content_hash=row.comparison_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Evaluation run not found") from exc
    except ImprovementContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Comparison already exists") from exc
    return comparison_out(row)


@eval_router.get("/comparisons/{comparison_id}", response_model=ComparisonOut)
async def get_eval_comparison(
    comparison_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ComparisonOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            Comparison,
            comparison_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Comparison not found") from exc
    return comparison_out(row)


@eval_router.post(
    "/attestations",
    response_model=EvaluationAttestationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_evaluation_attestation(
    body: EvaluationAttestationCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> EvaluationAttestationOut:
    auth.require("write")
    try:
        signer = await get_receipt_signer()
        row = await create_evaluation_attestation(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
            signer=signer,
        )
        await _commit_evidence(
            db,
            auth=auth,
            operation="evaluation_attestation_signed",
            resource_type="evaluation_attestation",
            resource_id=row.id,
            content_hash=row.payload_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Comparison not found") from exc
    except ReceiptSignerConfigurationError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Evaluation signer is misconfigured") from exc
    except ReceiptSigningUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Evaluation signer is unavailable") from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Evaluation attestation already exists"
        ) from exc
    return evaluation_attestation_out(row)


@eval_router.get("/attestations/{attestation_id}", response_model=EvaluationAttestationOut)
async def get_evaluation_attestation(
    attestation_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> EvaluationAttestationOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            EvaluationAttestation,
            attestation_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Evaluation attestation not found") from exc
    return evaluation_attestation_out(row)


@eval_router.post("/attestations/verify", response_model=EvaluationAttestationVerification)
async def post_evaluation_attestation_verify(
    body: EvaluationAttestationVerifyRequest,
    auth: AuthContext = Depends(get_auth),
) -> EvaluationAttestationVerification:
    auth.require("read")
    return verify_evaluation_attestation(
        body.attestation, trusted_public_key=body.trusted_public_key
    )


@optimization_router.post(
    "/studies",
    response_model=OptimizationStudyOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def post_optimization_study(
    body: OptimizationStudyCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> OptimizationStudyOut:
    auth.require("write")
    try:
        row = await create_optimization_study(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=_principal(auth),
            body=body,
        )
        await _commit_evidence(
            db,
            auth=auth,
            operation="optimization_study_created",
            resource_type="optimization_study",
            resource_id=row.id,
            content_hash=row.study_hash,
        )
    except ImprovementNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Optimization dependency not found") from exc
    except ImprovementContractError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Optimization study already exists") from exc
    return await optimization_study_out(db, row)


@optimization_router.get("/studies/{study_id}", response_model=OptimizationStudyOut)
async def get_optimization_study(
    study_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> OptimizationStudyOut:
    auth.require("read")
    try:
        row = await visible_by_id(
            db,
            OptimizationStudy,
            study_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except ImprovementNotFound as exc:
        raise HTTPException(status_code=404, detail="Optimization study not found") from exc
    return await optimization_study_out(db, row)


__all__ = ["agents_router", "eval_router", "optimization_router"]
