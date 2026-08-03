"""Cross-industry decision ledger and evidence-pack API."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import Text, and_, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from ..audit_chain import AuditCapacityExceeded, chain_log, verify_chain
from ..config import get_settings
from ..control_models import DecisionReviewEvent
from ..db import get_db
from ..decision_receipt import sha256_hex, verify_decision_receipt
from ..decision_record_integrity import (
    DECISION_RECORD_HASH_VERSION,
    VERIFIED_INTEGRITY_STATUS,
    DecisionRecordIntegrityError,
    assert_decision_record_integrity,
    authenticated_recorder_authorization_snapshot,
    authenticated_recorder_provenance,
    compute_decision_record_hash,
    decision_record_binding_payload,
)
from ..decision_review_service import (
    DecisionReviewIntegrityError,
    create_decision_review_event,
    decision_review_event_out,
    reviewer_ref_hash,
    verify_decision_review_event,
)
from ..evidence_models import (
    DecisionEvidenceCoverageSet,
    DecisionEvidenceKindCoverage,
    DecisionEvidenceLink,
    DecisionEvidenceLinkRegistration,
    DecisionImpactAssessmentJob,
    DecisionImpactAssessmentMatch,
    EvidenceArtifact,
)
from ..evidence_schemas import (
    DecisionEvidenceCoverageOut,
    DecisionEvidenceGraphOut,
    DecisionEvidenceLinkCreate,
    DecisionEvidenceLinkOut,
    EvidenceArtifactCreate,
    EvidenceArtifactKind,
    EvidenceArtifactOut,
    EvidenceDependencyChange,
    EvidenceGraphCoverage,
    ExhaustiveImpactAssessmentAdvance,
    ExhaustiveImpactAssessmentCreate,
    ExhaustiveImpactAssessmentMatchOut,
    ExhaustiveImpactAssessmentResults,
    ExhaustiveImpactAssessmentStatus,
    IndexedDecisionImpactResult,
)
from ..evidence_service import (
    DecisionEvidenceCapacityExceeded,
    artifact_dependency_filter,
    artifact_lookup_hash,
    artifact_out,
    create_artifact_from_request,
    create_impact_assessment_job,
    decision_artifact_specs,
    ensure_link,
    get_decision_coverage,
    get_impact_assessment_job,
    impact_assessment_status_out,
    index_decision_evidence,
    link_out,
)
from ..governance_service import estimate_ingest_bytes, reserve_namespace_usage
from ..idempotency import (
    IdempotencyConflict,
    IdempotencyReplayUnavailable,
    InvalidIdempotencyKey,
    InvalidIdempotencyRequest,
    OperationClaim,
    operation_claim,
)
from ..impact_assessment_service import (
    ImpactAssessmentLeaseConflict,
    ImpactAssessmentTerminal,
    advance_claimed_impact_assessment,
    claim_impact_assessment_for_request,
)
from ..memory_service import (
    get_knowledge_snapshot,
    measure_knowledge_snapshot_bytes,
)
from ..metering import enqueue_authoritative_decision_usage_event
from ..metrics import record_impact_job_outcome
from ..models import DecisionRecord, LedgerEvent, Memory, NamespacePolicy
from ..pii import assert_subject_not_erased
from ..receipt_signer import (
    ReceiptSignerConfigurationError,
    ReceiptSigningUnavailable,
    build_decision_receipt_with_signer,
    get_receipt_signer,
)
from ..recorder_service import index_recorder_evidence_for_decision
from ..schemas import (
    DecisionCreate,
    DecisionImpactItem,
    DecisionOut,
    DecisionReceiptVerifyRequest,
    DecisionReview,
    DecisionReviewHistoryResult,
    LedgerEventCreate,
    LedgerEventOut,
)
from ..subject_privacy import replace_subject_identifier, subject_reference
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/decisions", tags=["decisions"])
records_router = APIRouter(prefix="/v1/records", tags=["records"])
receipts_router = APIRouter(prefix="/v1/receipts", tags=["receipts"])

_LEDGER_EVENT_CREATE_OPERATION = "ledger_event.create"
_DECISION_CREATE_OPERATION = "decision.create"
_DECISION_REVIEW_OPERATION = "decision.review"
_MAX_IMPACT_LINKS_PER_DECISION = 2_000
_MAX_IMPACT_LINKS_PER_PAGE = 50_000
_RECEIPT_EVIDENCE_GRAPH_LIMIT = 10_000


def _require_paired_cursor(
    cursor_time: datetime | None,
    cursor_id: UUID | None,
    *,
    time_name: str,
) -> None:
    if (cursor_time is None) != (cursor_id is None):
        raise HTTPException(
            status_code=422,
            detail=f"{time_name} and before_id must be supplied together",
        )


def _descending_cursor(column, id_column, cursor_time: datetime, cursor_id: UUID):
    return or_(
        column < cursor_time,
        and_(column == cursor_time, id_column < cursor_id),
    )


def _set_compatibility_list_headers(
    response: Response,
    *,
    total: int,
    limit: int,
    returned: int,
    has_more: bool,
    cursor_supplied: bool,
    next_cursor: dict[str, str] | None,
) -> None:
    """Add traversal truth without changing the legacy JSON-array body."""

    response.headers["X-Lians-Total-Count"] = str(total)
    response.headers["X-Lians-Page-Limit"] = str(limit)
    response.headers["X-Lians-Page-Returned"] = str(returned)
    response.headers["X-Lians-Has-More"] = str(has_more).lower()
    # Page-Complete means there is no page after this cursor. Collection-
    # Complete is stricter: this one compatibility array contains the whole
    # filtered collection, which is only possible on an un-cursored first page.
    response.headers["X-Lians-Page-Complete"] = str(not has_more).lower()
    response.headers["X-Lians-Collection-Complete"] = str(
        not cursor_supplied and not has_more and total == returned
    ).lower()
    if has_more and next_cursor:
        for name, value in next_cursor.items():
            header_name = "-".join(part.capitalize() for part in name.split("_"))
            response.headers[f"X-Lians-Next-{header_name}"] = value


def _idempotency_request(req, auth: AuthContext, **route: object) -> dict:
    return {
        "body": req,
        "route": route,
        "barrier_group": auth.barrier_group,
        "principal_id": auth.principal_id,
        "auth_method": auth.auth_method,
    }


def _raise_idempotency_error(exc: Exception) -> None:
    if isinstance(exc, (InvalidIdempotencyKey, InvalidIdempotencyRequest)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, (IdempotencyConflict, IdempotencyReplayUnavailable)):
        if isinstance(exc, IdempotencyReplayUnavailable):
            from ..metrics import record_idempotency_outcome

            record_idempotency_outcome("replay_unavailable")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def _one_replay_id(claim: OperationClaim, expected_kind: str) -> UUID:
    if (
        claim.replay is None
        or claim.replay.resource_kind != expected_kind
        or claim.replay.response_status != 200
    ):
        raise IdempotencyReplayUnavailable(
            "The committed idempotency result has an unexpected resource kind"
        )
    ids = claim.resource_ids
    if len(ids) != 1:
        raise IdempotencyReplayUnavailable(
            "The committed idempotency result has invalid cardinality"
        )
    return ids[0]


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


def _utc(value: datetime) -> datetime:
    """Normalize API datetimes; naive values follow the existing UTC convention."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _out(row: DecisionRecord) -> DecisionOut:
    return DecisionOut(
        id=row.id,
        namespace=row.namespace,
        agent_id=row.agent_id,
        recorded_by_principal_ref=row.recorded_by_principal_ref,
        recorded_by_auth_method=row.recorded_by_auth_method,
        recorded_by_credential_ref=row.recorded_by_credential_ref,
        recorded_by_principal_type=row.recorded_by_principal_type,
        recorded_by_role=row.recorded_by_role,
        recorded_by_scopes=list(row.recorded_by_scopes or []),
        decision_type=row.decision_type,
        outcome=row.outcome,
        reason_codes=list(row.reason_codes or []),
        regime=row.regime,
        subject_id=row.subject_id,
        session_id=row.session_id,
        model_id=row.model_id,
        model_version=row.model_version,
        policy_version=row.policy_version,
        decided_at=_utc(row.decided_at),
        recorded_at=_utc(row.recorded_at),
        knowledge_as_of=_utc(row.knowledge_as_of),
        knowledge_recorded_as_of=_utc(
            getattr(row, "knowledge_recorded_as_of", None) or row.recorded_at
        ),
        evidence_memory_ids=[UUID(str(x)) for x in (row.evidence_memory_ids or [])],
        input_hash=row.input_hash,
        output_hash=row.output_hash,
        human_review_status=row.human_review_status,
        human_reviewer=row.human_reviewer,
        human_reviewed_at=(
            _utc(row.human_reviewed_at) if row.human_reviewed_at is not None else None
        ),
        supersedes_id=row.supersedes_id,
        metadata=dict(row.metadata_ or {}),
        record_hash_version=row.record_hash_version,
        record_integrity_status=row.record_integrity_status,
        record_hash=row.record_hash,
    )


def _event_out(row: LedgerEvent) -> LedgerEventOut:
    return LedgerEventOut(
        id=row.id,
        namespace=row.namespace,
        event_type=row.event_type,
        agent_id=row.agent_id,
        occurred_at=_utc(row.occurred_at),
        recorded_at=_utc(row.recorded_at),
        subject_id=row.subject_id,
        session_id=row.session_id,
        decision_id=row.decision_id,
        model_id=row.model_id,
        model_version=row.model_version,
        payload=dict(row.payload or {}),
        artifact_hash=row.artifact_hash,
        event_hash=row.event_hash,
    )


def _contains_exact(value: str, node) -> bool:
    """Return True when a nested metadata value exactly references a dependency."""
    if isinstance(node, dict):
        return any(_contains_exact(value, item) for item in node.values())
    if isinstance(node, (list, tuple, set)):
        return any(_contains_exact(value, item) for item in node)
    return node is not None and str(node).casefold() == value.casefold()


def _impact_risk(
    row: DecisionRecord,
    change_type: str,
    direct: bool,
    evidence_risk: list[dict] | None = None,
) -> tuple[int, str]:
    metadata = dict(row.metadata_ or {})
    risk_records = [metadata, *(evidence_risk or [])]
    declared_scores: list[int] = []
    levels = {"critical": 88, "high": 74, "medium": 55, "low": 35}
    for risk in risk_records:
        declared = str(
            risk.get("risk_level") or risk.get("criticality") or risk.get("risk_tier") or ""
        ).casefold()
        if declared in levels:
            declared_scores.append(levels[declared])
        raw_score = risk.get("risk_score")
        if isinstance(raw_score, (int, float)):
            declared_scores.append(max(0, min(100, int(raw_score))))
    score = max(declared_scores, default=50)
    if row.human_review_status not in {"affirmed", "overturned", "withdrawn"}:
        score += 10
    if change_type in {"revoked", "recalled", "corrupted", "erased"}:
        score += 10
    if direct:
        score += 5
    score = min(100, score)
    priority = (
        "critical"
        if score >= 85
        else "high"
        if score >= 70
        else "medium"
        if score >= 45
        else "low"
    )
    return score, priority


async def _decision_boundary(
    row: DecisionRecord,
    auth: AuthContext,
    db: AsyncSession,
    *,
    include_content: bool,
    byte_limit: int,
    reserved_bytes: int,
    serialization_multiplier: int,
    capacity_code: str,
):
    recorded_as_of = _utc(
        getattr(row, "knowledge_recorded_as_of", None) or row.recorded_at
    )
    snapshot_limit = 10_000
    snapshot_total, snapshot_bytes = await measure_knowledge_snapshot_bytes(
        db,
        auth.namespace,
        row.agent_id,
        row.knowledge_as_of,
        include_content=include_content,
        barrier_override=auth.barrier_group,
        recorded_as_of=recorded_as_of,
    )
    if snapshot_total > snapshot_limit:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "knowledge_snapshot_requires_paged_export",
                "message": (
                    "A receipt or evidence pack cannot claim complete reconstruction "
                    "when its bounded snapshot would be partial"
                ),
                "snapshot_total": snapshot_total,
                "snapshot_limit": snapshot_limit,
                "snapshot_endpoint": "/v1/snapshot",
            },
        )
    effective_snapshot_bytes = snapshot_bytes * serialization_multiplier
    combined_estimate = reserved_bytes + effective_snapshot_bytes
    if combined_estimate > byte_limit:
        raise HTTPException(
            status_code=413,
            detail={
                "code": capacity_code,
                "message": "The complete decision export exceeds its byte budget",
                "export_mode": "content" if include_content else "hash_only",
                "estimated_bytes": combined_estimate,
                "byte_limit": byte_limit,
                "snapshot_total": snapshot_total,
                "snapshot_endpoint": "/v1/snapshot",
            },
        )
    snapshot = await get_knowledge_snapshot(
        db,
        auth.namespace,
        row.agent_id,
        row.knowledge_as_of,
        snapshot_limit,
        barrier_override=auth.barrier_group,
        recorded_as_of=recorded_as_of,
        include_content=include_content,
    )
    evidence_ids = {str(x) for x in (row.evidence_memory_ids or [])}
    cited = [item for item in snapshot if str(item.id) in evidence_ids] if evidence_ids else []
    return snapshot, cited, recorded_as_of, effective_snapshot_bytes


def _portable_artifact_metadata(row: EvidenceArtifact) -> dict[str, object]:
    """Return a closed, content-free subset useful to independent verifiers."""

    allowed = {
        "memory_id",
        "source",
        "protocol",
        "event_kind",
        "hash_role",
        "tool_name",
        "tool_call_id",
        "provider",
    }
    result: dict[str, object] = {}
    for key, value in dict(row.metadata_ or {}).items():
        if key not in allowed or value is None:
            continue
        if isinstance(value, (bool, int)):
            result[key] = value
        elif isinstance(value, str) and len(value) <= 2_048:
            result[key] = value
    return result


async def _receipt_evidence_graph_manifest(
    row: DecisionRecord,
    auth: AuthContext,
    db: AsyncSession,
    *,
    byte_limit: int,
    reserved_bytes: int,
    capacity_code: str,
) -> tuple[dict[str, object], int]:
    """Freeze one complete, registration-bounded normalized graph manifest."""

    # Read coverage first.  If an indexer commits between this read and the
    # registration watermark below, the receipt remains conservatively partial
    # instead of claiming completeness for a graph state it did not observe.
    coverage = await get_decision_coverage(db, row)
    visible_filters = [
        DecisionEvidenceLink.namespace == auth.namespace,
        DecisionEvidenceLink.decision_id == row.id,
        EvidenceArtifact.namespace == auth.namespace,
    ]
    _apply_barrier_filter(visible_filters, DecisionEvidenceLink.barrier_group, auth)
    _apply_barrier_filter(visible_filters, EvidenceArtifact.barrier_group, auth)
    snapshot_filters = [
        *visible_filters,
        DecisionEvidenceLinkRegistration.namespace == auth.namespace,
    ]
    _apply_barrier_filter(
        snapshot_filters,
        DecisionEvidenceLinkRegistration.barrier_group,
        auth,
    )
    snapshot_max_link_sequence = int(
        (
            await db.execute(
                select(func.max(DecisionEvidenceLinkRegistration.sequence))
                .select_from(DecisionEvidenceLinkRegistration)
                .join(
                    DecisionEvidenceLink,
                    DecisionEvidenceLink.id
                    == DecisionEvidenceLinkRegistration.link_id,
                )
                .join(
                    EvidenceArtifact,
                    EvidenceArtifact.id == DecisionEvidenceLink.artifact_id,
                )
                .where(*snapshot_filters)
            )
        ).scalar_one_or_none()
        or 0
    )
    filters = [
        *snapshot_filters,
        DecisionEvidenceLinkRegistration.sequence <= snapshot_max_link_sequence,
    ]
    relation_rows = (
        await db.execute(
            select(DecisionEvidenceLink.relation, func.count())
            .join(EvidenceArtifact, EvidenceArtifact.id == DecisionEvidenceLink.artifact_id)
            .join(
                DecisionEvidenceLinkRegistration,
                DecisionEvidenceLinkRegistration.link_id == DecisionEvidenceLink.id,
            )
            .where(*filters)
            .group_by(DecisionEvidenceLink.relation)
        )
    ).all()
    relation_counts = {
        str(relation): int(count or 0) for relation, count in relation_rows
    }
    if set(relation_counts) - {"direct", "reachable"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "evidence_graph_integrity_failed"},
        )
    links_total = sum(relation_counts.values())
    if links_total > _RECEIPT_EVIDENCE_GRAPH_LIMIT:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "evidence_graph_requires_paged_export",
                "message": (
                    "A receipt cannot claim a complete normalized evidence graph "
                    "when its bounded manifest would be partial"
                ),
                "links_total": links_total,
                "manifest_limit": _RECEIPT_EVIDENCE_GRAPH_LIMIT,
                "graph_endpoint": f"/v1/decisions/{row.id}/evidence-graph",
            },
        )
    row_bytes = (
        literal(2_048)
        + 4 * func.coalesce(func.length(DecisionEvidenceLink.relation), 0)
        + 4
        * func.coalesce(
            func.length(cast(DecisionEvidenceLink.match_basis, Text)),
            0,
        )
        + 4 * func.coalesce(func.length(EvidenceArtifact.kind), 0)
        + 4 * func.coalesce(func.length(EvidenceArtifact.identifier), 0)
        + 4 * func.coalesce(func.length(EvidenceArtifact.version), 0)
        + 4 * func.coalesce(func.length(EvidenceArtifact.coordinate), 0)
        + 4 * func.coalesce(func.length(EvidenceArtifact.hash_algorithm), 0)
        + 4 * func.coalesce(func.length(EvidenceArtifact.artifact_hash), 0)
        + 4 * func.coalesce(func.length(EvidenceArtifact.identity_hash), 0)
        + 4
        * func.coalesce(
            func.length(cast(EvidenceArtifact.metadata_, Text)),
            0,
        )
    )
    bounded_bytes = (
        select(row_bytes.label("estimated_bytes"))
        .join(EvidenceArtifact, EvidenceArtifact.id == DecisionEvidenceLink.artifact_id)
        .join(
            DecisionEvidenceLinkRegistration,
            DecisionEvidenceLinkRegistration.link_id == DecisionEvidenceLink.id,
        )
        .where(*filters)
        .order_by(DecisionEvidenceLink.relation, DecisionEvidenceLink.id)
        .limit(_RECEIPT_EVIDENCE_GRAPH_LIMIT + 1)
        .subquery()
    )
    manifest_estimated_bytes = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(bounded_bytes.c.estimated_bytes), 0))
                .select_from(bounded_bytes)
            )
        ).scalar_one()
        or 0
    )
    if reserved_bytes + manifest_estimated_bytes > byte_limit:
        raise HTTPException(
            status_code=413,
            detail={
                "code": capacity_code,
                "message": "The complete decision export exceeds its byte budget",
                "estimated_bytes": reserved_bytes + manifest_estimated_bytes,
                "byte_limit": byte_limit,
                "links_total": links_total,
                "graph_endpoint": f"/v1/decisions/{row.id}/evidence-graph",
            },
        )
    graph_rows = (
        await db.execute(
            select(DecisionEvidenceLink, EvidenceArtifact)
            .join(EvidenceArtifact, EvidenceArtifact.id == DecisionEvidenceLink.artifact_id)
            .join(
                DecisionEvidenceLinkRegistration,
                DecisionEvidenceLinkRegistration.link_id == DecisionEvidenceLink.id,
            )
            .where(*filters)
            .order_by(DecisionEvidenceLink.relation, DecisionEvidenceLink.id)
            .limit(_RECEIPT_EVIDENCE_GRAPH_LIMIT + 1)
        )
    ).all()
    if len(graph_rows) > _RECEIPT_EVIDENCE_GRAPH_LIMIT:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "evidence_graph_requires_paged_export",
                "links_total": max(links_total, len(graph_rows)),
                "manifest_limit": _RECEIPT_EVIDENCE_GRAPH_LIMIT,
                "graph_endpoint": f"/v1/decisions/{row.id}/evidence-graph",
            },
        )
    if len(graph_rows) != links_total:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "evidence_graph_snapshot_changed",
                "message": "The evidence graph changed while its receipt snapshot was read",
            },
        )
    visible_links_total = int(
        (
            await db.execute(
                select(func.count(DecisionEvidenceLink.id))
                .join(
                    EvidenceArtifact,
                    EvidenceArtifact.id == DecisionEvidenceLink.artifact_id,
                )
                .where(*visible_filters)
            )
        ).scalar_one()
        or 0
    )
    if visible_links_total != links_total:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "evidence_graph_registration_incomplete",
                "message": (
                    "The evidence graph cannot be exported until every visible link "
                    "belongs to the fixed registration snapshot"
                ),
            },
        )
    entries = [
        {
            "link_id": str(link.id),
            "relation": link.relation,
            "match_basis": list(link.match_basis or []),
            "artifact": {
                "id": str(artifact.id),
                "kind": artifact.kind,
                "identifier": artifact.identifier,
                "version": artifact.version,
                "hash_algorithm": artifact.hash_algorithm,
                "artifact_hash": artifact.artifact_hash,
                "identity_hash": artifact.identity_hash,
                "recorded_at": _utc(artifact.recorded_at).isoformat(),
                "metadata": _portable_artifact_metadata(artifact),
            },
        }
        for link, artifact in graph_rows
    ]
    artifacts_total = len({str(artifact.id) for _, artifact in graph_rows})
    manifest_core = {
        "schema": "lians.evidence-graph-manifest.v1",
        "decision_id": str(row.id),
        "snapshot_max_link_sequence": snapshot_max_link_sequence,
        "entries": entries,
        "links_total": links_total,
        "artifacts_total": artifacts_total,
        "direct_count": relation_counts.get("direct", 0),
        "reachable_count": relation_counts.get("reachable", 0),
        "complete": True,
        "normalization": coverage.model_dump(mode="json"),
    }
    manifest = {**manifest_core, "manifest_hash": sha256_hex(manifest_core)}
    actual_bytes = len(_canonical(manifest).encode("utf-8"))
    manifest_bytes = max(manifest_estimated_bytes, actual_bytes)
    if reserved_bytes + manifest_bytes > byte_limit:
        raise HTTPException(
            status_code=413,
            detail={
                "code": capacity_code,
                "message": "The complete decision export exceeds its byte budget",
                "estimated_bytes": reserved_bytes + manifest_bytes,
                "byte_limit": byte_limit,
                "links_total": links_total,
                "graph_endpoint": f"/v1/decisions/{row.id}/evidence-graph",
            },
        )
    return manifest, manifest_bytes


async def _verified_chain(
    auth: AuthContext,
    db: AsyncSession,
    verify: bool,
    *,
    max_bytes: int,
):
    if verify and auth.barrier_group is None:
        try:
            return await verify_chain(
                db,
                auth.namespace,
                max_response_bytes=max_bytes,
            )
        except AuditCapacityExceeded as exc:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": exc.code,
                    "message": exc.public_message,
                    "estimated_bytes": exc.estimated_bytes,
                    "byte_limit": exc.byte_limit,
                },
            ) from exc
    if verify:
        return {
            "status": "unavailable_for_barrier_scoped_export",
            "rows_checked": 0,
            "violations": [],
        }
    return {"status": "unchecked", "rows_checked": 0, "violations": []}


async def _require_decision_integrity(
    db: AsyncSession,
    row: DecisionRecord,
) -> None:
    try:
        await assert_decision_record_integrity(db, row)
    except DecisionRecordIntegrityError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "decision_record_integrity_verification_failed",
                "message": "Decision record failed authenticated integrity verification",
            },
        ) from exc


@records_router.post("/events", response_model=LedgerEventOut)
async def record_event(
    req: LedgerEventCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
):
    """Append an inference, oversight, change, subject, incident, or memory event."""
    auth.require("write")
    try:
        async with operation_claim(
            db,
            namespace=auth.namespace,
            operation=_LEDGER_EVENT_CREATE_OPERATION,
            key=idempotency_key,
            request=_idempotency_request(req, auth),
        ) as claim:
            if claim.is_replay:
                event_id = _one_replay_id(claim, "ledger_event")
                replay = await db.get(LedgerEvent, event_id)
                if (
                    replay is None
                    or replay.namespace != auth.namespace
                    or not _barrier_visible(replay, auth)
                ):
                    raise IdempotencyReplayUnavailable(
                        "The committed ledger-event result is unavailable"
                    )
                claim.replay_served()
                return _event_out(replay)

            row = await _record_event_mutation(req, auth, db)
            response = _event_out(row)
            await claim.complete_and_commit(
                resource_kind="ledger_event",
                resource_ids=[row.id],
                response_status=200,
            )
            return response
    except (
        InvalidIdempotencyKey,
        InvalidIdempotencyRequest,
        IdempotencyConflict,
        IdempotencyReplayUnavailable,
    ) as exc:
        _raise_idempotency_error(exc)


async def _record_event_mutation(
    req: LedgerEventCreate,
    auth: AuthContext,
    db: AsyncSession,
) -> LedgerEvent:
    if req.decision_id:
        decision = await db.get(DecisionRecord, req.decision_id)
        if (
            decision is None
            or decision.namespace != auth.namespace
            or not _barrier_visible(decision, auth)
        ):
            raise HTTPException(422, "decision_id does not belong to this namespace")
    raw_subject_id = req.subject_id
    persisted_subject_ref = (
        await assert_subject_not_erased(db, raw_subject_id, auth.namespace)
        if raw_subject_id
        else None
    )
    payload = req.payload
    if raw_subject_id and persisted_subject_ref:
        payload = replace_subject_identifier(
            payload, raw_subject_id, persisted_subject_ref
        )
    recorded_at = datetime.now(timezone.utc)
    body = req.model_dump(mode="json") | {
        "namespace": auth.namespace,
        "recorded_at": recorded_at.isoformat(),
        "subject_id": persisted_subject_ref,
        "payload": payload,
    }
    event_hash = hashlib.sha256(_canonical(body).encode()).hexdigest()
    row = LedgerEvent(
        namespace=auth.namespace,
        event_type=req.event_type,
        agent_id=req.agent_id,
        barrier_group=auth.barrier_group,
        occurred_at=req.occurred_at,
        recorded_at=recorded_at,
        subject_id=persisted_subject_ref,
        session_id=req.session_id,
        decision_id=req.decision_id,
        model_id=req.model_id,
        model_version=req.model_version,
        payload=payload,
        artifact_hash=req.artifact_hash,
        event_hash=event_hash,
    )
    db.add(row)
    await db.flush()
    await chain_log(
        db,
        auth.namespace,
        req.agent_id,
        f"record_{req.event_type}",
        content_hash=event_hash,
        payload={"record_id": str(row.id), "event_type": req.event_type},
    )
    return row


@records_router.get("/events", response_model=list[LedgerEventOut])
async def list_events(
    response: Response,
    event_type: str | None = None,
    agent_id: str | None = None,
    decision_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=1000),
    before_occurred_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    _require_paired_cursor(
        before_occurred_at,
        before_id,
        time_name="before_occurred_at",
    )
    filters = [LedgerEvent.namespace == auth.namespace]
    _apply_barrier_filter(filters, LedgerEvent.barrier_group, auth)
    if event_type:
        filters.append(LedgerEvent.event_type == event_type)
    if agent_id:
        filters.append(LedgerEvent.agent_id == agent_id)
    if decision_id:
        filters.append(LedgerEvent.decision_id == decision_id)
    page_filters = list(filters)
    if before_occurred_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor(
                LedgerEvent.occurred_at,
                LedgerEvent.id,
                _utc(before_occurred_at),
                before_id,
            )
        )
    rows = (
        (
            await db.execute(
                select(LedgerEvent)
                .where(*page_filters)
                .order_by(LedgerEvent.occurred_at.desc(), LedgerEvent.id.desc())
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    page = rows[:limit]
    has_more = len(rows) > limit
    # Count after reading the immutable page so concurrent appends cannot make
    # an uncursored response claim completeness with total < returned.
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(LedgerEvent).where(*filters)
            )
        ).scalar_one()
    )
    next_cursor = None
    if has_more and page:
        next_cursor = {
            "before_occurred_at": page[-1].occurred_at.isoformat(),
            "before_id": str(page[-1].id),
        }
    _set_compatibility_list_headers(
        response,
        total=total,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        cursor_supplied=before_occurred_at is not None,
        next_cursor=next_cursor,
    )
    return [_event_out(row) for row in page]


@router.post(
    "",
    response_model=DecisionOut,
    responses={
        413: {
            "description": (
                "The complete normalized evidence candidate set exceeds the "
                "configured atomic row or byte budget; nothing is committed"
            )
        }
    },
)
async def create_decision(
    req: DecisionCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
):
    """Append an authoritative record of a consequential agent decision."""
    auth.require("write")
    try:
        async with operation_claim(
            db,
            namespace=auth.namespace,
            operation=_DECISION_CREATE_OPERATION,
            key=idempotency_key,
            request=_idempotency_request(req, auth),
        ) as claim:
            if claim.is_replay:
                decision_id = _one_replay_id(claim, "decision")
                replay = await db.get(DecisionRecord, decision_id)
                if (
                    replay is None
                    or replay.namespace != auth.namespace
                    or not _barrier_visible(replay, auth)
                ):
                    raise IdempotencyReplayUnavailable(
                        "The committed decision result is unavailable"
                    )
                await _require_decision_integrity(db, replay)
                claim.replay_served()
                # Decision create returns the append-time projection even if a
                # later review changed the mutable compatibility fields.
                return _out(replay).model_copy(
                    update={
                        "human_review_status": "not_requested",
                        "human_reviewer": None,
                        "human_reviewed_at": None,
                    }
                )

            row = await _create_decision_mutation(req, auth, db)
            response = _out(row).model_copy(
                update={
                    "human_review_status": "not_requested",
                    "human_reviewer": None,
                    "human_reviewed_at": None,
                }
            )
            await claim.complete_and_commit(
                resource_kind="decision",
                resource_ids=[row.id],
                response_status=200,
            )
            return response
    except (
        InvalidIdempotencyKey,
        InvalidIdempotencyRequest,
        IdempotencyConflict,
        IdempotencyReplayUnavailable,
    ) as exc:
        _raise_idempotency_error(exc)


async def _create_decision_mutation(
    req: DecisionCreate,
    auth: AuthContext,
    db: AsyncSession,
) -> DecisionRecord:
    raw_subject_id = req.subject_id
    persisted_subject_ref = (
        await assert_subject_not_erased(db, raw_subject_id, auth.namespace)
        if raw_subject_id
        else None
    )
    try:
        principal_ref, auth_method, credential_ref = authenticated_recorder_provenance(
            principal_ref=auth.principal_id,
            auth_method=auth.auth_method,
            credential_id=auth.credential_id,
        )
        principal_type, role, scopes = authenticated_recorder_authorization_snapshot(
            principal_type=auth.principal_type,
            role=auth.role,
            effective_scopes=auth.scopes,
        )
    except DecisionRecordIntegrityError as exc:
        raise HTTPException(401, "Authenticated recorder provenance is required") from exc
    await reserve_namespace_usage(
        db,
        namespace=auth.namespace,
        decision_records=1,
        estimated_ingest_bytes=estimate_ingest_bytes(req),
    )
    decided_at = _utc(req.decided_at)
    as_of = _utc(req.knowledge_as_of or decided_at)
    ids = [str(x) for x in req.evidence_memory_ids]
    evidence_rows: list[Memory] = []
    if ids:
        evidence_filters = [
            Memory.namespace == auth.namespace,
            Memory.id.in_(req.evidence_memory_ids),
        ]
        _apply_barrier_filter(evidence_filters, Memory.barrier_group, auth)
        evidence_rows = (
            (
                await db.execute(
                    select(Memory)
                    .options(
                        load_only(
                            Memory.id,
                            Memory.source,
                            Memory.content_hash,
                            Memory.metadata_,
                            Memory.barrier_group,
                        )
                    )
                    .where(*evidence_filters)
                )
            )
            .scalars()
            .all()
        )
        if {memory.id for memory in evidence_rows} != set(req.evidence_memory_ids):
            raise HTTPException(
                422, "One or more evidence_memory_ids do not belong to this namespace"
            )
    if req.supersedes_id:
        prior = await db.get(DecisionRecord, req.supersedes_id)
        if prior is None or prior.namespace != auth.namespace or not _barrier_visible(prior, auth):
            raise HTTPException(422, "supersedes_id does not belong to this namespace")

    recorded_at = datetime.now(timezone.utc)
    if (
        req.knowledge_recorded_as_of is not None
        and _utc(req.knowledge_recorded_as_of) > recorded_at
    ):
        raise HTTPException(
            422,
            "knowledge_recorded_as_of cannot be later than recorded_at",
        )
    recorded_as_of = (
        _utc(req.knowledge_recorded_as_of)
        if req.knowledge_recorded_as_of is not None
        else recorded_at
    )
    outcome = req.outcome
    reason_codes = req.reason_codes
    metadata = req.metadata
    if raw_subject_id and persisted_subject_ref:
        outcome = replace_subject_identifier(
            outcome, raw_subject_id, persisted_subject_ref
        )
        reason_codes = replace_subject_identifier(
            reason_codes, raw_subject_id, persisted_subject_ref
        )
        metadata = replace_subject_identifier(
            metadata, raw_subject_id, persisted_subject_ref
        )
    row = DecisionRecord(
        id=uuid4(),
        namespace=auth.namespace,
        agent_id=req.agent_id,
        recorded_by_principal_ref=principal_ref,
        recorded_by_auth_method=auth_method,
        recorded_by_credential_ref=credential_ref,
        recorded_by_principal_type=principal_type,
        recorded_by_role=role,
        recorded_by_scopes=scopes,
        barrier_group=auth.barrier_group,
        decision_type=req.decision_type,
        outcome=outcome,
        reason_codes=reason_codes,
        regime=req.regime,
        subject_id=persisted_subject_ref,
        session_id=req.session_id,
        model_id=req.model_id,
        model_version=req.model_version,
        policy_version=req.policy_version,
        decided_at=decided_at,
        recorded_at=recorded_at,
        knowledge_as_of=as_of,
        knowledge_recorded_as_of=recorded_as_of,
        evidence_memory_ids=ids,
        input_hash=req.input_hash,
        output_hash=req.output_hash,
        supersedes_id=req.supersedes_id,
        metadata_=metadata,
        record_hash_version=DECISION_RECORD_HASH_VERSION,
        record_integrity_status=VERIFIED_INTEGRITY_STATUS,
        record_hash="",
    )
    row.record_hash = compute_decision_record_hash(row)
    try:
        evidence_candidate_plan = decision_artifact_specs(row, evidence_rows)
    except DecisionEvidenceCapacityExceeded as exc:
        from ..metrics import record_decision_evidence_capacity_rejection

        record_decision_evidence_capacity_rejection(
            "create",
            count_exceeded=exc.candidate_count > exc.candidate_limit,
            bytes_exceeded=exc.candidate_bytes > exc.candidate_bytes_limit,
        )
        raise HTTPException(
            status_code=413,
            detail={
                "code": exc.code,
                "message": str(exc),
                "candidate_count_lower_bound": exc.candidate_count,
                "candidate_limit": exc.candidate_limit,
                "candidate_bytes_lower_bound": exc.candidate_bytes,
                "candidate_bytes_limit": exc.candidate_bytes_limit,
            },
        ) from exc
    db.add(row)
    await db.flush()
    await index_decision_evidence(
        db,
        row,
        evidence_rows,
        candidate_plan=evidence_candidate_plan,
    )
    await index_recorder_evidence_for_decision(db, row)
    await chain_log(
        db,
        auth.namespace,
        principal_ref,
        "decision_recorded",
        content_hash=row.record_hash,
        payload=decision_record_binding_payload(row),
    )
    # The authoritative record, integrity audit, idempotency completion, and
    # protected-unit obligation all commit together. Replays return before this
    # mutation path, while the stable decision identity is a second dedupe fence.
    await enqueue_authoritative_decision_usage_event(
        db,
        namespace=auth.namespace,
        decision_id=row.id,
        occurred_at=row.recorded_at,
    )
    return row


@router.get("", response_model=list[DecisionOut])
async def list_decisions(
    response: Response,
    agent_id: str | None = None,
    subject_id: str | None = None,
    regime: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    before_decided_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    _require_paired_cursor(
        before_decided_at,
        before_id,
        time_name="before_decided_at",
    )
    filters = [DecisionRecord.namespace == auth.namespace]
    _apply_barrier_filter(filters, DecisionRecord.barrier_group, auth)
    if agent_id:
        filters.append(DecisionRecord.agent_id == agent_id)
    if subject_id:
        filters.append(
            DecisionRecord.subject_id == subject_reference(auth.namespace, subject_id)
        )
    if regime:
        filters.append(DecisionRecord.regime == regime)
    page_filters = list(filters)
    if before_decided_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor(
                DecisionRecord.decided_at,
                DecisionRecord.id,
                _utc(before_decided_at),
                before_id,
            )
        )
    rows = (
        (
            await db.execute(
                select(DecisionRecord)
                .where(*page_filters)
                .order_by(DecisionRecord.decided_at.desc(), DecisionRecord.id.desc())
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    page = rows[:limit]
    has_more = len(rows) > limit
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(DecisionRecord).where(*filters)
            )
        ).scalar_one()
    )
    next_cursor = None
    if has_more and page:
        next_cursor = {
            "before_decided_at": page[-1].decided_at.isoformat(),
            "before_id": str(page[-1].id),
        }
    _set_compatibility_list_headers(
        response,
        total=total,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        cursor_supplied=before_decided_at is not None,
        next_cursor=next_cursor,
    )
    return [_out(row) for row in page]


@router.post("/evidence/artifacts", response_model=EvidenceArtifactOut, status_code=201)
async def create_evidence_artifact(
    req: EvidenceArtifactCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Register an immutable, version/hash-addressable evidence artifact."""
    auth.require("write")
    if (
        auth.barrier_group is not None
        and req.barrier_group is not None
        and req.barrier_group != auth.barrier_group
    ):
        raise HTTPException(403, "Cannot create evidence in another information barrier")
    barrier_group = auth.barrier_group or req.barrier_group
    artifact, created = await create_artifact_from_request(
        db,
        namespace=auth.namespace,
        barrier_group=barrier_group,
        req=req,
    )
    if created:
        await chain_log(
            db,
            auth.namespace,
            req.created_by_agent_id or "lians-evidence-api",
            "evidence_artifact_recorded",
            content_hash=artifact.artifact_hash or artifact.identity_hash,
            payload={
                "artifact_id": str(artifact.id),
                "kind": artifact.kind,
                "coordinate": artifact.coordinate,
            },
        )
    await db.commit()
    return artifact_out(artifact)


@router.get("/evidence/artifacts", response_model=list[EvidenceArtifactOut])
async def list_evidence_artifacts(
    response: Response,
    kind: EvidenceArtifactKind | None = None,
    identifier: str | None = Query(default=None, min_length=1, max_length=1024),
    version: str | None = Query(default=None, min_length=1, max_length=512),
    coordinate: str | None = Query(default=None, min_length=1, max_length=1537),
    artifact_hash: str | None = Query(default=None, min_length=1, max_length=256),
    limit: int = Query(default=100, ge=1, le=1000),
    before_recorded_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """List visible artifact nodes using exact indexed identity filters."""
    auth.require("read")
    _require_paired_cursor(
        before_recorded_at,
        before_id,
        time_name="before_recorded_at",
    )
    filters = [EvidenceArtifact.namespace == auth.namespace]
    _apply_barrier_filter(filters, EvidenceArtifact.barrier_group, auth)
    if kind:
        filters.append(EvidenceArtifact.kind == kind)
    if identifier:
        normalized = identifier.strip().casefold()
        filters.append(
            and_(
                EvidenceArtifact.identifier_lookup_hash
                == artifact_lookup_hash(normalized),
                EvidenceArtifact.identifier_normalized == normalized,
            )
        )
    if version:
        normalized = version.strip().casefold()
        filters.append(
            and_(
                EvidenceArtifact.version_lookup_hash == artifact_lookup_hash(normalized),
                EvidenceArtifact.version_normalized == normalized,
            )
        )
    if coordinate:
        normalized = coordinate.strip().casefold()
        filters.append(
            and_(
                EvidenceArtifact.coordinate_lookup_hash
                == artifact_lookup_hash(normalized),
                EvidenceArtifact.coordinate == normalized,
            )
        )
    if artifact_hash:
        raw_hash = artifact_hash.strip()
        filters.append(
            EvidenceArtifact.artifact_hash.in_({raw_hash, raw_hash.casefold()})
        )
    page_filters = list(filters)
    if before_recorded_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor(
                EvidenceArtifact.recorded_at,
                EvidenceArtifact.id,
                _utc(before_recorded_at),
                before_id,
            )
        )
    rows = (
        (
            await db.execute(
                select(EvidenceArtifact)
                .where(*page_filters)
                .order_by(
                    EvidenceArtifact.recorded_at.desc(),
                    EvidenceArtifact.id.desc(),
                )
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    page = rows[:limit]
    has_more = len(rows) > limit
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(EvidenceArtifact).where(*filters)
            )
        ).scalar_one()
    )
    next_cursor = None
    if has_more and page:
        next_cursor = {
            "before_recorded_at": page[-1].recorded_at.isoformat(),
            "before_id": str(page[-1].id),
        }
    _set_compatibility_list_headers(
        response,
        total=total,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        cursor_supplied=before_recorded_at is not None,
        next_cursor=next_cursor,
    )
    return [artifact_out(row) for row in page]


@router.get(
    "/evidence/artifacts/{artifact_id}",
    response_model=EvidenceArtifactOut,
)
async def get_evidence_artifact(
    artifact_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    artifact = await db.get(EvidenceArtifact, artifact_id)
    if (
        artifact is None
        or artifact.namespace != auth.namespace
        or not _barrier_visible(artifact, auth)
    ):
        raise HTTPException(404, "Evidence artifact not found")
    return artifact_out(artifact)


def _artifact_match_basis(artifact: EvidenceArtifact, value: str) -> list[str]:
    normalized = value.casefold()
    candidates = {
        "evidence_graph.artifact_id": str(artifact.id),
        "evidence_graph.identifier": artifact.identifier,
        "evidence_graph.version": artifact.version,
        "evidence_graph.coordinate": artifact.coordinate,
        "evidence_graph.artifact_hash": artifact.artifact_hash,
    }
    return [
        basis
        for basis, candidate in candidates.items()
        if candidate is not None and str(candidate).casefold() == normalized
    ]


async def _impact_link_budget(
    db: AsyncSession,
    grouped_counts,
) -> tuple[int, int]:
    """Return ``(largest decision, page total)`` without hydrating link rows."""
    counts = grouped_counts.subquery()
    row = (
        await db.execute(
            select(
                func.coalesce(func.max(counts.c.link_count), 0),
                func.coalesce(func.sum(counts.c.link_count), 0),
            ).select_from(counts)
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


async def _indexed_impact_matches(
    req: EvidenceDependencyChange,
    auth: AuthContext,
    db: AsyncSession,
) -> tuple[int, int, int, list[DecisionImpactItem]]:
    artifact_match = artifact_dependency_filter(
        req.dependency_kind, req.dependency_value
    )
    filters = [
        DecisionEvidenceLink.namespace == auth.namespace,
        EvidenceArtifact.namespace == auth.namespace,
        DecisionRecord.namespace == auth.namespace,
        *artifact_match,
    ]
    _apply_barrier_filter(filters, DecisionEvidenceLink.barrier_group, auth)
    _apply_barrier_filter(filters, EvidenceArtifact.barrier_group, auth)
    _apply_barrier_filter(filters, DecisionRecord.barrier_group, auth)
    grouped = (
        select(
            DecisionEvidenceLink.decision_id.label("decision_id"),
            func.max(
                case((DecisionEvidenceLink.relation == "direct", 1), else_=0)
            ).label("is_direct"),
            func.max(func.coalesce(DecisionEvidenceLink.risk_score, 50)).label(
                "evidence_risk_score"
            ),
        )
        .join(EvidenceArtifact, EvidenceArtifact.id == DecisionEvidenceLink.artifact_id)
        .join(DecisionRecord, DecisionRecord.id == DecisionEvidenceLink.decision_id)
        .where(*filters)
        .group_by(DecisionEvidenceLink.decision_id)
        .subquery()
    )
    count_row = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(grouped.c.is_direct), 0),
            ).select_from(grouped)
        )
    ).one()
    total = int(count_row[0] or 0)
    direct_count = int(count_row[1] or 0)
    reachable_count = total - direct_count
    if total == 0:
        return 0, 0, 0, []

    pending_review_boost = case(
        (
            ~DecisionRecord.human_review_status.in_(
                ["affirmed", "overturned", "withdrawn"]
            ),
            10,
        ),
        else_=0,
    )
    ranking_score = (
        grouped.c.evidence_risk_score
        + grouped.c.is_direct * 5
        + pending_review_boost
    )
    decision_rows = (
        await db.execute(
            select(
                DecisionRecord,
                grouped.c.is_direct,
                grouped.c.evidence_risk_score,
            )
            .join(grouped, grouped.c.decision_id == DecisionRecord.id)
            .order_by(ranking_score.desc(), DecisionRecord.decided_at.desc())
            .limit(req.limit)
        )
    ).all()
    decision_ids = [row.id for row, _, _ in decision_rows]
    edge_filters = [
        DecisionEvidenceLink.namespace == auth.namespace,
        DecisionEvidenceLink.decision_id.in_(decision_ids),
        EvidenceArtifact.namespace == auth.namespace,
        *artifact_match,
    ]
    _apply_barrier_filter(edge_filters, DecisionEvidenceLink.barrier_group, auth)
    _apply_barrier_filter(edge_filters, EvidenceArtifact.barrier_group, auth)
    largest_decision, link_total = await _impact_link_budget(
        db,
        select(
            DecisionEvidenceLink.decision_id,
            func.count().label("link_count"),
        )
        .join(EvidenceArtifact, EvidenceArtifact.id == DecisionEvidenceLink.artifact_id)
        .where(*edge_filters)
        .group_by(DecisionEvidenceLink.decision_id),
    )
    if (
        largest_decision > _MAX_IMPACT_LINKS_PER_DECISION
        or link_total > _MAX_IMPACT_LINKS_PER_PAGE
    ):
        raise HTTPException(
            status_code=413,
            detail={
                "code": "impact_link_hydration_limit_exceeded",
                "links": link_total,
                "largest_decision_links": largest_decision,
                "max_links_per_decision": _MAX_IMPACT_LINKS_PER_DECISION,
                "max_links_per_page": _MAX_IMPACT_LINKS_PER_PAGE,
            },
        )
    edge_rows = (
        await db.execute(
            select(DecisionEvidenceLink, EvidenceArtifact)
            .join(EvidenceArtifact, EvidenceArtifact.id == DecisionEvidenceLink.artifact_id)
            .where(*edge_filters)
            .limit(_MAX_IMPACT_LINKS_PER_PAGE + 1)
        )
    ).all()
    if len(edge_rows) > _MAX_IMPACT_LINKS_PER_PAGE:
        raise HTTPException(
            status_code=413,
            detail={"code": "impact_link_hydration_limit_exceeded"},
        )
    edges_by_decision: dict[UUID, list[tuple[DecisionEvidenceLink, EvidenceArtifact]]] = {}
    for link, artifact in edge_rows:
        edges_by_decision.setdefault(link.decision_id, []).append((link, artifact))

    items: list[DecisionImpactItem] = []
    for decision, is_direct, _ in decision_rows:
        edges = edges_by_decision.get(decision.id, [])
        basis: set[str] = set()
        for link, artifact in edges:
            basis.update(link.match_basis or [])
            basis.update(_artifact_match_basis(artifact, req.dependency_value))
        risk_score, priority = _impact_risk(
            decision,
            req.change_type,
            bool(is_direct),
            [dict(link.risk_metadata or {}) for link, _ in edges]
            + [dict(artifact.risk_metadata or {}) for _, artifact in edges],
        )
        items.append(
            DecisionImpactItem(
                decision=_out(decision),
                match_basis=sorted(basis)[:100],
                impact_status="direct_reference" if is_direct else "reachable",
                risk_score=risk_score,
                priority=priority,
            )
        )
    return total, direct_count, reachable_count, items


async def _legacy_impact_matches(
    rows: list[DecisionRecord],
    req: EvidenceDependencyChange,
    auth: AuthContext,
    db: AsyncSession,
    *,
    memory_recorded_as_of: datetime | None = None,
    source_reference_limit: int | None = None,
) -> tuple[list[DecisionImpactItem], bool]:
    all_evidence_ids: list[str] = []
    seen_evidence_ids: set[str] = set()
    source_references_truncated = False
    if req.dependency_kind == "source":
        for row in rows:
            for memory_id in row.evidence_memory_ids or []:
                normalized_id = str(memory_id)
                if normalized_id in seen_evidence_ids:
                    continue
                if (
                    source_reference_limit is not None
                    and len(all_evidence_ids) >= source_reference_limit
                ):
                    source_references_truncated = True
                    continue
                seen_evidence_ids.add(normalized_id)
                all_evidence_ids.append(normalized_id)
    memory_uuids: list[UUID] = []
    for memory_id in all_evidence_ids:
        try:
            memory_uuids.append(UUID(memory_id))
        except ValueError:
            continue
    memory_rows: list[Memory] = []
    if memory_uuids:
        memory_batch_size = 500 if db.get_bind().dialect.name == "sqlite" else 5_000
        for offset in range(0, len(memory_uuids), memory_batch_size):
            memory_filters = [
                Memory.namespace == auth.namespace,
                Memory.id.in_(
                    memory_uuids[offset : offset + memory_batch_size]
                ),
            ]
            if memory_recorded_as_of is not None:
                memory_filters.append(
                    Memory.system_valid_from <= memory_recorded_as_of
                )
            _apply_barrier_filter(memory_filters, Memory.barrier_group, auth)
            memory_rows.extend(
                (
                    (await db.execute(select(Memory).where(*memory_filters)))
                    .scalars()
                    .all()
                )
            )
    memories = {str(memory.id): memory for memory in memory_rows}

    matches: list[DecisionImpactItem] = []
    value = req.dependency_value
    for row in rows:
        metadata = dict(row.metadata_ or {})
        basis: list[str] = []
        status = "direct_reference"
        if req.dependency_kind == "source":
            for memory_id in row.evidence_memory_ids or []:
                memory_id_string = str(memory_id)
                if memory_id_string.casefold() == value.casefold():
                    basis.append("legacy.decision.evidence_memory_ids")
                memory = memories.get(memory_id_string)
                if memory is None:
                    continue
                source_version = (memory.metadata_ or {}).get("source_version")
                if memory.source and memory.source.casefold() == value.casefold():
                    basis.append("legacy.source.name")
                if memory.content_hash and memory.content_hash.casefold() == value.casefold():
                    basis.append("legacy.source.content_hash")
                if (
                    source_version is not None
                    and str(source_version).casefold() == value.casefold()
                ):
                    basis.append("legacy.source.version")
        elif req.dependency_kind == "policy":
            if row.policy_version and row.policy_version.casefold() == value.casefold():
                basis.append("legacy.decision.policy_version")
            if _contains_exact(value, metadata.get("policy_evaluation")):
                basis.append("legacy.metadata.policy_evaluation")
        elif req.dependency_kind == "model":
            identifiers = {
                str(item)
                for item in (row.model_id, row.model_version)
                if item is not None
            }
            if row.model_id and row.model_version:
                identifiers.add(f"{row.model_id}:{row.model_version}")
            if value.casefold() in {item.casefold() for item in identifiers}:
                basis.append("legacy.decision.model")
        elif req.dependency_kind == "tool":
            if _contains_exact(value, metadata.get("tools") or metadata.get("tool_calls")):
                basis.append("legacy.metadata.tools")
        elif req.dependency_kind == "permission":
            if _contains_exact(
                value,
                metadata.get("authorization")
                or metadata.get("permissions")
                or metadata.get("principal"),
            ):
                basis.append("legacy.metadata.authorization")
        elif req.dependency_kind == "instruction":
            if _contains_exact(
                value,
                {
                    "id": metadata.get("instruction_id"),
                    "version": metadata.get("instruction_version"),
                    "hash": metadata.get("system_instruction_hash")
                    or metadata.get("instruction_hash"),
                },
            ):
                basis.append("legacy.metadata.instruction")
        elif req.dependency_kind == "input" and row.input_hash:
            if row.input_hash.casefold() == value.casefold():
                basis.append("legacy.decision.input_hash")
        elif req.dependency_kind == "output" and row.output_hash:
            if row.output_hash.casefold() == value.casefold():
                basis.append("legacy.decision.output_hash")

        reachable = metadata.get("reachable_dependencies") or metadata.get("dependencies")
        if not basis and _contains_exact(value, reachable):
            basis.append("legacy.metadata.reachable_dependencies")
            status = "reachable"
        if not basis:
            continue
        risk_score, priority = _impact_risk(row, req.change_type, status == "direct_reference")
        matches.append(
            DecisionImpactItem(
                decision=_out(row),
                match_basis=sorted(set(basis)),
                impact_status=status,
                risk_score=risk_score,
                priority=priority,
            )
        )
    return matches, source_references_truncated


@router.post("/impact", response_model=IndexedDecisionImpactResult)
async def assess_decision_impact(
    req: EvidenceDependencyChange,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Assess blast radius through indexed evidence, then a bounded legacy gap scan."""
    auth.require("read")
    if req.record_event:
        auth.require("write")

    indexed_total, indexed_direct, indexed_reachable, indexed_items = (
        await _indexed_impact_matches(req, auth, db)
    )

    artifact_match = artifact_dependency_filter(
        req.dependency_kind, req.dependency_value
    )
    matching_kind_link_filters = [
        DecisionEvidenceLink.namespace == auth.namespace,
        DecisionEvidenceLink.decision_id == DecisionRecord.id,
        EvidenceArtifact.namespace == auth.namespace,
        *artifact_match,
    ]
    _apply_barrier_filter(
        matching_kind_link_filters, DecisionEvidenceLink.barrier_group, auth
    )
    _apply_barrier_filter(
        matching_kind_link_filters, EvidenceArtifact.barrier_group, auth
    )
    matching_kind_link = (
        select(DecisionEvidenceLink.id)
        .join(EvidenceArtifact, EvidenceArtifact.id == DecisionEvidenceLink.artifact_id)
        .where(*matching_kind_link_filters)
        .correlate(DecisionRecord)
        .exists()
    )
    kind_coverage_complete = (
        select(DecisionEvidenceKindCoverage.id)
        .where(
            DecisionEvidenceKindCoverage.namespace == auth.namespace,
            DecisionEvidenceKindCoverage.decision_id == DecisionRecord.id,
            DecisionEvidenceKindCoverage.kind == req.dependency_kind,
            DecisionEvidenceKindCoverage.status == "complete",
        )
        .correlate(DecisionRecord)
        .exists()
    )
    candidate_limit = 10_000
    legacy_filters = [
        DecisionRecord.namespace == auth.namespace,
        ~kind_coverage_complete,
        ~matching_kind_link,
    ]
    _apply_barrier_filter(legacy_filters, DecisionRecord.barrier_group, auth)
    legacy_rows = (
        (
            await db.execute(
                select(DecisionRecord)
                .where(*legacy_filters)
                .order_by(DecisionRecord.decided_at.desc())
                .limit(candidate_limit + 1)
            )
        )
        .scalars()
        .all()
    )
    legacy_fallback_truncated = len(legacy_rows) > candidate_limit
    legacy_rows = legacy_rows[:candidate_limit]
    legacy_items, source_references_truncated = await _legacy_impact_matches(
        legacy_rows,
        req,
        auth,
        db,
        source_reference_limit=50_000,
    )
    legacy_fallback_truncated = (
        legacy_fallback_truncated or source_references_truncated
    )

    matches = [*indexed_items, *legacy_items]
    matches.sort(
        key=lambda item: (item.risk_score, item.decision.decided_at),
        reverse=True,
    )
    limited = matches[: req.limit]
    legacy_direct = sum(
        item.impact_status == "direct_reference" for item in legacy_items
    )
    legacy_reachable = len(legacy_items) - legacy_direct
    total = indexed_total + len(legacy_items)
    direct_count = indexed_direct + legacy_direct
    reachable_count = indexed_reachable + legacy_reachable
    fallback_used = bool(legacy_rows) or legacy_fallback_truncated
    analysis_mode = (
        "indexed"
        if not fallback_used
        else "hybrid_legacy_fallback"
        if indexed_total
        else "legacy_fallback"
    )

    now = datetime.now(timezone.utc)
    event_id = None
    if req.record_event:
        occurred_at = req.occurred_at or now
        affected_ids = [str(item.decision.id) for item in matches[:100]]
        payload = {
            "dependency_kind": req.dependency_kind,
            "dependency_value": req.dependency_value,
            "change_type": req.change_type,
            "note": req.note,
            "affected_decision_ids": affected_ids,
            "affected_decision_ids_truncated": len(affected_ids) < total,
            "direct_count": direct_count,
            "reachable_count": reachable_count,
            "analysis_mode": analysis_mode,
            "indexed_decisions_matched": indexed_total,
            "legacy_decisions_matched": len(legacy_items),
            "legacy_candidates_scanned": len(legacy_rows),
            "legacy_fallback_truncated": legacy_fallback_truncated,
            "total_is_lower_bound": legacy_fallback_truncated,
        }
        event_body = {
            "namespace": auth.namespace,
            "agent_id": req.agent_id,
            "event_type": "system_change",
            "occurred_at": occurred_at.isoformat(),
            "recorded_at": now.isoformat(),
            "payload": payload,
        }
        event_hash = hashlib.sha256(_canonical(event_body).encode()).hexdigest()
        event = LedgerEvent(
            namespace=auth.namespace,
            event_type="system_change",
            agent_id=req.agent_id,
            barrier_group=auth.barrier_group,
            occurred_at=occurred_at,
            recorded_at=now,
            payload=payload,
            event_hash=event_hash,
        )
        db.add(event)
        await db.flush()
        event_id = event.id
        await chain_log(
            db,
            auth.namespace,
            req.agent_id,
            "decision_impact_assessed",
            content_hash=event_hash,
            payload={"change_event_id": str(event.id), "affected_count": total},
        )
        await db.commit()

    return IndexedDecisionImpactResult(
        dependency={"kind": req.dependency_kind, "value": req.dependency_value},
        change_type=req.change_type,
        assessed_at=now,
        total=total,
        direct_count=direct_count,
        reachable_count=reachable_count,
        search_truncated=legacy_fallback_truncated or total > len(limited),
        change_event_id=event_id,
        items=limited,
        analysis_mode=analysis_mode,
        indexed_decisions_matched=indexed_total,
        legacy_decisions_matched=len(legacy_items),
        legacy_candidates_scanned=len(legacy_rows),
        legacy_fallback_truncated=legacy_fallback_truncated,
        total_is_lower_bound=legacy_fallback_truncated,
    )


async def _assessment_page_matches(
    job: DecisionImpactAssessmentJob,
    page: list[tuple[DecisionEvidenceCoverageSet, DecisionRecord]],
    auth: AuthContext,
    db: AsyncSession,
) -> tuple[
    dict[UUID, tuple[DecisionImpactItem, set[str]]],
    int,
    int,
]:
    """Match one bounded snapshot page through indexed and legacy evidence."""
    decisions = [decision for _, decision in page]
    decision_ids = [decision.id for decision in decisions]
    if not decision_ids:
        return {}, 0, 0
    request = EvidenceDependencyChange(
        dependency_kind=job.dependency_kind,
        dependency_value=job.dependency_value,
        change_type=job.change_type,
        occurred_at=job.change_occurred_at,
        note=job.note,
        agent_id=job.requested_by_principal_ref,
        limit=min(1000, len(decisions)),
        record_event=False,
    )

    artifact_match = artifact_dependency_filter(
        job.dependency_kind, job.dependency_value
    )
    edge_filters = [
        DecisionEvidenceLink.namespace == job.namespace,
        DecisionEvidenceLink.decision_id.in_(decision_ids),
        EvidenceArtifact.namespace == job.namespace,
        DecisionEvidenceLinkRegistration.namespace == job.namespace,
        DecisionEvidenceLinkRegistration.sequence <= job.snapshot_max_link_sequence,
        *artifact_match,
    ]
    if job.barrier_group is not None:
        for column in (
            DecisionEvidenceLink.barrier_group,
            EvidenceArtifact.barrier_group,
            DecisionEvidenceLinkRegistration.barrier_group,
        ):
            edge_filters.append(or_(column.is_(None), column == job.barrier_group))
    largest_decision, link_total = await _impact_link_budget(
        db,
        select(
            DecisionEvidenceLink.decision_id,
            func.count().label("link_count"),
        )
        .join(
            EvidenceArtifact,
            EvidenceArtifact.id == DecisionEvidenceLink.artifact_id,
        )
        .join(
            DecisionEvidenceLinkRegistration,
            DecisionEvidenceLinkRegistration.link_id == DecisionEvidenceLink.id,
        )
        .where(*edge_filters)
        .group_by(DecisionEvidenceLink.decision_id),
    )
    if (
        largest_decision > _MAX_IMPACT_LINKS_PER_DECISION
        or link_total > _MAX_IMPACT_LINKS_PER_PAGE
    ):
        raise RuntimeError(
            "Exhaustive impact page exceeds the fail-closed evidence-link "
            f"hydration budget (links={link_total}, "
            f"largest_decision_links={largest_decision}, "
            f"max_per_decision={_MAX_IMPACT_LINKS_PER_DECISION}, "
            f"max_per_page={_MAX_IMPACT_LINKS_PER_PAGE})"
        )
    edge_rows = (
        await db.execute(
            select(DecisionEvidenceLink, EvidenceArtifact)
            .join(
                EvidenceArtifact,
                EvidenceArtifact.id == DecisionEvidenceLink.artifact_id,
            )
            .join(
                DecisionEvidenceLinkRegistration,
                DecisionEvidenceLinkRegistration.link_id == DecisionEvidenceLink.id,
            )
            .where(*edge_filters)
            .limit(_MAX_IMPACT_LINKS_PER_PAGE + 1)
        )
    ).all()
    if len(edge_rows) > _MAX_IMPACT_LINKS_PER_PAGE:
        raise RuntimeError(
            "Exhaustive impact page changed beyond its evidence-link hydration budget"
        )
    edges_by_decision: dict[
        UUID, list[tuple[DecisionEvidenceLink, EvidenceArtifact]]
    ] = {}
    for link, artifact in edge_rows:
        edges_by_decision.setdefault(link.decision_id, []).append((link, artifact))

    matches: dict[UUID, tuple[DecisionImpactItem, set[str]]] = {}
    decisions_by_id = {decision.id: decision for decision in decisions}
    for decision_id, edges in edges_by_decision.items():
        decision = decisions_by_id[decision_id]
        direct = any(link.relation == "direct" for link, _ in edges)
        basis: set[str] = set()
        for link, artifact in edges:
            basis.update(link.match_basis or [])
            basis.update(_artifact_match_basis(artifact, job.dependency_value))
        risk_score, priority = _impact_risk(
            decision,
            job.change_type,
            direct,
            [dict(link.risk_metadata or {}) for link, _ in edges]
            + [dict(artifact.risk_metadata or {}) for _, artifact in edges],
        )
        matches[decision_id] = (
            DecisionImpactItem(
                decision=_out(decision),
                match_basis=sorted(basis)[:100],
                impact_status="direct_reference" if direct else "reachable",
                risk_score=risk_score,
                priority=priority,
            ),
            {"indexed"},
        )

    # Exhaustive jobs deliberately inspect immutable legacy fields for every
    # decision in the frozen page, including decisions whose current coverage
    # projection says complete. Coverage can be reassessed after job creation;
    # using that mutable projection to skip fallback would make the frozen
    # decision/link snapshot drift and could miss a link created after the link
    # watermark. The interactive endpoint retains the coverage optimization.
    fallback_decisions = decisions
    legacy_items, source_references_truncated = await _legacy_impact_matches(
        fallback_decisions,
        request,
        auth,
        db,
        memory_recorded_as_of=job.created_at,
    )
    if source_references_truncated:
        raise RuntimeError(
            "Exhaustive impact assessment unexpectedly truncated source references"
        )
    for legacy in legacy_items:
        decision_id = legacy.decision.id
        existing = matches.get(decision_id)
        if existing is None:
            matches[decision_id] = (legacy, {"legacy_fallback"})
            continue
        indexed, sources = existing
        direct = (
            indexed.impact_status == "direct_reference"
            or legacy.impact_status == "direct_reference"
        )
        risk_score = max(indexed.risk_score, legacy.risk_score)
        priority = (
            "critical"
            if risk_score >= 85
            else "high"
            if risk_score >= 70
            else "medium"
            if risk_score >= 45
            else "low"
        )
        matches[decision_id] = (
            DecisionImpactItem(
                decision=indexed.decision,
                match_basis=sorted(
                    {*indexed.match_basis, *legacy.match_basis}
                )[:100],
                impact_status="direct_reference" if direct else "reachable",
                risk_score=risk_score,
                priority=priority,
            ),
            {*sources, "legacy_fallback"},
        )
    return matches, len(edges_by_decision), len(legacy_items)


async def _complete_impact_assessment(
    job: DecisionImpactAssessmentJob,
    db: AsyncSession,
) -> None:
    counts = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                DecisionImpactAssessmentMatch.impact_status
                                == "direct_reference",
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(
                DecisionImpactAssessmentMatch.namespace == job.namespace,
                DecisionImpactAssessmentMatch.job_id == job.id,
            )
        )
    ).one()
    now = datetime.now(timezone.utc)
    job.matches_found = int(counts[0] or 0)
    job.direct_count = int(counts[1] or 0)
    job.reachable_count = job.matches_found - job.direct_count
    if job.record_event and job.completion_event_id is None:
        payload = {
            "assessment_id": str(job.id),
            "dependency_kind": job.dependency_kind,
            "dependency_value": job.dependency_value,
            "dependency_lookup_hash": job.dependency_lookup_hash,
            "change_type": job.change_type,
            "snapshot_max_coverage_sequence": job.snapshot_max_coverage_sequence,
            "snapshot_max_link_sequence": job.snapshot_max_link_sequence,
            "decisions_scanned": job.decisions_scanned,
            "matches_found": job.matches_found,
            "direct_count": job.direct_count,
            "reachable_count": job.reachable_count,
            "completion_scope": "explicit_registration_sequence_snapshot",
        }
        occurred_at = job.change_occurred_at or now
        event_body = {
            "namespace": job.namespace,
            "agent_id": job.requested_by_principal_ref,
            "event_type": "system_change",
            "occurred_at": occurred_at.isoformat(),
            "recorded_at": now.isoformat(),
            "payload": payload,
        }
        event_hash = hashlib.sha256(_canonical(event_body).encode()).hexdigest()
        event = LedgerEvent(
            namespace=job.namespace,
            event_type="system_change",
            agent_id=job.requested_by_principal_ref,
            barrier_group=job.barrier_group,
            occurred_at=occurred_at,
            recorded_at=now,
            payload=payload,
            event_hash=event_hash,
        )
        db.add(event)
        await db.flush()
        await chain_log(
            db,
            job.namespace,
            job.requested_by_principal_ref,
            "exhaustive_decision_impact_completed",
            content_hash=event_hash,
            payload={
                "assessment_id": str(job.id),
                "change_event_id": str(event.id),
                "affected_count": job.matches_found,
            },
        )
        job.completion_event_id = event.id
    job.status = "completed"
    job.completed_at = now
    job.updated_at = now


@router.post(
    "/impact-assessments",
    response_model=ExhaustiveImpactAssessmentStatus,
    status_code=202,
)
async def create_exhaustive_impact_assessment(
    req: ExhaustiveImpactAssessmentCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Freeze a barrier-safe decision/link snapshot for exhaustive assessment."""
    auth.require("read")
    auth.require("write")
    if not auth.principal_id:
        raise HTTPException(401, "Authenticated principal identity is required")
    try:
        job, created = await create_impact_assessment_job(
            db,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            principal_ref=auth.principal_id,
            auth_method=auth.auth_method,
            request=req,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.commit()
    await db.refresh(job)
    if created:
        record_impact_job_outcome("created")
    return impact_assessment_status_out(job)


@router.get(
    "/impact-assessments/{assessment_id}",
    response_model=ExhaustiveImpactAssessmentStatus,
)
async def get_exhaustive_impact_assessment(
    assessment_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    job = await get_impact_assessment_job(
        db,
        namespace=auth.namespace,
        barrier_group=auth.barrier_group,
        job_id=assessment_id,
    )
    if job is None:
        raise HTTPException(404, "Impact assessment not found")
    return impact_assessment_status_out(job)


@router.post(
    "/impact-assessments/{assessment_id}/advance",
    response_model=ExhaustiveImpactAssessmentStatus,
)
async def advance_exhaustive_impact_assessment(
    assessment_id: UUID,
    req: ExhaustiveImpactAssessmentAdvance,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Advance through the same leased service used by the autonomous worker."""
    auth.require("read")
    auth.require("write")
    settings = get_settings()
    worker_id = f"request:{uuid4().hex}"
    try:
        job, claim = await claim_impact_assessment_for_request(
            db,
            job_id=assessment_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            worker_id=worker_id,
            lease_seconds=settings.impact_assessment_worker_lease_seconds,
        )
    except LookupError:
        raise HTTPException(404, "Impact assessment not found")
    except (ImpactAssessmentLeaseConflict, ImpactAssessmentTerminal) as exc:
        raise HTTPException(409, str(exc)) from exc
    if claim is None:
        return impact_assessment_status_out(job)
    job, _result = await advance_claimed_impact_assessment(
        db,
        claim=claim,
        worker_id=worker_id,
        auth=auth,
        page_size=req.page_size,
        max_pages=req.max_pages,
        lease_seconds=settings.impact_assessment_worker_lease_seconds,
        page_matcher=_assessment_page_matches,
        completer=_complete_impact_assessment,
        raise_on_error=True,
    )
    return impact_assessment_status_out(job)


@router.get(
    "/impact-assessments/{assessment_id}/results",
    response_model=ExhaustiveImpactAssessmentResults,
)
async def list_exhaustive_impact_assessment_results(
    assessment_id: UUID,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    job = await get_impact_assessment_job(
        db,
        namespace=auth.namespace,
        barrier_group=auth.barrier_group,
        job_id=assessment_id,
    )
    if job is None:
        raise HTTPException(404, "Impact assessment not found")
    result_filters = [
        DecisionImpactAssessmentMatch.namespace == auth.namespace,
        DecisionImpactAssessmentMatch.job_id == job.id,
        DecisionImpactAssessmentMatch.sequence > after,
        DecisionRecord.namespace == auth.namespace,
    ]
    _apply_barrier_filter(result_filters, DecisionRecord.barrier_group, auth)
    rows = (
        await db.execute(
            select(DecisionImpactAssessmentMatch, DecisionRecord)
            .join(
                DecisionRecord,
                DecisionRecord.id == DecisionImpactAssessmentMatch.decision_id,
            )
            .where(*result_filters)
            .order_by(DecisionImpactAssessmentMatch.sequence)
            .limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    items = [
        ExhaustiveImpactAssessmentMatchOut(
            sequence=match.sequence,
            decision=_out(decision),
            match_basis=list(match.match_basis or [])[:100],
            impact_status=match.impact_status,
            risk_score=match.risk_score,
            priority=match.risk_level,
            match_sources=list(match.match_sources or []),
        )
        for match, decision in page
    ]
    return ExhaustiveImpactAssessmentResults(
        assessment_id=job.id,
        status=job.status,
        snapshot_complete=job.status == "completed",
        total_matches=job.matches_found,
        items=items,
        next_cursor=page[-1][0].sequence if has_more and page else None,
    )


@router.get("/{decision_id}", response_model=DecisionOut)
async def get_decision(
    decision_id: UUID, auth: AuthContext = Depends(get_auth), db: AsyncSession = Depends(get_db)
):
    auth.require("read")
    row = await db.get(DecisionRecord, decision_id)
    if row is None or row.namespace != auth.namespace or not _barrier_visible(row, auth):
        raise HTTPException(404, "Decision not found")
    return _out(row)


@router.post(
    "/{decision_id}/evidence-links",
    response_model=DecisionEvidenceLinkOut,
    status_code=201,
)
async def link_decision_evidence(
    decision_id: UUID,
    req: DecisionEvidenceLinkCreate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Link a visible artifact to a decision without crossing information barriers."""
    auth.require("write")
    decision = await db.get(DecisionRecord, decision_id)
    if (
        decision is None
        or decision.namespace != auth.namespace
        or not _barrier_visible(decision, auth)
    ):
        raise HTTPException(404, "Decision not found")
    await _require_decision_integrity(db, decision)
    artifact = await db.get(EvidenceArtifact, req.artifact_id)
    if (
        artifact is None
        or artifact.namespace != auth.namespace
        or not _barrier_visible(artifact, auth)
    ):
        raise HTTPException(404, "Evidence artifact not found")
    try:
        link, created = await ensure_link(
            db,
            namespace=auth.namespace,
            decision=decision,
            artifact=artifact,
            relation=req.relation,
            match_basis=req.match_basis,
            risk_metadata={
                **dict(artifact.risk_metadata or {}),
                **req.risk_metadata,
            },
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if created:
        await chain_log(
            db,
            auth.namespace,
            auth.principal_id or decision.recorded_by_principal_ref,
            "decision_evidence_linked",
            content_hash=artifact.artifact_hash or artifact.identity_hash,
            payload={
                "decision_id": str(decision.id),
                "artifact_id": str(artifact.id),
                "relation": link.relation,
            },
        )
    await db.commit()
    return link_out(link, artifact)


@router.get(
    "/{decision_id}/evidence-coverage",
    response_model=DecisionEvidenceCoverageOut,
)
async def inspect_decision_evidence_coverage(
    decision_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return persisted per-kind normalization claims and watermarks."""
    auth.require("read")
    decision = await db.get(DecisionRecord, decision_id)
    if (
        decision is None
        or decision.namespace != auth.namespace
        or not _barrier_visible(decision, auth)
    ):
        raise HTTPException(404, "Decision not found")
    await _require_decision_integrity(db, decision)
    return await get_decision_coverage(db, decision)


@router.get(
    "/{decision_id}/evidence-graph",
    response_model=DecisionEvidenceGraphOut,
)
async def inspect_decision_evidence_graph(
    decision_id: UUID,
    limit: int = Query(default=500, ge=1, le=2_000),
    after_relation: str | None = Query(
        default=None,
        pattern="^(direct|reachable)$",
    ),
    after_link_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return one bounded evidence-link page with exact graph cardinalities."""
    auth.require("read")
    decision = await db.get(DecisionRecord, decision_id)
    if (
        decision is None
        or decision.namespace != auth.namespace
        or not _barrier_visible(decision, auth)
    ):
        raise HTTPException(404, "Decision not found")
    await _require_decision_integrity(db, decision)
    filters = [
        DecisionEvidenceLink.namespace == auth.namespace,
        DecisionEvidenceLink.decision_id == decision.id,
        EvidenceArtifact.namespace == auth.namespace,
    ]
    _apply_barrier_filter(filters, DecisionEvidenceLink.barrier_group, auth)
    _apply_barrier_filter(filters, EvidenceArtifact.barrier_group, auth)
    if (after_relation is None) != (after_link_id is None):
        raise HTTPException(
            400,
            "evidence graph cursor requires both after_relation and after_link_id",
        )

    relation_counts = {
        str(relation): int(count or 0)
        for relation, count in (
            await db.execute(
                select(DecisionEvidenceLink.relation, func.count())
                .join(
                    EvidenceArtifact,
                    EvidenceArtifact.id == DecisionEvidenceLink.artifact_id,
                )
                .where(*filters)
                .group_by(DecisionEvidenceLink.relation)
            )
        ).all()
    }
    direct_count = relation_counts.get("direct", 0)
    reachable_count = relation_counts.get("reachable", 0)
    links_total = direct_count + reachable_count
    artifacts_total = int(
        (
            await db.execute(
                select(func.count(func.distinct(DecisionEvidenceLink.artifact_id)))
                .join(
                    EvidenceArtifact,
                    EvidenceArtifact.id == DecisionEvidenceLink.artifact_id,
                )
                .where(*filters)
            )
        ).scalar_one()
        or 0
    )

    page_filters = list(filters)
    if after_relation is not None and after_link_id is not None:
        page_filters.append(
            or_(
                DecisionEvidenceLink.relation > after_relation,
                and_(
                    DecisionEvidenceLink.relation == after_relation,
                    DecisionEvidenceLink.id > after_link_id,
                ),
            )
        )
    raw_rows = (
        await db.execute(
            select(DecisionEvidenceLink, EvidenceArtifact)
            .join(EvidenceArtifact, EvidenceArtifact.id == DecisionEvidenceLink.artifact_id)
            .where(*page_filters)
            .order_by(
                DecisionEvidenceLink.relation,
                DecisionEvidenceLink.id,
            )
            .limit(limit + 1)
        )
    ).all()
    has_more = len(raw_rows) > limit
    rows = list(raw_rows[:limit])
    artifact_rows = {artifact.id: artifact for _, artifact in rows}
    legacy_ids: set[UUID] = set()
    for raw_id in decision.evidence_memory_ids or []:
        try:
            legacy_ids.add(UUID(str(raw_id)))
        except ValueError:
            continue
    indexed_memory_ids: set[UUID] = set()
    legacy_id_values = [str(memory_id) for memory_id in legacy_ids]
    memory_id_expression = EvidenceArtifact.metadata_["memory_id"].as_string()
    for offset in range(0, len(legacy_id_values), 400):
        batch = legacy_id_values[offset : offset + 400]
        indexed_rows = (
            await db.execute(
                select(memory_id_expression)
                .join(
                    DecisionEvidenceLink,
                    DecisionEvidenceLink.artifact_id == EvidenceArtifact.id,
                )
                .where(*filters, memory_id_expression.in_(batch))
                .distinct()
                .limit(len(batch) + 1)
            )
        ).scalars()
        for raw_memory_id in indexed_rows:
            try:
                indexed_memory_ids.add(UUID(str(raw_memory_id)))
            except ValueError:
                continue
    unindexed = sorted(legacy_ids - indexed_memory_ids, key=str)
    persisted_coverage = await get_decision_coverage(db, decision)
    next_link = rows[-1][0] if has_more and rows else None
    return DecisionEvidenceGraphOut(
        decision_id=decision.id,
        namespace=auth.namespace,
        links_total=links_total,
        links_returned=len(rows),
        links_complete=after_relation is None and not has_more,
        has_more=has_more,
        next_relation=next_link.relation if next_link is not None else None,
        next_link_id=next_link.id if next_link is not None else None,
        artifacts_total=artifacts_total,
        artifacts_returned=len(artifact_rows),
        direct_count=direct_count,
        reachable_count=reachable_count,
        artifacts=[artifact_out(artifact) for artifact in artifact_rows.values()],
        links=[link_out(link, artifact) for link, artifact in rows],
        coverage=EvidenceGraphCoverage(
            indexed_links=links_total,
            indexed_artifacts=artifacts_total,
            legacy_memory_references=len(legacy_ids),
            unindexed_legacy_memory_references=len(unindexed),
            unindexed_legacy_memory_ids=unindexed[:1000],
            unindexed_legacy_memory_ids_truncated=len(unindexed) > 1000,
            coverage_sequence=persisted_coverage.coverage_sequence,
            overall_status=persisted_coverage.overall_status,
            kinds=persisted_coverage.kinds,
            normalized_complete=persisted_coverage.normalized_complete,
            normalization_scope="persisted_per_kind_watermarks",
        ),
    )


@router.post("/{decision_id}/review", response_model=DecisionOut)
async def review_decision(
    decision_id: UUID,
    req: DecisionReview,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
):
    auth.require("admin")
    try:
        async with operation_claim(
            db,
            namespace=auth.namespace,
            operation=_DECISION_REVIEW_OPERATION,
            key=idempotency_key,
            request=_idempotency_request(req, auth, decision_id=decision_id),
        ) as claim:
            if claim.is_replay:
                event_id = _one_replay_id(claim, "decision_review")
                event = await db.get(DecisionReviewEvent, event_id)
                if (
                    event is None
                    or event.namespace != auth.namespace
                    or (
                        auth.barrier_group is not None
                        and event.barrier_group not in (None, auth.barrier_group)
                    )
                    or not verify_decision_review_event(event)
                ):
                    raise IdempotencyReplayUnavailable(
                        "The committed decision-review result is unavailable"
                    )
                row = await db.get(DecisionRecord, event.decision_id)
                if (
                    row is None
                    or row.namespace != auth.namespace
                    or not _barrier_visible(row, auth)
                ):
                    raise IdempotencyReplayUnavailable(
                        "The reviewed decision is unavailable"
                    )
                await _require_decision_integrity(db, row)
                claim.replay_served()
                return _out(row).model_copy(
                    update={
                        "human_review_status": event.status,
                        "human_reviewer": event.reviewer_principal_id,
                        "human_reviewed_at": _utc(event.reviewed_at),
                    }
                )

            row, review_event = await _review_decision_mutation(
                decision_id, req, auth, db
            )
            response = _out(row).model_copy(
                update={
                    "human_review_status": review_event.status,
                    "human_reviewer": review_event.reviewer_principal_id,
                    "human_reviewed_at": _utc(review_event.reviewed_at),
                }
            )
            await claim.complete_and_commit(
                resource_kind="decision_review",
                resource_ids=[review_event.id],
                response_status=200,
            )
            return response
    except (
        InvalidIdempotencyKey,
        InvalidIdempotencyRequest,
        IdempotencyConflict,
        IdempotencyReplayUnavailable,
    ) as exc:
        _raise_idempotency_error(exc)


async def _review_decision_mutation(
    decision_id: UUID,
    req: DecisionReview,
    auth: AuthContext,
    db: AsyncSession,
) -> tuple[DecisionRecord, DecisionReviewEvent]:
    row = await db.get(DecisionRecord, decision_id)
    if row is None or row.namespace != auth.namespace or not _barrier_visible(row, auth):
        raise HTTPException(404, "Decision not found")
    await _require_decision_integrity(db, row)
    principal = auth.principal_id
    if not principal:
        raise HTTPException(401, "Authenticated reviewer identity is required")
    if req.reviewer is not None and req.reviewer != principal:
        raise HTTPException(
            403, "reviewer does not match the authenticated principal"
        )
    try:
        review_event = await create_decision_review_event(
            db,
            decision=row,
            status=req.status,
            note=req.note,
            reviewer_principal_id=principal,
            reviewer_principal_type=auth.principal_type,
            reviewer_role=auth.role,
            auth_method=auth.auth_method,
            credential_id=auth.credential_id,
        )
    except DecisionReviewIntegrityError as exc:
        raise HTTPException(409, str(exc)) from exc
    reviewer_hash = reviewer_ref_hash(principal)
    await chain_log(
        db,
        auth.namespace,
        principal,
        "decision_reviewed",
        content_hash=review_event.event_hash,
        payload={
            "decision_id": str(row.id),
            "status": req.status,
            "review_sequence": review_event.sequence,
            "review_event_hash": review_event.event_hash,
            "reviewer_ref_hash": reviewer_hash,
            "reviewer_role": auth.role,
            "note_hash": review_event.note_hash,
        },
    )
    return row, review_event


@router.get(
    "/{decision_id}/review-history",
    response_model=DecisionReviewHistoryResult,
)
async def decision_review_history(
    decision_id: UUID,
    include_notes: bool = False,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2_000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DecisionReviewHistoryResult:
    """Return a bounded, internally verified immutable review-chain page."""
    auth.require("read")
    if include_notes:
        auth.require("admin")
    decision = await db.get(DecisionRecord, decision_id)
    if (
        decision is None
        or decision.namespace != auth.namespace
        or not _barrier_visible(decision, auth)
    ):
        raise HTTPException(404, "Decision not found")
    await _require_decision_integrity(db, decision)
    filters = [
        DecisionReviewEvent.namespace == auth.namespace,
        DecisionReviewEvent.decision_id == decision.id,
    ]
    _apply_barrier_filter(filters, DecisionReviewEvent.barrier_group, auth)
    total = int(
        (
            await db.execute(
                select(func.count(DecisionReviewEvent.id)).where(*filters)
            )
        ).scalar_one()
        or 0
    )
    prior_hash = None
    if after_sequence:
        anchor = (
            await db.execute(
                select(DecisionReviewEvent).where(
                    *filters,
                    DecisionReviewEvent.sequence == after_sequence,
                )
            )
        ).scalar_one_or_none()
        if anchor is None:
            raise HTTPException(400, "review history cursor does not exist")
        if not verify_decision_review_event(anchor):
            raise HTTPException(
                409,
                "Decision review history cursor failed integrity verification",
            )
        prior_hash = anchor.event_hash

    raw_rows = list(
        (
            await db.execute(
                select(DecisionReviewEvent)
                .where(
                    *filters,
                    DecisionReviewEvent.sequence > after_sequence,
                )
                .order_by(DecisionReviewEvent.sequence)
                .limit(limit + 1)
            )
        ).scalars().all()
    )
    has_more = len(raw_rows) > limit
    rows = raw_rows[:limit]
    for expected_sequence, event in enumerate(rows, start=after_sequence + 1):
        if (
            event.sequence != expected_sequence
            or event.prior_event_hash != prior_hash
            or not verify_decision_review_event(event)
        ):
            raise HTTPException(
                409,
                "Decision review history failed chain-integrity verification",
            )
        prior_hash = event.event_hash
    collection_complete = after_sequence == 0 and not has_more
    return DecisionReviewHistoryResult(
        decision_id=decision.id,
        total=total,
        returned=len(rows),
        complete=collection_complete,
        has_more=has_more,
        next_sequence=rows[-1].sequence if has_more and rows else None,
        page_chain_verified=True,
        chain_scope_complete=collection_complete,
        events=[
            decision_review_event_out(event, include_note=include_notes)
            for event in rows
        ],
    )


@receipts_router.post("/verify")
async def verify_receipt(
    req: DecisionReceiptVerifyRequest,
    auth: AuthContext = Depends(get_auth),
):
    """Verify a portable Decision Receipt without requiring database access."""
    auth.require("read")
    return verify_decision_receipt(
        req.receipt,
        trusted_public_key=req.trusted_public_key,
        require_signature=req.require_signature,
    )


@router.get("/{decision_id}/receipt")
async def decision_receipt(
    decision_id: UUID,
    verify: bool = True,
    include_source_content: bool = False,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Produce a complete, optionally signed receipt.

    Returns HTTP 413 with ``knowledge_snapshot_requires_paged_export`` when the
    reconstructed boundary exceeds 10,000 facts; a partial page is never signed
    or presented as a complete receipt.
    """
    auth.require("read")
    row = await db.get(DecisionRecord, decision_id)
    if row is None or row.namespace != auth.namespace or not _barrier_visible(row, auth):
        raise HTTPException(404, "Decision not found")

    await _require_decision_integrity(db, row)

    capacity_code = "decision_receipt_byte_capacity_exceeded"
    settings = get_settings()
    byte_limit = (
        settings.content_export_page_bytes_limit
        if include_source_content
        else settings.hash_only_export_page_bytes_limit
    )
    decision_out = _out(row)
    # Covers the decision projection plus the fixed receipt/signature/completeness
    # envelope. Dynamic snapshot, graph, and audit material are budgeted below.
    reserved_bytes = len(
        _canonical(decision_out.model_dump(mode="json")).encode("utf-8")
    ) + 65_536
    if reserved_bytes > byte_limit:
        raise HTTPException(
            status_code=413,
            detail={
                "code": capacity_code,
                "message": "The complete Decision Receipt exceeds its byte budget",
                "export_mode": "content" if include_source_content else "hash_only",
                "estimated_bytes": reserved_bytes,
                "byte_limit": byte_limit,
            },
        )
    snapshot, cited, _, snapshot_bytes = await _decision_boundary(
        row,
        auth,
        db,
        include_content=include_source_content,
        byte_limit=byte_limit,
        reserved_bytes=reserved_bytes,
        serialization_multiplier=2,
        capacity_code=capacity_code,
    )
    reserved_bytes += snapshot_bytes
    evidence_manifest, manifest_bytes = await _receipt_evidence_graph_manifest(
        row,
        auth,
        db,
        byte_limit=byte_limit,
        reserved_bytes=reserved_bytes,
        capacity_code=capacity_code,
    )
    reserved_bytes += manifest_bytes
    chain = dict(
        await _verified_chain(
            auth,
            db,
            verify,
            max_bytes=max(0, byte_limit - reserved_bytes),
        )
    )
    chain["lians_evidence_graph"] = evidence_manifest
    chain["receipt_exported_at"] = datetime.now(timezone.utc).isoformat()
    try:
        signer = await get_receipt_signer()
        receipt = await build_decision_receipt_with_signer(
            signer=signer,
            decision=decision_out,
            knowledge_snapshot=snapshot,
            cited_evidence=cited,
            audit_chain=chain,
            include_source_content=include_source_content,
        )
    except ReceiptSignerConfigurationError as exc:
        # Detailed signer posture belongs on the authenticated operator
        # readiness surface, not in a tenant-facing response.
        raise HTTPException(500, "Decision Receipt signing configuration is invalid") from exc
    except ReceiptSigningUnavailable as exc:
        raise HTTPException(503, "Decision Receipt signing service is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(500, "Decision Receipt signing state is invalid") from exc

    receipt_bytes = len(_canonical(receipt).encode("utf-8"))
    if receipt_bytes > byte_limit:
        raise HTTPException(
            status_code=413,
            detail={
                "code": capacity_code,
                "message": "The complete Decision Receipt exceeds its byte budget",
                "export_mode": "content" if include_source_content else "hash_only",
                "estimated_bytes": receipt_bytes,
                "byte_limit": byte_limit,
            },
        )

    await chain_log(
        db,
        auth.namespace,
        auth.principal_id or row.recorded_by_principal_ref,
        "decision_receipt_exported",
        content_hash=receipt["integrity"]["receipt_hash"],
        payload={
            "decision_id": str(row.id),
            "receipt_version": receipt["receipt_version"],
            "completeness_grade": receipt["completeness"]["grade"],
        },
    )
    await db.commit()
    return receipt


@router.get("/{decision_id}/evidence-pack")
async def evidence_pack(
    decision_id: UUID,
    verify: bool = True,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """Produce a complete point-in-time evidence pack for a dispute or audit.

    The same 10,000-fact fail-closed boundary as Decision Receipts applies.
    """
    auth.require("read")
    row = await db.get(DecisionRecord, decision_id)
    if row is None or row.namespace != auth.namespace or not _barrier_visible(row, auth):
        raise HTTPException(404, "Decision not found")
    await _require_decision_integrity(db, row)
    capacity_code = "evidence_pack_byte_capacity_exceeded"
    byte_limit = get_settings().content_export_page_bytes_limit
    decision_out = _out(row)
    reserved_bytes = len(
        _canonical(decision_out.model_dump(mode="json")).encode("utf-8")
    ) + 65_536
    if reserved_bytes > byte_limit:
        raise HTTPException(
            status_code=413,
            detail={
                "code": capacity_code,
                "message": "The complete evidence pack exceeds its byte budget",
                "export_mode": "content",
                "estimated_bytes": reserved_bytes,
                "byte_limit": byte_limit,
            },
        )
    snapshot, cited_rows, recorded_as_of, snapshot_bytes = await _decision_boundary(
        row,
        auth,
        db,
        include_content=True,
        byte_limit=byte_limit,
        reserved_bytes=reserved_bytes,
        serialization_multiplier=2,
        capacity_code=capacity_code,
    )
    reserved_bytes += snapshot_bytes
    evidence_manifest, manifest_bytes = await _receipt_evidence_graph_manifest(
        row,
        auth,
        db,
        byte_limit=byte_limit,
        reserved_bytes=reserved_bytes,
        capacity_code=capacity_code,
    )
    reserved_bytes += manifest_bytes
    cited = [memory.model_dump(mode="json") for memory in cited_rows]
    policy = await db.get(NamespacePolicy, auth.namespace)
    chain = await _verified_chain(
        auth,
        db,
        verify,
        max_bytes=max(0, byte_limit - reserved_bytes),
    )
    pack = {
        "schema": "https://lians.ai/schemas/evidence-pack/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision_out.model_dump(mode="json"),
        "knowledge_recorded_as_of": recorded_as_of.isoformat(),
        "knowledge_snapshot": [m.model_dump(mode="json") for m in snapshot],
        "cited_evidence": cited,
        "normalized_evidence_graph": evidence_manifest,
        "audit_chain": chain,
        "retention": None
        if policy is None
        else {
            "content_ttl_days": policy.content_ttl_days,
            "audit_retention_days": policy.audit_retention_days,
            "legal_hold": policy.legal_hold,
        },
    }
    pack["pack_hash"] = hashlib.sha256(_canonical(pack).encode()).hexdigest()
    pack_bytes = len(_canonical(pack).encode("utf-8"))
    if pack_bytes > byte_limit:
        raise HTTPException(
            status_code=413,
            detail={
                "code": capacity_code,
                "message": "The complete evidence pack exceeds its byte budget",
                "export_mode": "content",
                "estimated_bytes": pack_bytes,
                "byte_limit": byte_limit,
            },
        )
    await chain_log(
        db,
        auth.namespace,
        auth.principal_id or row.recorded_by_principal_ref,
        "evidence_pack_exported",
        content_hash=pack["pack_hash"],
        payload={"decision_id": str(row.id), "schema": pack["schema"]},
    )
    await db.commit()
    return pack
