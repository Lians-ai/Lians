"""Trust registry, runtime Gate, and investigation/remediation APIs."""

# FastAPI intentionally evaluates Depends/Query marker objects in signatures.
# ruff: noqa: B008

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import String, and_, bindparam, case, cast, func, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log
from ..control_models import (
    ControlClosureAttestation,
    GateApprovalAttestation,
    GateDecisionRecord,
    GatePolicyRule,
    GatePolicySet,
    InvestigationCase,
    ReceiptIssuer,
    RemediationTask,
    TrustedReceiptKey,
)
from ..control_schemas import (
    SAFE_KEY_ID_PATTERN,
    AttestedClosureResult,
    ClosureAttestationCreate,
    ClosureAttestationOut,
    GateApprovalAttestationCreate,
    GateApprovalAttestationOut,
    GateApprovalAttestationSupersede,
    GateDecisionOut,
    GateEvaluationOut,
    GateEvaluationRequest,
    GateExecutionPermitConsume,
    GateExecutionPermitConsumptionOut,
    GateExecutionPermitIssued,
    GatePolicyActivate,
    GatePolicyRuleOut,
    GatePolicySetCreate,
    GatePolicySetOut,
    InvestigationCaseCreate,
    InvestigationCaseOut,
    InvestigationCaseUpdate,
    IssuerCreate,
    IssuerOut,
    IssuerRevoke,
    RemediationTaskCreate,
    RemediationTaskOut,
    RemediationTaskUpdate,
    TrustedKeyCreate,
    TrustedKeyOut,
    TrustedKeyRevoke,
    TrustedKeyRotate,
    UntrustedContentSignal,
)
from ..control_service import (
    CASE_TRANSITIONS,
    TASK_TRANSITIONS,
    GatePermitRedemptionError,
    add_barrier_filter,
    closure_attestation_out,
    consume_gate_execution_permit,
    create_closure_attestation,
    effective_barrier,
    evaluate_gate,
    normalize_ed25519_public_key,
    policy_definition_payload,
    sha256_json,
    target_selector_matches,
    utc_now,
    validate_transition,
)
from ..db import get_db
from ..decision_record_integrity import (
    DecisionRecordIntegrityError,
    assert_decision_record_integrity,
)
from ..evidence_models import DecisionEvidenceLink
from ..governance_service import GovernanceViolation, reserve_namespace_usage
from ..immutable_attestation_service import (
    ApprovalAttestationError,
    create_gate_approval_attestation,
    gate_approval_out,
    principal_ref_hash,
    supersede_gate_approval_attestation,
)
from ..metering import enqueue_protected_action_usage_event
from ..metrics import record_gate_evaluation, record_gate_permit_outcome
from ..models import DecisionRecord, LedgerEvent, Memory
from ..mutation_safety import (
    MutationVersionConflict,
    assert_expected_updated_at,
    reject_non_replayable_idempotency_key,
)
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/control", tags=["control-plane"])
_DEFAULT_LIST_LIMIT = 100
_MAX_LIST_LIMIT = 1_000
_MAX_ISSUER_KEYS_PER_REVOCATION = 500
_MAX_GATE_POLICY_RULES = 1_000
_MAX_GATE_POLICY_RULES_PER_RESPONSE = 5_000
_MAX_ACTIVE_GATE_POLICIES_PER_BARRIER = 1_000
_MAX_LIST_OFFSET = 50_000
_MAX_GATE_EVIDENCE_LINKS = 1_000
_MAX_GATE_DECISION_EVIDENCE_IDS = 1_000
_MAX_GATE_SECURITY_SIGNAL_SCAN_ITEMS = 5_000
_MAX_GATE_DERIVED_SIGNALS = 1_000
_MAX_GATE_SECURITY_METADATA_BYTES = 128 * 1024
SafeKeyId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=255,
        pattern=SAFE_KEY_ID_PATTERN,
        description="Stable trusted-key URL segment",
    ),
]


def _set_page_headers(
    response: Response,
    *,
    total: int,
    limit: int,
    returned: int,
    has_more: bool,
    offset: int | None = None,
    next_cursor: dict[str, str] | None = None,
) -> None:
    """Expose exact cardinality and truthful continuation for list responses."""

    response.headers["X-Lians-Total-Count"] = str(total)
    response.headers["X-Lians-Page-Limit"] = str(limit)
    response.headers["X-Lians-Page-Returned"] = str(returned)
    response.headers["X-Lians-Page-Complete"] = str(not has_more).lower()
    response.headers["X-Lians-Has-More"] = str(has_more).lower()
    if offset is not None:
        response.headers["X-Lians-Page-Offset"] = str(offset)
    if has_more and next_cursor:
        for name, value in next_cursor.items():
            header_name = "-".join(part.capitalize() for part in name.split("_"))
            response.headers[f"X-Lians-Next-{header_name}"] = value


def _require_paired_cursor(
    first_value: object | None,
    second_value: object | None,
    *,
    first_name: str,
    second_name: str,
) -> None:
    if (first_value is None) != (second_value is None):
        raise HTTPException(
            status_code=422,
            detail=f"{first_name} and {second_name} must be supplied together",
        )


def _descending_cursor_condition(
    time_column: Any,
    id_column: Any,
    *,
    before_time: datetime,
    before_id: UUID,
) -> Any:
    return or_(
        time_column < before_time,
        and_(time_column == before_time, id_column < before_id),
    )


def _ascending_cursor_condition(
    time_column: Any,
    id_column: Any,
    *,
    after_time: datetime,
    after_id: UUID,
) -> Any:
    return or_(
        time_column > after_time,
        and_(time_column == after_time, id_column > after_id),
    )


def _require(auth: AuthContext, scope: str) -> None:
    auth.require(scope)


def _require_any(auth: AuthContext, *scopes: str) -> None:
    if not set(scopes).intersection(auth.scopes):
        raise HTTPException(
            status_code=403,
            detail=f"One of these scopes is required: {', '.join(scopes)}",
        )


def _authenticated_actor(auth: AuthContext, asserted: str | None) -> str:
    """Bind audit authorship to the authenticated principal, never a free-form body."""
    principal = getattr(auth, "principal_id", None)
    if principal:
        if asserted is not None and asserted != principal:
            raise HTTPException(
                status_code=403,
                detail="actor/principal does not match the authenticated identity",
            )
        return principal
    if asserted:
        return asserted
    raise HTTPException(status_code=401, detail="Authenticated principal identity is required")


def _safe_barrier(requested: str | None, auth: AuthContext) -> str | None:
    try:
        return effective_barrier(requested, auth.barrier_group)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _visible_filters(model, resource_id: UUID, auth: AuthContext) -> list[Any]:
    filters: list[Any] = [model.id == resource_id, model.namespace == auth.namespace]
    add_barrier_filter(filters, model.barrier_group, auth.barrier_group)
    return filters


async def _visible_by_id(
    db: AsyncSession,
    model,
    resource_id: UUID,
    auth: AuthContext,
    label: str,
    *,
    for_update: bool = False,
):
    statement = select(model).where(*_visible_filters(model, resource_id, auth))
    if for_update:
        statement = statement.execution_options(populate_existing=True).with_for_update()
    result = await db.execute(statement)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return row


async def _lock_task_and_parent_case(
    db: AsyncSession,
    task_id: UUID,
    auth: AuthContext,
) -> RemediationTask:
    """Use the case as the lock root for every child-task mutation.

    Case closure takes the same parent lock before its SQL outstanding-task
    count. This prevents a close from racing a task create, update, or closure
    without transferring an unbounded task inventory to the application.
    """
    snapshot = await _visible_by_id(
        db, RemediationTask, task_id, auth, "Remediation task"
    )
    await _visible_by_id(
        db,
        InvestigationCase,
        snapshot.case_id,
        auth,
        "Investigation case",
        for_update=True,
    )
    return await _visible_by_id(
        db,
        RemediationTask,
        task_id,
        auth,
        "Remediation task",
        for_update=True,
    )


def _assert_updated_at(current: datetime, expected: datetime) -> None:
    try:
        assert_expected_updated_at(current, expected)
    except MutationVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _audit(
    db: AsyncSession,
    *,
    auth: AuthContext,
    actor_id: str,
    operation: str,
    resource_type: str,
    resource_id: UUID,
    barrier_group: str | None,
    details: dict[str, Any] | None = None,
) -> None:
    def audit_safe(value: Any, key_hint: str = "") -> Any:
        """Hash free-form/sensitive identity fields before the global chain."""
        key = key_hint.casefold()
        sensitive = (
            "principal",
            "reviewer",
            "subject",
            "statement",
            "note",
            "description",
            "resolution",
            "summary",
            "reason",
            "owner",
        )
        already_reference = "hash" in key or key.endswith("_ref") or "ref_hash" in key
        if any(marker in key for marker in sensitive) and not already_reference:
            if value is None:
                return None
            return {"$redacted": True, "$sha256": sha256_json(value)}
        if isinstance(value, dict):
            return {str(k): audit_safe(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [audit_safe(item, key_hint) for item in value]
        return value

    payload = {
        "control_resource_type": resource_type,
        "control_resource_id": str(resource_id),
        "barrier_group": barrier_group,
        "actor_ref_hash": principal_ref_hash(actor_id),
        **audit_safe(details or {}),
    }
    await chain_log(
        db,
        namespace=auth.namespace,
        # OIDC subjects and human names can be sensitive.  The audit chain gets
        # a stable pseudonymous reference, never the raw authenticated subject.
        agent_id=f"principal:{principal_ref_hash(actor_id)}",
        op=operation,
        content_hash=sha256_json(payload),
        payload=payload,
    )


async def _commit(db: AsyncSession, conflict: str) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=conflict) from exc


async def _policies_out(
    db: AsyncSession,
    rows: list[GatePolicySet],
    *,
    include_rules: bool = True,
) -> list[GatePolicySetOut]:
    """Serialize a bounded policy page with one batched rule query."""
    if not rows:
        return []
    rules_by_policy: dict[UUID, list[GatePolicyRuleOut]] = {
        row.id: [] for row in rows
    }
    if include_rules:
        response_rule_limit = min(
            len(rows) * _MAX_GATE_POLICY_RULES,
            _MAX_GATE_POLICY_RULES_PER_RESPONSE,
        )
        result = await db.execute(
            select(GatePolicyRule)
            .where(GatePolicyRule.policy_set_id.in_(rules_by_policy))
            .order_by(
                GatePolicyRule.policy_set_id,
                GatePolicyRule.priority,
                GatePolicyRule.name,
            )
            .limit(response_rule_limit + 1)
        )
        rule_rows = list(result.scalars().all())
        if len(rule_rows) > response_rule_limit:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Expanded policy rules exceed the bounded response ceiling; "
                    "request include_rules=false or a narrower policy page"
                ),
            )
        policy_rule_counts: dict[UUID, int] = {row.id: 0 for row in rows}
        for item in rule_rows:
            policy_rule_counts[item.policy_set_id] += 1
            if policy_rule_counts[item.policy_set_id] > _MAX_GATE_POLICY_RULES:
                raise HTTPException(
                    status_code=503,
                    detail="Gate policy exceeds the supported rule capacity",
                )
            rules_by_policy[item.policy_set_id].append(
                GatePolicyRuleOut.model_validate(item)
            )
    return [
        GatePolicySetOut.model_validate(row).model_copy(
            update={"rules": rules_by_policy[row.id]}
        )
        for row in rows
    ]


async def _policy_out(db: AsyncSession, row: GatePolicySet) -> GatePolicySetOut:
    return (await _policies_out(db, [row]))[0]


async def _validate_link(
    db: AsyncSession,
    *,
    auth: AuthContext,
    model,
    resource_id: UUID | None,
    label: str,
) -> Any | None:
    if resource_id is None:
        return None
    filters: list[Any] = [model.id == resource_id, model.namespace == auth.namespace]
    add_barrier_filter(filters, model.barrier_group, auth.barrier_group)
    result = await db.execute(select(model).where(*filters))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Linked {label} not found")
    return row


def _require_boundary_barrier(
    row: Any | None, record_barrier: str | None, label: str
) -> None:
    if row is not None and row.barrier_group not in {None, record_barrier}:
        raise HTTPException(
            status_code=403,
            detail=f"Linked {label} belongs to a different information barrier",
        )


async def _decision_gate_context(
    db: AsyncSession,
    *,
    auth: AuthContext,
    decision: DecisionRecord | None,
    record_barrier: str | None,
) -> dict[str, Any]:
    """Derive Gate assertions from the immutable decision and current store state."""
    if decision is None:
        return {
            "sources_current": None,
            "attached_policy_version": None,
        }

    if decision.metadata_ is not None and not isinstance(decision.metadata_, dict):
        raise HTTPException(
            status_code=503,
            detail={"code": "gate_decision_context_invalid"},
        )
    metadata = dict(decision.metadata_ or {})
    risk_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    risk_candidates = [metadata.get("risk_level")]
    link_filters: list[Any] = [
        DecisionEvidenceLink.namespace == auth.namespace,
        DecisionEvidenceLink.decision_id == decision.id,
    ]
    if record_barrier is None:
        link_filters.append(DecisionEvidenceLink.barrier_group.is_(None))
    else:
        link_filters.append(
            or_(
                DecisionEvidenceLink.barrier_group.is_(None),
                DecisionEvidenceLink.barrier_group == record_barrier,
            )
        )
    link_inventory = (
        await db.execute(
            select(
                func.count(),
                func.max(
                    func.length(cast(DecisionEvidenceLink.risk_metadata, String))
                ),
            )
            .select_from(DecisionEvidenceLink)
            .where(*link_filters)
        )
    ).one()
    link_count = int(link_inventory[0] or 0)
    largest_link_metadata = int(link_inventory[1] or 0)
    if (
        link_count > _MAX_GATE_EVIDENCE_LINKS
        or largest_link_metadata > _MAX_GATE_SECURITY_METADATA_BYTES
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "gate_decision_context_capacity_exceeded",
                "message": "Decision security context exceeds the evaluation capacity",
            },
        )
    link_result = await db.execute(
        select(
            DecisionEvidenceLink.risk_level,
            DecisionEvidenceLink.risk_metadata,
        )
        .where(*link_filters)
        .order_by(DecisionEvidenceLink.id)
        .limit(_MAX_GATE_EVIDENCE_LINKS + 1)
    )
    evidence_links = list(link_result.all())
    if len(evidence_links) > _MAX_GATE_EVIDENCE_LINKS:
        raise HTTPException(
            status_code=503,
            detail={"code": "gate_decision_context_capacity_exceeded"},
        )
    risk_candidates.extend(link_risk for link_risk, _ in evidence_links)
    risk_level = max(
        (value for value in risk_candidates if value in risk_rank),
        key=risk_rank.__getitem__,
        default="medium",
    )
    raw_evidence_ids = decision.evidence_memory_ids or []
    if not isinstance(raw_evidence_ids, list) or len(raw_evidence_ids) > (
        _MAX_GATE_DECISION_EVIDENCE_IDS
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "gate_decision_context_capacity_exceeded",
                "message": "Decision evidence references exceed the evaluation capacity",
            },
        )
    evidence_ids: list[UUID] = []
    evidence_ids_valid = True
    for value in raw_evidence_ids:
        try:
            evidence_ids.append(UUID(str(value)))
        except (TypeError, ValueError):
            evidence_ids_valid = False
            break

    sources_current = False
    source_rows: list[Any] = []
    if evidence_ids_valid and evidence_ids:
        distinct_evidence_ids = list(dict.fromkeys(evidence_ids))
        filters: list[Any] = [
            Memory.namespace == auth.namespace,
            Memory.id.in_(distinct_evidence_ids),
        ]
        if record_barrier is None:
            filters.append(Memory.barrier_group.is_(None))
        else:
            filters.append(
                or_(Memory.barrier_group.is_(None), Memory.barrier_group == record_barrier)
            )
        source_inventory = (
            await db.execute(
                select(
                    func.count(),
                    func.max(func.length(cast(Memory.metadata_, String))),
                )
                .select_from(Memory)
                .where(*filters)
            )
        ).one()
        if int(source_inventory[1] or 0) > _MAX_GATE_SECURITY_METADATA_BYTES:
            raise HTTPException(
                status_code=503,
                detail={"code": "gate_decision_context_capacity_exceeded"},
            )
        source_rows = list(
            (
                await db.execute(
                    select(
                        Memory.barrier_group,
                        Memory.valid_to,
                        Memory.erased_at,
                        Memory.source,
                        Memory.metadata_,
                    )
                    .where(*filters)
                    .order_by(Memory.id)
                    .limit(_MAX_GATE_DECISION_EVIDENCE_IDS + 1)
                )
            ).all()
        )
        if len(source_rows) > _MAX_GATE_DECISION_EVIDENCE_IDS:
            raise HTTPException(
                status_code=503,
                detail={"code": "gate_decision_context_capacity_exceeded"},
            )
        sources_current = int(source_inventory[0] or 0) == len(distinct_evidence_ids) and all(
            row.barrier_group in {None, record_barrier}
            and row.valid_to is None
            and row.erased_at is None
            for row in source_rows
        )

    # Detector signals used by policy are rebuilt from the immutable decision
    # boundary and its cited, admitted source rows.  Evaluation callers cannot
    # suppress a recorded prompt-injection or blocked-source finding.
    signal_values: list[UntrustedContentSignal] = []
    seen_signals: set[tuple[str, str | None, int]] = set()
    signal_scan_items = 0

    def add_signal(signal_type: str, source: str | None, score: int) -> None:
        normalized = (signal_type[:255], source[:2048] if source else None, score)
        if normalized in seen_signals:
            return
        if len(signal_values) >= _MAX_GATE_DERIVED_SIGNALS:
            raise HTTPException(
                status_code=503,
                detail={"code": "gate_decision_context_capacity_exceeded"},
            )
        seen_signals.add(normalized)
        signal_values.append(
            UntrustedContentSignal(
                signal_type=normalized[0],
                source=normalized[1],
                score=normalized[2],
                trusted=False,
                details={"origin": "recorded_decision_boundary"},
            )
        )

    for source_row in source_rows:
        if source_row.metadata_ is not None and not isinstance(
            source_row.metadata_, dict
        ):
            raise HTTPException(
                status_code=503,
                detail={"code": "gate_decision_context_invalid"},
            )
        raw_admission = (source_row.metadata_ or {}).get("_admission") or {}
        if not isinstance(raw_admission, dict):
            raise HTTPException(
                status_code=503,
                detail={"code": "gate_decision_context_invalid"},
            )
        admission = dict(raw_admission)
        risk_tags = admission.get("risk_tags") or []
        if not isinstance(risk_tags, list):
            raise HTTPException(
                status_code=503,
                detail={"code": "gate_decision_context_invalid"},
            )
        signal_scan_items += len(risk_tags)
        if signal_scan_items > _MAX_GATE_SECURITY_SIGNAL_SCAN_ITEMS:
            raise HTTPException(
                status_code=503,
                detail={"code": "gate_decision_context_capacity_exceeded"},
            )
        for tag in risk_tags:
            if tag == "injection":
                add_signal("prompt_injection", source_row.source, 100)
            elif tag == "source:blocked":
                add_signal("blocked_source", source_row.source, 100)

    for _, link_risk_metadata in evidence_links:
        if link_risk_metadata is not None and not isinstance(
            link_risk_metadata, dict
        ):
            raise HTTPException(
                status_code=503,
                detail={"code": "gate_decision_context_invalid"},
            )
        risk_metadata = dict(link_risk_metadata or {})
        raw_signals = risk_metadata.get("untrusted_content_signals") or []
        if not isinstance(raw_signals, list):
            raise HTTPException(
                status_code=503,
                detail={"code": "gate_decision_context_invalid"},
            )
        signal_scan_items += len(raw_signals)
        if signal_scan_items > _MAX_GATE_SECURITY_SIGNAL_SCAN_ITEMS:
            raise HTTPException(
                status_code=503,
                detail={"code": "gate_decision_context_capacity_exceeded"},
            )
        for item in raw_signals:
            if not isinstance(item, dict):
                continue
            signal_type = item.get("signal_type")
            score = item.get("score")
            source = item.get("source")
            if (
                isinstance(signal_type, str)
                and signal_type.strip()
                and isinstance(score, int)
                and not isinstance(score, bool)
                and 0 <= score <= 100
                and (source is None or isinstance(source, str))
            ):
                add_signal(signal_type.strip(), source, score)
        if risk_metadata.get("prompt_injection_detected") is True:
            add_signal("prompt_injection", None, 100)
        if risk_metadata.get("untrusted_source") is True:
            add_signal("untrusted_source", None, 100)

    return {
        "decision_type": decision.decision_type,
        "risk_level": risk_level,
        "attached_policy_version": decision.policy_version,
        "sources_current": sources_current,
        "untrusted_content_signals": signal_values,
    }


# ---------------------------------------------------------------------------
# Receipt issuer and trusted public-key lifecycle
# ---------------------------------------------------------------------------


@router.post(
    "/trust/issuers",
    response_model=IssuerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a trusted Decision Receipt issuer",
)
async def create_issuer(
    body: IssuerCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> IssuerOut:
    _require(auth, "admin")
    actor = _authenticated_actor(auth, body.actor_id)
    barrier = _safe_barrier(body.barrier_group, auth)
    row = ReceiptIssuer(
        namespace=auth.namespace,
        barrier_group=barrier,
        name=body.name,
        issuer_uri=body.issuer_uri,
        description=body.description,
        status="active",
        metadata_=body.metadata,
        created_by=actor,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An issuer with this name already exists in the namespace",
        ) from exc
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.trust.issuer_registered",
        resource_type="receipt_issuer",
        resource_id=row.id,
        barrier_group=barrier,
        details={"name": row.name, "issuer_uri": row.issuer_uri},
    )
    await _commit(db, "Issuer registration conflicted with another request")
    await db.refresh(row)
    return IssuerOut.model_validate(row)


@router.get("/trust/issuers", response_model=list[IssuerOut])
async def list_issuers(
    response: Response,
    include_revoked: bool = Query(default=False),
    offset: int = Query(default=0, ge=0, le=_MAX_LIST_OFFSET),
    before_created_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[IssuerOut]:
    _require(auth, "read")
    _require_paired_cursor(
        before_created_at,
        before_id,
        first_name="before_created_at",
        second_name="before_id",
    )
    if before_created_at is not None and offset:
        raise HTTPException(status_code=422, detail="offset cannot be combined with a cursor")
    filters: list[Any] = [ReceiptIssuer.namespace == auth.namespace]
    add_barrier_filter(filters, ReceiptIssuer.barrier_group, auth.barrier_group)
    if not include_revoked:
        filters.append(ReceiptIssuer.status == "active")
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(ReceiptIssuer).where(*filters)
            )
        ).scalar_one()
    )
    page_filters = list(filters)
    if before_created_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor_condition(
                ReceiptIssuer.created_at,
                ReceiptIssuer.id,
                before_time=before_created_at,
                before_id=before_id,
            )
        )
    result = await db.execute(
        select(ReceiptIssuer)
        .where(*page_filters)
        .order_by(ReceiptIssuer.created_at.desc(), ReceiptIssuer.id.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = {
            "before_created_at": last.created_at.isoformat(),
            "before_id": str(last.id),
        }
    _set_page_headers(
        response,
        total=total,
        offset=offset,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        next_cursor=next_cursor,
    )
    return [IssuerOut.model_validate(row) for row in page]


@router.post(
    "/trust/issuers/{issuer_id}/revoke",
    response_model=IssuerOut,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def revoke_issuer(
    issuer_id: UUID,
    body: IssuerRevoke,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> IssuerOut:
    _require(auth, "admin")
    actor = _authenticated_actor(auth, body.actor_id)
    issuer = await _visible_by_id(
        db, ReceiptIssuer, issuer_id, auth, "Issuer", for_update=True
    )
    if issuer.status == "revoked":
        raise HTTPException(status_code=409, detail="Issuer is already revoked")
    active_key_filters = [
        TrustedReceiptKey.namespace == auth.namespace,
        TrustedReceiptKey.issuer_id == issuer.id,
        TrustedReceiptKey.status == "active",
    ]
    active_key_count = int(
        (
            await db.execute(
                select(func.count()).select_from(TrustedReceiptKey).where(
                    *active_key_filters
                )
            )
        ).scalar_one()
    )
    if active_key_count > _MAX_ISSUER_KEYS_PER_REVOCATION:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Issuer revocation exceeds the bounded key cascade; revoke "
                    "keys individually and retry"
                ),
                "active_key_count": active_key_count,
                "max_keys_per_request": _MAX_ISSUER_KEYS_PER_REVOCATION,
            },
        )
    now = utc_now()
    issuer.status = "revoked"
    issuer.revoked_at = now
    issuer.revoked_by = actor
    issuer.revocation_reason = body.reason
    if active_key_count:
        key_update = await db.execute(
            update(TrustedReceiptKey)
            .where(*active_key_filters)
            .values(
                status="revoked",
                revoked_at=now,
                revoked_by=actor,
                revocation_reason=f"Issuer revoked: {body.reason}",
            )
            .execution_options(synchronize_session=False)
        )
        if int(key_update.rowcount or 0) != active_key_count:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Trusted-key inventory changed during issuer revocation; retry",
            )
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.trust.issuer_revoked",
        resource_type="receipt_issuer",
        resource_id=issuer.id,
        barrier_group=issuer.barrier_group,
        details={"reason": body.reason, "keys_revoked": active_key_count},
    )
    await db.commit()
    await db.refresh(issuer)
    return IssuerOut.model_validate(issuer)


@router.post(
    "/trust/issuers/{issuer_id}/keys",
    response_model=TrustedKeyOut,
    status_code=status.HTTP_201_CREATED,
)
async def register_trusted_key(
    issuer_id: UUID,
    body: TrustedKeyCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> TrustedKeyOut:
    _require(auth, "admin")
    actor = _authenticated_actor(auth, body.actor_id)
    issuer = await _visible_by_id(
        db, ReceiptIssuer, issuer_id, auth, "Issuer", for_update=True
    )
    if issuer.status != "active":
        raise HTTPException(status_code=409, detail="Cannot add a key to a revoked issuer")
    try:
        public_key, fingerprint = normalize_ed25519_public_key(body.public_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = TrustedReceiptKey(
        namespace=auth.namespace,
        barrier_group=issuer.barrier_group,
        issuer_id=issuer.id,
        key_id=body.key_id,
        algorithm=body.algorithm,
        public_key=public_key,
        public_key_format="raw-base64",
        fingerprint_sha256=fingerprint,
        status="active",
        valid_from=body.valid_from or utc_now(),
        valid_until=body.valid_until,
        created_by=actor,
        metadata_=body.metadata,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="key_id already exists in this namespace",
        ) from exc
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.trust.key_registered",
        resource_type="trusted_receipt_key",
        resource_id=row.id,
        barrier_group=row.barrier_group,
        details={
            "issuer_id": str(issuer.id),
            "key_id": row.key_id,
            "algorithm": row.algorithm,
            "fingerprint_sha256": row.fingerprint_sha256,
        },
    )
    await _commit(db, "Trusted-key registration conflicted with another request")
    await db.refresh(row)
    return TrustedKeyOut.model_validate(row)


@router.get("/trust/issuers/{issuer_id}/keys", response_model=list[TrustedKeyOut])
async def list_trusted_keys(
    issuer_id: UUID,
    response: Response,
    include_revoked: bool = Query(default=False),
    offset: int = Query(default=0, ge=0, le=_MAX_LIST_OFFSET),
    before_created_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[TrustedKeyOut]:
    _require(auth, "read")
    _require_paired_cursor(
        before_created_at,
        before_id,
        first_name="before_created_at",
        second_name="before_id",
    )
    if before_created_at is not None and offset:
        raise HTTPException(status_code=422, detail="offset cannot be combined with a cursor")
    await _visible_by_id(db, ReceiptIssuer, issuer_id, auth, "Issuer")
    filters: list[Any] = [
        TrustedReceiptKey.namespace == auth.namespace,
        TrustedReceiptKey.issuer_id == issuer_id,
    ]
    add_barrier_filter(filters, TrustedReceiptKey.barrier_group, auth.barrier_group)
    if not include_revoked:
        filters.append(TrustedReceiptKey.status == "active")
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(TrustedReceiptKey).where(*filters)
            )
        ).scalar_one()
    )
    page_filters = list(filters)
    if before_created_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor_condition(
                TrustedReceiptKey.created_at,
                TrustedReceiptKey.id,
                before_time=before_created_at,
                before_id=before_id,
            )
        )
    result = await db.execute(
        select(TrustedReceiptKey)
        .where(*page_filters)
        .order_by(TrustedReceiptKey.created_at.desc(), TrustedReceiptKey.id.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = {
            "before_created_at": last.created_at.isoformat(),
            "before_id": str(last.id),
        }
    _set_page_headers(
        response,
        total=total,
        offset=offset,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        next_cursor=next_cursor,
    )
    return [TrustedKeyOut.model_validate(row) for row in page]


@router.get("/trust/keys/{key_id}", response_model=TrustedKeyOut)
async def resolve_trusted_key(
    key_id: SafeKeyId,
    at: datetime | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> TrustedKeyOut:
    _require(auth, "read")
    when = at or utc_now()
    filters: list[Any] = [
        TrustedReceiptKey.namespace == auth.namespace,
        TrustedReceiptKey.key_id == key_id,
        TrustedReceiptKey.status == "active",
        TrustedReceiptKey.valid_from <= when,
        or_(TrustedReceiptKey.valid_until.is_(None), TrustedReceiptKey.valid_until >= when),
    ]
    add_barrier_filter(filters, TrustedReceiptKey.barrier_group, auth.barrier_group)
    result = await db.execute(select(TrustedReceiptKey).where(*filters))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No active trusted key at this time")
    return TrustedKeyOut.model_validate(row)


@router.post(
    "/trust/issuers/{issuer_id}/keys/{key_id}/rotate",
    response_model=TrustedKeyOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def rotate_trusted_key(
    issuer_id: UUID,
    key_id: SafeKeyId,
    body: TrustedKeyRotate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> TrustedKeyOut:
    _require(auth, "admin")
    actor = _authenticated_actor(auth, body.actor_id)
    issuer = await _visible_by_id(
        db, ReceiptIssuer, issuer_id, auth, "Issuer", for_update=True
    )
    if issuer.status != "active":
        raise HTTPException(status_code=409, detail="Cannot rotate a key for a revoked issuer")
    filters: list[Any] = [
        TrustedReceiptKey.namespace == auth.namespace,
        TrustedReceiptKey.issuer_id == issuer_id,
        TrustedReceiptKey.key_id == key_id,
    ]
    add_barrier_filter(filters, TrustedReceiptKey.barrier_group, auth.barrier_group)
    result = await db.execute(select(TrustedReceiptKey).where(*filters).with_for_update())
    old = result.scalar_one_or_none()
    if old is None:
        raise HTTPException(status_code=404, detail="Trusted key not found")
    if old.status != "active":
        raise HTTPException(status_code=409, detail="Only an active key can be rotated")
    if body.key_id == key_id:
        raise HTTPException(status_code=422, detail="Rotation requires a new key_id")
    try:
        public_key, fingerprint = normalize_ed25519_public_key(body.public_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = utc_now()
    new = TrustedReceiptKey(
        namespace=auth.namespace,
        barrier_group=old.barrier_group,
        issuer_id=issuer.id,
        key_id=body.key_id,
        algorithm=body.algorithm,
        public_key=public_key,
        public_key_format="raw-base64",
        fingerprint_sha256=fingerprint,
        status="active",
        valid_from=body.valid_from or now,
        valid_until=body.valid_until,
        created_by=actor,
        rotated_from_key_id=old.key_id,
        rotation_reason=body.reason,
        metadata_=body.metadata,
    )
    old.status = "revoked"
    old.revoked_at = now
    old.revoked_by = actor
    old.revocation_reason = f"Rotated: {body.reason}"
    old.rotated_at = now
    old.replaced_by_key_id = new.key_id
    old.rotation_reason = body.reason
    db.add(new)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="New key_id already exists") from exc
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.trust.key_rotated",
        resource_type="trusted_receipt_key",
        resource_id=new.id,
        barrier_group=new.barrier_group,
        details={
            "issuer_id": str(issuer.id),
            "old_key_id": old.key_id,
            "new_key_id": new.key_id,
            "new_fingerprint_sha256": new.fingerprint_sha256,
            "reason": body.reason,
        },
    )
    await _commit(db, "Key rotation conflicted with another request")
    await db.refresh(new)
    return TrustedKeyOut.model_validate(new)


@router.post(
    "/trust/issuers/{issuer_id}/keys/{key_id}/revoke",
    response_model=TrustedKeyOut,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def revoke_trusted_key(
    issuer_id: UUID,
    key_id: SafeKeyId,
    body: TrustedKeyRevoke,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> TrustedKeyOut:
    _require(auth, "admin")
    actor = _authenticated_actor(auth, body.actor_id)
    await _visible_by_id(
        db, ReceiptIssuer, issuer_id, auth, "Issuer", for_update=True
    )
    filters: list[Any] = [
        TrustedReceiptKey.namespace == auth.namespace,
        TrustedReceiptKey.issuer_id == issuer_id,
        TrustedReceiptKey.key_id == key_id,
    ]
    add_barrier_filter(filters, TrustedReceiptKey.barrier_group, auth.barrier_group)
    result = await db.execute(select(TrustedReceiptKey).where(*filters).with_for_update())
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Trusted key not found")
    if row.status == "revoked":
        raise HTTPException(status_code=409, detail="Trusted key is already revoked")
    row.status = "revoked"
    row.revoked_at = utc_now()
    row.revoked_by = actor
    row.revocation_reason = body.reason
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.trust.key_revoked",
        resource_type="trusted_receipt_key",
        resource_id=row.id,
        barrier_group=row.barrier_group,
        details={"issuer_id": str(issuer_id), "key_id": key_id, "reason": body.reason},
    )
    await db.commit()
    await db.refresh(row)
    return TrustedKeyOut.model_validate(row)


# ---------------------------------------------------------------------------
# Versioned Gate policy and immutable runtime evaluations
# ---------------------------------------------------------------------------


@router.post(
    "/gate/policies",
    response_model=GatePolicySetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_gate_policy(
    body: GatePolicySetCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> GatePolicySetOut:
    _require(auth, "admin")
    actor = _authenticated_actor(auth, body.actor_id)
    barrier = _safe_barrier(body.barrier_group, auth)
    definition = policy_definition_payload(body, barrier)
    policy = GatePolicySet(
        namespace=auth.namespace,
        barrier_group=barrier,
        name=body.name,
        version=body.version,
        description=body.description,
        status="draft",
        default_disposition=body.default_disposition,
        protected_actions=body.protected_actions,
        target_ref_prefixes=body.target_ref_prefixes,
        enforcement_principal_ids=body.enforcement_principal_ids,
        maximum_permit_ttl_seconds=body.maximum_permit_ttl_seconds,
        created_by=actor,
        policy_hash=sha256_json(definition),
        metadata_=body.metadata,
    )
    db.add(policy)
    try:
        await db.flush()
        for item in body.rules:
            values = item.model_dump(mode="python")
            db.add(
                GatePolicyRule(
                    id=uuid.uuid4(),
                    namespace=auth.namespace,
                    barrier_group=barrier,
                    policy_set_id=policy.id,
                    **values,
                )
            )
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Policy name/version or rule names already exist",
        ) from exc
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.gate.policy_created",
        resource_type="gate_policy_set",
        resource_id=policy.id,
        barrier_group=barrier,
        details={
            "name": policy.name,
            "version": policy.version,
            "policy_hash": policy.policy_hash,
            "rule_count": len(body.rules),
            "protected_actions": policy.protected_actions,
            "target_ref_prefixes": policy.target_ref_prefixes,
            "enforcement_principal_ids": policy.enforcement_principal_ids,
            "maximum_permit_ttl_seconds": policy.maximum_permit_ttl_seconds,
        },
    )
    await _commit(db, "Policy creation conflicted with another request")
    await db.refresh(policy)
    return await _policy_out(db, policy)


@router.get("/gate/policies", response_model=list[GatePolicySetOut])
async def list_gate_policies(
    response: Response,
    name: str | None = Query(default=None),
    policy_status: str | None = Query(default=None, alias="status"),
    include_rules: bool = Query(default=True),
    offset: int = Query(default=0, ge=0, le=_MAX_LIST_OFFSET),
    before_created_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[GatePolicySetOut]:
    _require(auth, "read")
    _require_paired_cursor(
        before_created_at,
        before_id,
        first_name="before_created_at",
        second_name="before_id",
    )
    if before_created_at is not None and offset:
        raise HTTPException(status_code=422, detail="offset cannot be combined with a cursor")
    filters: list[Any] = [GatePolicySet.namespace == auth.namespace]
    add_barrier_filter(filters, GatePolicySet.barrier_group, auth.barrier_group)
    if name:
        filters.append(GatePolicySet.name == name)
    if policy_status:
        if policy_status not in {"draft", "active", "retired"}:
            raise HTTPException(status_code=422, detail="Invalid policy status")
        filters.append(GatePolicySet.status == policy_status)
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(GatePolicySet).where(*filters)
            )
        ).scalar_one()
    )
    page_filters = list(filters)
    if before_created_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor_condition(
                GatePolicySet.created_at,
                GatePolicySet.id,
                before_time=before_created_at,
                before_id=before_id,
            )
        )
    result = await db.execute(
        select(GatePolicySet)
        .where(*page_filters)
        .order_by(GatePolicySet.created_at.desc(), GatePolicySet.id.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = {
            "before_created_at": last.created_at.isoformat(),
            "before_id": str(last.id),
        }
    _set_page_headers(
        response,
        total=total,
        offset=offset,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        next_cursor=next_cursor,
    )
    return await _policies_out(db, page, include_rules=include_rules)


@router.get("/gate/policies/{policy_id}", response_model=GatePolicySetOut)
async def get_gate_policy(
    policy_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> GatePolicySetOut:
    _require(auth, "read")
    row = await _visible_by_id(db, GatePolicySet, policy_id, auth, "Gate policy")
    return await _policy_out(db, row)


@router.post("/gate/policies/{policy_id}/activate", response_model=GatePolicySetOut)
async def activate_gate_policy(
    policy_id: UUID,
    body: GatePolicyActivate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> GatePolicySetOut:
    _require(auth, "admin")
    actor = _authenticated_actor(auth, body.actor_id)
    policy = await _visible_by_id(
        db, GatePolicySet, policy_id, auth, "Gate policy", for_update=True
    )
    if policy.status == "active":
        return await _policy_out(db, policy)
    if policy.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft policy can be activated")
    if (
        not policy.protected_actions
        or not policy.target_ref_prefixes
        or not policy.enforcement_principal_ids
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Legacy selector-free policies cannot be activated; create a new version "
                "with protected_actions, target_ref_prefixes, and "
                "enforcement_principal_ids"
            ),
        )
    # Serialize selector activation per namespace/barrier. Row locks alone do
    # not close the empty-result race when two new policy names activate at once.
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {
                "lock_key": (
                    f"lians:gate-policy:{auth.namespace}:"
                    f"{policy.barrier_group if policy.barrier_group is not None else '<shared>'}"
                )
            },
        )
    now = utc_now()
    active_result = await db.execute(
        select(GatePolicySet).where(
            GatePolicySet.namespace == auth.namespace,
            GatePolicySet.barrier_group == policy.barrier_group,
            GatePolicySet.status == "active",
        )
        .order_by(GatePolicySet.id)
        .limit(_MAX_ACTIVE_GATE_POLICIES_PER_BARRIER + 1)
        .with_for_update()
    )
    active_policies = list(active_result.scalars().all())
    if len(active_policies) > _MAX_ACTIVE_GATE_POLICIES_PER_BARRIER:
        raise HTTPException(
            status_code=409,
            detail="Active Gate policy capacity is already exceeded",
        )
    collision = next(
        (
            active
            for active in active_policies
            if active.name != policy.name and _policy_selectors_overlap(active, policy)
        ),
        None,
    )
    if collision is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Gate selector overlaps active policy "
                f"{collision.name!r} version {collision.version!r}; active mappings "
                "within one information barrier must be unambiguous"
            ),
        )
    replaces_active_version = any(
        previous.name == policy.name for previous in active_policies
    )
    if (
        len(active_policies) >= _MAX_ACTIVE_GATE_POLICIES_PER_BARRIER
        and not replaces_active_version
    ):
        raise HTTPException(
            status_code=409,
            detail="Active Gate policy capacity is reached for this information barrier",
        )
    retired_ids: list[str] = []
    for previous in active_policies:
        if previous.name != policy.name:
            continue
        previous.status = "retired"
        previous.retired_at = now
        retired_ids.append(str(previous.id))
    # Retire first so the partial unique index never observes two active
    # versions even when SQLAlchemy orders the later UPDATE batch by UUID.
    if retired_ids:
        await db.flush()
    policy.status = "active"
    policy.activated_by = actor
    policy.activated_at = now
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.gate.policy_activated",
        resource_type="gate_policy_set",
        resource_id=policy.id,
        barrier_group=policy.barrier_group,
        details={
            "name": policy.name,
            "version": policy.version,
            "policy_hash": policy.policy_hash,
            "retired_policy_ids": retired_ids,
            "protected_actions": policy.protected_actions,
            "target_ref_prefixes": policy.target_ref_prefixes,
            "enforcement_principal_ids": policy.enforcement_principal_ids,
            "maximum_permit_ttl_seconds": policy.maximum_permit_ttl_seconds,
        },
    )
    await _commit(db, "Another policy version became active concurrently")
    await db.refresh(policy)
    return await _policy_out(db, policy)


def _target_selector_matches(selector: str, target_ref: str) -> bool:
    """Match an exact URI or an explicitly boundary-terminated URI prefix.

    A selector such as ``urn:lians:order:prod`` is exact.  Administrators must
    write ``urn:lians:order:prod:`` (or a path ending in ``/``) to select
    descendants, preventing ``.../prod`` from accidentally covering
    ``.../production``.
    """
    return target_selector_matches(selector, target_ref)


def _matching_prefix_length(policy: GatePolicySet, action: str, target_ref: str) -> int:
    """Return the longest boundary-safe matching target selector, or -1."""
    if action not in set(policy.protected_actions or []):
        return -1
    return max(
        (
            len(prefix)
            for prefix in policy.target_ref_prefixes or []
            if _target_selector_matches(prefix, target_ref)
        ),
        default=-1,
    )


def _target_selector_candidates(target_ref: str) -> list[str]:
    """Return every selector that can boundary-match this canonical target."""
    candidates = [target_ref]
    candidates.extend(
        target_ref[:index]
        for index, character in enumerate(target_ref, start=1)
        if character in "/:#?"
    )
    return list(dict.fromkeys(candidates))


def _policy_match_expressions(
    db: AsyncSession,
    *,
    action: str,
    target_ref: str,
) -> tuple[Any, Any, Any]:
    """Build indexed selector filters plus an exact specificity expression."""
    dialect = db.get_bind().dialect.name
    target_selectors = func.json_each(
        GatePolicySet.target_ref_prefixes
    ).table_valued("value").alias("gate_target_selector")
    target_value = cast(target_selectors.c.value, String)
    specificity = (
        select(func.max(func.length(target_value)))
        .select_from(target_selectors)
        .where(
            or_(
                target_value == target_ref,
                and_(
                    func.substr(target_value, -1, 1).in_(("/", ":", "#", "?")),
                    func.substr(literal(target_ref), 1, func.length(target_value))
                    == target_value,
                ),
            )
        )
        .correlate(GatePolicySet)
        .scalar_subquery()
    )

    if dialect == "postgresql":
        target_selectors = func.jsonb_array_elements_text(
            cast(GatePolicySet.target_ref_prefixes, JSONB)
        ).table_valued("value").alias("gate_target_selector")
        target_value = cast(target_selectors.c.value, String)
        specificity = (
            select(func.max(func.length(target_value)))
            .select_from(target_selectors)
            .where(
                or_(
                    target_value == target_ref,
                    and_(
                        func.right(target_value, 1).in_(("/", ":", "#", "?")),
                        func.starts_with(literal(target_ref), target_value),
                    ),
                )
            )
            .correlate(GatePolicySet)
            .scalar_subquery()
        )
        action_filter = cast(GatePolicySet.protected_actions, JSONB).op("?")(
            action
        )
        target_filter = cast(GatePolicySet.target_ref_prefixes, JSONB).op("?|")(
            bindparam(
                "gate_target_selector_candidates",
                value=_target_selector_candidates(target_ref),
                type_=ARRAY(String()),
            )
        )
        return action_filter, target_filter, specificity

    if dialect == "sqlite":
        action_values = func.json_each(
            GatePolicySet.protected_actions
        ).table_valued("value").alias("gate_action_selector")
        action_filter = (
            select(literal(1))
            .select_from(action_values)
            .where(cast(action_values.c.value, String) == action)
            .correlate(GatePolicySet)
            .exists()
        )
        return action_filter, specificity.is_not(None), specificity

    raise RuntimeError(f"Gate policy matching is unsupported on {dialect}")


def _policy_selectors_overlap(left: GatePolicySet, right: GatePolicySet) -> bool:
    if not set(left.protected_actions or []).intersection(right.protected_actions or []):
        return False
    return any(
        _target_selector_matches(left_prefix, right_prefix)
        or _target_selector_matches(right_prefix, left_prefix)
        for left_prefix in left.target_ref_prefixes or []
        for right_prefix in right.target_ref_prefixes or []
    )


def _assert_policy_selection(request: Any, policy: GatePolicySet) -> None:
    """Treat optional caller policy fields as assertions, never selectors."""
    mismatches: list[str] = []
    policy_set_id = getattr(request, "policy_set_id", None)
    policy_name = getattr(request, "policy_name", None)
    policy_version = getattr(request, "policy_version", None)
    if policy_set_id is not None and policy_set_id != policy.id:
        mismatches.append("policy_set_id")
    if policy_name is not None and policy_name != policy.name:
        mismatches.append("policy_name")
    if policy_version is not None and policy_version != policy.version:
        mismatches.append("policy_version")
    if mismatches:
        raise HTTPException(
            status_code=409,
            detail=(
                "Caller policy assertion does not match the authoritative action/target "
                f"mapping: {', '.join(mismatches)}"
            ),
        )


async def _resolve_active_policy(
    db: AsyncSession,
    request: Any,
    auth: AuthContext,
    preferred_barrier: str | None,
) -> GatePolicySet:
    action_filter, target_filter, specificity = _policy_match_expressions(
        db,
        action=request.action,
        target_ref=request.target_ref,
    )
    filters: list[Any] = [
        GatePolicySet.namespace == auth.namespace,
        GatePolicySet.status == "active",
        action_filter,
        target_filter,
        specificity.is_not(None),
    ]
    add_barrier_filter(filters, GatePolicySet.barrier_group, auth.barrier_group)
    if preferred_barrier is None:
        filters.append(GatePolicySet.barrier_group.is_(None))
        barrier_rank = literal(0)
    else:
        filters.append(
            or_(
                GatePolicySet.barrier_group == preferred_barrier,
                GatePolicySet.barrier_group.is_(None),
            )
        )
        barrier_rank = case(
            (GatePolicySet.barrier_group == preferred_barrier, 0),
            else_=1,
        )
    result = await db.execute(
        select(
            GatePolicySet,
            specificity.label("selector_specificity"),
            barrier_rank.label("barrier_rank"),
        )
        .where(*filters)
        .order_by(
            barrier_rank,
            specificity.desc(),
            GatePolicySet.id,
        )
        .limit(2)
    )
    candidates = list(result.all())
    if not candidates:
        raise HTTPException(
            status_code=409,
            detail="No active Gate policy protects this exact action and target",
        )
    selected, selected_specificity, selected_barrier_rank = candidates[0]
    if len(candidates) > 1:
        _, next_specificity, next_barrier_rank = candidates[1]
    else:
        next_specificity = next_barrier_rank = None
    if (
        next_specificity == selected_specificity
        and next_barrier_rank == selected_barrier_rank
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Ambiguous active Gate policy mapping for this action and target; "
                "an administrator must retire or re-version the overlapping policies"
            ),
        )
    _assert_policy_selection(request, selected)
    return selected


def _attestation_principal(auth: AuthContext) -> tuple[str, str]:
    principal = _authenticated_actor(auth, None)
    if not auth.role:
        raise HTTPException(
            status_code=403,
            detail="A server-derived named role is required to create an approval attestation",
        )
    return principal, auth.role


@router.post(
    "/gate/approvals",
    response_model=GateApprovalAttestationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Append a role-bound approval attestation for an exact Gate boundary",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def create_gate_approval(
    body: GateApprovalAttestationCreate,
    response: Response,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> GateApprovalAttestationOut:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    _require_any(auth, "write", "admin")
    principal, role = _attestation_principal(auth)
    record_barrier = _safe_barrier(body.target_barrier_group, auth)
    policy = await _resolve_active_policy(db, body, auth, record_barrier)
    linked_decision = await _validate_link(
        db,
        auth=auth,
        model=DecisionRecord,
        resource_id=body.decision_id,
        label="decision",
    )
    linked_change = await _validate_link(
        db,
        auth=auth,
        model=LedgerEvent,
        resource_id=body.change_event_id,
        label="change event",
    )
    _require_boundary_barrier(linked_decision, record_barrier, "decision")
    _require_boundary_barrier(linked_change, record_barrier, "change event")
    try:
        row = await create_gate_approval_attestation(
            db,
            namespace=auth.namespace,
            barrier_group=record_barrier,
            principal_id=principal,
            principal_type=auth.principal_type,
            role=role,
            auth_method=auth.auth_method,
            credential_id=auth.credential_id,
            policy=policy,
            body=body,
        )
    except ApprovalAttestationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _audit(
        db,
        auth=auth,
        actor_id=principal,
        operation="control.gate.approval_attested",
        resource_type="gate_approval_attestation",
        resource_id=row.id,
        barrier_group=row.barrier_group,
        details={
            "series_key": row.series_key,
            "sequence": row.sequence,
            "status": row.status,
            "context_hash": row.context_hash,
            "statement_hash": row.statement_hash,
            "attestation_hash": row.attestation_hash,
        },
    )
    await _commit(db, "Approval series was created concurrently")
    await db.refresh(row)
    return gate_approval_out(row)


@router.post(
    "/gate/approvals/{approval_id}/supersede",
    response_model=GateApprovalAttestationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Append a superseding approval, rejection, or revocation",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def supersede_gate_approval(
    approval_id: UUID,
    body: GateApprovalAttestationSupersede,
    response: Response,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> GateApprovalAttestationOut:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    _require_any(auth, "write", "admin")
    principal, role = _attestation_principal(auth)
    prior = await _visible_by_id(
        db, GateApprovalAttestation, approval_id, auth, "Gate approval attestation"
    )
    if auth.barrier_group is not None and prior.barrier_group != auth.barrier_group:
        raise HTTPException(status_code=404, detail="Gate approval attestation not found")
    allow_revoke_other = (
        "admin" in auth.scopes and role in {"owner", "compliance"}
    )
    try:
        row = await supersede_gate_approval_attestation(
            db,
            prior=prior,
            actor_principal_id=principal,
            actor_principal_type=auth.principal_type,
            actor_role=role,
            auth_method=auth.auth_method,
            credential_id=auth.credential_id,
            allow_revoke_other=allow_revoke_other,
            body=body,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ApprovalAttestationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _audit(
        db,
        auth=auth,
        actor_id=principal,
        operation="control.gate.approval_superseded",
        resource_type="gate_approval_attestation",
        resource_id=row.id,
        barrier_group=row.barrier_group,
        details={
            "series_key": row.series_key,
            "sequence": row.sequence,
            "status": row.status,
            "supersedes_id": str(row.supersedes_id),
            "context_hash": row.context_hash,
            "statement_hash": row.statement_hash,
            "attestation_hash": row.attestation_hash,
        },
    )
    await _commit(db, "Approval was superseded concurrently")
    await db.refresh(row)
    return gate_approval_out(row)


@router.get("/gate/approvals", response_model=list[GateApprovalAttestationOut])
async def list_gate_approvals(
    response: Response,
    context_hash: str | None = Query(default=None, pattern=r"^[0-9a-fA-F]{64}$"),
    decision_id: UUID | None = Query(default=None),
    approval_status: str | None = Query(default=None, alias="status"),
    only_current: bool = Query(default=True),
    include_statement: bool = Query(default=False),
    before_attested_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[GateApprovalAttestationOut]:
    _require(auth, "read")
    _require_paired_cursor(
        before_attested_at,
        before_id,
        first_name="before_attested_at",
        second_name="before_id",
    )
    if include_statement:
        _require(auth, "admin")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    filters: list[Any] = [GateApprovalAttestation.namespace == auth.namespace]
    add_barrier_filter(filters, GateApprovalAttestation.barrier_group, auth.barrier_group)
    if context_hash:
        filters.append(GateApprovalAttestation.context_hash == context_hash.lower())
    if decision_id:
        filters.append(GateApprovalAttestation.decision_id == decision_id)
    if approval_status:
        if approval_status not in {"approved", "rejected", "revoked"}:
            raise HTTPException(status_code=422, detail="Invalid approval status")
        filters.append(GateApprovalAttestation.status == approval_status)
    if only_current:
        superseded_filters: list[Any] = [
            GateApprovalAttestation.namespace == auth.namespace,
            GateApprovalAttestation.supersedes_id.is_not(None),
        ]
        add_barrier_filter(
            superseded_filters,
            GateApprovalAttestation.barrier_group,
            auth.barrier_group,
        )
        superseded_ids = select(GateApprovalAttestation.supersedes_id).where(
            *superseded_filters
        )
        filters.append(GateApprovalAttestation.id.not_in(superseded_ids))
    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(GateApprovalAttestation)
                .where(*filters)
            )
        ).scalar_one()
    )
    page_filters = list(filters)
    if before_attested_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor_condition(
                GateApprovalAttestation.attested_at,
                GateApprovalAttestation.id,
                before_time=before_attested_at,
                before_id=before_id,
            )
        )
    result = await db.execute(
        select(GateApprovalAttestation)
        .where(*page_filters)
        .order_by(
            GateApprovalAttestation.attested_at.desc(),
            GateApprovalAttestation.id.desc(),
        )
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = {
            "before_attested_at": last.attested_at.isoformat(),
            "before_id": str(last.id),
        }
    _set_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        next_cursor=next_cursor,
    )
    return [
        gate_approval_out(row, include_statement=include_statement)
        for row in page
    ]


@router.get(
    "/gate/approvals/{approval_id}", response_model=GateApprovalAttestationOut
)
async def get_gate_approval(
    approval_id: UUID,
    response: Response,
    include_statement: bool = Query(default=False),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> GateApprovalAttestationOut:
    _require(auth, "read")
    if include_statement:
        _require(auth, "admin")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    row = await _visible_by_id(
        db, GateApprovalAttestation, approval_id, auth, "Gate approval attestation"
    )
    return gate_approval_out(row, include_statement=include_statement)


@router.post(
    "/gate/evaluate",
    response_model=GateEvaluationOut,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Evaluate an action and atomically issue a one-time permit only for allow"
    ),
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def evaluate_runtime_gate(
    body: GateEvaluationRequest,
    response: Response,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> GateEvaluationOut:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    _require(auth, "write")
    if body.untrusted_content_signals:
        raise HTTPException(
            status_code=422,
            detail=(
                "untrusted_content_signals are server-derived from the immutable "
                "decision evidence boundary and cannot be supplied by the evaluator"
            ),
        )
    principal = _authenticated_actor(auth, body.principal_id)
    if body.principal_scopes and set(body.principal_scopes) != set(auth.scopes):
        raise HTTPException(
            status_code=403,
            detail="principal_scopes must exactly match the authenticated principal",
        )
    if (
        body.principal_barrier_group is not None
        and body.principal_barrier_group != auth.barrier_group
    ):
        raise HTTPException(
            status_code=403,
            detail="principal_barrier_group must match the authenticated principal",
        )
    principal_barrier = auth.barrier_group
    target_barrier = body.target_barrier_group
    if auth.barrier_group is not None:
        if target_barrier not in {None, auth.barrier_group}:
            raise HTTPException(status_code=403, detail="Target crosses information barrier")
        record_barrier = auth.barrier_group
    else:
        record_barrier = target_barrier
    request = body.model_copy(
        update={
            "principal_id": principal,
            "principal_scopes": list(auth.scopes),
            "principal_barrier_group": principal_barrier,
            "target_barrier_group": target_barrier,
        }
    )
    policy = await _resolve_active_policy(db, request, auth, record_barrier)
    if policy.barrier_group is not None and policy.barrier_group != record_barrier:
        raise HTTPException(
            status_code=403,
            detail="Selected Gate policy belongs to a different information barrier",
        )
    if request.enforcement_principal_id == request.principal_id:
        raise HTTPException(
            status_code=403,
            detail="Gate evaluator and enforcement mediator must be separate identities",
        )
    if request.enforcement_principal_id not in set(
        policy.enforcement_principal_ids or []
    ) or request.permit_ttl_seconds > int(policy.maximum_permit_ttl_seconds or 0):
        raise HTTPException(
            status_code=403,
            detail=(
                "Requested enforcement principal or permit TTL is not authorized "
                "by the immutable Gate policy"
            ),
        )
    linked_decision = await _validate_link(
        db,
        auth=auth,
        model=DecisionRecord,
        resource_id=request.decision_id,
        label="decision",
    )
    try:
        await assert_decision_record_integrity(db, linked_decision)
    except DecisionRecordIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "decision_record_integrity_verification_failed",
                "message": "Linked decision failed authenticated integrity verification",
            },
        ) from exc
    linked_change = await _validate_link(
        db,
        auth=auth,
        model=LedgerEvent,
        resource_id=request.change_event_id,
        label="change event",
    )
    _require_boundary_barrier(linked_decision, record_barrier, "decision")
    _require_boundary_barrier(linked_change, record_barrier, "change event")
    derived_context = await _decision_gate_context(
        db,
        auth=auth,
        decision=linked_decision,
        record_barrier=record_barrier,
    )
    if (
        "decision_type" in body.model_fields_set
        and body.decision_type != derived_context["decision_type"]
    ):
        raise HTTPException(
            status_code=409,
            detail="decision_type assertion does not match the immutable decision",
        )
    if (
        "risk_level" in body.model_fields_set
        and body.risk_level != derived_context["risk_level"]
    ):
        raise HTTPException(
            status_code=409,
            detail="risk_level assertion does not match the recorded decision evidence",
        )
    request = request.model_copy(update=derived_context)
    rules_result = await db.execute(
        select(GatePolicyRule)
        .where(GatePolicyRule.policy_set_id == policy.id)
        .order_by(GatePolicyRule.priority, GatePolicyRule.name)
        .limit(_MAX_GATE_POLICY_RULES + 1)
    )
    policy_rules = list(rules_result.scalars().all())
    if len(policy_rules) > _MAX_GATE_POLICY_RULES:
        raise HTTPException(
            status_code=503,
            detail="Gate policy exceeds the supported rule capacity",
        )
    try:
        row, issued_permit = await evaluate_gate(
            db,
            namespace=auth.namespace,
            barrier_group=record_barrier,
            policy=policy,
            rules=policy_rules,
            request=request,
        )
    except ApprovalAttestationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _audit(
        db,
        auth=auth,
        actor_id=request.principal_id,
        operation="control.gate.evaluated",
        resource_type="gate_decision",
        resource_id=row.id,
        barrier_group=row.barrier_group,
        details={
            "policy_set_id": str(policy.id),
            "policy_hash": policy.policy_hash,
            "disposition": row.disposition,
            "evaluation_hash": row.evaluation_hash,
            "decision_id": str(row.decision_id) if row.decision_id else None,
            "change_event_id": str(row.change_event_id) if row.change_event_id else None,
            "target_ref": row.target_ref,
            "enforcement_principal_id": row.enforcement_principal_id,
            "execution_request_hash": row.execution_request_hash,
            "execution_permit_id": (
                str(issued_permit.row.id) if issued_permit is not None else None
            ),
            "execution_permit_expires_at": (
                issued_permit.row.expires_at.isoformat()
                if issued_permit is not None
                else None
            ),
            "execution_permit_grant_hash": (
                issued_permit.row.grant_hash if issued_permit is not None else None
            ),
        },
    )
    await _commit(db, "Gate evaluation hash conflicted with another request")
    record_gate_evaluation(row.disposition)
    if issued_permit is not None:
        record_gate_permit_outcome("issued")
    await db.refresh(row)
    decision = GateDecisionOut.model_validate(row)
    execution_permit = None
    if issued_permit is not None:
        grant = issued_permit.row
        execution_permit = GateExecutionPermitIssued(
            permit_id=grant.id,
            evaluation_id=grant.evaluation_id,
            enforcement_principal_id=grant.enforcement_principal_id,
            action=grant.action,
            target_ref=grant.target_ref,
            decision_id=grant.decision_id,
            execution_request_hash=grant.execution_request_hash,
            issued_at=grant.issued_at,
            expires_at=grant.expires_at,
            token=issued_permit.token,
        )
    return GateEvaluationOut(
        **decision.model_dump(mode="python"), execution_permit=execution_permit
    )


@router.post(
    "/gate/permits/consume",
    response_model=GateExecutionPermitConsumptionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Atomically redeem an allow permit as its exact enforcement mediator",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def consume_runtime_gate_permit(
    body: GateExecutionPermitConsume,
    response: Response,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> GateExecutionPermitConsumptionOut:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    _require(auth, "write")
    principal = _authenticated_actor(auth, None)
    try:
        row = await consume_gate_execution_permit(
            db,
            namespace=auth.namespace,
            caller_barrier=auth.barrier_group,
            principal_id=principal,
            body=body,
        )
        # Capacity is reserved only after exact permit validation, but before
        # anything commits. A denial rolls the consumption back and leaves the
        # single-use grant unconsumed, subject to its original expiry.
        await reserve_namespace_usage(
            db,
            namespace=auth.namespace,
            protected_actions=1,
        )
        await _audit(
            db,
            auth=auth,
            actor_id=principal,
            operation="control.gate.execution_permit_consumed",
            resource_type="gate_execution_permit_consumption",
            resource_id=row.id,
            barrier_group=row.barrier_group,
            details={
                "permit_id": str(row.permit_id),
                "evaluation_id": str(row.evaluation_id),
                "policy_set_id": str(row.policy_set_id),
                "decision_id": str(row.decision_id),
                "action": row.action,
                "target_ref": row.target_ref,
                "execution_request_hash": row.execution_request_hash,
                "consuming_principal_id": row.consuming_principal_id,
                "grant_hash": row.grant_hash,
                "consumption_hash": row.consumption_hash,
            },
        )
        # A protected action becomes billable only after the exact permit has
        # been redeemed successfully. The consumption proof, audit binding,
        # and durable billing fact are one commit; rejected/replayed attempts
        # roll back without staging usage.
        await enqueue_protected_action_usage_event(
            db,
            namespace=auth.namespace,
            permit_id=row.permit_id,
            occurred_at=row.consumed_at,
        )
        await db.commit()
        record_gate_permit_outcome("consumed")
    except GatePermitRedemptionError as exc:
        await db.rollback()
        record_gate_permit_outcome(exc.outcome)
        # Deliberately identical for unknown IDs, bad tokens, claim mismatch,
        # expiry, replay, and concurrent redemption.
        raise HTTPException(
            status_code=403, detail="Execution permit is invalid or unusable"
        ) from exc
    except GovernanceViolation:
        await db.rollback()
        record_gate_permit_outcome("rejected")
        raise
    except DBAPIError as exc:
        await db.rollback()
        sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(
            exc.orig, "pgcode", None
        )
        # Constraint failures and the PostgreSQL validation trigger's P0001 are
        # redemption rejections. Connectivity/availability failures propagate
        # as server errors instead of being misreported as an invalid permit.
        if isinstance(exc, IntegrityError) or sqlstate == "P0001":
            record_gate_permit_outcome("rejected")
            raise HTTPException(
                status_code=403, detail="Execution permit is invalid or unusable"
            ) from exc
        raise
    await db.refresh(row)
    return GateExecutionPermitConsumptionOut.model_validate(row)


@router.get("/gate/evaluations", response_model=list[GateDecisionOut])
async def list_gate_evaluations(
    response: Response,
    disposition: str | None = Query(default=None),
    decision_id: UUID | None = Query(default=None),
    before_evaluated_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[GateDecisionOut]:
    _require(auth, "read")
    _require_paired_cursor(
        before_evaluated_at,
        before_id,
        first_name="before_evaluated_at",
        second_name="before_id",
    )
    filters: list[Any] = [GateDecisionRecord.namespace == auth.namespace]
    add_barrier_filter(filters, GateDecisionRecord.barrier_group, auth.barrier_group)
    if disposition:
        if disposition not in {"allow", "deny", "review"}:
            raise HTTPException(status_code=422, detail="Invalid disposition")
        filters.append(GateDecisionRecord.disposition == disposition)
    if decision_id:
        filters.append(GateDecisionRecord.decision_id == decision_id)
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(GateDecisionRecord).where(*filters)
            )
        ).scalar_one()
    )
    page_filters = list(filters)
    if before_evaluated_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor_condition(
                GateDecisionRecord.evaluated_at,
                GateDecisionRecord.id,
                before_time=before_evaluated_at,
                before_id=before_id,
            )
        )
    result = await db.execute(
        select(GateDecisionRecord)
        .where(*page_filters)
        .order_by(
            GateDecisionRecord.evaluated_at.desc(),
            GateDecisionRecord.id.desc(),
        )
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = {
            "before_evaluated_at": last.evaluated_at.isoformat(),
            "before_id": str(last.id),
        }
    _set_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        next_cursor=next_cursor,
    )
    return [GateDecisionOut.model_validate(row) for row in page]


@router.get("/gate/evaluations/{evaluation_id}", response_model=GateDecisionOut)
async def get_gate_evaluation(
    evaluation_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> GateDecisionOut:
    _require(auth, "read")
    row = await _visible_by_id(db, GateDecisionRecord, evaluation_id, auth, "Gate evaluation")
    return GateDecisionOut.model_validate(row)


# ---------------------------------------------------------------------------
# Investigations, owned remediation, and attested closure
# ---------------------------------------------------------------------------


@router.post(
    "/investigations/cases",
    response_model=InvestigationCaseOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_investigation_case(
    body: InvestigationCaseCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> InvestigationCaseOut:
    _require(auth, "write")
    actor = _authenticated_actor(auth, body.actor_id)
    barrier = _safe_barrier(body.barrier_group, auth)
    linked_decision = await _validate_link(
        db, auth=auth, model=DecisionRecord, resource_id=body.decision_id, label="decision"
    )
    linked_change = await _validate_link(
        db,
        auth=auth,
        model=LedgerEvent,
        resource_id=body.change_event_id,
        label="change event",
    )
    linked_gate = await _validate_link(
        db,
        auth=auth,
        model=GateDecisionRecord,
        resource_id=body.gate_decision_id,
        label="Gate evaluation",
    )
    for linked, label in (
        (linked_decision, "decision"),
        (linked_change, "change event"),
        (linked_gate, "Gate evaluation"),
    ):
        _require_boundary_barrier(linked, barrier, label)
    if linked_gate is not None:
        if (
            body.decision_id is not None
            and linked_gate.decision_id is not None
            and body.decision_id != linked_gate.decision_id
        ):
            raise HTTPException(
                status_code=422,
                detail="Linked Gate evaluation belongs to a different decision",
            )
        if (
            body.change_event_id is not None
            and linked_gate.change_event_id is not None
            and body.change_event_id != linked_gate.change_event_id
        ):
            raise HTTPException(
                status_code=422,
                detail="Linked Gate evaluation belongs to a different change event",
            )
    decision_id = body.decision_id or (
        linked_gate.decision_id if linked_gate is not None else None
    )
    change_event_id = body.change_event_id or (
        linked_gate.change_event_id if linked_gate is not None else None
    )
    row = InvestigationCase(
        namespace=auth.namespace,
        barrier_group=barrier,
        title=body.title,
        description=body.description,
        severity=body.severity,
        status="open",
        owner_principal=body.owner_principal,
        decision_id=decision_id,
        change_event_id=change_event_id,
        gate_decision_id=body.gate_decision_id,
        opened_by=actor,
        metadata_=body.metadata,
    )
    db.add(row)
    await db.flush()
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.investigation.opened",
        resource_type="investigation_case",
        resource_id=row.id,
        barrier_group=barrier,
        details={
            "severity": row.severity,
            "owner_principal": row.owner_principal,
            "decision_id": str(row.decision_id) if row.decision_id else None,
            "change_event_id": str(row.change_event_id) if row.change_event_id else None,
            "gate_decision_id": str(row.gate_decision_id) if row.gate_decision_id else None,
        },
    )
    await db.commit()
    await db.refresh(row)
    return InvestigationCaseOut.model_validate(row)


@router.get("/investigations/cases", response_model=list[InvestigationCaseOut])
async def list_investigation_cases(
    response: Response,
    case_status: str | None = Query(default=None, alias="status"),
    owner_principal: str | None = Query(default=None),
    decision_id: UUID | None = Query(default=None),
    before_opened_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[InvestigationCaseOut]:
    _require(auth, "read")
    _require_paired_cursor(
        before_opened_at,
        before_id,
        first_name="before_opened_at",
        second_name="before_id",
    )
    filters: list[Any] = [InvestigationCase.namespace == auth.namespace]
    add_barrier_filter(filters, InvestigationCase.barrier_group, auth.barrier_group)
    if case_status:
        filters.append(InvestigationCase.status == case_status)
    if owner_principal:
        filters.append(InvestigationCase.owner_principal == owner_principal)
    if decision_id:
        gate_filters: list[Any] = [
            GateDecisionRecord.namespace == auth.namespace,
            GateDecisionRecord.decision_id == decision_id,
        ]
        add_barrier_filter(
            gate_filters,
            GateDecisionRecord.barrier_group,
            auth.barrier_group,
        )
        gate_ids = select(GateDecisionRecord.id).where(*gate_filters)
        # Case creation now materializes a Gate-linked decision_id, while this
        # compatibility branch keeps pre-upgrade Gate-only cases discoverable.
        filters.append(
            or_(
                InvestigationCase.decision_id == decision_id,
                InvestigationCase.gate_decision_id.in_(gate_ids),
            )
        )
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(InvestigationCase).where(*filters)
            )
        ).scalar_one()
    )
    page_filters = list(filters)
    if before_opened_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor_condition(
                InvestigationCase.opened_at,
                InvestigationCase.id,
                before_time=before_opened_at,
                before_id=before_id,
            )
        )
    result = await db.execute(
        select(InvestigationCase)
        .where(*page_filters)
        .order_by(InvestigationCase.opened_at.desc(), InvestigationCase.id.desc())
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = {
            "before_opened_at": last.opened_at.isoformat(),
            "before_id": str(last.id),
        }
    _set_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        next_cursor=next_cursor,
    )
    return [InvestigationCaseOut.model_validate(row) for row in page]


@router.get("/investigations/cases/{case_id}", response_model=InvestigationCaseOut)
async def get_investigation_case(
    case_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> InvestigationCaseOut:
    _require(auth, "read")
    row = await _visible_by_id(db, InvestigationCase, case_id, auth, "Investigation case")
    return InvestigationCaseOut.model_validate(row)


@router.patch("/investigations/cases/{case_id}", response_model=InvestigationCaseOut)
async def update_investigation_case(
    case_id: UUID,
    body: InvestigationCaseUpdate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> InvestigationCaseOut:
    _require(auth, "write")
    actor = _authenticated_actor(auth, body.actor_id)
    row = await _visible_by_id(
        db, InvestigationCase, case_id, auth, "Investigation case", for_update=True
    )
    _assert_updated_at(row.updated_at, body.expected_updated_at)
    if row.status == "closed":
        raise HTTPException(status_code=409, detail="Closed cases are immutable")
    changes: dict[str, Any] = {}
    if "owner_principal" in body.model_fields_set:
        changes["owner_principal"] = {"from": row.owner_principal, "to": body.owner_principal}
        row.owner_principal = body.owner_principal
    if body.status is not None:
        try:
            validate_transition(row.status, body.status, CASE_TRANSITIONS, "case")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        changes["status"] = {"from": row.status, "to": body.status}
        row.status = body.status
    if body.severity is not None:
        changes["severity"] = {"from": row.severity, "to": body.severity}
        row.severity = body.severity
    if "resolution_summary" in body.model_fields_set:
        changes["resolution_summary"] = {
            "from": row.resolution_summary,
            "to": body.resolution_summary,
        }
        row.resolution_summary = body.resolution_summary
    if not changes:
        return InvestigationCaseOut.model_validate(row)
    row.updated_at = utc_now()
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.investigation.updated",
        resource_type="investigation_case",
        resource_id=row.id,
        barrier_group=row.barrier_group,
        details={"changes": changes},
    )
    await db.commit()
    await db.refresh(row)
    return InvestigationCaseOut.model_validate(row)


@router.post(
    "/investigations/cases/{case_id}/tasks",
    response_model=RemediationTaskOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_remediation_task(
    case_id: UUID,
    body: RemediationTaskCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RemediationTaskOut:
    _require(auth, "write")
    actor = _authenticated_actor(auth, body.actor_id)
    case = await _visible_by_id(
        db, InvestigationCase, case_id, auth, "Investigation case", for_update=True
    )
    _assert_updated_at(case.updated_at, body.expected_case_updated_at)
    if case.status == "closed":
        raise HTTPException(status_code=409, detail="Cannot add work to a closed case")
    if case.decision_id is not None and body.decision_id not in {None, case.decision_id}:
        raise HTTPException(status_code=422, detail="Task decision must match its case")
    if case.change_event_id is not None and body.change_event_id not in {
        None,
        case.change_event_id,
    }:
        raise HTTPException(status_code=422, detail="Task change event must match its case")
    decision_id = case.decision_id or body.decision_id
    change_event_id = case.change_event_id or body.change_event_id
    linked_decision = await _validate_link(
        db, auth=auth, model=DecisionRecord, resource_id=decision_id, label="decision"
    )
    linked_change = await _validate_link(
        db,
        auth=auth,
        model=LedgerEvent,
        resource_id=change_event_id,
        label="change event",
    )
    _require_boundary_barrier(linked_decision, case.barrier_group, "decision")
    _require_boundary_barrier(linked_change, case.barrier_group, "change event")
    row = RemediationTask(
        namespace=auth.namespace,
        barrier_group=case.barrier_group,
        case_id=case.id,
        title=body.title,
        description=body.description,
        status="pending",
        owner_principal=body.owner_principal,
        due_at=body.due_at,
        decision_id=decision_id,
        change_event_id=change_event_id,
        created_by=actor,
        metadata_=body.metadata,
    )
    db.add(row)
    if case.status in {"open", "in_review"}:
        case.status = "remediating"
    # Creating a child is a case mutation even when its lifecycle was already
    # remediating. Advancing the token makes an ambiguous create reconcile-only.
    case.updated_at = utc_now()
    await db.flush()
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.remediation.task_created",
        resource_type="remediation_task",
        resource_id=row.id,
        barrier_group=row.barrier_group,
        details={
            "case_id": str(case.id),
            "owner_principal": row.owner_principal,
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "decision_id": str(row.decision_id) if row.decision_id else None,
            "change_event_id": str(row.change_event_id) if row.change_event_id else None,
        },
    )
    await db.commit()
    await db.refresh(row)
    return RemediationTaskOut.model_validate(row)


@router.get("/investigations/cases/{case_id}/tasks", response_model=list[RemediationTaskOut])
async def list_remediation_tasks(
    case_id: UUID,
    response: Response,
    task_status: str | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0, le=_MAX_LIST_OFFSET),
    after_created_at: datetime | None = Query(default=None),
    after_id: UUID | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[RemediationTaskOut]:
    _require(auth, "read")
    _require_paired_cursor(
        after_created_at,
        after_id,
        first_name="after_created_at",
        second_name="after_id",
    )
    if after_created_at is not None and offset:
        raise HTTPException(status_code=422, detail="offset cannot be combined with a cursor")
    await _visible_by_id(db, InvestigationCase, case_id, auth, "Investigation case")
    filters: list[Any] = [
        RemediationTask.namespace == auth.namespace,
        RemediationTask.case_id == case_id,
    ]
    add_barrier_filter(filters, RemediationTask.barrier_group, auth.barrier_group)
    if task_status:
        filters.append(RemediationTask.status == task_status)
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(RemediationTask).where(*filters)
            )
        ).scalar_one()
    )
    page_filters = list(filters)
    if after_created_at is not None and after_id is not None:
        page_filters.append(
            _ascending_cursor_condition(
                RemediationTask.created_at,
                RemediationTask.id,
                after_time=after_created_at,
                after_id=after_id,
            )
        )
    result = await db.execute(
        select(RemediationTask)
        .where(*page_filters)
        .order_by(RemediationTask.created_at.asc(), RemediationTask.id.asc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = {
            "after_created_at": last.created_at.isoformat(),
            "after_id": str(last.id),
        }
    _set_page_headers(
        response,
        total=total,
        offset=offset,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        next_cursor=next_cursor,
    )
    return [RemediationTaskOut.model_validate(row) for row in page]


@router.patch("/investigations/tasks/{task_id}", response_model=RemediationTaskOut)
async def update_remediation_task(
    task_id: UUID,
    body: RemediationTaskUpdate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> RemediationTaskOut:
    _require(auth, "write")
    actor = _authenticated_actor(auth, body.actor_id)
    row = await _lock_task_and_parent_case(db, task_id, auth)
    _assert_updated_at(row.updated_at, body.expected_updated_at)
    if row.status == "closed":
        raise HTTPException(status_code=409, detail="Closed tasks are immutable")
    changes: dict[str, Any] = {}
    if "owner_principal" in body.model_fields_set:
        changes["owner_principal"] = {"from": row.owner_principal, "to": body.owner_principal}
        row.owner_principal = body.owner_principal
    if body.status is not None:
        try:
            validate_transition(row.status, body.status, TASK_TRANSITIONS, "task")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        changes["status"] = {"from": row.status, "to": body.status}
        row.status = body.status
    if "due_at" in body.model_fields_set:
        changes["due_at"] = {
            "from": row.due_at.isoformat() if row.due_at else None,
            "to": body.due_at.isoformat() if body.due_at else None,
        }
        row.due_at = body.due_at
    if not changes:
        return RemediationTaskOut.model_validate(row)
    row.updated_at = utc_now()
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.remediation.task_updated",
        resource_type="remediation_task",
        resource_id=row.id,
        barrier_group=row.barrier_group,
        details={"case_id": str(row.case_id), "changes": changes},
    )
    await db.commit()
    await db.refresh(row)
    return RemediationTaskOut.model_validate(row)


@router.post(
    "/investigations/tasks/{task_id}/close",
    response_model=AttestedClosureResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def close_remediation_task(
    task_id: UUID,
    body: ClosureAttestationCreate,
    response: Response,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> AttestedClosureResult:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    _require(auth, "write")
    actor = _authenticated_actor(auth, body.actor_id)
    row = await _lock_task_and_parent_case(db, task_id, auth)
    _assert_updated_at(row.updated_at, body.expected_updated_at)
    if row.status == "closed":
        raise HTTPException(status_code=409, detail="Task is already closed")
    attestation = await create_closure_attestation(
        db,
        namespace=auth.namespace,
        barrier_group=row.barrier_group,
        resource_type="task",
        resource_id=row.id,
        decision_id=row.decision_id,
        change_event_id=row.change_event_id,
        body=body.model_copy(update={"actor_id": actor}),
    )
    now = utc_now()
    row.status = "closed"
    row.closed_at = now
    row.updated_at = now
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.remediation.task_closed",
        resource_type="remediation_task",
        resource_id=row.id,
        barrier_group=row.barrier_group,
        details={
            "case_id": str(row.case_id),
            "attestation_id": str(attestation.id),
            "attestation_hash": attestation.attestation_hash,
        },
    )
    await _commit(db, "This task already has a closure attestation")
    await db.refresh(attestation)
    return AttestedClosureResult(
        resource_type="task",
        resource_id=row.id,
        status="closed",
        attestation=closure_attestation_out(attestation, include_statement=True),
    )


@router.post(
    "/investigations/cases/{case_id}/close",
    response_model=AttestedClosureResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def close_investigation_case(
    case_id: UUID,
    body: ClosureAttestationCreate,
    response: Response,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> AttestedClosureResult:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    _require(auth, "write")
    actor = _authenticated_actor(auth, body.actor_id)
    row = await _visible_by_id(
        db, InvestigationCase, case_id, auth, "Investigation case", for_update=True
    )
    _assert_updated_at(row.updated_at, body.expected_updated_at)
    if row.status == "closed":
        raise HTTPException(status_code=409, detail="Case is already closed")
    # The locked case is the serialization root for every task mutation. The
    # exact SQL count is therefore stable for the remainder of this transaction.
    outstanding_task_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(RemediationTask)
                .where(
                    RemediationTask.namespace == auth.namespace,
                    RemediationTask.case_id == row.id,
                    RemediationTask.status != "closed",
                )
            )
        ).scalar_one()
    )
    if outstanding_task_count:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Every remediation task requires attested closure first",
                "outstanding_task_count": outstanding_task_count,
            },
        )
    resolution = body.resolution_summary or row.resolution_summary
    if not resolution:
        raise HTTPException(
            status_code=422,
            detail="resolution_summary is required to close an investigation case",
        )
    attestation = await create_closure_attestation(
        db,
        namespace=auth.namespace,
        barrier_group=row.barrier_group,
        resource_type="case",
        resource_id=row.id,
        decision_id=row.decision_id,
        change_event_id=row.change_event_id,
        body=body.model_copy(update={"actor_id": actor}),
    )
    now = utc_now()
    row.status = "closed"
    row.closed_at = now
    row.updated_at = now
    row.resolution_summary = resolution
    await _audit(
        db,
        auth=auth,
        actor_id=actor,
        operation="control.investigation.closed",
        resource_type="investigation_case",
        resource_id=row.id,
        barrier_group=row.barrier_group,
        details={
            "attestation_id": str(attestation.id),
            "attestation_hash": attestation.attestation_hash,
            "resolution_summary_hash": sha256_json(resolution),
        },
    )
    await _commit(db, "This case already has a closure attestation")
    await db.refresh(attestation)
    return AttestedClosureResult(
        resource_type="case",
        resource_id=row.id,
        status="closed",
        attestation=closure_attestation_out(attestation, include_statement=True),
    )


@router.get(
    "/investigations/{resource_type}/{resource_id}/attestation",
    response_model=ClosureAttestationOut,
)
async def get_closure_attestation(
    resource_type: str,
    resource_id: UUID,
    response: Response,
    include_statement: bool = Query(default=False),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> ClosureAttestationOut:
    _require(auth, "read")
    if include_statement:
        _require(auth, "admin")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    if resource_type not in {"case", "task"}:
        raise HTTPException(status_code=422, detail="resource_type must be case or task")
    filters: list[Any] = [
        ControlClosureAttestation.namespace == auth.namespace,
        ControlClosureAttestation.resource_type == resource_type,
        ControlClosureAttestation.resource_id == resource_id,
    ]
    add_barrier_filter(filters, ControlClosureAttestation.barrier_group, auth.barrier_group)
    result = await db.execute(select(ControlClosureAttestation).where(*filters))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Closure attestation not found")
    return closure_attestation_out(row, include_statement=include_statement)
