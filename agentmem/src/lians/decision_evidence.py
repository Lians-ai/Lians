"""Decision-envelope capture, completeness assessment, and impact analysis."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import chain_log
from .memory_service import get_knowledge_snapshot
from .models import (
    DecisionEnvelope,
    DecisionEvidenceLink,
    DecisionRecord,
    LedgerEvent,
    Memory,
    OTelSpan,
)
from .schemas import (
    BlastRadiusDecision,
    BlastRadiusResult,
    CompletenessGap,
    DecisionCompleteness,
    DecisionEnvelopeOpen,
    DecisionEnvelopeOut,
    DecisionEnvelopeSeal,
    DecisionEvidenceCreate,
    DecisionEvidenceOut,
    DecisionOut,
    DecisionReconstructionOut,
    LedgerEventOut,
)

GRADE_ORDER = ("recorded", "reconstructable", "verifiable", "replayable")

BASE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "recorded": ("decision_record",),
    "reconstructable": ("temporal_context", "influence_evidence"),
    "verifiable": ("input_integrity", "output_integrity", "evidence_integrity"),
    "replayable": ("model_identity", "prompt_identity", "trace_context", "replay_manifest"),
}

PROFILE_REQUIREMENTS: dict[str, dict[str, tuple[str, ...]]] = {
    "standard": {},
    "regulated_recordkeeping": {
        "reconstructable": ("trace_context", "policy_context"),
        "verifiable": ("model_identity",),
    },
    "human_review": {
        "verifiable": ("human_oversight",),
    },
}

CHECK_COPY: dict[str, tuple[str, str]] = {
    "decision_record": (
        "Decision record",
        "Seal the envelope to create an append-only decision record.",
    ),
    "temporal_context": (
        "Point-in-time context",
        "Record the knowledge-as-of time used by the decision.",
    ),
    "influence_evidence": (
        "Influence evidence",
        "Attach a recall receipt, memory, trace, policy decision, tool result, or review.",
    ),
    "input_integrity": (
        "Input integrity",
        "Attach a SHA-256 commitment for the decision input.",
    ),
    "output_integrity": (
        "Output integrity",
        "Attach a SHA-256 commitment for the decision output.",
    ),
    "evidence_integrity": (
        "Evidence integrity",
        "Every material evidence edge must carry an artifact hash.",
    ),
    "model_identity": (
        "Model identity",
        "Record both the model identifier and exact version.",
    ),
    "policy_context": (
        "Policy context",
        "Attach the governing policy decision or exact policy version.",
    ),
    "prompt_identity": (
        "Prompt identity",
        "Record the prompt identifier and exact version.",
    ),
    "trace_context": (
        "Execution trace",
        "Correlate an OTLP trace or attach a trace artifact.",
    ),
    "human_oversight": (
        "Human oversight",
        "Record the reviewer and review outcome required by this profile.",
    ),
    "replay_manifest": (
        "Replay manifest",
        "Attach a replay manifest hash covering runtime and external dependencies.",
    ),
    "tool_context": (
        "Tool context",
        "Attach the material tool calls and results.",
    ),
    "runtime_identity": (
        "Runtime identity",
        "Record the exact agent runtime version.",
    ),
}

VALID_CHECKS = frozenset(CHECK_COPY)
MATERIAL_ROLES = frozenset({"used", "governed", "executed", "reviewed", "produced", "outcome"})
INFLUENCE_TYPES = frozenset(
    {
        "memory",
        "recall_receipt",
        "otel_trace",
        "otel_span",
        "policy_decision",
        "prompt",
        "model",
        "tool_call",
        "tool_result",
        "human_review",
        "external",
    }
)


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def canonical_sha256(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode()).hexdigest()


def barrier_visible(row: Any, barrier_group: str | None) -> bool:
    return (
        barrier_group is None
        or getattr(row, "barrier_group", None) is None
        or row.barrier_group == barrier_group
    )


def apply_barrier_filter(filters: list[Any], column: Any, barrier_group: str | None) -> None:
    if barrier_group is not None:
        filters.append(or_(column.is_(None), column == barrier_group))


def decision_out(row: DecisionRecord) -> DecisionOut:
    return DecisionOut(
        id=row.id,
        envelope_id=row.envelope_id,
        namespace=row.namespace,
        agent_id=row.agent_id,
        decision_type=row.decision_type,
        outcome=row.outcome,
        reason_codes=list(row.reason_codes or []),
        regime=row.regime,
        subject_id=row.subject_id,
        session_id=row.session_id,
        trace_id=row.trace_id,
        run_id=row.run_id,
        model_id=row.model_id,
        model_version=row.model_version,
        policy_version=row.policy_version,
        prompt_id=row.prompt_id,
        prompt_version=row.prompt_version,
        runtime_version=row.runtime_version,
        decided_at=row.decided_at,
        recorded_at=row.recorded_at,
        knowledge_as_of=row.knowledge_as_of,
        evidence_memory_ids=[UUID(str(item)) for item in (row.evidence_memory_ids or [])],
        input_hash=row.input_hash,
        output_hash=row.output_hash,
        replay_manifest_hash=row.replay_manifest_hash,
        human_review_status=row.human_review_status,
        human_reviewer=row.human_reviewer,
        human_reviewed_at=row.human_reviewed_at,
        supersedes_id=row.supersedes_id,
        metadata=dict(row.metadata_ or {}),
        record_hash=row.record_hash,
    )


def ledger_event_out(row: LedgerEvent) -> LedgerEventOut:
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


def evidence_out(row: DecisionEvidenceLink) -> DecisionEvidenceOut:
    return DecisionEvidenceOut(
        id=row.id,
        namespace=row.namespace,
        envelope_id=row.envelope_id,
        evidence_type=row.evidence_type,
        role=row.role,
        source_id=row.source_id,
        source_version=row.source_version,
        artifact_hash=row.artifact_hash,
        occurred_at=row.occurred_at,
        metadata=dict(row.metadata_ or {}),
        created_at=row.created_at,
    )


def validate_required_checks(required_checks: dict[str, list[str]]) -> None:
    unknown_levels = set(required_checks) - set(GRADE_ORDER)
    if unknown_levels:
        raise ValueError(
            f"Unknown completeness levels: {', '.join(sorted(unknown_levels))}"
        )
    unknown_checks = {
        check
        for checks in required_checks.values()
        for check in checks
        if check not in VALID_CHECKS
    }
    if unknown_checks:
        raise ValueError(f"Unknown completeness checks: {', '.join(sorted(unknown_checks))}")


async def create_envelope(
    db: AsyncSession,
    namespace: str,
    barrier_group: str | None,
    req: DecisionEnvelopeOpen,
) -> DecisionEnvelope:
    validate_required_checks(req.required_checks)
    row = DecisionEnvelope(
        namespace=namespace,
        agent_id=req.agent_id,
        barrier_group=barrier_group,
        decision_type=req.decision_type,
        regime=req.regime,
        subject_id=req.subject_id,
        session_id=req.session_id,
        trace_id=req.trace_id.lower() if req.trace_id else None,
        run_id=req.run_id,
        knowledge_as_of=req.knowledge_as_of,
        completeness_profile=req.completeness_profile,
        requirements=req.required_checks,
        metadata_=req.metadata,
    )
    db.add(row)
    await db.flush()
    await chain_log(
        db,
        namespace,
        req.agent_id,
        "decision_envelope_opened",
        content_hash=canonical_sha256(
            {
                "envelope_id": row.id,
                "decision_type": row.decision_type,
                "trace_id": row.trace_id,
                "run_id": row.run_id,
                "profile": row.completeness_profile,
            }
        ),
        payload={
            "envelope_id": str(row.id),
            "decision_type": row.decision_type,
            "profile": row.completeness_profile,
        },
    )
    return row


async def get_envelope(
    db: AsyncSession,
    namespace: str,
    envelope_id: UUID,
    barrier_group: str | None,
) -> DecisionEnvelope | None:
    row = await db.get(DecisionEnvelope, envelope_id)
    if row is None or row.namespace != namespace or not barrier_visible(row, barrier_group):
        return None
    return row


async def envelope_decision(
    db: AsyncSession, envelope_id: UUID
) -> DecisionRecord | None:
    return (
        await db.execute(
            select(DecisionRecord).where(DecisionRecord.envelope_id == envelope_id)
        )
    ).scalar_one_or_none()


async def list_evidence(
    db: AsyncSession,
    namespace: str,
    envelope_id: UUID,
    barrier_group: str | None,
) -> list[DecisionEvidenceLink]:
    filters = [
        DecisionEvidenceLink.namespace == namespace,
        DecisionEvidenceLink.envelope_id == envelope_id,
    ]
    apply_barrier_filter(filters, DecisionEvidenceLink.barrier_group, barrier_group)
    return list(
        (
            await db.execute(
                select(DecisionEvidenceLink)
                .where(*filters)
                .order_by(DecisionEvidenceLink.created_at, DecisionEvidenceLink.id)
            )
        )
        .scalars()
        .all()
    )


def _link_key(item: DecisionEvidenceCreate | DecisionEvidenceLink) -> tuple[str, ...]:
    return (
        item.evidence_type,
        item.role,
        item.source_id,
        item.source_version or "",
        item.artifact_hash or "",
    )


async def add_evidence(
    db: AsyncSession,
    envelope: DecisionEnvelope,
    items: Iterable[DecisionEvidenceCreate],
    *,
    actor_id: str | None = None,
    audit: bool = True,
) -> list[DecisionEvidenceLink]:
    existing = await list_evidence(
        db, envelope.namespace, envelope.id, envelope.barrier_group
    )
    by_key = {_link_key(row): row for row in existing}
    added: list[DecisionEvidenceLink] = []
    output: list[DecisionEvidenceLink] = []
    for item in items:
        key = _link_key(item)
        if key in by_key:
            output.append(by_key[key])
            continue
        row = DecisionEvidenceLink(
            namespace=envelope.namespace,
            envelope_id=envelope.id,
            barrier_group=envelope.barrier_group,
            evidence_type=item.evidence_type,
            role=item.role,
            source_id=item.source_id,
            source_version=item.source_version,
            artifact_hash=item.artifact_hash.lower() if item.artifact_hash else None,
            occurred_at=item.occurred_at,
            metadata_=item.metadata,
        )
        db.add(row)
        added.append(row)
        output.append(row)
        by_key[key] = row
    if added:
        await db.flush()
        envelope.version += 1
        if audit:
            content_hash = canonical_sha256(
                [
                    {
                        "id": row.id,
                        "type": row.evidence_type,
                        "role": row.role,
                        "source_id": row.source_id,
                        "source_version": row.source_version,
                        "artifact_hash": row.artifact_hash,
                    }
                    for row in added
                ]
            )
            await chain_log(
                db,
                envelope.namespace,
                actor_id or envelope.agent_id,
                "decision_evidence_linked",
                content_hash=content_hash,
                payload={
                    "envelope_id": str(envelope.id),
                    "link_ids": [str(row.id) for row in added],
                    "evidence_types": sorted({row.evidence_type for row in added}),
                },
            )
    return output


async def attach_recall_receipt(
    db: AsyncSession,
    envelope: DecisionEnvelope,
    receipt_sha256: str,
    receipt: dict[str, Any],
) -> list[DecisionEvidenceLink]:
    items = [
        DecisionEvidenceCreate(
            evidence_type="recall_receipt",
            role="retrieved",
            source_id=receipt_sha256,
            source_version=str(
                (receipt.get("policy") or {}).get("policy_version") or "unknown"
            ),
            artifact_hash=receipt_sha256,
            occurred_at=datetime.now(UTC),
            metadata={"receipt": receipt},
        )
    ]
    for result in receipt.get("results", []):
        source_id = str(result.get("id") or "").strip()
        if not source_id:
            continue
        items.append(
            DecisionEvidenceCreate(
                evidence_type="memory",
                role="retrieved",
                source_id=source_id,
                artifact_hash=result.get("content_hash"),
                occurred_at=result.get("event_time"),
                metadata={"source": result.get("source"), "receipt_sha256": receipt_sha256},
            )
        )
    return await add_evidence(db, envelope, items)


def _requirements_for(envelope: DecisionEnvelope) -> dict[str, list[str]]:
    requirements = {
        level: list(BASE_REQUIREMENTS[level])
        for level in GRADE_ORDER
    }
    profile = PROFILE_REQUIREMENTS.get(envelope.completeness_profile, {})
    for level, checks in profile.items():
        requirements[level].extend(checks)
    for level, checks in dict(envelope.requirements or {}).items():
        if level in requirements:
            requirements[level].extend(checks)
    return {
        level: list(dict.fromkeys(checks))
        for level, checks in requirements.items()
    }


def assess_completeness(
    envelope: DecisionEnvelope,
    decision: DecisionRecord | None,
    evidence: list[DecisionEvidenceLink],
    *,
    evaluated_at: datetime | None = None,
) -> DecisionCompleteness:
    material = [row for row in evidence if row.role in MATERIAL_ROLES]
    influence = [
        row
        for row in evidence
        if row.evidence_type in INFLUENCE_TYPES
        and row.role in {"retrieved", "used", "governed", "executed", "reviewed"}
    ]
    evidence_types = {row.evidence_type for row in evidence}
    model_links = [row for row in evidence if row.evidence_type == "model"]
    prompt_links = [row for row in evidence if row.evidence_type == "prompt"]
    policy_links = [row for row in evidence if row.evidence_type == "policy_decision"]
    checks = {
        "decision_record": bool(decision and decision.record_hash),
        "temporal_context": bool(
            (decision and decision.knowledge_as_of) or envelope.knowledge_as_of
        ),
        "influence_evidence": bool(influence),
        "input_integrity": bool(
            (decision and decision.input_hash)
            or any(row.evidence_type == "input" and row.artifact_hash for row in evidence)
        ),
        "output_integrity": bool(
            (decision and decision.output_hash)
            or any(row.evidence_type == "output" and row.artifact_hash for row in evidence)
        ),
        "evidence_integrity": bool(material)
        and all(bool(row.artifact_hash) for row in material),
        "model_identity": bool(
            decision
            and decision.model_id
            and decision.model_version
            or any(
                row.source_id
                and row.source_version
                and (
                    row.metadata_.get("ledger_event_type") != "inference"
                    or row.metadata_.get("model_id")
                )
                for row in model_links
            )
        ),
        "policy_context": bool(
            decision and decision.policy_version
            or any(row.source_id or row.source_version for row in policy_links)
        ),
        "prompt_identity": bool(
            decision and decision.prompt_id and decision.prompt_version
            or any(row.source_id and row.source_version for row in prompt_links)
        ),
        "trace_context": any(
            row.evidence_type in {"otel_trace", "otel_span"}
            and bool(row.artifact_hash)
            for row in evidence
        ),
        "human_oversight": bool(
            decision
            and decision.human_review_status in {"affirmed", "overturned", "withdrawn"}
            or "human_review" in evidence_types
        ),
        "replay_manifest": bool(
            decision
            and decision.replay_manifest_hash
            or any(
                row.evidence_type == "external"
                and row.role == "produced"
                and row.metadata_.get("kind") == "replay_manifest"
                and row.artifact_hash
                for row in evidence
            )
        ),
        "tool_context": bool(
            {"tool_call", "tool_result"}.issubset(evidence_types)
        ),
        "runtime_identity": bool(decision and decision.runtime_version),
    }
    requirements = _requirements_for(envelope)
    cumulative: list[str] = []
    grade: str | None = None
    for level in GRADE_ORDER:
        cumulative.extend(requirements[level])
        if all(checks.get(check, False) for check in cumulative):
            grade = level
        else:
            break
    next_grade = (
        GRADE_ORDER[0]
        if grade is None
        else (
            GRADE_ORDER[GRADE_ORDER.index(grade) + 1]
            if grade != GRADE_ORDER[-1]
            else None
        )
    )
    all_required = list(
        dict.fromkeys(check for level in GRADE_ORDER for check in requirements[level])
    )
    first_level: dict[str, str] = {}
    for level in GRADE_ORDER:
        for check in requirements[level]:
            first_level.setdefault(check, level)
    gaps = []
    for check in all_required:
        if checks.get(check, False):
            continue
        label, message = CHECK_COPY.get(
            check,
            (check.replace("_", " ").title(), f"Satisfy the {check} requirement."),
        )
        gaps.append(
            CompletenessGap(
                code=check,
                label=label,
                blocks=first_level[check],
                message=message,
            )
        )
    satisfied = sum(bool(checks.get(check, False)) for check in all_required)
    return DecisionCompleteness(
        grade=grade,
        next_grade=next_grade,
        score=round(satisfied / len(all_required), 6) if all_required else 1.0,
        profile=envelope.completeness_profile,
        checks=checks,
        gaps=gaps,
        evaluated_at=evaluated_at or datetime.now(UTC),
    )


async def envelope_out(
    db: AsyncSession,
    envelope: DecisionEnvelope,
    *,
    decision: DecisionRecord | None = None,
    evidence: list[DecisionEvidenceLink] | None = None,
) -> DecisionEnvelopeOut:
    if decision is None:
        decision = await envelope_decision(db, envelope.id)
    if evidence is None:
        evidence = await list_evidence(
            db, envelope.namespace, envelope.id, envelope.barrier_group
        )
    assessment = assess_completeness(envelope, decision, evidence)
    return DecisionEnvelopeOut(
        id=envelope.id,
        namespace=envelope.namespace,
        agent_id=envelope.agent_id,
        decision_type=envelope.decision_type,
        regime=envelope.regime,
        subject_id=envelope.subject_id,
        session_id=envelope.session_id,
        trace_id=envelope.trace_id,
        run_id=envelope.run_id,
        knowledge_as_of=envelope.knowledge_as_of,
        completeness_profile=envelope.completeness_profile,
        required_checks=dict(envelope.requirements or {}),
        metadata=dict(envelope.metadata_ or {}),
        status=envelope.status,
        version=envelope.version,
        decision_id=decision.id if decision else None,
        created_at=envelope.created_at,
        sealed_at=envelope.sealed_at,
        completeness=assessment,
    )


async def _validate_memory_evidence(
    db: AsyncSession,
    envelope: DecisionEnvelope,
    memory_ids: list[UUID],
) -> list[Memory]:
    if not memory_ids:
        return []
    filters = [
        Memory.namespace == envelope.namespace,
        Memory.id.in_(memory_ids),
    ]
    apply_barrier_filter(filters, Memory.barrier_group, envelope.barrier_group)
    memories = list((await db.execute(select(Memory).where(*filters))).scalars().all())
    if len({row.id for row in memories}) != len(set(memory_ids)):
        raise ValueError("One or more evidence_memory_ids do not belong to this namespace")
    return memories


async def _validate_supersedes(
    db: AsyncSession,
    envelope: DecisionEnvelope,
    supersedes_id: UUID | None,
) -> None:
    if supersedes_id is None:
        return
    prior = await db.get(DecisionRecord, supersedes_id)
    if (
        prior is None
        or prior.namespace != envelope.namespace
        or not barrier_visible(prior, envelope.barrier_group)
    ):
        raise ValueError("supersedes_id does not belong to this namespace")


async def _automatic_links(
    db: AsyncSession,
    envelope: DecisionEnvelope,
    req: DecisionEnvelopeSeal,
    memories: list[Memory],
) -> list[DecisionEvidenceCreate]:
    items: list[DecisionEvidenceCreate] = []
    for memory in memories:
        items.append(
            DecisionEvidenceCreate(
                evidence_type="memory",
                role="used",
                source_id=str(memory.id),
                artifact_hash=memory.content_hash,
                occurred_at=memory.event_time,
                metadata={"source": memory.source},
            )
        )
    if req.model_id:
        items.append(
            DecisionEvidenceCreate(
                evidence_type="model",
                role="executed",
                source_id=req.model_id,
                source_version=req.model_version,
                artifact_hash=req.model_artifact_hash,
            )
        )
    if req.policy_id or req.policy_version:
        items.append(
            DecisionEvidenceCreate(
                evidence_type="policy_decision",
                role="governed",
                source_id=req.policy_id or "default-policy",
                source_version=req.policy_version,
                artifact_hash=req.policy_artifact_hash,
            )
        )
    if req.prompt_id or req.prompt_version:
        items.append(
            DecisionEvidenceCreate(
                evidence_type="prompt",
                role="used",
                source_id=req.prompt_id or "default-prompt",
                source_version=req.prompt_version,
                artifact_hash=req.prompt_artifact_hash,
            )
        )
    if req.input_hash:
        items.append(
            DecisionEvidenceCreate(
                evidence_type="input",
                role="used",
                source_id="decision-input",
                artifact_hash=req.input_hash,
            )
        )
    if req.output_hash:
        items.append(
            DecisionEvidenceCreate(
                evidence_type="output",
                role="produced",
                source_id="decision-output",
                artifact_hash=req.output_hash,
            )
        )
    if req.replay_manifest_hash:
        items.append(
            DecisionEvidenceCreate(
                evidence_type="external",
                role="produced",
                source_id="replay-manifest",
                artifact_hash=req.replay_manifest_hash,
                metadata={"kind": "replay_manifest"},
            )
        )
    if envelope.trace_id:
        spans = list(
            (
                await db.execute(
                    select(OTelSpan).where(
                        OTelSpan.namespace == envelope.namespace,
                        OTelSpan.trace_id == envelope.trace_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        aggregate_hash = (
            canonical_sha256(sorted(row.payload_hash for row in spans))
            if spans
            else None
        )
        items.append(
            DecisionEvidenceCreate(
                evidence_type="otel_trace",
                role="executed" if spans else "available",
                source_id=envelope.trace_id,
                artifact_hash=aggregate_hash,
                metadata={
                    "span_count": len(spans),
                    "service_names": sorted(
                        {row.service_name for row in spans if row.service_name}
                    ),
                },
            )
        )
    return items


async def attach_otel_spans(
    db: AsyncSession,
    namespace: str,
    trace_ids: set[str],
    barrier_group: str | None,
) -> int:
    """Bind newly received OTLP spans to every matching Decision Envelope."""
    if not trace_ids:
        return 0
    envelope_filters = [
        DecisionEnvelope.namespace == namespace,
        DecisionEnvelope.trace_id.in_(trace_ids),
    ]
    apply_barrier_filter(
        envelope_filters, DecisionEnvelope.barrier_group, barrier_group
    )
    envelopes = list(
        (
            await db.execute(
                select(DecisionEnvelope).where(*envelope_filters)
            )
        )
        .scalars()
        .all()
    )
    if not envelopes:
        return 0
    spans = list(
        (
            await db.execute(
                select(OTelSpan).where(
                    OTelSpan.namespace == namespace,
                    OTelSpan.trace_id.in_(trace_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    spans_by_trace: dict[str, list[OTelSpan]] = defaultdict(list)
    for span in spans:
        spans_by_trace[span.trace_id].append(span)
    linked = 0
    for envelope in envelopes:
        items = []
        for span in spans_by_trace[envelope.trace_id]:
            try:
                occurred_at = datetime.fromtimestamp(
                    int(span.start_time_unix_nano) / 1_000_000_000,
                    tz=UTC,
                )
            except (TypeError, ValueError, OSError, OverflowError):
                occurred_at = span.received_at
            items.append(
                DecisionEvidenceCreate(
                    evidence_type="otel_span",
                    role="executed",
                    source_id=f"{span.trace_id}:{span.span_id}",
                    source_version=span.scope_version,
                    artifact_hash=span.payload_hash,
                    occurred_at=occurred_at,
                    metadata={
                        "trace_id": span.trace_id,
                        "span_id": span.span_id,
                        "parent_span_id": span.parent_span_id,
                        "name": span.name,
                        "service_name": span.service_name,
                        "model_id": span.model_id,
                        "model_version": span.model_version,
                    },
                )
            )
        links = await add_evidence(
            db,
            envelope,
            items,
            actor_id=envelope.agent_id,
            audit=False,
        )
        linked += len(links)
    return linked


async def seal_envelope(
    db: AsyncSession,
    envelope: DecisionEnvelope,
    req: DecisionEnvelopeSeal,
) -> tuple[DecisionRecord, DecisionCompleteness, list[DecisionEvidenceLink]]:
    if envelope.status != "open":
        raise ValueError("Decision envelope is already sealed")
    memories = await _validate_memory_evidence(db, envelope, req.evidence_memory_ids)
    await _validate_supersedes(db, envelope, req.supersedes_id)
    recorded_at = datetime.now(UTC)
    knowledge_as_of = req.knowledge_as_of or envelope.knowledge_as_of or req.decided_at
    body = {
        "envelope_id": str(envelope.id),
        "namespace": envelope.namespace,
        "agent_id": envelope.agent_id,
        "decision_type": envelope.decision_type,
        "outcome": req.outcome,
        "reason_codes": req.reason_codes,
        "regime": envelope.regime,
        "subject_id": envelope.subject_id,
        "session_id": envelope.session_id,
        "trace_id": envelope.trace_id,
        "run_id": envelope.run_id,
        "model_id": req.model_id,
        "model_version": req.model_version,
        "policy_version": req.policy_version,
        "prompt_id": req.prompt_id,
        "prompt_version": req.prompt_version,
        "runtime_version": req.runtime_version,
        "decided_at": req.decided_at,
        "recorded_at": recorded_at,
        "knowledge_as_of": knowledge_as_of,
        "evidence_memory_ids": [str(item) for item in req.evidence_memory_ids],
        "input_hash": req.input_hash,
        "output_hash": req.output_hash,
        "replay_manifest_hash": req.replay_manifest_hash,
        "supersedes_id": req.supersedes_id,
        "metadata": {**dict(envelope.metadata_ or {}), **req.metadata},
    }
    record_hash = canonical_sha256(body)
    row = DecisionRecord(
        envelope_id=envelope.id,
        namespace=envelope.namespace,
        agent_id=envelope.agent_id,
        barrier_group=envelope.barrier_group,
        decision_type=envelope.decision_type,
        outcome=req.outcome,
        reason_codes=req.reason_codes,
        regime=envelope.regime,
        subject_id=envelope.subject_id,
        session_id=envelope.session_id,
        trace_id=envelope.trace_id,
        run_id=envelope.run_id,
        model_id=req.model_id,
        model_version=req.model_version,
        policy_version=req.policy_version,
        prompt_id=req.prompt_id,
        prompt_version=req.prompt_version,
        runtime_version=req.runtime_version,
        decided_at=req.decided_at,
        recorded_at=recorded_at,
        knowledge_as_of=knowledge_as_of,
        evidence_memory_ids=[str(item) for item in req.evidence_memory_ids],
        input_hash=req.input_hash.lower() if req.input_hash else None,
        output_hash=req.output_hash.lower() if req.output_hash else None,
        replay_manifest_hash=(
            req.replay_manifest_hash.lower() if req.replay_manifest_hash else None
        ),
        supersedes_id=req.supersedes_id,
        metadata_={**dict(envelope.metadata_ or {}), **req.metadata},
        record_hash=record_hash,
    )
    db.add(row)
    envelope.status = "sealed"
    envelope.sealed_at = recorded_at
    envelope.knowledge_as_of = knowledge_as_of
    envelope.version += 1
    await db.flush()
    automatic = await _automatic_links(db, envelope, req, memories)
    await add_evidence(
        db,
        envelope,
        automatic,
        actor_id=envelope.agent_id,
        audit=False,
    )
    await chain_log(
        db,
        envelope.namespace,
        envelope.agent_id,
        "decision_recorded",
        content_hash=record_hash,
        payload={
            "decision_id": str(row.id),
            "envelope_id": str(envelope.id),
            "decision_type": envelope.decision_type,
            "regime": envelope.regime,
        },
    )
    await db.commit()
    await db.refresh(row)
    await db.refresh(envelope)
    evidence = await list_evidence(
        db, envelope.namespace, envelope.id, envelope.barrier_group
    )
    return row, assess_completeness(envelope, row, evidence), evidence


async def blast_radius(
    db: AsyncSession,
    namespace: str,
    *,
    evidence_type: str,
    source_id: str,
    source_version: str | None,
    artifact_hash: str | None,
    barrier_group: str | None,
    limit: int,
) -> BlastRadiusResult:
    link_filters = [
        DecisionEvidenceLink.namespace == namespace,
        DecisionEvidenceLink.evidence_type == evidence_type,
        DecisionEvidenceLink.source_id == source_id,
    ]
    if source_version is not None:
        link_filters.append(DecisionEvidenceLink.source_version == source_version)
    if artifact_hash is not None:
        link_filters.append(DecisionEvidenceLink.artifact_hash == artifact_hash.lower())
    apply_barrier_filter(link_filters, DecisionEvidenceLink.barrier_group, barrier_group)

    impacted_decisions = int(
        (
            await db.execute(
                select(func.count(distinct(DecisionRecord.id)))
                .select_from(DecisionEvidenceLink)
                .join(
                    DecisionRecord,
                    DecisionRecord.envelope_id == DecisionEvidenceLink.envelope_id,
                )
                .where(*link_filters)
            )
        ).scalar_one()
    )
    impacted_open = int(
        (
            await db.execute(
                select(func.count(distinct(DecisionEnvelope.id)))
                .select_from(DecisionEvidenceLink)
                .join(
                    DecisionEnvelope,
                    DecisionEnvelope.id == DecisionEvidenceLink.envelope_id,
                )
                .where(*link_filters, DecisionEnvelope.status == "open")
            )
        ).scalar_one()
    )
    matching_links = int(
        (
            await db.execute(
                select(func.count(DecisionEvidenceLink.id)).where(*link_filters)
            )
        ).scalar_one()
    )
    decisions = list(
        (
            await db.execute(
                select(DecisionRecord)
                .join(
                    DecisionEvidenceLink,
                    DecisionEvidenceLink.envelope_id == DecisionRecord.envelope_id,
                )
                .where(*link_filters)
                .distinct()
                .order_by(DecisionRecord.decided_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    envelope_ids = [row.envelope_id for row in decisions if row.envelope_id]
    envelopes = {
        row.id: row
        for row in (
            (
                await db.execute(
                    select(DecisionEnvelope).where(DecisionEnvelope.id.in_(envelope_ids))
                )
            )
            .scalars()
            .all()
            if envelope_ids
            else []
        )
    }
    all_links = list(
        (
            await db.execute(
                select(DecisionEvidenceLink).where(
                    DecisionEvidenceLink.envelope_id.in_(envelope_ids)
                )
            )
        )
        .scalars()
        .all()
        if envelope_ids
        else []
    )
    links_by_envelope: dict[UUID, list[DecisionEvidenceLink]] = defaultdict(list)
    for link in all_links:
        links_by_envelope[link.envelope_id].append(link)
    matching_by_envelope: dict[UUID, list[DecisionEvidenceLink]] = defaultdict(list)
    if envelope_ids:
        matched = list(
            (
                await db.execute(
                    select(DecisionEvidenceLink).where(
                        *link_filters,
                        DecisionEvidenceLink.envelope_id.in_(envelope_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        for link in matched:
            matching_by_envelope[link.envelope_id].append(link)
    output = []
    for decision in decisions:
        envelope = envelopes.get(decision.envelope_id)
        if envelope is None:
            continue
        matching = matching_by_envelope[envelope.id]
        output.append(
            BlastRadiusDecision(
                decision=decision_out(decision),
                matching_roles=sorted({row.role for row in matching}),
                matching_link_ids=[row.id for row in matching],
                completeness=assess_completeness(
                    envelope, decision, links_by_envelope[envelope.id]
                ),
            )
        )
    return BlastRadiusResult(
        generated_at=datetime.now(UTC),
        evidence_type=evidence_type,
        source_id=source_id,
        source_version=source_version,
        artifact_hash=artifact_hash.lower() if artifact_hash else None,
        impacted_decisions=impacted_decisions,
        impacted_open_envelopes=impacted_open,
        matching_links=matching_links,
        decisions=output,
    )


def _span_out(row: OTelSpan) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "trace_id": row.trace_id,
        "span_id": row.span_id,
        "parent_span_id": row.parent_span_id,
        "name": row.name,
        "service_name": row.service_name,
        "model_id": row.model_id,
        "model_version": row.model_version,
        "start_time_unix_nano": row.start_time_unix_nano,
        "end_time_unix_nano": row.end_time_unix_nano,
        "status_code": row.status_code,
        "status_message": row.status_message,
        "attributes": dict(row.attributes or {}),
        "events": list(row.events or []),
        "links": list(row.links or []),
        "payload_hash": row.payload_hash,
        "received_at": row.received_at.isoformat(),
    }


async def reconstruct_decision(
    db: AsyncSession,
    envelope: DecisionEnvelope,
    decision: DecisionRecord,
) -> DecisionReconstructionOut:
    evidence = await list_evidence(
        db, envelope.namespace, envelope.id, envelope.barrier_group
    )
    assessment = assess_completeness(envelope, decision, evidence)
    snapshot = await get_knowledge_snapshot(
        db,
        envelope.namespace,
        envelope.agent_id,
        decision.knowledge_as_of,
        10000,
        barrier_override=envelope.barrier_group,
    )
    linked_event_ids: list[UUID] = []
    for link in evidence:
        if not link.metadata_.get("ledger_event_type"):
            continue
        try:
            linked_event_ids.append(UUID(link.metadata_.get("ledger_event_id") or link.source_id))
        except (TypeError, ValueError):
            continue
    event_match = LedgerEvent.decision_id == decision.id
    if linked_event_ids:
        event_match = or_(event_match, LedgerEvent.id.in_(linked_event_ids))
    event_filters = [
        LedgerEvent.namespace == envelope.namespace,
        event_match,
    ]
    apply_barrier_filter(event_filters, LedgerEvent.barrier_group, envelope.barrier_group)
    events = list(
        (
            await db.execute(
                select(LedgerEvent)
                .where(*event_filters)
                .order_by(LedgerEvent.occurred_at, LedgerEvent.id)
            )
        )
        .scalars()
        .all()
    )
    spans: list[OTelSpan] = []
    if envelope.trace_id:
        spans = list(
            (
                await db.execute(
                    select(OTelSpan)
                    .where(
                        OTelSpan.namespace == envelope.namespace,
                        OTelSpan.trace_id == envelope.trace_id,
                    )
                    .order_by(OTelSpan.start_time_unix_nano, OTelSpan.span_id)
                )
            )
            .scalars()
            .all()
        )
    timeline: list[tuple[datetime, dict[str, Any]]] = [
        (
            decision.decided_at,
            {
                "at": decision.decided_at.isoformat(),
                "kind": "decision",
                "id": str(decision.id),
                "label": decision.decision_type,
            },
        )
    ]
    for link in evidence:
        at = link.occurred_at or link.created_at
        timeline.append(
            (
                at,
                {
                    "at": at.isoformat(),
                    "kind": "evidence",
                    "id": str(link.id),
                    "label": f"{link.evidence_type}:{link.role}",
                    "source_id": link.source_id,
                },
            )
        )
    for event in events:
        timeline.append(
            (
                event.occurred_at,
                {
                    "at": event.occurred_at.isoformat(),
                    "kind": "ledger_event",
                    "id": str(event.id),
                    "label": event.event_type,
                },
            )
        )
    for span in spans:
        try:
            at = datetime.fromtimestamp(
                int(span.start_time_unix_nano) / 1_000_000_000,
                tz=UTC,
            )
        except (TypeError, ValueError, OSError, OverflowError):
            at = span.received_at
        timeline.append(
            (
                at,
                {
                    "at": at.isoformat(),
                    "kind": "otel_span",
                    "id": str(span.id),
                    "label": span.name,
                    "span_id": span.span_id,
                },
            )
        )
    timeline.sort(key=lambda item: (item[0], item[1]["kind"], item[1]["id"]))
    envelope_payload = await envelope_out(
        db, envelope, decision=decision, evidence=evidence
    )
    return DecisionReconstructionOut(
        schema="https://lians.ai/schemas/decision-reconstruction/v2",
        generated_at=datetime.now(UTC),
        decision=decision_out(decision),
        envelope=envelope_payload,
        completeness=assessment,
        evidence=[evidence_out(row) for row in evidence],
        knowledge_snapshot=snapshot,
        ledger_events=[ledger_event_out(row) for row in events],
        trace_spans=[_span_out(row) for row in spans],
        timeline=[item[1] for item in timeline],
    )
