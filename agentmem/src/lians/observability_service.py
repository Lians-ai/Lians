"""Refresh bounded operational inventory from durable database state.

Prometheus gauges must not pretend that one process owns a distributed queue.
This refresher aggregates authoritative rows across all tenant boundaries under
the internal admin RLS sentinel and publishes only closed status vocabularies.
No tenant, resource, destination, error, or policy value reaches a metric.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .control_models import (
    ControlClosureAttestation,
    GateExecutionPermitConsumption,
    InvestigationCase,
    RemediationTask,
)
from .db import set_current_barrier_group, set_current_namespace
from .evidence_models import (
    DecisionEvidenceKindCoverage,
    DecisionImpactAssessmentJob,
    DecisionImpactAssessmentMatch,
)
from .integration_models import IntegrationDelivery, IntegrationOutboxEvent
from .enterprise_models import ScimTenantReconciliationJob
from .metrics import (
    record_inventory_refresh,
    set_conflict_inventory,
    set_impact_inventory,
    set_integration_inventory,
    set_product_inventory,
    set_recorder_index_inventory,
    set_recorder_inventory,
    set_scim_reconciliation_inventory,
    set_subject_erasure_inventory,
)
from .models import ConflictFlag, DecisionRecord
from .recorder_models import RecorderEvent, RecorderEvidenceIndexJob, RecorderRun
from .subject_erasure_models import SubjectErasureJob

logger = logging.getLogger("lians.observability")

_refresher_running = False
_refresher_last_heartbeat_at: datetime | None = None
_refresher_last_iteration_healthy = False


def _exception_digest(exc: BaseException) -> str:
    """Return a stable failure correlation value without logging a traceback."""

    error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
    return hashlib.sha256(error_type.encode()).hexdigest()[:16]


async def refresh_durable_inventory(db: AsyncSession) -> None:
    """Publish one internally consistent-enough aggregate inventory snapshot."""

    conflict_counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(ConflictFlag.status, func.count(ConflictFlag.id)).group_by(
                    ConflictFlag.status
                )
            )
        ).all()
    }

    recorder_run_counts = {"ready": 0, "waiting": 0}
    for ready, count in (
        await db.execute(
            select(RecorderRun.receipt_ready, func.count(RecorderRun.id)).group_by(
                RecorderRun.receipt_ready
            )
        )
    ).all():
        recorder_run_counts["ready" if ready else "waiting"] = int(count)
    recorder_capture_counts = {
        str(mode): int(count)
        for mode, count in (
            await db.execute(
                select(RecorderEvent.capture_mode, func.count(RecorderEvent.id)).group_by(
                    RecorderEvent.capture_mode
                )
            )
        ).all()
    }

    integration_counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(IntegrationDelivery.status, func.count(IntegrationDelivery.id)).group_by(
                    IntegrationDelivery.status
                )
            )
        ).all()
    }
    integration_outbox_events = int(
        (await db.execute(select(func.count(IntegrationOutboxEvent.id)))).scalar_one()
    )
    due_at = case(
        (IntegrationDelivery.status == "leased", IntegrationDelivery.lease_expires_at),
        else_=IntegrationDelivery.next_attempt_at,
    )
    integration_oldest_due = (
        await db.execute(
            select(func.min(due_at)).where(
                IntegrationDelivery.status.in_(("pending", "leased", "retry"))
            )
        )
    ).scalar_one_or_none()

    impact_counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(
                    DecisionImpactAssessmentJob.status,
                    func.count(DecisionImpactAssessmentJob.id),
                ).group_by(DecisionImpactAssessmentJob.status)
            )
        ).all()
    }
    active_impact = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(DecisionImpactAssessmentJob.decisions_scanned), 0
                ),
                func.coalesce(
                    func.sum(DecisionImpactAssessmentJob.snapshot_decision_count), 0
                ),
                func.min(DecisionImpactAssessmentJob.created_at),
            ).where(DecisionImpactAssessmentJob.status.in_(("pending", "running")))
        )
    ).one()
    progress_numerator = int(active_impact[0] or 0)
    progress_denominator = int(active_impact[1] or 0)
    progress_ratio = (
        progress_numerator / progress_denominator if progress_denominator > 0 else 0.0
    )

    recorder_index_counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(
                    RecorderEvidenceIndexJob.status,
                    func.count(RecorderEvidenceIndexJob.id),
                ).group_by(RecorderEvidenceIndexJob.status)
            )
        ).all()
    }
    active_recorder_index = (
        await db.execute(
            select(
                func.coalesce(func.sum(RecorderEvidenceIndexJob.events_indexed), 0),
                func.coalesce(
                    func.sum(RecorderEvidenceIndexJob.snapshot_event_count), 0
                ),
                func.min(RecorderEvidenceIndexJob.created_at),
            ).where(RecorderEvidenceIndexJob.status.in_(("pending", "running")))
        )
    ).one()

    subject_erasure_counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(
                    SubjectErasureJob.status,
                    func.count(SubjectErasureJob.id),
                ).group_by(SubjectErasureJob.status)
            )
        ).all()
    }
    active_subject_erasure = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        SubjectErasureJob.memories_scrubbed
                        + SubjectErasureJob.live_facts_scrubbed
                        + SubjectErasureJob.relationships_scrubbed
                        + SubjectErasureJob.pending_admissions_scrubbed
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        SubjectErasureJob.snapshot_memory_count
                        + SubjectErasureJob.snapshot_live_fact_count
                        + SubjectErasureJob.snapshot_relationship_count
                        + SubjectErasureJob.snapshot_pending_admission_count
                    ),
                    0,
                ),
                func.min(SubjectErasureJob.created_at),
            ).where(SubjectErasureJob.status.in_(("pending", "running")))
        )
    ).one()

    scim_reconciliation_counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(
                    ScimTenantReconciliationJob.status,
                    func.count(ScimTenantReconciliationJob.id),
                ).group_by(ScimTenantReconciliationJob.status)
            )
        ).all()
    }
    active_scim_reconciliation = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(ScimTenantReconciliationJob.users_reconciled), 0
                ),
                func.coalesce(
                    func.sum(ScimTenantReconciliationJob.snapshot_user_count), 0
                ),
                func.min(ScimTenantReconciliationJob.created_at),
            ).where(
                ScimTenantReconciliationJob.status.in_(("pending", "running"))
            )
        )
    ).one()

    protected_decisions = int(
        (await db.execute(select(func.count(DecisionRecord.id)))).scalar_one()
    )
    complete_coverage = (
        select(DecisionEvidenceKindCoverage.decision_id)
        .where(DecisionEvidenceKindCoverage.status == "complete")
        .group_by(
            DecisionEvidenceKindCoverage.namespace,
            DecisionEvidenceKindCoverage.decision_id,
        )
        .having(func.count(func.distinct(DecisionEvidenceKindCoverage.kind)) == 8)
        .subquery()
    )
    evidence_complete_decisions = int(
        (
            await db.execute(select(func.count()).select_from(complete_coverage))
        ).scalar_one()
    )
    protected_actions = int(
        (
            await db.execute(select(func.count(GateExecutionPermitConsumption.id)))
        ).scalar_one()
    )
    impact_matches = int(
        (
            await db.execute(select(func.count(DecisionImpactAssessmentMatch.sequence)))
        ).scalar_one()
    )
    investigation_counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(InvestigationCase.status, func.count(InvestigationCase.id)).group_by(
                    InvestigationCase.status
                )
            )
        ).all()
    }
    remediation_counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(RemediationTask.status, func.count(RemediationTask.id)).group_by(
                    RemediationTask.status
                )
            )
        ).all()
    }
    now = datetime.now(UTC)
    overdue_tasks = int(
        (
            await db.execute(
                select(func.count(RemediationTask.id)).where(
                    RemediationTask.status != "closed",
                    RemediationTask.due_at.is_not(None),
                    RemediationTask.due_at < now,
                )
            )
        ).scalar_one()
    )
    closure_attestations = int(
        (
            await db.execute(select(func.count(ControlClosureAttestation.id)))
        ).scalar_one()
    )

    # Publish only after every query succeeds.  A failed refresh therefore
    # leaves the last complete snapshot in place and flips the freshness/health
    # signal instead of mixing old and new inventory families.
    set_conflict_inventory(conflict_counts)
    set_recorder_inventory(
        run_counts=recorder_run_counts,
        capture_counts=recorder_capture_counts,
    )
    set_integration_inventory(
        counts=integration_counts,
        outbox_events=integration_outbox_events,
        oldest_due_at=integration_oldest_due,
    )
    set_impact_inventory(
        counts=impact_counts,
        progress_ratio=progress_ratio,
        oldest_active_at=active_impact[2],
    )
    set_recorder_index_inventory(
        counts=recorder_index_counts,
        events_indexed=int(active_recorder_index[0] or 0),
        snapshot_events=int(active_recorder_index[1] or 0),
        oldest_active_at=active_recorder_index[2],
    )
    set_subject_erasure_inventory(
        counts=subject_erasure_counts,
        rows_scrubbed=int(active_subject_erasure[0] or 0),
        snapshot_rows=int(active_subject_erasure[1] or 0),
        oldest_active_at=active_subject_erasure[2],
    )
    set_scim_reconciliation_inventory(
        counts=scim_reconciliation_counts,
        users_reconciled=int(active_scim_reconciliation[0] or 0),
        snapshot_users=int(active_scim_reconciliation[1] or 0),
        oldest_active_at=active_scim_reconciliation[2],
    )
    set_product_inventory(
        protected_decisions=protected_decisions,
        evidence_complete_decisions=evidence_complete_decisions,
        protected_actions=protected_actions,
        impact_matches=impact_matches,
        investigation_counts=investigation_counts,
        remediation_counts=remediation_counts,
        overdue_tasks=overdue_tasks,
        closure_attestations=closure_attestations,
    )


async def run_durable_inventory_refresher(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval_seconds: float,
) -> None:
    """Continuously refresh durable gauges; failures never kill the loop."""

    global _refresher_last_heartbeat_at, _refresher_last_iteration_healthy
    global _refresher_running

    interval = max(5.0, min(300.0, interval_seconds))
    _refresher_running = True
    _refresher_last_heartbeat_at = datetime.now(UTC)
    _refresher_last_iteration_healthy = False
    logger.info("Durable observability inventory refresher started")
    try:
        while True:
            try:
                set_current_namespace("__admin__")
                set_current_barrier_group(None)
                try:
                    async with session_factory() as db:
                        await refresh_durable_inventory(db)
                    now = datetime.now(UTC)
                    _refresher_last_heartbeat_at = now
                    _refresher_last_iteration_healthy = True
                    record_inventory_refresh("success", at=now)
                finally:
                    set_current_namespace(None)
                    set_current_barrier_group(None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _refresher_last_heartbeat_at = datetime.now(UTC)
                _refresher_last_iteration_healthy = False
                record_inventory_refresh("failure")
                logger.error(
                    "Durable observability inventory refresh failed",
                    extra={"error_digest": _exception_digest(exc)},
                )
            await asyncio.sleep(interval)
    finally:
        _refresher_running = False
        _refresher_last_iteration_healthy = False


def durable_inventory_refresher_status() -> tuple[bool, datetime | None]:
    """Return bounded per-process liveness/refresh health for readiness."""

    from .config import get_settings

    heartbeat = _refresher_last_heartbeat_at
    threshold = max(30.0, get_settings().observability_refresh_seconds * 5)
    fresh = heartbeat is not None and (
        datetime.now(UTC) - heartbeat
    ).total_seconds() <= threshold
    return (
        _refresher_running and _refresher_last_iteration_healthy and fresh,
        heartbeat,
    )
