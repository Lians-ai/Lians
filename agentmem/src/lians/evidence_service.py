"""Evidence graph normalization and idempotent persistence helpers."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Sequence

from sqlalchemy import String, and_, cast, func, insert, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .evidence_models import (
    EVIDENCE_ARTIFACT_KINDS,
    EVIDENCE_RELATIONS,
    DecisionEvidenceCoverageSet,
    DecisionEvidenceKindCoverage,
    DecisionEvidenceLink,
    DecisionEvidenceLinkRegistration,
    DecisionImpactAssessmentJob,
    DecisionImpactAssessmentMatch,
    EvidenceArtifact,
)
from .evidence_schemas import (
    DecisionEvidenceCoverageOut,
    DecisionEvidenceLinkOut,
    EvidenceArtifactCreate,
    EvidenceArtifactOut,
    EvidenceKindCoverageOut,
    ExhaustiveImpactAssessmentCreate,
    ExhaustiveImpactAssessmentStatus,
)
from .models import DecisionRecord, Memory

DECISION_EVIDENCE_INDEXER_VERSION = "decision-normalizer:v2"
DECISION_EVIDENCE_NORMALIZATION_SCOPE = "decision_record_immutable_fields"
LEGACY_COVERAGE_INDEXER_VERSION = "legacy-unassessed"
_REGISTRATION_FENCE_HASH_SEED = 1279873363
_EVIDENCE_BULK_PAGE_SIZE = 500
_COVERAGE_DISCLOSURE = (
    "Completeness is asserted only by persisted per-kind normalization watermarks; "
    "the absence or presence of an evidence link is not a completeness claim."
)


@dataclass(slots=True)
class ArtifactSpec:
    kind: str
    identifier: str
    version: str | None = None
    artifact_hash: str | None = None
    hash_algorithm: str = "sha256"
    metadata: dict[str, Any] = field(default_factory=dict)
    risk_metadata: dict[str, Any] = field(default_factory=dict)


class DecisionEvidenceCapacityExceeded(ValueError):
    """The complete normalized candidate set does not fit one atomic mutation."""

    code = "decision_evidence_candidate_capacity_exceeded"

    def __init__(
        self,
        *,
        candidate_count: int,
        candidate_limit: int,
        candidate_bytes: int,
        candidate_bytes_limit: int,
    ) -> None:
        super().__init__("Decision evidence candidates exceed the atomic capacity")
        self.candidate_count = candidate_count
        self.candidate_limit = candidate_limit
        self.candidate_bytes = candidate_bytes
        self.candidate_bytes_limit = candidate_bytes_limit


@dataclass(slots=True)
class _DecisionEvidenceCandidateBudget:
    candidate_limit: int
    candidate_bytes_limit: int
    candidate_count: int = 0
    candidate_bytes: int = 0

    def consume(
        self,
        candidate: tuple[ArtifactSpec, str, list[str], str | None],
    ) -> None:
        spec, relation, match_basis, barrier_group = candidate
        encoded_bytes = len(
            json.dumps(
                {
                    "barrier_group": barrier_group,
                    "kind": spec.kind,
                    "identifier": spec.identifier,
                    "version": spec.version,
                    "hash_algorithm": spec.hash_algorithm,
                    "artifact_hash": spec.artifact_hash,
                    "metadata": spec.metadata,
                    "risk_metadata": spec.risk_metadata,
                    "relation": relation,
                    "match_basis": match_basis,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        )
        next_count = self.candidate_count + 1
        next_bytes = self.candidate_bytes + encoded_bytes
        if (
            next_count > self.candidate_limit
            or next_bytes > self.candidate_bytes_limit
        ):
            raise DecisionEvidenceCapacityExceeded(
                candidate_count=next_count,
                candidate_limit=self.candidate_limit,
                candidate_bytes=next_bytes,
                candidate_bytes_limit=self.candidate_bytes_limit,
            )
        self.candidate_count = next_count
        self.candidate_bytes = next_bytes


class _DecisionEvidenceCandidates(
    list[tuple[ArtifactSpec, str, list[str], str | None]]
):
    def __init__(self, budget: _DecisionEvidenceCandidateBudget) -> None:
        super().__init__()
        self._budget = budget

    def append(
        self,
        candidate: tuple[ArtifactSpec, str, list[str], str | None],
    ) -> None:
        self._budget.consume(candidate)
        super().append(candidate)


def _canonical_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def impact_barrier_scope(barrier_group: str | None) -> str:
    """Return a collision-resistant non-null scope for exact job ownership."""
    return _canonical_hash(
        {
            "domain": "lians.impact-assessment-barrier-scope.v1",
            "barrier_group": barrier_group,
        }
    )


async def _acquire_registration_fence(
    db: AsyncSession,
    namespace: str,
) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    await db.execute(
        sql_text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:namespace, :hash_seed))"
        ),
        {
            "namespace": namespace,
            "hash_seed": _REGISTRATION_FENCE_HASH_SEED,
        },
    )


async def ensure_decision_coverage_set(
    db: AsyncSession,
    decision: DecisionRecord,
) -> DecisionEvidenceCoverageSet:
    lookup = (
        DecisionEvidenceCoverageSet.namespace == decision.namespace,
        DecisionEvidenceCoverageSet.decision_id == decision.id,
    )
    existing = (
        await db.execute(select(DecisionEvidenceCoverageSet).where(*lookup))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.barrier_group != decision.barrier_group:
            raise ValueError("Decision evidence coverage barrier does not match decision")
        return existing

    await _acquire_registration_fence(db, decision.namespace)
    values = {
        "namespace": decision.namespace,
        "barrier_group": decision.barrier_group,
        "decision_id": decision.id,
        "registered_at": datetime.now(timezone.utc),
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(DecisionEvidenceCoverageSet).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["namespace", "decision_id"]
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(DecisionEvidenceCoverageSet).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["namespace", "decision_id"]
        )
    else:
        statement = insert(DecisionEvidenceCoverageSet).values(**values)
    await db.execute(statement)
    return (
        await db.execute(select(DecisionEvidenceCoverageSet).where(*lookup))
    ).scalar_one()


def _declared_reachable_kinds(node: Any) -> set[str]:
    if isinstance(node, (list, tuple, set)):
        kinds: set[str] = set()
        for item in node:
            kinds.update(_declared_reachable_kinds(item))
        return kinds
    if node is None:
        return set()
    if not isinstance(node, dict):
        # Older clients could declare reachable dependencies as opaque scalar
        # identifiers.  Their kind cannot be inferred safely, so every kind
        # must remain incomplete and eligible for the bounded legacy scan.
        # Claiming zero-input completeness here would create false negatives
        # in change-impact analysis.
        if not isinstance(node, str) or node.strip():
            return set(EVIDENCE_ARTIFACT_KINDS)
        return set()
    kinds = set()
    declared = str(node.get("kind") or node.get("type") or "").casefold()
    if declared.endswith("s"):
        declared = declared[:-1]
    if declared in EVIDENCE_ARTIFACT_KINDS:
        kinds.add(declared)
    for key, value in node.items():
        mapped = key.casefold().rstrip("s")
        if mapped in EVIDENCE_ARTIFACT_KINDS:
            kinds.add(mapped)
        if isinstance(value, (dict, list, tuple, set)):
            kinds.update(_declared_reachable_kinds(value))
    return kinds


def _declared_kind_inputs(decision: DecisionRecord) -> dict[str, bool]:
    metadata = dict(decision.metadata_ or {})
    reachable = metadata.get("reachable_dependencies") or metadata.get("dependencies")
    reachable_kinds = _declared_reachable_kinds(reachable)
    instruction_declared = any(
        metadata.get(key) is not None
        for key in (
            "system_instruction_hash",
            "instruction_hash",
            "instruction_id",
            "instruction_version",
        )
    )
    return {
        "source": bool(decision.evidence_memory_ids) or "source" in reachable_kinds,
        "policy": bool(decision.policy_version)
        or metadata.get("policy_evaluation") is not None
        or "policy" in reachable_kinds,
        "model": bool(decision.model_id or decision.model_version)
        or "model" in reachable_kinds,
        "tool": (metadata.get("tools") or metadata.get("tool_calls")) is not None
        or "tool" in reachable_kinds,
        "permission": any(
            metadata.get(key) is not None
            for key in ("permissions", "authorization", "principal")
        )
        or "permission" in reachable_kinds,
        "instruction": instruction_declared or "instruction" in reachable_kinds,
        "input": bool(decision.input_hash) or "input" in reachable_kinds,
        "output": bool(decision.output_hash) or "output" in reachable_kinds,
    }


async def _record_decision_kind_coverage(
    db: AsyncSession,
    *,
    decision: DecisionRecord,
    coverage_set: DecisionEvidenceCoverageSet,
    candidates: list[tuple[ArtifactSpec, str, list[str], str | None]],
    memories: list[Memory],
) -> None:
    declared = _declared_kind_inputs(decision)
    expected_memory_ids = {str(value) for value in (decision.evidence_memory_ids or [])}
    resolved_memory_ids = {str(memory.id) for memory in memories}
    now = (await db.execute(select(func.now()))).scalar_one()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    candidates_by_kind: dict[str, list[tuple[ArtifactSpec, str, list[str], str | None]]] = {
        kind: [] for kind in EVIDENCE_ARTIFACT_KINDS
    }
    for candidate in candidates:
        candidates_by_kind[candidate[0].kind].append(candidate)

    for kind in EVIDENCE_ARTIFACT_KINDS:
        kind_candidates = candidates_by_kind[kind]
        gaps: list[str] = []
        if kind == "source" and expected_memory_ids - resolved_memory_ids:
            gaps.append("unresolved_source_reference")
        if declared[kind] and not kind_candidates:
            gaps.append("declared_input_not_normalized")
        gaps = sorted(set(gaps))[:32]
        candidate_identities = sorted(
            {
                artifact_identity_hash(
                    barrier_group=artifact_barrier,
                    kind=spec.kind,
                    identifier=spec.identifier,
                    version=spec.version,
                    hash_algorithm=spec.hash_algorithm,
                    artifact_hash=spec.artifact_hash,
                )
                for spec, _, _, artifact_barrier in kind_candidates
            }
        )
        source_watermark = _canonical_hash(
            {
                "indexer_version": DECISION_EVIDENCE_INDEXER_VERSION,
                "decision_id": str(decision.id),
                "decision_record_hash": decision.record_hash,
                "kind": kind,
                "declared": declared[kind],
                "candidate_identities": candidate_identities,
                "expected_memory_ids": (
                    sorted(expected_memory_ids) if kind == "source" else []
                ),
                "resolved_memory_ids": (
                    sorted(resolved_memory_ids) if kind == "source" else []
                ),
                "gap_codes": gaps,
            }
        )
        values = {
            "id": uuid.uuid4(),
            "coverage_set_sequence": coverage_set.sequence,
            "namespace": decision.namespace,
            "barrier_group": decision.barrier_group,
            "decision_id": decision.id,
            "kind": kind,
            "status": "partial" if gaps else "complete",
            "indexer_version": DECISION_EVIDENCE_INDEXER_VERSION,
            "normalization_scope": DECISION_EVIDENCE_NORMALIZATION_SCOPE,
            "source_watermark": source_watermark,
            "gap_codes": gaps,
            "indexed_artifact_count": len(candidate_identities),
            "assessed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        update_values = {
            key: value
            for key, value in values.items()
            if key not in {"id", "created_at"}
        }
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            statement = postgresql_insert(DecisionEvidenceKindCoverage).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=["namespace", "decision_id", "kind"],
                set_=update_values,
            )
        elif dialect == "sqlite":
            statement = sqlite_insert(DecisionEvidenceKindCoverage).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=["namespace", "decision_id", "kind"],
                set_=update_values,
            )
        else:
            existing = (
                await db.execute(
                    select(DecisionEvidenceKindCoverage).where(
                        DecisionEvidenceKindCoverage.namespace == decision.namespace,
                        DecisionEvidenceKindCoverage.decision_id == decision.id,
                        DecisionEvidenceKindCoverage.kind == kind,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                for key, value in update_values.items():
                    setattr(existing, key, value)
                continue
            statement = insert(DecisionEvidenceKindCoverage).values(**values)
        await db.execute(statement)


def decision_coverage_out(
    decision: DecisionRecord,
    coverage_set: DecisionEvidenceCoverageSet | None,
    rows: Iterable[DecisionEvidenceKindCoverage],
) -> DecisionEvidenceCoverageOut:
    by_kind = {row.kind: row for row in rows}
    kinds: list[EvidenceKindCoverageOut] = []
    statuses: list[str] = []
    for kind in EVIDENCE_ARTIFACT_KINDS:
        row = by_kind.get(kind)
        if row is None:
            statuses.append("unknown")
            kinds.append(
                EvidenceKindCoverageOut(
                    kind=kind,
                    status="unknown",
                    indexer_version="missing",
                    normalization_scope="unregistered",
                    source_watermark=None,
                    gap_codes=["coverage_row_missing"],
                    indexed_artifact_count=0,
                    assessed_at=None,
                )
            )
            continue
        statuses.append(row.status)
        kinds.append(
            EvidenceKindCoverageOut(
                kind=row.kind,
                status=row.status,
                indexer_version=row.indexer_version,
                normalization_scope=row.normalization_scope,
                source_watermark=row.source_watermark,
                gap_codes=list(row.gap_codes or [])[:32],
                indexed_artifact_count=row.indexed_artifact_count,
                assessed_at=(
                    row.assessed_at.replace(tzinfo=timezone.utc)
                    if row.assessed_at is not None and row.assessed_at.tzinfo is None
                    else row.assessed_at
                ),
            )
        )
    overall = (
        "complete"
        if statuses and all(status == "complete" for status in statuses)
        else "partial"
        if any(status == "partial" for status in statuses)
        else "unknown"
    )
    return DecisionEvidenceCoverageOut(
        decision_id=decision.id,
        namespace=decision.namespace,
        coverage_sequence=coverage_set.sequence if coverage_set is not None else 0,
        overall_status=overall,
        normalized_complete=overall == "complete",
        kinds=kinds,
        disclosure=_COVERAGE_DISCLOSURE,
    )


async def get_decision_coverage(
    db: AsyncSession,
    decision: DecisionRecord,
) -> DecisionEvidenceCoverageOut:
    coverage_set = (
        await db.execute(
            select(DecisionEvidenceCoverageSet).where(
                DecisionEvidenceCoverageSet.namespace == decision.namespace,
                DecisionEvidenceCoverageSet.decision_id == decision.id,
            )
        )
    ).scalar_one_or_none()
    rows = list(
        (
            await db.execute(
                select(DecisionEvidenceKindCoverage).where(
                    DecisionEvidenceKindCoverage.namespace == decision.namespace,
                    DecisionEvidenceKindCoverage.decision_id == decision.id,
                )
            )
        ).scalars()
    )
    return decision_coverage_out(decision, coverage_set, rows)


async def create_impact_assessment_job(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    principal_ref: str,
    auth_method: str,
    request: ExhaustiveImpactAssessmentCreate,
) -> tuple[DecisionImpactAssessmentJob, bool]:
    """Create an idempotent job over an explicit monotonic coverage snapshot."""
    # PostgreSQL sequences are allocation ordered, not commit ordered.  Without
    # a namespace registration fence, an older uncommitted registration could
    # become visible later with a sequence below the captured high-watermark.
    # The decision/link registration triggers take the same transaction lock
    # before allocating their sequence, so every registration is wholly before
    # or wholly after this snapshot.
    await _acquire_registration_fence(db, namespace)
    barrier_scope = impact_barrier_scope(barrier_group)
    idempotency_key_hash = _canonical_hash(
        {
            "domain": "lians.impact-assessment-idempotency.v1",
            "key": request.idempotency_key,
        }
    )
    request_fingerprint = _canonical_hash(
        {
            "dependency_kind": request.dependency_kind,
            "dependency_value": request.dependency_value,
            "change_type": request.change_type,
            "occurred_at": request.occurred_at,
            "note": request.note,
            "record_event": request.record_event,
        }
    )
    lookup = (
        DecisionImpactAssessmentJob.namespace == namespace,
        DecisionImpactAssessmentJob.barrier_scope == barrier_scope,
        DecisionImpactAssessmentJob.idempotency_key_hash == idempotency_key_hash,
    )
    existing = (
        await db.execute(select(DecisionImpactAssessmentJob).where(*lookup))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_fingerprint != request_fingerprint:
            raise ValueError("Idempotency key was already used for another assessment")
        return existing, False

    snapshot_filters = [
        DecisionEvidenceCoverageSet.namespace == namespace,
        DecisionRecord.namespace == namespace,
    ]
    if barrier_group is not None:
        for column in (
            DecisionEvidenceCoverageSet.barrier_group,
            DecisionRecord.barrier_group,
        ):
            snapshot_filters.append(
                or_(
                    column.is_(None),
                    column == barrier_group,
                )
            )
    snapshot_row = (
        await db.execute(
            select(
                func.max(DecisionEvidenceCoverageSet.sequence),
                func.count(DecisionEvidenceCoverageSet.sequence),
            )
            .select_from(DecisionEvidenceCoverageSet)
            .join(
                DecisionRecord,
                and_(
                    DecisionRecord.id == DecisionEvidenceCoverageSet.decision_id,
                    DecisionRecord.namespace == DecisionEvidenceCoverageSet.namespace,
                ),
            )
            .where(*snapshot_filters)
        )
    ).one()
    snapshot_max = int(snapshot_row[0] or 0)
    snapshot_decision_count = int(snapshot_row[1] or 0)
    link_snapshot_filters = [
        DecisionEvidenceLinkRegistration.namespace == namespace,
    ]
    if barrier_group is not None:
        link_snapshot_filters.append(
            or_(
                DecisionEvidenceLinkRegistration.barrier_group.is_(None),
                DecisionEvidenceLinkRegistration.barrier_group == barrier_group,
            )
        )
    snapshot_max_link = int(
        (
            await db.execute(
                select(func.max(DecisionEvidenceLinkRegistration.sequence)).where(
                    *link_snapshot_filters
                )
            )
        ).scalar_one_or_none()
        or 0
    )
    # Queue eligibility is evaluated against the database clock.  Seed the
    # initial due time from that same clock so a newly committed job cannot be
    # hidden until application/database clock skew (or SQLite sub-second
    # precision) catches up.
    now = (await db.execute(select(func.now()))).scalar_one()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    attempt_limit = get_settings().impact_assessment_worker_max_attempts
    values = {
        "id": uuid.uuid4(),
        "namespace": namespace,
        "barrier_group": barrier_group,
        "barrier_scope": barrier_scope,
        "idempotency_key_hash": idempotency_key_hash,
        "request_fingerprint": request_fingerprint,
        "dependency_kind": request.dependency_kind,
        "dependency_value": request.dependency_value,
        "dependency_lookup_hash": artifact_lookup_hash(request.dependency_value),
        "change_type": request.change_type,
        "change_occurred_at": request.occurred_at,
        "note": request.note,
        "requested_by_principal_ref": principal_ref,
        "requested_by_auth_method": auth_method,
        "status": "pending",
        "snapshot_max_coverage_sequence": snapshot_max,
        "snapshot_decision_count": snapshot_decision_count,
        "snapshot_max_link_sequence": snapshot_max_link,
        "cursor_coverage_sequence": 0,
        "decisions_scanned": 0,
        "fallback_candidates_scanned": 0,
        "indexed_decisions_matched": 0,
        "legacy_decisions_matched": 0,
        "matches_found": 0,
        "direct_count": 0,
        "reachable_count": 0,
        "pages_completed": 0,
        "record_event": request.record_event,
        "processing_attempts": 0,
        "consecutive_failures": 0,
        "attempt_limit": attempt_limit,
        "next_attempt_at": now,
        "created_at": now,
        "updated_at": now,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(DecisionImpactAssessmentJob).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["namespace", "barrier_scope", "idempotency_key_hash"]
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(DecisionImpactAssessmentJob).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["namespace", "barrier_scope", "idempotency_key_hash"]
        )
    else:
        statement = insert(DecisionImpactAssessmentJob).values(**values)
    result = await db.execute(statement)
    job = (
        await db.execute(select(DecisionImpactAssessmentJob).where(*lookup))
    ).scalar_one()
    if job.request_fingerprint != request_fingerprint:
        raise ValueError("Idempotency key was already used for another assessment")
    return job, bool(getattr(result, "rowcount", 0))


async def get_impact_assessment_job(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    job_id: uuid.UUID,
    for_update: bool = False,
) -> DecisionImpactAssessmentJob | None:
    query = select(DecisionImpactAssessmentJob).where(
        DecisionImpactAssessmentJob.id == job_id,
        DecisionImpactAssessmentJob.namespace == namespace,
        DecisionImpactAssessmentJob.barrier_scope == impact_barrier_scope(barrier_group),
    )
    if for_update:
        query = query.with_for_update()
    return (await db.execute(query)).scalar_one_or_none()


def impact_assessment_status_out(
    row: DecisionImpactAssessmentJob,
) -> ExhaustiveImpactAssessmentStatus:
    return ExhaustiveImpactAssessmentStatus(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        dependency={"kind": row.dependency_kind, "value": row.dependency_value},
        change_type=row.change_type,
        status=row.status,
        snapshot_max_coverage_sequence=row.snapshot_max_coverage_sequence,
        snapshot_max_link_sequence=row.snapshot_max_link_sequence,
        snapshot_decision_count=row.snapshot_decision_count,
        cursor_coverage_sequence=row.cursor_coverage_sequence,
        decisions_scanned=row.decisions_scanned,
        fallback_candidates_scanned=row.fallback_candidates_scanned,
        indexed_decisions_matched=row.indexed_decisions_matched,
        legacy_decisions_matched=row.legacy_decisions_matched,
        matches_found=row.matches_found,
        direct_count=row.direct_count,
        reachable_count=row.reachable_count,
        pages_completed=row.pages_completed,
        record_event=row.record_event,
        completion_event_id=row.completion_event_id,
        failure_code=row.failure_code,
        processing_attempts=row.processing_attempts,
        consecutive_failures=row.consecutive_failures,
        attempt_limit=row.attempt_limit,
        next_attempt_at=row.next_attempt_at,
        lease_expires_at=row.lease_expires_at,
        heartbeat_at=row.heartbeat_at,
        last_attempt_at=row.last_attempt_at,
        last_error_code=row.last_error_code,
        last_error_digest=row.last_error_digest,
        created_at=row.created_at,
        started_at=row.started_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
        failed_at=row.failed_at,
        snapshot_complete=row.status == "completed",
        disclosure=(
            "Completion covers exactly decisions and evidence links registered at or "
            "below the persisted coverage/link snapshot sequences in this namespace "
            "and barrier view, with immutable legacy decision fields scanned for every "
            "covered decision. Later decisions or links require a new assessment."
        ),
    )


async def upsert_impact_assessment_match(
    db: AsyncSession,
    *,
    job: DecisionImpactAssessmentJob,
    decision_id: uuid.UUID,
    impact_status: str,
    match_basis: Iterable[str],
    match_sources: Iterable[str],
    risk_score: int,
    risk_level: str,
) -> tuple[DecisionImpactAssessmentMatch, bool]:
    lookup = (
        DecisionImpactAssessmentMatch.namespace == job.namespace,
        DecisionImpactAssessmentMatch.job_id == job.id,
        DecisionImpactAssessmentMatch.decision_id == decision_id,
    )
    existing = (
        await db.execute(select(DecisionImpactAssessmentMatch).where(*lookup))
    ).scalar_one_or_none()
    normalized_basis = sorted({str(value)[:512] for value in match_basis if str(value)})[
        :100
    ]
    normalized_sources = sorted(
        {value for value in match_sources if value in {"indexed", "legacy_fallback"}}
    )
    if existing is not None:
        existing_basis = list(existing.match_basis or [])[:100]
        existing_basis_set = set(existing_basis)
        existing.match_basis = [
            *existing_basis,
            *[
                value
                for value in normalized_basis
                if value not in existing_basis_set
            ][: 100 - len(existing_basis)],
        ]
        existing.match_sources = sorted(
            {*list(existing.match_sources or []), *normalized_sources}
        )
        if impact_status == "direct_reference":
            existing.impact_status = "direct_reference"
        if risk_score > existing.risk_score:
            existing.risk_score = risk_score
            existing.risk_level = risk_level
        existing.updated_at = datetime.now(timezone.utc)
        return existing, False

    row = DecisionImpactAssessmentMatch(
        namespace=job.namespace,
        job_id=job.id,
        job_barrier_group=job.barrier_group,
        decision_id=decision_id,
        impact_status=impact_status,
        match_basis=normalized_basis,
        match_sources=normalized_sources,
        risk_score=risk_score,
        risk_level=risk_level,
    )
    db.add(row)
    await db.flush()
    return row, True


_ASCII_TRIM = " \t\n\r\f\v"
_ASCII_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"
_ASCII_FOLD = str.maketrans(_ASCII_UPPER, _ASCII_LOWER)


def _canonical_trim(value: str) -> str:
    """Use an explicit cross-runtime whitespace alphabet for stable identities."""
    return str(value).strip(_ASCII_TRIM)


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    # Unicode case-folding differs across Python, PostgreSQL, and collations.
    # Artifact coordinates are protocol identifiers, so normalize ASCII syntax
    # only and preserve all non-ASCII code points byte-for-byte.
    normalized = _canonical_trim(value).translate(_ASCII_FOLD)
    return normalized or None


def _coordinate(identifier: str, version: str | None) -> str:
    normalized_identifier = _normalized(identifier) or ""
    normalized_version = _normalized(version)
    return (
        f"{normalized_identifier}:{normalized_version}"
        if normalized_version
        else normalized_identifier
    )


def _lookup_hash(normalized_value: str | None) -> str | None:
    if normalized_value is None:
        return None
    return hashlib.sha256(normalized_value.encode()).hexdigest()


def _normalized_artifact_hash(value: str | None, algorithm: str) -> str | None:
    if value is None:
        return None
    stripped = _canonical_trim(value)
    if not stripped:
        return None
    normalized_algorithm = _normalized_hash_algorithm(algorithm)
    if (
        normalized_algorithm.startswith(("sha", "blake"))
        or normalized_algorithm in {"md5", "xxh32", "xxh64"}
    ):
        return stripped.translate(_ASCII_FOLD)
    return stripped


def _normalized_hash_algorithm(value: str) -> str:
    normalized = _canonical_trim(value).translate(_ASCII_FOLD)
    compacted = normalized.replace("-", "")
    if compacted.startswith(("sha", "blake")):
        return compacted
    return normalized


def artifact_lookup_hash(value: str) -> str:
    """Hash a case-insensitive lookup value for bounded B-tree indexes."""
    return _lookup_hash(_normalized(value)) or hashlib.sha256(b"").hexdigest()


def _hash_algorithm_for(value: Any) -> str:
    if value is None:
        return "sha256"
    normalized = _normalized(str(value)) if value is not None else None
    if normalized and len(normalized) == 64 and all(
        char in "0123456789abcdef" for char in normalized
    ):
        return "sha256"
    return "opaque"


def artifact_identity_hash(
    *,
    barrier_group: str | None,
    kind: str,
    identifier: str,
    version: str | None,
    hash_algorithm: str,
    artifact_hash: str | None,
) -> str:
    """Return the immutable, barrier-aware artifact deduplication identity."""
    normalized_algorithm = _normalized_hash_algorithm(hash_algorithm)
    normalized_hash = _normalized_artifact_hash(
        artifact_hash, normalized_algorithm
    )
    body = {
        "barrier_group": barrier_group,
        "kind": _normalized(kind),
        "identifier": _normalized(identifier),
        "version": _normalized(version),
        "hash_algorithm": normalized_algorithm if normalized_hash is not None else None,
        "artifact_hash": normalized_hash,
    }
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def artifact_out(row: EvidenceArtifact) -> EvidenceArtifactOut:
    return EvidenceArtifactOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        kind=row.kind,
        identifier=row.identifier,
        version=row.version,
        coordinate=row.coordinate,
        hash_algorithm=row.hash_algorithm,
        artifact_hash=row.artifact_hash,
        identity_hash=row.identity_hash,
        metadata=dict(row.metadata_ or {}),
        risk_metadata=dict(row.risk_metadata or {}),
        created_by_agent_id=row.created_by_agent_id,
        recorded_at=row.recorded_at,
    )


def link_out(
    row: DecisionEvidenceLink,
    artifact: EvidenceArtifact | None = None,
) -> DecisionEvidenceLinkOut:
    return DecisionEvidenceLinkOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        decision_id=row.decision_id,
        artifact_id=row.artifact_id,
        relation=row.relation,
        match_basis=list(row.match_basis or []),
        risk_metadata=dict(row.risk_metadata or {}),
        risk_score=row.risk_score,
        risk_level=row.risk_level,
        recorded_at=row.recorded_at,
        artifact=artifact_out(artifact) if artifact is not None else None,
    )


async def ensure_artifact(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    spec: ArtifactSpec,
    created_by_agent_id: str | None,
    recorded_at: datetime | None = None,
) -> tuple[EvidenceArtifact, bool]:
    """Create an artifact once, safely under concurrent PostgreSQL/SQLite writes."""
    values = _artifact_insert_values(
        namespace=namespace,
        barrier_group=barrier_group,
        spec=spec,
        created_by_agent_id=created_by_agent_id,
        recorded_at=recorded_at,
    )
    identity_hash = str(values["identity_hash"])
    existing = (
        await db.execute(
            select(EvidenceArtifact).where(
                EvidenceArtifact.namespace == namespace,
                EvidenceArtifact.identity_hash == identity_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(EvidenceArtifact.__table__).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["namespace", "identity_hash"]
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(EvidenceArtifact.__table__).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["namespace", "identity_hash"]
        )
    else:
        statement = insert(EvidenceArtifact.__table__).values(**values)

    result = await db.execute(statement)
    row = (
        await db.execute(
            select(EvidenceArtifact).where(
                EvidenceArtifact.namespace == namespace,
                EvidenceArtifact.identity_hash == identity_hash,
            )
        )
    ).scalar_one()
    return row, bool(getattr(result, "rowcount", 0))


def _artifact_insert_values(
    *,
    namespace: str,
    barrier_group: str | None,
    spec: ArtifactSpec,
    created_by_agent_id: str | None,
    recorded_at: datetime | None,
) -> dict[str, Any]:
    """Validate and canonicalize one artifact before an idempotent insert."""
    identifier = _canonical_trim(spec.identifier)
    if not identifier:
        raise ValueError("Evidence artifact identifier cannot be blank")
    if len(identifier) > 1024:
        raise ValueError("Evidence artifact identifier cannot exceed 1024 characters")
    normalized_kind = _normalized(spec.kind)
    if normalized_kind not in EVIDENCE_ARTIFACT_KINDS:
        raise ValueError(f"Unsupported evidence artifact kind: {spec.kind}")
    version = (_canonical_trim(spec.version) or None) if spec.version else None
    if version is not None and len(version) > 512:
        raise ValueError("Evidence artifact version cannot exceed 512 characters")
    hash_algorithm = _normalized_hash_algorithm(spec.hash_algorithm)
    if not hash_algorithm:
        raise ValueError("Evidence artifact hash_algorithm cannot be blank")
    if len(hash_algorithm) > 32:
        raise ValueError("Evidence artifact hash_algorithm cannot exceed 32 characters")
    artifact_hash = _normalized_artifact_hash(spec.artifact_hash, hash_algorithm)
    if artifact_hash is not None and len(artifact_hash) > 256:
        raise ValueError("Evidence artifact hash cannot exceed 256 characters")
    identity_hash = artifact_identity_hash(
        barrier_group=barrier_group,
        kind=spec.kind,
        identifier=identifier,
        version=version,
        hash_algorithm=hash_algorithm,
        artifact_hash=artifact_hash,
    )
    identifier_normalized = _normalized(identifier) or ""
    version_normalized = _normalized(version)
    coordinate = _coordinate(identifier, version)
    return {
        "id": uuid.uuid4(),
        "namespace": namespace,
        "barrier_group": barrier_group,
        "kind": normalized_kind,
        "identifier": identifier,
        "identifier_normalized": identifier_normalized,
        "identifier_lookup_hash": _lookup_hash(identifier_normalized),
        "version": version,
        "version_normalized": version_normalized,
        "version_lookup_hash": _lookup_hash(version_normalized),
        "coordinate": coordinate,
        "coordinate_lookup_hash": _lookup_hash(coordinate),
        "hash_algorithm": hash_algorithm,
        "artifact_hash": artifact_hash,
        "identity_hash": identity_hash,
        "metadata": dict(spec.metadata or {}),
        "risk_metadata": dict(spec.risk_metadata or {}),
        "created_by_agent_id": created_by_agent_id,
        "recorded_at": recorded_at or datetime.now(timezone.utc),
    }


async def ensure_artifacts_bulk(
    db: AsyncSession,
    *,
    namespace: str,
    candidates: Sequence[
        tuple[str | None, ArtifactSpec, str | None, datetime | None]
    ],
) -> tuple[dict[str, EvidenceArtifact], int]:
    """Idempotently materialize one bounded page of artifacts without N+1 I/O."""

    if not candidates:
        return {}, 0
    if len(candidates) > 2_000:
        raise ValueError("Bulk evidence indexing is limited to 2000 artifact candidates")
    values_by_identity: dict[str, dict[str, Any]] = {}
    for barrier_group, spec, created_by_agent_id, recorded_at in candidates:
        values = _artifact_insert_values(
            namespace=namespace,
            barrier_group=barrier_group,
            spec=spec,
            created_by_agent_id=created_by_agent_id,
            recorded_at=recorded_at,
        )
        values_by_identity.setdefault(str(values["identity_hash"]), values)
    identity_hashes = sorted(values_by_identity)
    existing_ids = set(
        (
            await db.execute(
                select(EvidenceArtifact.identity_hash).where(
                    EvidenceArtifact.namespace == namespace,
                    EvidenceArtifact.identity_hash.in_(identity_hashes),
                )
            )
        ).scalars()
    )
    pending = [
        values
        for identity, values in values_by_identity.items()
        if identity not in existing_ids
    ]
    if pending:
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            # Use the Core table for bulk values.  The persisted column is
            # named ``metadata`` while the ORM attribute is ``metadata_``;
            # passing the database-column key through an ORM insert makes
            # SQLAlchemy resolve ``metadata`` to DeclarativeBase.metadata.
            statement = postgresql_insert(EvidenceArtifact.__table__).values(pending)
            statement = statement.on_conflict_do_nothing(
                index_elements=["namespace", "identity_hash"]
            )
        elif dialect == "sqlite":
            statement = sqlite_insert(EvidenceArtifact.__table__).values(pending)
            statement = statement.on_conflict_do_nothing(
                index_elements=["namespace", "identity_hash"]
            )
        else:
            statement = insert(EvidenceArtifact.__table__).values(pending)
        await db.execute(statement)
    rows = list(
        (
            await db.execute(
                select(EvidenceArtifact).where(
                    EvidenceArtifact.namespace == namespace,
                    EvidenceArtifact.identity_hash.in_(identity_hashes),
                )
            )
        ).scalars()
    )
    if len(rows) != len(identity_hashes):
        raise RuntimeError("Bulk evidence artifact materialization was incomplete")
    return {row.identity_hash: row for row in rows}, len(set(identity_hashes) - existing_ids)


async def ensure_links_bulk(
    db: AsyncSession,
    *,
    namespace: str,
    decision: DecisionRecord,
    candidates: Sequence[tuple[EvidenceArtifact, list[str], datetime | None]],
) -> tuple[int, set[uuid.UUID]]:
    """Idempotently materialize a bounded direct-link page without N+1 I/O."""

    created, new_keys = await ensure_decision_links_bulk(
        db,
        namespace=namespace,
        decision=decision,
        candidates=[
            (artifact, "direct", match_basis, {}, recorded_at)
            for artifact, match_basis, recorded_at in candidates
        ],
    )
    return created, {artifact_id for artifact_id, _relation in new_keys}


async def _ensure_link_registrations_bulk(
    db: AsyncSession,
    *,
    namespace: str,
    links: Sequence[tuple[uuid.UUID, str | None]],
) -> None:
    """Backstop migration triggers when metadata was created without Alembic."""

    if not links:
        return
    await _acquire_registration_fence(db, namespace)
    link_ids = [link_id for link_id, _barrier_group in links]
    existing_ids = set(
        (
            await db.execute(
                select(DecisionEvidenceLinkRegistration.link_id).where(
                    DecisionEvidenceLinkRegistration.namespace == namespace,
                    DecisionEvidenceLinkRegistration.link_id.in_(link_ids),
                )
            )
        ).scalars()
    )
    now = datetime.now(timezone.utc)
    pending = [
        {
            "namespace": namespace,
            "barrier_group": barrier_group,
            "link_id": link_id,
            "registered_at": now,
        }
        for link_id, barrier_group in links
        if link_id not in existing_ids
    ]
    if not pending:
        return
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(DecisionEvidenceLinkRegistration).values(pending)
        statement = statement.on_conflict_do_nothing(index_elements=["link_id"])
    elif dialect == "sqlite":
        statement = sqlite_insert(DecisionEvidenceLinkRegistration).values(pending)
        statement = statement.on_conflict_do_nothing(index_elements=["link_id"])
    else:
        statement = insert(DecisionEvidenceLinkRegistration).values(pending)
    await db.execute(statement)


async def ensure_decision_links_bulk(
    db: AsyncSession,
    *,
    namespace: str,
    decision: DecisionRecord,
    candidates: Sequence[
        tuple[
            EvidenceArtifact,
            str,
            list[str],
            dict[str, Any] | None,
            datetime | None,
        ]
    ],
) -> tuple[int, set[tuple[uuid.UUID, str]]]:
    """Idempotently materialize one bounded mixed-relation link page."""

    if not candidates:
        return 0, set()
    if len(candidates) > 2_000:
        raise ValueError("Bulk evidence indexing is limited to 2000 link candidates")
    await _acquire_registration_fence(db, namespace)
    by_key: dict[
        tuple[uuid.UUID, str],
        tuple[EvidenceArtifact, set[str], dict[str, Any], datetime | None],
    ] = {}
    for artifact, relation, match_basis, risk_metadata, recorded_at in candidates:
        if relation not in EVIDENCE_RELATIONS:
            raise ValueError(f"Unsupported evidence relation: {relation}")
        if artifact.namespace != namespace or decision.namespace != namespace:
            raise ValueError("Decision and artifact must belong to the same namespace")
        if (
            decision.barrier_group is not None
            and artifact.barrier_group is not None
            and decision.barrier_group != artifact.barrier_group
        ):
            raise ValueError("Decision and artifact belong to different information barriers")
        key = (artifact.id, relation)
        prior = by_key.get(key)
        if prior is None:
            by_key[key] = (
                artifact,
                set(match_basis),
                dict(risk_metadata or {}),
                recorded_at,
            )
        else:
            prior[1].update(match_basis)
            prior[2].update(risk_metadata or {})
            if recorded_at is not None and (
                prior[3] is None or recorded_at < prior[3]
            ):
                by_key[key] = (prior[0], prior[1], prior[2], recorded_at)
    artifact_ids = {artifact_id for artifact_id, _relation in by_key}
    relations = {relation for _artifact_id, relation in by_key}
    existing_result = await db.execute(
        select(
            DecisionEvidenceLink.id,
            DecisionEvidenceLink.artifact_id,
            DecisionEvidenceLink.relation,
            DecisionEvidenceLink.barrier_group,
        ).where(
            DecisionEvidenceLink.namespace == namespace,
            DecisionEvidenceLink.decision_id == decision.id,
            DecisionEvidenceLink.artifact_id.in_(artifact_ids),
            DecisionEvidenceLink.relation.in_(relations),
        )
    )
    existing_rows = [
        row
        for row in existing_result.all()
        if (row.artifact_id, row.relation) in by_key
    ]
    existing_keys = {(row.artifact_id, row.relation) for row in existing_rows}
    new_keys = set(by_key) - existing_keys
    values: list[dict[str, Any]] = []
    for artifact_id, relation in sorted(new_keys, key=lambda item: (str(item[0]), item[1])):
        artifact, basis, risk_metadata, recorded_at = by_key[(artifact_id, relation)]
        risk_score, risk_level = _declared_risk(risk_metadata)
        values.append(
            {
                "id": uuid.uuid4(),
                "namespace": namespace,
                "barrier_group": decision.barrier_group or artifact.barrier_group,
                "decision_id": decision.id,
                "artifact_id": artifact.id,
                "relation": relation,
                "match_basis": sorted(basis),
                "risk_metadata": risk_metadata,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "recorded_at": recorded_at or datetime.now(timezone.utc),
            }
        )
    if values:
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            statement = postgresql_insert(DecisionEvidenceLink).values(values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["namespace", "decision_id", "artifact_id", "relation"]
            )
        elif dialect == "sqlite":
            statement = sqlite_insert(DecisionEvidenceLink).values(values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["namespace", "decision_id", "artifact_id", "relation"]
            )
        else:
            statement = insert(DecisionEvidenceLink).values(values)
        await db.execute(statement)
    materialized_result = await db.execute(
        select(
            DecisionEvidenceLink.id,
            DecisionEvidenceLink.artifact_id,
            DecisionEvidenceLink.relation,
            DecisionEvidenceLink.barrier_group,
        ).where(
            DecisionEvidenceLink.namespace == namespace,
            DecisionEvidenceLink.decision_id == decision.id,
            DecisionEvidenceLink.artifact_id.in_(artifact_ids),
            DecisionEvidenceLink.relation.in_(relations),
        )
    )
    materialized_rows = [
        row
        for row in materialized_result.all()
        if (row.artifact_id, row.relation) in by_key
    ]
    materialized_keys = {(row.artifact_id, row.relation) for row in materialized_rows}
    if materialized_keys != set(by_key):
        raise RuntimeError("Bulk evidence link materialization was incomplete")
    await _ensure_link_registrations_bulk(
        db,
        namespace=namespace,
        links=[(row.id, row.barrier_group) for row in materialized_rows],
    )
    return len(new_keys), new_keys


async def ensure_link(
    db: AsyncSession,
    *,
    namespace: str,
    decision: DecisionRecord,
    artifact: EvidenceArtifact,
    relation: str,
    match_basis: list[str],
    risk_metadata: dict[str, Any] | None = None,
    recorded_at: datetime | None = None,
) -> tuple[DecisionEvidenceLink, bool]:
    """Persist a decision edge after enforcing namespace and barrier compatibility."""
    if relation not in EVIDENCE_RELATIONS:
        raise ValueError(f"Unsupported evidence relation: {relation}")
    if decision.namespace != namespace or artifact.namespace != namespace:
        raise ValueError("Decision and artifact must belong to the same namespace")
    if (
        decision.barrier_group is not None
        and artifact.barrier_group is not None
        and decision.barrier_group != artifact.barrier_group
    ):
        raise ValueError("Decision and artifact belong to different information barriers")
    barrier_group = decision.barrier_group or artifact.barrier_group
    lookup = (
        DecisionEvidenceLink.namespace == namespace,
        DecisionEvidenceLink.decision_id == decision.id,
        DecisionEvidenceLink.artifact_id == artifact.id,
        DecisionEvidenceLink.relation == relation,
    )
    existing = (await db.execute(select(DecisionEvidenceLink).where(*lookup))).scalar_one_or_none()
    if existing is not None:
        await _ensure_link_registration(db, existing)
        return existing, False

    normalized_risk = dict(risk_metadata or {})
    risk_score, risk_level = _declared_risk(normalized_risk)
    values = {
        "id": uuid.uuid4(),
        "namespace": namespace,
        "barrier_group": barrier_group,
        "decision_id": decision.id,
        "artifact_id": artifact.id,
        "relation": relation,
        "match_basis": sorted(set(match_basis)),
        "risk_metadata": normalized_risk,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "recorded_at": recorded_at or datetime.now(timezone.utc),
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(DecisionEvidenceLink).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["namespace", "decision_id", "artifact_id", "relation"]
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(DecisionEvidenceLink).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["namespace", "decision_id", "artifact_id", "relation"]
        )
    else:
        statement = insert(DecisionEvidenceLink).values(**values)

    result = await db.execute(statement)
    row = (await db.execute(select(DecisionEvidenceLink).where(*lookup))).scalar_one()
    await _ensure_link_registration(db, row)
    return row, bool(getattr(result, "rowcount", 0))


async def _ensure_link_registration(
    db: AsyncSession,
    link: DecisionEvidenceLink,
) -> DecisionEvidenceLinkRegistration:
    existing = (
        await db.execute(
            select(DecisionEvidenceLinkRegistration).where(
                DecisionEvidenceLinkRegistration.link_id == link.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    await _acquire_registration_fence(db, link.namespace)
    values = {
        "namespace": link.namespace,
        "barrier_group": link.barrier_group,
        "link_id": link.id,
        "registered_at": datetime.now(timezone.utc),
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(DecisionEvidenceLinkRegistration).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["link_id"])
    elif dialect == "sqlite":
        statement = sqlite_insert(DecisionEvidenceLinkRegistration).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["link_id"])
    else:
        statement = insert(DecisionEvidenceLinkRegistration).values(**values)
    await db.execute(statement)
    return (
        await db.execute(
            select(DecisionEvidenceLinkRegistration).where(
                DecisionEvidenceLinkRegistration.link_id == link.id
            )
        )
    ).scalar_one()


def _risk_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    keys = ("risk_level", "risk_score", "criticality", "impact", "risk_tier")
    return {key: metadata[key] for key in keys if key in metadata}


def _declared_risk(metadata: dict[str, Any]) -> tuple[int | None, str | None]:
    levels = {"critical": 88, "high": 74, "medium": 55, "low": 35}
    scores: list[int] = []
    for key in ("risk_level", "criticality", "risk_tier"):
        level = str(metadata.get(key) or "").casefold()
        if level in levels:
            scores.append(levels[level])
    raw_score = metadata.get("risk_score")
    if isinstance(raw_score, (int, float)):
        scores.append(max(0, min(100, int(raw_score))))
    if not scores:
        return None, None
    score = max(scores)
    level = (
        "critical"
        if score >= 85
        else "high"
        if score >= 70
        else "medium"
        if score >= 45
        else "low"
    )
    return score, level


def _named_specs(kind: str, node: Any) -> Iterator[ArtifactSpec]:
    """Extract conservative named dependencies without indexing arbitrary values."""
    if node is None:
        return
    if isinstance(node, str):
        value = node.strip()
        if value and len(value) <= 1024:
            yield ArtifactSpec(kind=kind, identifier=value)
        return
    if isinstance(node, (list, tuple, set)):
        for item in node:
            yield from _named_specs(kind, item)
        return
    if not isinstance(node, dict):
        return

    if kind == "tool" and isinstance(node.get("function"), dict):
        function = node["function"]
        identifier = function.get("name") or function.get("id")
    else:
        identifier = (
            node.get("identifier")
            or node.get("name")
            or node.get("id")
            or node.get("role")
            or node.get("scope")
        )
    if identifier is not None:
        identifier = str(identifier).strip()
        if not identifier or len(identifier) > 1024:
            return
        version = str(node["version"]).strip() if node.get("version") is not None else None
        if version is not None and len(version) > 512:
            return
        captured_fields = sorted(str(key) for key in node)[:50]
        if kind == "tool":
            role_specs: list[ArtifactSpec] = []
            for hash_role, field in (
                ("definition", "definition_hash"),
                ("result", "result_hash"),
            ):
                role_hash = node.get(field)
                if role_hash is None or len(str(role_hash).strip()) > 256:
                    continue
                role_identifier = f"{identifier}#{hash_role}"
                if len(role_identifier) > 1024:
                    role_identifier = (
                        "lians-tool-role:"
                        + hashlib.sha256(identifier.encode("utf-8")).hexdigest()
                        + f":{hash_role}"
                    )
                role_metadata = {
                    "captured_fields": captured_fields,
                    "hash_role": hash_role,
                    "tool_name": identifier,
                }
                tool_call_id = node.get("tool_call_id") or node.get("call_id")
                if isinstance(tool_call_id, str) and 0 < len(tool_call_id) <= 512:
                    role_metadata["tool_call_id"] = tool_call_id
                role_specs.append(
                    ArtifactSpec(
                        kind=kind,
                        identifier=role_identifier,
                        version=version,
                        artifact_hash=str(role_hash),
                        hash_algorithm=_hash_algorithm_for(role_hash),
                        metadata=role_metadata,
                        risk_metadata=_risk_metadata(node),
                    )
                )
            if role_specs:
                yield from role_specs
                return
        artifact_hash = node.get("artifact_hash") or node.get("hash") or node.get("schema_hash")
        if artifact_hash is not None and len(str(artifact_hash).strip()) > 256:
            artifact_hash = None
        yield ArtifactSpec(
            kind=kind,
            identifier=identifier,
            version=version,
            artifact_hash=str(artifact_hash) if artifact_hash is not None else None,
            hash_algorithm=_hash_algorithm_for(artifact_hash),
            metadata={"captured_fields": captured_fields},
            risk_metadata=_risk_metadata(node),
        )
        return

    # Permission maps commonly use ``scopes``/``permissions`` as their only
    # structured identifiers. Do not index tokens, secrets, or arbitrary values.
    if kind == "permission":
        nested = node.get("scopes") or node.get("permissions")
        yield from _named_specs(kind, nested)


def _reachable_specs(node: Any) -> Iterator[ArtifactSpec]:
    if node is None:
        return
    if isinstance(node, (list, tuple, set)):
        for item in node:
            yield from _reachable_specs(item)
        return
    if not isinstance(node, dict):
        return
    kind = str(node.get("kind") or node.get("type") or "").casefold()
    if kind.endswith("s"):
        kind = kind[:-1]
    if kind in {
        "source",
        "policy",
        "model",
        "tool",
        "permission",
        "instruction",
        "input",
        "output",
    }:
        identifier = node.get("identifier") or node.get("value") or node.get("name")
        if identifier is None:
            return
        identifier = str(identifier).strip()
        if not identifier or len(identifier) > 1024:
            return
        version = str(node["version"]).strip() if node.get("version") is not None else None
        if version is not None and len(version) > 512:
            return
        artifact_hash = node.get("artifact_hash") or node.get("hash")
        if artifact_hash is not None and len(str(artifact_hash).strip()) > 256:
            artifact_hash = None
        yield ArtifactSpec(
            kind=kind,
            identifier=identifier,
            version=version,
            artifact_hash=str(artifact_hash) if artifact_hash is not None else None,
            hash_algorithm=_hash_algorithm_for(artifact_hash),
            metadata={"declared_fields": sorted(str(key) for key in node)[:50]},
            risk_metadata=_risk_metadata(node),
        )
        return

    for key, value in node.items():
        mapped_kind = key.casefold().rstrip("s")
        if mapped_kind in {
            "source",
            "policy",
            "model",
            "tool",
            "permission",
            "instruction",
            "input",
            "output",
        }:
            yield from _named_specs(mapped_kind, value)


def decision_artifact_specs(
    decision: DecisionRecord,
    memories: Iterable[Memory],
) -> list[tuple[ArtifactSpec, str, list[str], str | None]]:
    """Normalize legacy decision fields into artifact/link candidates."""
    settings = get_settings()
    budget = _DecisionEvidenceCandidateBudget(
        candidate_limit=settings.decision_evidence_candidate_limit,
        candidate_bytes_limit=settings.decision_evidence_candidate_bytes_limit,
    )
    metadata = dict(decision.metadata_ or {})
    risk = _risk_metadata(metadata)
    candidates = _DecisionEvidenceCandidates(budget)

    for memory in memories:
        memory_metadata = dict(memory.metadata_ or {})
        source_version = (
            str(memory_metadata["source_version"])
            if memory_metadata.get("source_version") is not None
            else None
        )
        source_metadata = {
            "memory_id": str(memory.id),
            "source": memory.source,
        }
        if source_version is not None and len(source_version) > 512:
            source_version = None
        source_identifier = memory.source or f"memory:{memory.id}"
        if len(source_identifier) > 1024:
            source_identifier = f"memory:{memory.id}"
        for identifier, basis in (
            (source_identifier, "source.identity"),
            (str(memory.id), "decision.evidence_memory_ids"),
        ):
            candidates.append(
                (
                    ArtifactSpec(
                        kind="source",
                        identifier=identifier,
                        version=source_version,
                        artifact_hash=memory.content_hash,
                        metadata=source_metadata,
                        risk_metadata=_risk_metadata(memory_metadata),
                    ),
                    "direct",
                    [basis],
                    memory.barrier_group,
                )
            )

    if (
        decision.model_id
        and decision.model_id.strip()
        and len(decision.model_id.strip()) <= 1024
    ):
        model_version = decision.model_version.strip() if decision.model_version else None
        if model_version is not None and len(model_version) > 512:
            model_version = None
        candidates.append(
            (
                ArtifactSpec(
                    kind="model",
                    identifier=decision.model_id.strip(),
                    version=model_version,
                    risk_metadata=risk,
                ),
                "direct",
                ["decision.model"],
                decision.barrier_group,
            )
        )
    if (
        decision.policy_version
        and decision.policy_version.strip()
        and len(decision.policy_version.strip()) <= 512
    ):
        policy_identifier = str(metadata.get("policy_id") or "decision-policy").strip()
        policy_identifier = policy_identifier or "decision-policy"
        if len(policy_identifier) > 1024:
            policy_identifier = "decision-policy"
        candidates.append(
            (
                ArtifactSpec(
                    kind="policy",
                    identifier=policy_identifier,
                    version=decision.policy_version.strip(),
                    risk_metadata=risk,
                ),
                "direct",
                ["decision.policy_version"],
                decision.barrier_group,
            )
        )
    for spec in _named_specs("policy", metadata.get("policy_evaluation")):
        candidates.append(
            (spec, "direct", ["metadata.policy_evaluation"], decision.barrier_group)
        )
    for kind, value in (("input", decision.input_hash), ("output", decision.output_hash)):
        if value:
            candidates.append(
                (
                    ArtifactSpec(
                        kind=kind,
                        identifier=f"decision:{decision.id}:{kind}",
                        artifact_hash=value,
                        metadata={"hash_role": f"decision_{kind}"},
                        risk_metadata=risk,
                    ),
                    "direct",
                    [f"decision.{kind}_hash"],
                    decision.barrier_group,
                )
            )

    for spec in _named_specs("tool", metadata.get("tools") or metadata.get("tool_calls")):
        candidates.append((spec, "direct", ["metadata.tools"], decision.barrier_group))
    permission_node = (
        metadata.get("permissions")
        or metadata.get("authorization")
        or metadata.get("principal")
    )
    for spec in _named_specs("permission", permission_node):
        candidates.append(
            (spec, "direct", ["metadata.authorization"], decision.barrier_group)
        )

    instruction_hash = metadata.get("system_instruction_hash") or metadata.get(
        "instruction_hash"
    )
    if instruction_hash:
        if len(str(instruction_hash).strip()) > 256:
            instruction_hash = None
    if instruction_hash:
        instruction_identifier = str(
            metadata.get("instruction_id") or "system-instruction"
        ).strip()
        if len(instruction_identifier) > 1024:
            instruction_identifier = "system-instruction"
        instruction_version = (
            str(metadata["instruction_version"]).strip()
            if metadata.get("instruction_version") is not None
            else None
        )
        if instruction_version is not None and len(instruction_version) > 512:
            instruction_version = None
        candidates.append(
            (
                ArtifactSpec(
                    kind="instruction",
                    identifier=instruction_identifier or "system-instruction",
                    version=instruction_version,
                    artifact_hash=str(instruction_hash),
                    hash_algorithm=_hash_algorithm_for(instruction_hash),
                    metadata={"hash_role": "instruction"},
                    risk_metadata=risk,
                ),
                "direct",
                ["metadata.system_instruction_hash"],
                decision.barrier_group,
            )
        )

    dependencies = metadata.get("reachable_dependencies") or metadata.get("dependencies")
    for spec in _reachable_specs(dependencies):
        candidates.append(
            (
                spec,
                "reachable",
                ["metadata.reachable_dependencies"],
                decision.barrier_group,
            )
        )
    return candidates


async def index_decision_evidence(
    db: AsyncSession,
    decision: DecisionRecord,
    memories: Iterable[Memory],
    *,
    candidate_plan: list[tuple[ArtifactSpec, str, list[str], str | None]] | None = None,
) -> tuple[int, int]:
    """Atomically normalize one complete, explicitly bounded evidence set."""
    memory_rows = list(memories)
    raw_candidates = (
        decision_artifact_specs(decision, memory_rows)
        if candidate_plan is None
        else candidate_plan
    )
    candidates: dict[
        tuple[str | None, str, str],
        tuple[ArtifactSpec, str, set[str], str | None],
    ] = {}
    for spec, relation, match_basis, artifact_barrier in raw_candidates:
        identity = artifact_identity_hash(
            barrier_group=artifact_barrier,
            kind=spec.kind,
            identifier=spec.identifier,
            version=spec.version,
            hash_algorithm=spec.hash_algorithm,
            artifact_hash=spec.artifact_hash,
        )
        key = (artifact_barrier, identity, relation)
        if key in candidates:
            prior_spec, prior_relation, prior_basis, prior_barrier = candidates[key]
            prior_basis.update(match_basis)
            prior_spec.risk_metadata = {
                **prior_spec.risk_metadata,
                **spec.risk_metadata,
            }
            candidates[key] = (
                prior_spec,
                prior_relation,
                prior_basis,
                prior_barrier,
            )
        else:
            candidates[key] = (spec, relation, set(match_basis), artifact_barrier)

    await _acquire_registration_fence(db, decision.namespace)
    artifact_candidates: dict[
        str,
        tuple[str | None, ArtifactSpec, str | None, datetime | None],
    ] = {}
    for spec, _relation, _match_basis, artifact_barrier in candidates.values():
        identity = artifact_identity_hash(
            barrier_group=artifact_barrier,
            kind=spec.kind,
            identifier=spec.identifier,
            version=spec.version,
            hash_algorithm=spec.hash_algorithm,
            artifact_hash=spec.artifact_hash,
        )
        artifact_candidates.setdefault(
            identity,
            (
                artifact_barrier,
                spec,
                decision.agent_id,
                decision.recorded_at,
            ),
        )

    created_artifacts = 0
    artifacts: dict[str, EvidenceArtifact] = {}
    artifact_pages = list(artifact_candidates.values())
    for offset in range(0, len(artifact_pages), _EVIDENCE_BULK_PAGE_SIZE):
        page_artifacts, page_created = await ensure_artifacts_bulk(
            db,
            namespace=decision.namespace,
            candidates=artifact_pages[offset : offset + _EVIDENCE_BULK_PAGE_SIZE],
        )
        artifacts.update(page_artifacts)
        created_artifacts += page_created

    link_candidates: list[
        tuple[
            EvidenceArtifact,
            str,
            list[str],
            dict[str, Any],
            datetime | None,
        ]
    ] = []
    decision_risk = _risk_metadata(dict(decision.metadata_ or {}))
    for spec, relation, match_basis, artifact_barrier in candidates.values():
        identity = artifact_identity_hash(
            barrier_group=artifact_barrier,
            kind=spec.kind,
            identifier=spec.identifier,
            version=spec.version,
            hash_algorithm=spec.hash_algorithm,
            artifact_hash=spec.artifact_hash,
        )
        link_candidates.append(
            (
                artifacts[identity],
                relation,
                sorted(match_basis),
                {**decision_risk, **spec.risk_metadata},
                decision.recorded_at,
            )
        )

    created_links = 0
    for offset in range(0, len(link_candidates), _EVIDENCE_BULK_PAGE_SIZE):
        page_created, _new_keys = await ensure_decision_links_bulk(
            db,
            namespace=decision.namespace,
            decision=decision,
            candidates=link_candidates[offset : offset + _EVIDENCE_BULK_PAGE_SIZE],
        )
        created_links += page_created
    coverage_set = await ensure_decision_coverage_set(db, decision)
    await _record_decision_kind_coverage(
        db,
        decision=decision,
        coverage_set=coverage_set,
        candidates=raw_candidates,
        memories=memory_rows,
    )
    return created_artifacts, created_links


async def create_artifact_from_request(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    req: EvidenceArtifactCreate,
) -> tuple[EvidenceArtifact, bool]:
    return await ensure_artifact(
        db,
        namespace=namespace,
        barrier_group=barrier_group,
        spec=ArtifactSpec(
            kind=req.kind,
            identifier=req.identifier,
            version=req.version,
            artifact_hash=req.artifact_hash,
            hash_algorithm=req.hash_algorithm,
            metadata=req.metadata,
            risk_metadata=req.risk_metadata,
        ),
        created_by_agent_id=req.created_by_agent_id,
    )


def artifact_dependency_filter(kind: str, value: str):
    """Build an exact, index-friendly match across artifact identity coordinates."""
    raw_value = _canonical_trim(value)
    normalized = _normalized(raw_value) or ""
    lookup_hash = _lookup_hash(normalized)
    try:
        artifact_id_match = EvidenceArtifact.id == uuid.UUID(normalized)
    except ValueError:
        artifact_id_match = cast(EvidenceArtifact.id, String) == normalized
    return (
        EvidenceArtifact.kind == (_normalized(kind) or ""),
        or_(
            and_(
                EvidenceArtifact.identifier_lookup_hash == lookup_hash,
                EvidenceArtifact.identifier_normalized == normalized,
            ),
            and_(
                EvidenceArtifact.version_lookup_hash == lookup_hash,
                EvidenceArtifact.version_normalized == normalized,
            ),
            and_(
                EvidenceArtifact.coordinate_lookup_hash == lookup_hash,
                EvidenceArtifact.coordinate == normalized,
            ),
            EvidenceArtifact.artifact_hash.in_({raw_value, normalized}),
            artifact_id_match,
        ),
    )
