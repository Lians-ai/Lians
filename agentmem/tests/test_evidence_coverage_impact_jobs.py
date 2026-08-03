"""Focused contracts for evidence coverage and exhaustive impact snapshots."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from lians.api.routes_decisions import (
    _assessment_page_matches,
    _complete_impact_assessment,
    advance_exhaustive_impact_assessment,
    assess_decision_impact,
)
from lians.decision_record_integrity import compute_decision_record_hash
from lians.evidence_models import (
    EVIDENCE_ARTIFACT_KINDS,
    DecisionEvidenceKindCoverage,
)
from lians.evidence_schemas import ExhaustiveImpactAssessmentCreate
from lians.evidence_service import (
    ArtifactSpec,
    create_impact_assessment_job,
    ensure_artifact,
    ensure_decision_coverage_set,
    ensure_link,
    get_decision_coverage,
    index_decision_evidence,
    upsert_impact_assessment_match,
)
from lians.impact_assessment_service import advance_claimed_impact_assessment
from lians.models import DecisionRecord


def _decision(*, evidence_memory_ids: list[str] | None = None) -> DecisionRecord:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    row = DecisionRecord(
        id=uuid4(),
        namespace="coverage-test",
        agent_id="claimed-agent",
        recorded_by_principal_ref=(
            "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000042"
        ),
        recorded_by_auth_method="api_key",
        recorded_by_credential_ref="lians:credential:v1:sha256:" + "c" * 64,
        barrier_group=None,
        decision_type="approval",
        outcome="allow",
        reason_codes=[],
        regime="internal",
        subject_id=None,
        session_id=None,
        model_id=None,
        model_version=None,
        policy_version=None,
        decided_at=now,
        recorded_at=now,
        knowledge_as_of=now,
        knowledge_recorded_as_of=now,
        evidence_memory_ids=evidence_memory_ids or [],
        input_hash=None,
        output_hash=None,
        supersedes_id=None,
        metadata_={},
        record_hash_version=2,
        record_integrity_status="verified",
        record_hash="",
    )
    row.record_hash = compute_decision_record_hash(row)
    return row


@pytest.mark.asyncio
async def test_new_decision_records_all_kind_watermarks_even_without_links(db) -> None:
    decision = _decision()
    db.add(decision)
    await db.flush()

    artifacts_created, links_created = await index_decision_evidence(db, decision, [])
    coverage = await get_decision_coverage(db, decision)

    assert (artifacts_created, links_created) == (0, 0)
    assert coverage.normalized_complete is True
    assert coverage.overall_status == "complete"
    assert {item.kind for item in coverage.kinds} == set(EVIDENCE_ARTIFACT_KINDS)
    assert all(item.status == "complete" for item in coverage.kinds)
    assert all(item.source_watermark and len(item.source_watermark) == 64 for item in coverage.kinds)


@pytest.mark.asyncio
async def test_unresolved_source_is_partial_not_inferred_complete(db) -> None:
    decision = _decision(evidence_memory_ids=[str(uuid4())])
    db.add(decision)
    await db.flush()

    await index_decision_evidence(db, decision, [])
    coverage = await get_decision_coverage(db, decision)
    by_kind = {item.kind: item for item in coverage.kinds}

    assert coverage.normalized_complete is False
    assert by_kind["source"].status == "partial"
    assert by_kind["source"].gap_codes == [
        "declared_input_not_normalized",
        "unresolved_source_reference",
    ]
    assert by_kind["model"].status == "complete"


@pytest.mark.asyncio
async def test_existing_link_does_not_upgrade_unknown_legacy_coverage(db) -> None:
    decision = _decision()
    db.add(decision)
    await db.flush()
    coverage_set = await ensure_decision_coverage_set(db, decision)
    for kind in EVIDENCE_ARTIFACT_KINDS:
        db.add(
            DecisionEvidenceKindCoverage(
                coverage_set_sequence=coverage_set.sequence,
                namespace=decision.namespace,
                barrier_group=None,
                decision_id=decision.id,
                kind=kind,
                status="unknown",
                indexer_version="legacy-unassessed",
                normalization_scope="legacy_pre_watermark",
                source_watermark=None,
                gap_codes=["legacy_backfill_unknown"],
                indexed_artifact_count=0,
                assessed_at=None,
            )
        )
    artifact, _ = await ensure_artifact(
        db,
        namespace=decision.namespace,
        barrier_group=None,
        spec=ArtifactSpec(kind="model", identifier="model-legacy"),
        created_by_agent_id="legacy",
    )
    await ensure_link(
        db,
        namespace=decision.namespace,
        decision=decision,
        artifact=artifact,
        relation="direct",
        match_basis=["legacy.manual"],
    )
    await db.flush()

    coverage = await get_decision_coverage(db, decision)
    assert coverage.overall_status == "unknown"
    assert coverage.normalized_complete is False
    assert all(item.status == "unknown" for item in coverage.kinds)


@pytest.mark.asyncio
async def test_job_freezes_monotonic_decision_and_link_snapshots_idempotently(db) -> None:
    first = _decision()
    db.add(first)
    await db.flush()
    await index_decision_evidence(db, first, [])
    artifact, _ = await ensure_artifact(
        db,
        namespace=first.namespace,
        barrier_group=None,
        spec=ArtifactSpec(kind="policy", identifier="policy-42"),
        created_by_agent_id="test",
    )
    await ensure_link(
        db,
        namespace=first.namespace,
        decision=first,
        artifact=artifact,
        relation="direct",
        match_basis=["test.policy"],
    )
    request = ExhaustiveImpactAssessmentCreate(
        idempotency_key="stable-request-42",
        dependency_kind="policy",
        dependency_value="policy-42",
        record_event=False,
    )
    job, created = await create_impact_assessment_job(
        db,
        namespace=first.namespace,
        barrier_group=None,
        principal_ref=first.recorded_by_principal_ref,
        auth_method="api_key",
        request=request,
    )
    assert created is True
    assert job.snapshot_decision_count == 1

    second = _decision()
    db.add(second)
    await db.flush()
    await index_decision_evidence(db, second, [])
    second_coverage = await ensure_decision_coverage_set(db, second)
    assert second_coverage.sequence > job.snapshot_max_coverage_sequence

    same_job, created_again = await create_impact_assessment_job(
        db,
        namespace=first.namespace,
        barrier_group=None,
        principal_ref=first.recorded_by_principal_ref,
        auth_method="api_key",
        request=request,
    )
    assert created_again is False
    assert same_job.id == job.id
    assert same_job.snapshot_max_link_sequence == job.snapshot_max_link_sequence
    assert same_job.snapshot_decision_count == job.snapshot_decision_count

    changed = request.model_copy(update={"dependency_value": "another-policy"})
    with pytest.raises(ValueError, match="Idempotency key"):
        await create_impact_assessment_job(
            db,
            namespace=first.namespace,
            barrier_group=None,
            principal_ref=first.recorded_by_principal_ref,
            auth_method="api_key",
            request=changed,
        )


@pytest.mark.asyncio
async def test_job_matches_are_idempotent_and_bounded(db) -> None:
    decision = _decision()
    db.add(decision)
    await db.flush()
    await index_decision_evidence(db, decision, [])
    job, _ = await create_impact_assessment_job(
        db,
        namespace=decision.namespace,
        barrier_group=None,
        principal_ref=decision.recorded_by_principal_ref,
        auth_method="api_key",
        request=ExhaustiveImpactAssessmentCreate(
            idempotency_key="match-upsert",
            dependency_kind="model",
            dependency_value="model-42",
            record_event=False,
        ),
    )

    match, created = await upsert_impact_assessment_match(
        db,
        job=job,
        decision_id=decision.id,
        impact_status="reachable",
        match_basis=[f"basis-{index}" for index in range(150)],
        match_sources=["legacy_fallback"],
        risk_score=50,
        risk_level="medium",
    )
    same, created_again = await upsert_impact_assessment_match(
        db,
        job=job,
        decision_id=decision.id,
        impact_status="direct_reference",
        match_basis=["indexed-basis"],
        match_sources=["indexed"],
        risk_score=90,
        risk_level="critical",
    )
    assert created is True
    assert created_again is False
    assert same.sequence == match.sequence
    assert same.impact_status == "direct_reference"
    assert same.risk_score == 90
    assert same.match_sources == ["indexed", "legacy_fallback"]
    assert len(same.match_basis) <= 100


def test_routes_use_keyset_cursor_bounded_results_and_single_completion_event() -> None:
    route_source = inspect.getsource(advance_exhaustive_impact_assessment)
    assert "claim_impact_assessment_for_request" in route_source
    assert "advance_claimed_impact_assessment" in route_source

    advance_source = inspect.getsource(advance_claimed_impact_assessment)
    assert "DecisionEvidenceCoverageSet.sequence > job.cursor_coverage_sequence" in advance_source
    assert "DecisionEvidenceCoverageSet.sequence" in advance_source
    assert "<= job.snapshot_max_coverage_sequence" in advance_source
    assert "job.decisions_scanned != job.snapshot_decision_count" in advance_source
    assert ".limit(bounded_page_size + 1)" in advance_source
    assert ".with_for_update()" in advance_source
    assert "ImpactSnapshotInvariantError" in advance_source

    page_source = inspect.getsource(_assessment_page_matches)
    assert "fallback_decisions = decisions" in page_source
    assert "if row.status == \"complete\"" not in page_source

    completion_source = inspect.getsource(_complete_impact_assessment)
    assert "job.completion_event_id is None" in completion_source
    assert "affected_decision_ids" not in completion_source
    assert "snapshot_max_link_sequence" in completion_source

    fast_source = inspect.getsource(assess_decision_impact)
    assert "~kind_coverage_complete" in fast_source
    assert "~matching_kind_link" in fast_source
    assert "matches[:100]" in fast_source
    assert "legacy_fallback_truncated" in fast_source
    assert "total_is_lower_bound" in fast_source


def test_snapshot_count_migration_backfills_exact_scan_relation_and_guards_completion() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0051_impact_snapshot_row_count.py"
    )
    source = migration_path.read_text(encoding="utf-8")
    assert "JOIN decision_records AS decision" in source
    assert "coverage.namespace = job.namespace" in source
    assert "coverage.sequence <= job.snapshot_max_coverage_sequence" in source
    assert "snapshot count is immutable" in source
    assert "completed impact assessment did not scan its snapshot" in source


def test_migration_backfills_unknown_and_installs_registration_boundaries() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0043_evidence_coverage_impact_jobs.py"
    )
    spec = spec_from_file_location("migration_0043_evidence_coverage", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "0043_evidence_impact_jobs"
    assert migration.down_revision == "0042a_recorder_backfill"
    backfill = inspect.getsource(migration._backfill_unknown_legacy_coverage)
    assert "legacy_backfill_unknown" in backfill
    assert "'unknown'" in backfill
    assert "'complete'" not in backfill
    postgres = inspect.getsource(migration._install_postgresql_rls)
    assert "FORCE ROW LEVEL SECURITY" in postgres
    assert "pg_advisory_xact_lock" in postgres
    assert "trg_decision_register_evidence_coverage" in postgres
    assert "trg_decision_register_evidence_link" in postgres
    assert "barrier_exact" in postgres
    integrity = inspect.getsource(migration._install_postgresql_integrity_boundaries)
    assert "evidence_artifacts" in repr(migration._APPEND_ONLY_EVIDENCE_TABLES)
    assert "BEFORE UPDATE OR DELETE" in integrity
    assert "BEFORE TRUNCATE" in integrity
    assert "REVOKE DELETE, TRUNCATE" in integrity
