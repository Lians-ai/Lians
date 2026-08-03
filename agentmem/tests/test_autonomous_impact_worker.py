"""Deferred positive and adversarial contracts for autonomous impact work.

These tests are authored during implementation and intentionally run only in
the later comprehensive validation campaign.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from lians.api.deps import AuthContext
from lians.api.routes_decisions import _complete_impact_assessment
from lians.decision_record_integrity import compute_decision_record_hash
from lians.evidence_schemas import ExhaustiveImpactAssessmentCreate
from lians.evidence_service import create_impact_assessment_job, index_decision_evidence
from lians.impact_assessment_service import (
    ImpactSnapshotInvariantError,
    advance_claimed_impact_assessment,
    claim_due_impact_assessments,
    run_impact_assessment_worker,
    validate_impact_worker_configuration,
)
from lians.models import DecisionRecord


def _decision(namespace: str) -> DecisionRecord:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    row = DecisionRecord(
        id=uuid4(),
        namespace=namespace,
        agent_id="impact-test-agent",
        recorded_by_principal_ref=(
            "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000049"
        ),
        recorded_by_auth_method="api_key",
        recorded_by_credential_ref="lians:credential:v1:sha256:" + "a" * 64,
        barrier_group=None,
        decision_type="approval",
        outcome="allow",
        reason_codes=[],
        regime="internal",
        decided_at=now,
        recorded_at=now,
        knowledge_as_of=now,
        knowledge_recorded_as_of=now,
        evidence_memory_ids=[],
        metadata_={},
        record_hash_version=2,
        record_integrity_status="verified",
        record_hash="",
    )
    row.record_hash = compute_decision_record_hash(row)
    return row


async def _job(db, *, namespace: str):
    decision = _decision(namespace)
    db.add(decision)
    await db.flush()
    await index_decision_evidence(db, decision, [])
    job, _created = await create_impact_assessment_job(
        db,
        namespace=namespace,
        barrier_group=None,
        principal_ref=decision.recorded_by_principal_ref,
        auth_method="api_key",
        request=ExhaustiveImpactAssessmentCreate(
            idempotency_key=f"impact-{namespace}",
            dependency_kind="policy",
            dependency_value="policy-49",
            record_event=False,
        ),
    )
    await db.commit()
    return decision, job


def test_worker_source_enforces_admin_discovery_and_exact_processing_context():
    claim_source = inspect.getsource(claim_due_impact_assessments)
    worker_source = inspect.getsource(run_impact_assessment_worker)
    advance_source = inspect.getsource(advance_claimed_impact_assessment)

    assert "skip_locked=db.get_bind().dialect.name == \"postgresql\"" in claim_source
    assert 'set_current_namespace("__admin__")' in worker_source
    assert "set_current_namespace(claim.namespace)" in worker_source
    assert "set_current_barrier_group(claim.barrier_group)" in worker_source
    assert "DecisionEvidenceCoverageSet.barrier_group" in advance_source
    assert "DecisionRecord.barrier_group" in advance_source
    assert "await db.commit()" in advance_source
    assert "ImpactSnapshotInvariantError" in advance_source


def test_worker_configuration_is_bounded_and_requires_production_autonomy():
    from lians.config import Settings

    safe = Settings(
        impact_assessment_worker_enabled=True,
        impact_assessment_worker_lease_seconds=120,
    )
    assert validate_impact_worker_configuration(safe, production=True) == []

    unsafe = safe.model_copy(
        update={
            "impact_assessment_worker_enabled": False,
            "impact_assessment_worker_lease_seconds": 30,
            "impact_assessment_worker_page_size": 501,
            "impact_assessment_worker_retry_base_seconds": 10,
            "impact_assessment_worker_retry_max_seconds": 1,
        }
    )
    errors = validate_impact_worker_configuration(unsafe, production=True)
    assert any("ENABLED" in error for error in errors)
    assert any("LEASE_SECONDS" in error for error in errors)
    assert any("PAGE_SIZE" in error for error in errors)
    assert any("RETRY_MAX_SECONDS" in error for error in errors)


def test_worker_migration_adds_durable_state_and_database_guard():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0049_autonomous_impact_worker.py"
    )
    spec = spec_from_file_location("migration_0049_impact_worker", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "0049_autonomous_impact_worker"
    assert migration.down_revision == "0048_observability_indexes"
    upgrade_source = inspect.getsource(migration.upgrade)
    guard_source = inspect.getsource(migration._install_postgresql_guard)
    for field in (
        "processing_attempts",
        "consecutive_failures",
        "attempt_limit",
        "next_attempt_at",
        "lease_owner",
        "lease_expires_at",
        "last_error_digest",
        "failed_at",
    ):
        assert field in upgrade_source or field in guard_source
    assert "terminal impact assessment cannot retain a lease" in guard_source
    assert "impact assessment attempts cannot move backward" in guard_source


@pytest.mark.asyncio
async def test_claim_is_exclusive_and_success_commits_cursor_with_page(db):
    _decision_row, job = await _job(db, namespace="impact-worker-success")
    claims = await claim_due_impact_assessments(
        db,
        worker_id="worker-one",
        batch_size=1,
        lease_seconds=120,
    )
    assert [claim.job_id for claim in claims] == [job.id]
    assert await claim_due_impact_assessments(
        db,
        worker_id="worker-two",
        batch_size=1,
        lease_seconds=120,
    ) == []

    async def no_matches(_job_row, _page, _auth, _db):
        return {}, 0, 0

    auth = AuthContext(
        namespace=job.namespace,
        scopes=["read", "write"],
        principal_id=job.requested_by_principal_ref,
    )
    completed, result = await advance_claimed_impact_assessment(
        db,
        claim=claims[0],
        worker_id="worker-one",
        auth=auth,
        page_size=1,
        max_pages=1,
        lease_seconds=120,
        page_matcher=no_matches,
        completer=_complete_impact_assessment,
    )

    assert result.completed is True
    assert completed.status == "completed"
    assert completed.cursor_coverage_sequence == completed.snapshot_max_coverage_sequence
    assert completed.decisions_scanned == completed.snapshot_decision_count
    assert completed.pages_completed == 1
    assert completed.processing_attempts == 1
    assert completed.lease_owner is None


@pytest.mark.asyncio
async def test_processing_failure_preserves_cursor_and_stores_no_error_text(db):
    _decision_row, job = await _job(db, namespace="impact-worker-retry")
    claim = (
        await claim_due_impact_assessments(
            db,
            worker_id="worker-retry",
            batch_size=1,
            lease_seconds=120,
        )
    )[0]

    async def poisoned_page(_job_row, _page, _auth, _db):
        raise RuntimeError("customer-secret-error-text")

    auth = AuthContext(namespace=job.namespace, scopes=["read", "write"])
    persisted, result = await advance_claimed_impact_assessment(
        db,
        claim=claim,
        worker_id="worker-retry",
        auth=auth,
        page_size=1,
        max_pages=1,
        lease_seconds=120,
        page_matcher=poisoned_page,
        completer=_complete_impact_assessment,
    )

    assert result.error_code == "processing_error"
    assert persisted.status == "running"
    assert persisted.cursor_coverage_sequence == 0
    assert persisted.pages_completed == 0
    assert persisted.consecutive_failures == 1
    assert persisted.lease_owner is None
    assert persisted.last_error_code == "processing_error"
    assert len(persisted.last_error_digest) == 64
    assert "customer-secret" not in persisted.last_error_digest


@pytest.mark.asyncio
async def test_unreachable_frozen_watermark_fails_instead_of_claiming_completion(db):
    _decision_row, job = await _job(db, namespace="impact-worker-invariant")
    job.snapshot_max_coverage_sequence += 1
    await db.commit()
    claim = (
        await claim_due_impact_assessments(
            db,
            worker_id="worker-invariant",
            batch_size=1,
            lease_seconds=120,
        )
    )[0]

    async def no_matches(_job_row, _page, _auth, _db):
        return {}, 0, 0

    auth = AuthContext(namespace=job.namespace, scopes=["read", "write"])
    persisted, result = await advance_claimed_impact_assessment(
        db,
        claim=claim,
        worker_id="worker-invariant",
        auth=auth,
        page_size=10,
        max_pages=1,
        lease_seconds=120,
        page_matcher=no_matches,
        completer=_complete_impact_assessment,
    )

    assert result.error_code == "snapshot_invariant"
    assert persisted.status == "failed"
    assert persisted.failure_code == "snapshot_visibility_invariant"
    assert persisted.completed_at is None
    assert persisted.failed_at is not None


@pytest.mark.asyncio
async def test_repeated_crash_lease_exhaustion_terminates_poison_job(db):
    _decision_row, job = await _job(db, namespace="impact-worker-crash")
    first_claim = await claim_due_impact_assessments(
        db,
        worker_id="worker-crashed",
        batch_size=1,
        lease_seconds=120,
    )
    assert len(first_claim) == 1
    job.attempt_limit = 1
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()

    second_claim = await claim_due_impact_assessments(
        db,
        worker_id="worker-recovery",
        batch_size=1,
        lease_seconds=120,
    )
    await db.refresh(job)

    assert second_claim == []
    assert job.status == "failed"
    assert job.failure_code == "worker_attempt_limit_exhausted"
    assert job.last_error_code == "lease_expired"
    assert job.lease_owner is None
    assert job.failed_at is not None


def test_snapshot_invariant_error_never_embeds_dependency_content():
    error = ImpactSnapshotInvariantError("fixed operational invariant")
    assert "dependency_value" not in str(error)
