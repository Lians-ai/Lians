"""Deferred behavioral contracts for the bounded Investigator queue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from lians.decision_record_integrity import compute_decision_record_hash
from lians.evidence_models import (
    EVIDENCE_ARTIFACT_KINDS,
    DecisionEvidenceKindCoverage,
)
from lians.evidence_service import ensure_decision_coverage_set, index_decision_evidence
from lians.investigator_service import build_investigator_queue
from lians.models import DecisionRecord


def _decision(*, decided_at: datetime) -> DecisionRecord:
    row = DecisionRecord(
        id=uuid4(),
        namespace="investigator-queue-test",
        agent_id="underwriting-agent",
        recorded_by_principal_ref=(
            "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000042"
        ),
        recorded_by_auth_method="api_key",
        recorded_by_credential_ref="lians:credential:v1:sha256:" + "c" * 64,
        barrier_group=None,
        decision_type="underwriting",
        outcome="decline",
        reason_codes=["income_verification"],
        regime="internal",
        subject_id=None,
        session_id=None,
        model_id=None,
        model_version=None,
        policy_version=None,
        decided_at=decided_at,
        recorded_at=decided_at,
        knowledge_as_of=decided_at,
        knowledge_recorded_as_of=decided_at,
        evidence_memory_ids=[],
        input_hash=None,
        output_hash=None,
        human_review_status="not_requested",
        supersedes_id=None,
        metadata_={},
        record_hash_version=2,
        record_integrity_status="verified",
        record_hash="",
    )
    row.record_hash = compute_decision_record_hash(row)
    return row


@pytest.mark.asyncio
async def test_queue_uses_persisted_all_kind_coverage_not_empty_legacy_refs(db) -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    complete = _decision(decided_at=now)
    unknown = _decision(decided_at=now - timedelta(seconds=1))
    db.add_all([complete, unknown])
    await db.flush()

    await index_decision_evidence(db, complete, [])
    coverage_set = await ensure_decision_coverage_set(db, unknown)
    for kind in EVIDENCE_ARTIFACT_KINDS:
        db.add(
            DecisionEvidenceKindCoverage(
                coverage_set_sequence=coverage_set.sequence,
                namespace=unknown.namespace,
                barrier_group=None,
                decision_id=unknown.id,
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
    await db.flush()

    queue = await build_investigator_queue(
        db,
        namespace=complete.namespace,
        barrier_group=None,
        limit=10,
        scan_limit=10,
    )
    by_id = {item.decision.id: item for item in queue.items}

    assert by_id[complete.id].normalized_evidence_complete is True
    assert "evidence_graph_incomplete" not in by_id[complete.id].signals
    assert by_id[unknown.id].normalized_evidence_complete is False
    assert "evidence_graph_incomplete" in by_id[unknown.id].signals
