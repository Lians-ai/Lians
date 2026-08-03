"""Cross-control aggregation and deterministic triage for Lians Investigator."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy import case as sql_case
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import verify_chain
from .config import get_settings
from .control_models import (
    ControlClosureAttestation,
    DecisionReviewEvent,
    GateApprovalAttestation,
    GateDecisionRecord,
    InvestigationCase,
    RemediationTask,
)
from .control_schemas import GateDecisionOut, InvestigationCaseOut, RemediationTaskOut
from .control_service import (
    closure_statement,
    verify_closure_attestation_integrity,
)
from .decision_receipt import assess_completeness
from .decision_record_integrity import assert_decision_record_integrity
from .decision_review_service import decision_review_event_out, verify_decision_review_event
from .evidence_models import (
    EVIDENCE_ARTIFACT_KINDS,
    DecisionEvidenceCoverageSet,
    DecisionEvidenceKindCoverage,
    DecisionEvidenceLink,
    EvidenceArtifact,
)
from .evidence_schemas import (
    DecisionEvidenceCoverageOut,
    DecisionEvidenceGraphOut,
    EvidenceGraphCoverage,
)
from .evidence_service import artifact_out, decision_coverage_out, link_out
from .immutable_attestation_service import (
    gate_approval_out,
    verify_approval_attestation_integrity,
)
from .investigator_schemas import (
    DecisionInvestigationReport,
    InvestigatorCaseBundle,
    InvestigatorClosureOut,
    InvestigatorCollectionWindow,
    InvestigatorIntegrity,
    InvestigatorLinks,
    InvestigatorQueueItem,
    InvestigatorQueueOut,
    InvestigatorReportCoverage,
    InvestigatorRiskSummary,
)
from .models import DecisionRecord, LedgerEvent, Memory
from .receipt_signer import receipt_signing_enabled
from .schemas import DecisionOut, LedgerEventOut

_RECEIPT_EVIDENCE_LIMIT = 1000


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _barrier_filter(filters: list[Any], column, barrier_group: str | None) -> None:
    if barrier_group is not None:
        filters.append(or_(column.is_(None), column == barrier_group))


def _decision_out(row: DecisionRecord) -> DecisionOut:
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
        decided_at=row.decided_at,
        recorded_at=row.recorded_at,
        knowledge_as_of=row.knowledge_as_of,
        knowledge_recorded_as_of=_utc(row.knowledge_recorded_as_of or row.recorded_at),
        evidence_memory_ids=[UUID(str(value)) for value in (row.evidence_memory_ids or [])],
        input_hash=row.input_hash,
        output_hash=row.output_hash,
        human_review_status=row.human_review_status,
        human_reviewer=row.human_reviewer,
        human_reviewed_at=row.human_reviewed_at,
        supersedes_id=row.supersedes_id,
        metadata=dict(row.metadata_ or {}),
        record_hash_version=row.record_hash_version,
        record_integrity_status=row.record_integrity_status,
        record_hash=row.record_hash,
    )


def _ledger_out(row: LedgerEvent) -> LedgerEventOut:
    return LedgerEventOut(
        id=row.id,
        namespace=row.namespace,
        event_type=row.event_type,
        agent_id=row.agent_id,
        occurred_at=row.occurred_at,
        recorded_at=row.recorded_at,
        subject_id=row.subject_id,
        session_id=row.session_id,
        decision_id=row.decision_id,
        model_id=row.model_id,
        model_version=row.model_version,
        payload=dict(row.payload or {}),
        artifact_hash=row.artifact_hash,
        event_hash=row.event_hash,
    )


def _priority_level(score: int) -> str:
    return "critical" if score >= 80 else "high" if score >= 55 else "medium" if score >= 30 else "low"


def _collection_window(
    *,
    limit: int,
    returned: int,
    truncated: bool,
    ordering: str,
    scope: str,
    parent_complete: bool = True,
    exact_total: int | None = None,
) -> InvestigatorCollectionWindow:
    complete = parent_complete and not truncated
    if exact_total is None:
        total = returned + int(truncated)
        total_is_lower_bound = truncated or not parent_complete
    else:
        total = exact_total
        total_is_lower_bound = False
    return InvestigatorCollectionWindow(
        limit=limit,
        returned=returned,
        total=total,
        total_is_lower_bound=total_is_lower_bound,
        truncated=truncated,
        complete=complete,
        ordering=ordering,
        scope=scope,
    )


def _review_integrity(rows: list[DecisionReviewEvent]) -> tuple[str, list[dict[str, Any]]]:
    if not rows:
        return "missing", []
    violations: list[dict[str, Any]] = []
    previous_hash: str | None = None
    for expected_sequence, row in enumerate(rows, start=1):
        if row.sequence != expected_sequence:
            violations.append(
                {
                    "event_id": str(row.id),
                    "code": "sequence_gap",
                    "expected": expected_sequence,
                    "actual": row.sequence,
                }
            )
        if row.prior_event_hash != previous_hash:
            violations.append(
                {
                    "event_id": str(row.id),
                    "code": "prior_hash_mismatch",
                }
            )
        if not verify_decision_review_event(row):
            violations.append(
                {
                    "event_id": str(row.id),
                    "code": "event_hash_mismatch",
                }
            )
        previous_hash = row.event_hash
    return ("tampered" if violations else "ok"), violations


def _invalid_approval_attestations(rows: list[GateApprovalAttestation]) -> list[UUID]:
    invalid: set[UUID] = set()
    by_series: dict[str, list[GateApprovalAttestation]] = defaultdict(list)
    for row in rows:
        by_series[row.series_key].append(row)
        if not verify_approval_attestation_integrity(row):
            invalid.add(row.id)
    for series_rows in by_series.values():
        series_rows.sort(key=lambda row: row.sequence)
        previous: GateApprovalAttestation | None = None
        for expected_sequence, row in enumerate(series_rows, start=1):
            if row.sequence != expected_sequence:
                invalid.add(row.id)
            if previous is None:
                if row.supersedes_id is not None or row.prior_attestation_hash is not None:
                    invalid.add(row.id)
            elif (
                row.supersedes_id != previous.id
                or row.prior_attestation_hash != previous.attestation_hash
            ):
                invalid.add(row.id)
                invalid.add(previous.id)
            previous = row
    return sorted(invalid, key=str)


async def _visible_decision(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    decision_id: UUID,
) -> DecisionRecord | None:
    filters: list[Any] = [
        DecisionRecord.id == decision_id,
        DecisionRecord.namespace == namespace,
    ]
    _barrier_filter(filters, DecisionRecord.barrier_group, barrier_group)
    return (await db.execute(select(DecisionRecord).where(*filters))).scalar_one_or_none()


async def _visible_decision_coverage(
    db: AsyncSession,
    *,
    decision: DecisionRecord,
    barrier_group: str | None,
) -> DecisionEvidenceCoverageOut:
    set_filters: list[Any] = [
        DecisionEvidenceCoverageSet.namespace == decision.namespace,
        DecisionEvidenceCoverageSet.decision_id == decision.id,
    ]
    _barrier_filter(
        set_filters,
        DecisionEvidenceCoverageSet.barrier_group,
        barrier_group,
    )
    coverage_set = (
        await db.execute(select(DecisionEvidenceCoverageSet).where(*set_filters))
    ).scalar_one_or_none()

    kind_filters: list[Any] = [
        DecisionEvidenceKindCoverage.namespace == decision.namespace,
        DecisionEvidenceKindCoverage.decision_id == decision.id,
    ]
    _barrier_filter(
        kind_filters,
        DecisionEvidenceKindCoverage.barrier_group,
        barrier_group,
    )
    rows = list(
        (
            await db.execute(
                select(DecisionEvidenceKindCoverage)
                .where(*kind_filters)
                .order_by(DecisionEvidenceKindCoverage.kind)
                .limit(len(EVIDENCE_ARTIFACT_KINDS))
            )
        ).scalars()
    )
    return decision_coverage_out(decision, coverage_set, rows)


async def _evidence_graph(
    db: AsyncSession,
    *,
    decision: DecisionRecord,
    namespace: str,
    barrier_group: str | None,
    limit: int,
) -> tuple[
    DecisionEvidenceGraphOut,
    InvestigatorCollectionWindow,
    InvestigatorCollectionWindow,
    int | None,
]:
    filters: list[Any] = [
        DecisionEvidenceLink.namespace == namespace,
        DecisionEvidenceLink.decision_id == decision.id,
        EvidenceArtifact.namespace == namespace,
    ]
    _barrier_filter(filters, DecisionEvidenceLink.barrier_group, barrier_group)
    _barrier_filter(filters, EvidenceArtifact.barrier_group, barrier_group)
    aggregate = (
        await db.execute(
            select(
                func.count(DecisionEvidenceLink.id),
                func.count(func.distinct(DecisionEvidenceLink.artifact_id)),
                func.coalesce(
                    func.sum(
                        sql_case(
                            (DecisionEvidenceLink.relation == "direct", 1),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        sql_case(
                            (DecisionEvidenceLink.relation == "reachable", 1),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.max(DecisionEvidenceLink.risk_score),
            )
            .join(EvidenceArtifact, EvidenceArtifact.id == DecisionEvidenceLink.artifact_id)
            .where(*filters)
        )
    ).one()
    indexed_link_count = int(aggregate[0] or 0)
    indexed_artifact_count = int(aggregate[1] or 0)
    direct_count = int(aggregate[2] or 0)
    reachable_count = int(aggregate[3] or 0)
    maximum_risk = int(aggregate[4]) if aggregate[4] is not None else None

    fetched = (
        await db.execute(
            select(DecisionEvidenceLink, EvidenceArtifact)
            .join(EvidenceArtifact, EvidenceArtifact.id == DecisionEvidenceLink.artifact_id)
            .where(*filters)
            .order_by(
                DecisionEvidenceLink.relation,
                EvidenceArtifact.kind,
                EvidenceArtifact.coordinate,
                DecisionEvidenceLink.id,
            )
            .limit(limit + 1)
        )
    ).all()
    truncated = len(fetched) > limit
    rows = fetched[:limit]
    artifacts = {artifact.id: artifact for _, artifact in rows}
    legacy_ids: set[UUID] = set()
    for raw_id in decision.evidence_memory_ids or []:
        try:
            legacy_ids.add(UUID(str(raw_id)))
        except (TypeError, ValueError):
            continue
    indexed_ids: set[UUID] = set()
    if legacy_ids:
        memory_id_expression = EvidenceArtifact.metadata_["memory_id"].as_string()
        indexed_values = (
            await db.execute(
                select(memory_id_expression)
                .join(
                    DecisionEvidenceLink,
                    DecisionEvidenceLink.artifact_id == EvidenceArtifact.id,
                )
                .where(
                    *filters,
                    memory_id_expression.in_([str(value) for value in legacy_ids]),
                )
                .distinct()
            )
        ).scalars()
        for raw_memory_id in indexed_values:
            try:
                indexed_ids.add(UUID(str(raw_memory_id)))
            except (TypeError, ValueError):
                continue
    unindexed = sorted(legacy_ids - indexed_ids, key=str)
    persisted_coverage = await _visible_decision_coverage(
        db,
        decision=decision,
        barrier_group=barrier_group,
    )
    graph = DecisionEvidenceGraphOut(
        decision_id=decision.id,
        namespace=namespace,
        direct_count=direct_count,
        reachable_count=reachable_count,
        artifacts=[artifact_out(row) for row in artifacts.values()],
        links=[link_out(link, artifact) for link, artifact in rows],
        coverage=EvidenceGraphCoverage(
            indexed_links=indexed_link_count,
            indexed_artifacts=indexed_artifact_count,
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
    link_window = _collection_window(
        limit=limit,
        returned=len(rows),
        truncated=truncated,
        exact_total=indexed_link_count,
        ordering="relation asc, artifact.kind asc, artifact.coordinate asc, link.id asc",
        scope="all visible decision evidence links",
    )
    artifact_window = _collection_window(
        limit=limit,
        returned=len(artifacts),
        truncated=len(artifacts) < indexed_artifact_count,
        exact_total=indexed_artifact_count,
        ordering="first appearance in the evidence-link window",
        scope="all visible decision evidence artifacts emitted by first link appearance",
    )
    return graph, link_window, artifact_window, maximum_risk


async def _receipt_completeness(
    db: AsyncSession,
    *,
    decision: DecisionRecord,
    decision_out: DecisionOut,
    namespace: str,
    barrier_group: str | None,
    audit_report: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    memory_ids: set[UUID] = set()
    for value in decision.evidence_memory_ids or []:
        try:
            memory_ids.add(UUID(str(value)))
        except (TypeError, ValueError):
            continue
    ordered_memory_ids = sorted(memory_ids, key=str)
    inspected_ids = ordered_memory_ids[:_RECEIPT_EVIDENCE_LIMIT]
    truncated = len(ordered_memory_ids) > _RECEIPT_EVIDENCE_LIMIT
    cited: list[dict[str, Any]] = []
    if inspected_ids:
        filters: list[Any] = [Memory.namespace == namespace, Memory.id.in_(inspected_ids)]
        _barrier_filter(filters, Memory.barrier_group, barrier_group)
        rows = (
            await db.execute(select(Memory).where(*filters).order_by(Memory.id))
        ).scalars().all()
        cited = [
            {
                "id": row.id,
                "source": row.source,
                "content_hash": row.content_hash,
                "valid_from": row.valid_from,
                "valid_to": row.valid_to,
                "ingestion_time": row.ingestion_time,
                "erased_at": row.erased_at,
                "metadata": dict(row.metadata_ or {}),
            }
            for row in rows
        ]
    scope_complete = not truncated and len(cited) == len(ordered_memory_ids)
    assessment = assess_completeness(
        decision_out.model_dump(mode="json"),
        cited,
        audit_report,
        will_sign=receipt_signing_enabled(get_settings()),
    )
    if not scope_complete:
        for check in assessment["checks"]:
            if check["id"] == "sources.provenance":
                check["status"] = "missing"
        assessment["score"] = sum(
            check["weight"]
            for check in assessment["checks"]
            if check["status"] == "present"
        )
        score = int(assessment["score"])
        assessment["grade"] = (
            "A"
            if score >= 90
            else "B"
            if score >= 75
            else "C"
            if score >= 60
            else "D"
            if score >= 40
            else "F"
        )
        assessment["missing"] = [
            check["id"] for check in assessment["checks"] if check["status"] == "missing"
        ]
        assessment["status"] = "incomplete"
    assessment["evidence_scope"] = {
        "expected_references": len(ordered_memory_ids),
        "records_inspected": len(cited),
        "limit": _RECEIPT_EVIDENCE_LIMIT,
        "truncated": truncated,
        "complete": scope_complete,
        "missing_or_inaccessible_references": max(0, len(ordered_memory_ids) - len(cited)),
    }
    return assessment, scope_complete


def _risk_summary(
    *,
    completeness: dict[str, Any],
    graph: DecisionEvidenceGraphOut,
    latest_gate: str | None,
    gate_disposition_counts: dict[str, int],
    latest_review: str | None,
    open_case_count: int,
    critical_open_case_count: int,
    overdue_task_count: int,
    maximum_evidence_risk: int | None,
    integrity: InvestigatorIntegrity,
    read_model_complete: bool,
) -> InvestigatorRiskSummary:
    blockers: list[str] = []
    attention: list[str] = []
    actions: list[str] = []
    score = 0

    if integrity.audit_chain.get("status") == "tampered":
        blockers.append("audit_chain_tampered")
        actions.append("Freeze exports and investigate the audit-chain integrity violation.")
        score += 60
    elif integrity.audit_chain.get("status") != "ok":
        attention.append("audit_chain_not_fully_verified")
        actions.append("Run a full unbarriered audit-chain verification with a compliance principal.")
        score += 10
    if integrity.review_chain_status == "tampered":
        blockers.append("review_chain_tampered")
        actions.append("Quarantine the decision review history and investigate its integrity failure.")
        score += 50
    elif integrity.review_chain_status == "partial":
        attention.append("review_chain_partially_verified")
        actions.append("Verify the complete immutable review chain before relying on its integrity.")
        score += 10
    if integrity.approval_attestations_status == "invalid":
        blockers.append("approval_attestation_integrity_failure")
        actions.append("Revoke affected Gate approval series and investigate the invalid attestations.")
        score += 50
    elif integrity.approval_attestations_status == "partial":
        attention.append("approval_attestation_history_partially_verified")
        actions.append(
            "Verify every approval-attestation series before treating the approval history as valid."
        )
        score += 10
    if latest_gate == "deny":
        blockers.append("latest_gate_denied")
        actions.append("Keep the action blocked until a new policy evaluation explicitly allows it.")
        score += 40
    elif latest_gate == "review":
        attention.append("latest_gate_requires_review")
        actions.append("Resolve the Gate review outcome before executing the action.")
        score += 25
    elif latest_gate is None:
        attention.append("no_gate_evaluation")
        actions.append("Evaluate consequential follow-on actions through the runtime Gate.")
        score += 10
    if latest_review in {"overturned", "withdrawn"}:
        blockers.append(f"review_{latest_review}")
        actions.append("Prevent reuse of the outcome and open remediation for downstream effects.")
        score += 35
    elif latest_review in {None, "requested"}:
        attention.append("human_review_incomplete")
        actions.append("Complete an authenticated human review for this decision.")
        score += 15
    if critical_open_case_count:
        blockers.append("critical_investigation_open")
        actions.append("Resolve the open critical investigation and attest every remediation closure.")
        score += 45
    elif open_case_count:
        attention.append("investigation_open")
        actions.append("Advance the open investigation cases and assign every remediation task.")
        score += min(25, open_case_count * 8)
    if overdue_task_count:
        attention.append("remediation_overdue")
        actions.append("Escalate overdue remediation tasks to their owners.")
        score += min(20, overdue_task_count * 5)
    if not graph.coverage.normalized_complete:
        attention.append("evidence_graph_incomplete")
        actions.append("Backfill the remaining legacy evidence references into the normalized graph.")
        score += 15
    if maximum_evidence_risk is not None:
        score += 30 if maximum_evidence_risk >= 85 else 20 if maximum_evidence_risk >= 70 else 10 if maximum_evidence_risk >= 45 else 0
        if maximum_evidence_risk >= 70:
            attention.append("high_risk_evidence_dependency")
            actions.append("Revalidate high-risk evidence dependencies and assess their blast radius.")
    receipt_score = int(completeness.get("score", 0))
    if receipt_score < 75:
        attention.append("receipt_below_grade_b")
        actions.append("Capture the missing receipt fields before treating the record as audit-ready.")
        score += 20 if receipt_score < 60 else 10
    if not read_model_complete:
        attention.append("investigator_read_model_incomplete")
        actions.append(
            "Increase the bounded report windows or inspect the linked authoritative source APIs."
        )
        score += 10

    score = min(100, score)
    posture = "blocked" if blockers else "needs_attention" if attention else "defensible"
    return InvestigatorRiskSummary(
        posture=posture,
        priority_score=score,
        priority_level=_priority_level(score),
        receipt_grade=str(completeness.get("grade", "F")),
        receipt_score=receipt_score,
        receipt_missing=list(completeness.get("missing") or []),
        maximum_evidence_risk_score=maximum_evidence_risk,
        latest_gate_disposition=latest_gate,
        gate_disposition_counts=gate_disposition_counts,
        open_case_count=open_case_count,
        overdue_task_count=overdue_task_count,
        blockers=sorted(set(blockers)),
        attention_signals=sorted(set(attention)),
        recommended_actions=list(dict.fromkeys(actions)),
    )


async def build_decision_investigation(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    decision_id: UUID,
    timeline_limit: int,
    evidence_limit: int,
    control_history_limit: int,
    case_limit: int,
    task_limit: int,
    closure_limit: int,
    include_sensitive: bool,
    verify_audit: bool,
) -> DecisionInvestigationReport | None:
    decision = await _visible_decision(
        db,
        namespace=namespace,
        barrier_group=barrier_group,
        decision_id=decision_id,
    )
    if decision is None:
        return None
    await assert_decision_record_integrity(db, decision)
    output_decision = _decision_out(decision)
    graph, evidence_link_window, evidence_artifact_window, maximum_evidence_risk = (
        await _evidence_graph(
            db,
            decision=decision,
            namespace=namespace,
            barrier_group=barrier_group,
            limit=evidence_limit,
        )
    )

    scoped_models = (
        (
            LedgerEvent,
            LedgerEvent.decision_id == decision_id,
            (LedgerEvent.occurred_at.asc(), LedgerEvent.id.asc()),
            timeline_limit,
        ),
        (
            GateDecisionRecord,
            GateDecisionRecord.decision_id == decision_id,
            (GateDecisionRecord.evaluated_at.desc(), GateDecisionRecord.id.desc()),
            control_history_limit,
        ),
        (
            GateApprovalAttestation,
            GateApprovalAttestation.decision_id == decision_id,
            # Series-first ordering guarantees that a bounded read contains
            # complete series prefixes starting at sequence one. It can be
            # labeled partial safely without manufacturing chain violations.
            (
                GateApprovalAttestation.series_key.asc(),
                GateApprovalAttestation.sequence.asc(),
                GateApprovalAttestation.id.asc(),
            ),
            control_history_limit,
        ),
        (
            DecisionReviewEvent,
            DecisionReviewEvent.decision_id == decision_id,
            (DecisionReviewEvent.sequence.asc(), DecisionReviewEvent.id.asc()),
            control_history_limit,
        ),
    )
    collected: dict[Any, list[Any]] = {}
    collection_truncated: dict[Any, bool] = {}
    collection_filters: dict[Any, list[Any]] = {}
    for model, decision_filter, ordering, limit in scoped_models:
        filters: list[Any] = [model.namespace == namespace, decision_filter]
        _barrier_filter(filters, model.barrier_group, barrier_group)
        fetched = list(
            (
                await db.execute(
                    select(model).where(*filters).order_by(*ordering).limit(limit + 1)
                )
            ).scalars()
        )
        collected[model] = fetched[:limit]
        collection_truncated[model] = len(fetched) > limit
        collection_filters[model] = filters

    gate_rows: list[GateDecisionRecord] = collected[GateDecisionRecord]
    approval_rows: list[GateApprovalAttestation] = collected[GateApprovalAttestation]
    review_rows: list[DecisionReviewEvent] = collected[DecisionReviewEvent]

    visible_gate_ids = select(GateDecisionRecord.id).where(
        *collection_filters[GateDecisionRecord]
    )
    case_filters: list[Any] = [
        InvestigationCase.namespace == namespace,
        or_(
            InvestigationCase.decision_id == decision_id,
            InvestigationCase.gate_decision_id.in_(visible_gate_ids),
        ),
    ]
    _barrier_filter(case_filters, InvestigationCase.barrier_group, barrier_group)
    fetched_cases = list(
        (
            await db.execute(
                select(InvestigationCase)
                .where(*case_filters)
                .order_by(InvestigationCase.opened_at.desc(), InvestigationCase.id.desc())
                .limit(case_limit + 1)
            )
        ).scalars()
    )
    cases_truncated = len(fetched_cases) > case_limit
    case_rows = fetched_cases[:case_limit]
    case_ids = [row.id for row in case_rows]

    task_rows: list[RemediationTask] = []
    tasks_truncated = False
    if case_ids:
        task_filters: list[Any] = [
            RemediationTask.namespace == namespace,
            RemediationTask.case_id.in_(case_ids),
        ]
        _barrier_filter(task_filters, RemediationTask.barrier_group, barrier_group)
        fetched_tasks = list(
            (
                await db.execute(
                    select(RemediationTask)
                    .where(*task_filters)
                    .order_by(RemediationTask.created_at.asc(), RemediationTask.id.asc())
                    .limit(task_limit + 1)
                )
            ).scalars()
        )
        tasks_truncated = len(fetched_tasks) > task_limit
        task_rows = fetched_tasks[:task_limit]

    task_ids = [row.id for row in task_rows]
    closure_rows: list[ControlClosureAttestation] = []
    closures_truncated = False
    closure_resources: list[Any] = []
    if case_ids:
        closure_resources.append(
            and_(
                ControlClosureAttestation.resource_type == "case",
                ControlClosureAttestation.resource_id.in_(case_ids),
            )
        )
    if task_ids:
        closure_resources.append(
            and_(
                ControlClosureAttestation.resource_type == "task",
                ControlClosureAttestation.resource_id.in_(task_ids),
            )
        )
    if closure_resources:
        closure_filters: list[Any] = [
            ControlClosureAttestation.namespace == namespace,
            or_(*closure_resources),
        ]
        _barrier_filter(closure_filters, ControlClosureAttestation.barrier_group, barrier_group)
        fetched_closures = list(
            (
                await db.execute(
                    select(ControlClosureAttestation)
                    .where(*closure_filters)
                    .order_by(
                        ControlClosureAttestation.attested_at.asc(),
                        ControlClosureAttestation.id.asc(),
                    )
                    .limit(closure_limit + 1)
                )
            ).scalars()
        )
        closures_truncated = len(fetched_closures) > closure_limit
        closure_rows = fetched_closures[:closure_limit]

    # Risk facts are independent of the embedded response windows. A smaller
    # packet must not hide a denial, critical case, overdue task, or high-risk
    # dependency and thereby produce a more favorable decision posture.
    gate_disposition_counts = {
        disposition: int(count)
        for disposition, count in (
            await db.execute(
                select(GateDecisionRecord.disposition, func.count(GateDecisionRecord.id))
                .where(*collection_filters[GateDecisionRecord])
                .group_by(GateDecisionRecord.disposition)
            )
        ).all()
    }
    latest_gate = gate_rows[0].disposition if gate_rows else None
    if collection_truncated[DecisionReviewEvent]:
        latest_review = (
            await db.execute(
                select(DecisionReviewEvent.status)
                .where(*collection_filters[DecisionReviewEvent])
                .order_by(DecisionReviewEvent.sequence.desc(), DecisionReviewEvent.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    else:
        latest_review = review_rows[-1].status if review_rows else None

    open_case_summary = (
        await db.execute(
            select(
                func.count(InvestigationCase.id),
                func.coalesce(
                    func.sum(
                        sql_case(
                            (InvestigationCase.severity == "critical", 1),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(*case_filters, InvestigationCase.status != "closed")
        )
    ).one()
    open_case_count = int(open_case_summary[0] or 0)
    critical_open_case_count = int(open_case_summary[1] or 0)

    all_linked_case_ids = select(InvestigationCase.id).where(*case_filters)
    overdue_task_filters: list[Any] = [
        RemediationTask.namespace == namespace,
        RemediationTask.case_id.in_(all_linked_case_ids),
        RemediationTask.status != "closed",
        RemediationTask.due_at.is_not(None),
        RemediationTask.due_at < datetime.now(UTC),
    ]
    _barrier_filter(overdue_task_filters, RemediationTask.barrier_group, barrier_group)
    overdue_task_count = int(
        (
            await db.execute(
                select(func.count(RemediationTask.id)).where(*overdue_task_filters)
            )
        ).scalar_one()
        or 0
    )

    if verify_audit and barrier_group is None:
        audit_report = await verify_chain(
            db,
            namespace,
            max_response_bytes=get_settings().audit_export_page_bytes_limit,
        )
    elif verify_audit:
        audit_report = {
            "namespace": namespace,
            "status": "unavailable_for_barrier_scope",
            "rows_checked": 0,
            "truncated": False,
            "violations": [],
        }
    else:
        audit_report = {
            "namespace": namespace,
            "status": "unchecked",
            "rows_checked": 0,
            "truncated": False,
            "violations": [],
        }
    review_status, review_violations = _review_integrity(review_rows)
    if collection_truncated[DecisionReviewEvent] and review_status != "tampered":
        review_status = "partial"
    invalid_approval_ids = _invalid_approval_attestations(approval_rows)
    if invalid_approval_ids:
        approval_status = "invalid"
        approvals_valid: bool | None = False
    elif collection_truncated[GateApprovalAttestation]:
        approval_status = "partial"
        approvals_valid = None
    elif approval_rows:
        approval_status = "valid"
        approvals_valid = True
    else:
        approval_status = "missing"
        approvals_valid = None
    integrity = InvestigatorIntegrity(
        audit_chain=audit_report,
        review_chain_status=review_status,
        review_chain_violations=review_violations,
        approval_attestations_status=approval_status,
        approval_attestations_valid=approvals_valid,
        invalid_approval_attestation_ids=invalid_approval_ids,
    )
    completeness, receipt_evidence_scope_complete = await _receipt_completeness(
        db,
        decision=decision,
        decision_out=output_decision,
        namespace=namespace,
        barrier_group=barrier_group,
        audit_report=audit_report,
    )

    timeline_window = _collection_window(
        limit=timeline_limit,
        returned=len(collected[LedgerEvent]),
        truncated=collection_truncated[LedgerEvent],
        ordering="occurred_at asc, id asc",
        scope="visible ledger events linked to this decision",
    )
    gate_window = _collection_window(
        limit=control_history_limit,
        returned=len(gate_rows),
        truncated=collection_truncated[GateDecisionRecord],
        ordering="evaluated_at desc, id desc",
        scope="visible Gate evaluations linked to this decision",
    )
    approval_window = _collection_window(
        limit=control_history_limit,
        returned=len(approval_rows),
        truncated=collection_truncated[GateApprovalAttestation],
        ordering="series_key asc, sequence asc, id asc",
        scope="visible approval-attestation history linked to this decision",
    )
    review_window = _collection_window(
        limit=control_history_limit,
        returned=len(review_rows),
        truncated=collection_truncated[DecisionReviewEvent],
        ordering="sequence asc, id asc",
        scope="visible immutable review history linked to this decision",
    )
    case_window = _collection_window(
        limit=case_limit,
        returned=len(case_rows),
        truncated=cases_truncated,
        ordering="opened_at desc, id desc",
        scope="visible direct and Gate-linked investigation cases",
    )
    task_window = _collection_window(
        limit=task_limit,
        returned=len(task_rows),
        truncated=tasks_truncated,
        parent_complete=case_window.complete,
        ordering="created_at asc, id asc",
        scope="visible remediation tasks belonging to the returned case window",
    )
    closure_window = _collection_window(
        limit=closure_limit,
        returned=len(closure_rows),
        truncated=closures_truncated,
        parent_complete=case_window.complete and task_window.complete,
        ordering="attested_at asc, id asc",
        scope="visible attestations belonging to returned cases and tasks",
    )
    windows = (
        evidence_link_window,
        evidence_artifact_window,
        timeline_window,
        gate_window,
        approval_window,
        review_window,
        case_window,
        task_window,
        closure_window,
    )
    report_coverage = InvestigatorReportCoverage(
        complete=(
            audit_report.get("status") == "ok"
            and receipt_evidence_scope_complete
            and all(item.complete for item in windows)
        ),
        audit_scope_complete=audit_report.get("status") == "ok",
        receipt_evidence_scope_complete=receipt_evidence_scope_complete,
        evidence_links=evidence_link_window,
        evidence_artifacts=evidence_artifact_window,
        timeline=timeline_window,
        gate_evaluations=gate_window,
        approval_attestations=approval_window,
        review_history=review_window,
        cases=case_window,
        remediation_tasks=task_window,
        closure_attestations=closure_window,
    )
    risk = _risk_summary(
        completeness=completeness,
        graph=graph,
        latest_gate=latest_gate,
        gate_disposition_counts=gate_disposition_counts,
        latest_review=latest_review,
        open_case_count=open_case_count,
        critical_open_case_count=critical_open_case_count,
        overdue_task_count=overdue_task_count,
        maximum_evidence_risk=maximum_evidence_risk,
        integrity=integrity,
        read_model_complete=report_coverage.complete,
    )

    tasks_by_case: dict[UUID, list[RemediationTask]] = defaultdict(list)
    for task in task_rows:
        tasks_by_case[task.case_id].append(task)
    closures_by_resource: dict[
        tuple[str, UUID], list[ControlClosureAttestation]
    ] = defaultdict(list)
    for closure in closure_rows:
        closures_by_resource[(closure.resource_type, closure.resource_id)].append(closure)
    case_bundles: list[InvestigatorCaseBundle] = []
    for case in case_rows:
        tasks = tasks_by_case.get(case.id, [])
        relevant_closures = [
            *closures_by_resource.get(("case", case.id), []),
            *(
                closure
                for task in tasks
                for closure in closures_by_resource.get(("task", task.id), [])
            ),
        ]
        case_bundles.append(
            InvestigatorCaseBundle(
                case=InvestigationCaseOut.model_validate(case),
                tasks=[RemediationTaskOut.model_validate(task) for task in tasks],
                closures=[
                    InvestigatorClosureOut(
                        id=row.id,
                        resource_type=row.resource_type,
                        resource_id=row.resource_id,
                        attested_by=row.attested_by,
                        statement=closure_statement(row) if include_sensitive else None,
                        statement_sha256=(
                            row.statement_hash
                            or hashlib.sha256(
                                closure_statement(row).encode("utf-8")
                            ).hexdigest()
                        ),
                        evidence_refs=list(row.evidence_refs or []),
                        attestation_hash=row.attestation_hash,
                        integrity_valid=verify_closure_attestation_integrity(row),
                        attested_at=row.attested_at,
                    )
                    for row in relevant_closures
                ],
            )
        )

    base = f"/v1/decisions/{decision_id}"
    disclosures = [
        "Investigator is a derived read model; authoritative records remain append-only in their source APIs.",
        "Receipt completeness measures captured evidence, not whether the decision was substantively correct.",
        "The coverage object is authoritative for every embedded collection's deterministic read window.",
    ]
    if barrier_group is not None:
        disclosures.append(
            "Namespace-wide audit verification is intentionally unavailable to a barrier-scoped principal."
        )
    if not report_coverage.complete:
        disclosures.append(
            "One or more embedded collections are incomplete; increase the bounded limits or inspect the linked authoritative records before treating this packet as complete."
        )
    return DecisionInvestigationReport(
        generated_at=datetime.now(UTC),
        decision=output_decision,
        risk=risk,
        receipt_completeness=completeness,
        coverage=report_coverage,
        evidence_graph=graph,
        timeline=[_ledger_out(row) for row in collected[LedgerEvent]],
        gate_evaluations=[GateDecisionOut.model_validate(row) for row in gate_rows],
        approval_attestations=[
            gate_approval_out(row, include_statement=include_sensitive) for row in approval_rows
        ],
        review_history=[
            decision_review_event_out(row, include_note=include_sensitive) for row in review_rows
        ],
        cases=case_bundles,
        integrity=integrity,
        links=InvestigatorLinks(
            decision=base,
            receipt=f"{base}/receipt",
            evidence_pack=f"{base}/evidence-pack",
            evidence_graph=f"{base}/evidence-graph",
            timeline=f"/v1/records/events?decision_id={decision_id}",
            review_history=f"{base}/review-history",
            gate_evaluations=f"/v1/control/gate/evaluations?decision_id={decision_id}",
            approval_attestations=(
                f"/v1/control/gate/approvals?decision_id={decision_id}&only_current=false"
            ),
            cases=f"/v1/control/investigations/cases?decision_id={decision_id}",
        ),
        disclosures=disclosures,
    )


async def build_investigator_queue(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    limit: int,
    scan_limit: int,
) -> InvestigatorQueueOut:
    decision_filters: list[Any] = [DecisionRecord.namespace == namespace]
    _barrier_filter(decision_filters, DecisionRecord.barrier_group, barrier_group)
    fetched = list(
        (
            await db.execute(
                select(DecisionRecord)
                .where(*decision_filters)
                .order_by(DecisionRecord.decided_at.desc(), DecisionRecord.id.desc())
                .limit(scan_limit + 1)
            )
        ).scalars()
    )
    scan_truncated = len(fetched) > scan_limit
    decisions = fetched[:scan_limit]
    ids = [row.id for row in decisions]
    if not ids:
        return InvestigatorQueueOut(
            generated_at=datetime.now(UTC),
            items=[],
            candidates_scanned=0,
            scan_limit=scan_limit,
            scan_truncated=False,
            total_is_lower_bound=False,
        )

    # Queue work must remain proportional to ``scan_limit``, not to the entire
    # history attached to each candidate. Rank or aggregate in the database so
    # a heavily evaluated decision cannot make this read model consume
    # unbounded process memory.
    gate_filters: list[Any] = [
        GateDecisionRecord.namespace == namespace,
        GateDecisionRecord.decision_id.in_(ids),
    ]
    _barrier_filter(gate_filters, GateDecisionRecord.barrier_group, barrier_group)
    ranked_gates = (
        select(
            GateDecisionRecord.decision_id.label("decision_id"),
            GateDecisionRecord.disposition.label("disposition"),
            func.row_number()
            .over(
                partition_by=GateDecisionRecord.decision_id,
                order_by=(
                    GateDecisionRecord.evaluated_at.desc(),
                    GateDecisionRecord.id.desc(),
                ),
            )
            .label("queue_rank"),
        )
        .where(*gate_filters)
        .subquery()
    )
    latest_gate_by_decision = {
        decision_id: disposition
        for decision_id, disposition in (
            await db.execute(
                select(ranked_gates.c.decision_id, ranked_gates.c.disposition).where(
                    ranked_gates.c.queue_rank == 1
                )
            )
        ).all()
    }

    review_filters: list[Any] = [
        DecisionReviewEvent.namespace == namespace,
        DecisionReviewEvent.decision_id.in_(ids),
    ]
    _barrier_filter(review_filters, DecisionReviewEvent.barrier_group, barrier_group)
    ranked_reviews = (
        select(
            DecisionReviewEvent.decision_id.label("decision_id"),
            DecisionReviewEvent.status.label("status"),
            func.row_number()
            .over(
                partition_by=DecisionReviewEvent.decision_id,
                order_by=(
                    DecisionReviewEvent.sequence.desc(),
                    DecisionReviewEvent.reviewed_at.desc(),
                    DecisionReviewEvent.id.desc(),
                ),
            )
            .label("queue_rank"),
        )
        .where(*review_filters)
        .subquery()
    )
    latest_review_by_decision = {
        decision_id: status
        for decision_id, status in (
            await db.execute(
                select(
                    ranked_reviews.c.decision_id,
                    ranked_reviews.c.status,
                ).where(ranked_reviews.c.queue_rank == 1)
            )
        ).all()
    }

    # Cases may point directly to a decision or indirectly through a Gate
    # evaluation. Resolve both forms in one bounded aggregate without loading
    # case descriptions or every historical evaluation into memory.
    gate_case_join: Any = and_(
        GateDecisionRecord.id == InvestigationCase.gate_decision_id,
        GateDecisionRecord.namespace == InvestigationCase.namespace,
    )
    if barrier_group is not None:
        gate_case_join = and_(
            gate_case_join,
            or_(
                GateDecisionRecord.barrier_group.is_(None),
                GateDecisionRecord.barrier_group == barrier_group,
            ),
        )
    case_decision_id = func.coalesce(
        InvestigationCase.decision_id,
        GateDecisionRecord.decision_id,
    )
    case_filters: list[Any] = [
        InvestigationCase.namespace == namespace,
        InvestigationCase.status != "closed",
        case_decision_id.in_(ids),
    ]
    _barrier_filter(case_filters, InvestigationCase.barrier_group, barrier_group)
    case_aggregates = {
        decision_id: (int(open_count), int(critical_count or 0))
        for decision_id, open_count, critical_count in (
            await db.execute(
                select(
                    case_decision_id.label("decision_id"),
                    func.count(InvestigationCase.id),
                    func.coalesce(
                        func.sum(
                            sql_case(
                                (InvestigationCase.severity == "critical", 1),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                )
                .outerjoin(GateDecisionRecord, gate_case_join)
                .where(*case_filters)
                .group_by(case_decision_id)
            )
        ).all()
    }

    evidence_filters: list[Any] = [
        DecisionEvidenceLink.namespace == namespace,
        DecisionEvidenceLink.decision_id.in_(ids),
        EvidenceArtifact.namespace == namespace,
    ]
    _barrier_filter(evidence_filters, DecisionEvidenceLink.barrier_group, barrier_group)
    _barrier_filter(evidence_filters, EvidenceArtifact.barrier_group, barrier_group)
    maximum_evidence_risk_by_decision = {
        decision_id: int(maximum_risk)
        for decision_id, maximum_risk in (
            await db.execute(
                select(
                    DecisionEvidenceLink.decision_id,
                    func.max(DecisionEvidenceLink.risk_score),
                )
                .join(
                    EvidenceArtifact,
                    and_(
                        EvidenceArtifact.id == DecisionEvidenceLink.artifact_id,
                        EvidenceArtifact.namespace == DecisionEvidenceLink.namespace,
                    ),
                )
                .where(*evidence_filters)
                .group_by(DecisionEvidenceLink.decision_id)
            )
        ).all()
        if maximum_risk is not None
    }

    coverage_filters: list[Any] = [
        DecisionEvidenceKindCoverage.namespace == namespace,
        DecisionEvidenceKindCoverage.decision_id.in_(ids),
        DecisionEvidenceKindCoverage.status == "complete",
    ]
    _barrier_filter(
        coverage_filters,
        DecisionEvidenceKindCoverage.barrier_group,
        barrier_group,
    )
    complete_kind_counts = {
        decision_id: int(complete_count)
        for decision_id, complete_count in (
            await db.execute(
                select(
                    DecisionEvidenceKindCoverage.decision_id,
                    func.count(func.distinct(DecisionEvidenceKindCoverage.kind)),
                )
                .where(*coverage_filters)
                .group_by(DecisionEvidenceKindCoverage.decision_id)
            )
        ).all()
    }

    items: list[InvestigatorQueueItem] = []
    for decision in decisions:
        signals: list[str] = []
        score = 0
        latest_gate = latest_gate_by_decision.get(decision.id)
        if latest_gate == "deny":
            signals.append("latest_gate_denied")
            score += 40
        elif latest_gate == "review":
            signals.append("latest_gate_requires_review")
            score += 25
        elif latest_gate is None:
            signals.append("no_gate_evaluation")
            score += 10
        open_case_count, critical_case_count = case_aggregates.get(
            decision.id,
            (0, 0),
        )
        if critical_case_count:
            signals.append("critical_investigation_open")
            score += 45
        elif open_case_count:
            signals.append("investigation_open")
            score += min(25, open_case_count * 8)
        review_status = latest_review_by_decision.get(
            decision.id,
            decision.human_review_status,
        )
        if review_status in {"overturned", "withdrawn"}:
            signals.append(f"review_{review_status}")
            score += 35
        elif review_status in {"not_requested", "requested"}:
            signals.append("human_review_incomplete")
            score += 15
        max_risk = maximum_evidence_risk_by_decision.get(decision.id)
        if max_risk is not None:
            score += 30 if max_risk >= 85 else 20 if max_risk >= 70 else 10 if max_risk >= 45 else 0
            if max_risk >= 70:
                signals.append("high_risk_evidence_dependency")
        # The evidence graph owns eight independent completeness claims. An
        # empty legacy-memory list says nothing about policy, model, tool,
        # permission, instruction, input, or output normalization.
        normalized = complete_kind_counts.get(decision.id, 0) == len(
            EVIDENCE_ARTIFACT_KINDS
        )
        if not normalized:
            signals.append("evidence_graph_incomplete")
            score += 15
        score = min(100, score)
        blocker_signals = {
            "latest_gate_denied",
            "critical_investigation_open",
            "review_overturned",
            "review_withdrawn",
        }
        posture = "blocked" if blocker_signals.intersection(signals) else "needs_attention" if signals else "defensible"
        items.append(
            InvestigatorQueueItem(
                decision=_decision_out(decision),
                priority_score=score,
                priority_level=_priority_level(score),
                posture=posture,
                signals=sorted(set(signals)),
                latest_gate_disposition=latest_gate,
                open_case_count=open_case_count,
                maximum_evidence_risk_score=max_risk,
                review_status=review_status,
                normalized_evidence_complete=normalized,
            )
        )
    items.sort(
        key=lambda item: (
            item.priority_score,
            item.decision.decided_at,
            str(item.decision.id),
        ),
        reverse=True,
    )
    return InvestigatorQueueOut(
        generated_at=datetime.now(UTC),
        items=items[:limit],
        candidates_scanned=len(decisions),
        scan_limit=scan_limit,
        scan_truncated=scan_truncated,
        total_is_lower_bound=scan_truncated,
    )
