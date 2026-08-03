"""Policy mutation, residency enforcement, and atomic daily quota reservation."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import chain_log
from .config import get_settings
from .governance_models import NamespaceDailyUsage, NamespacePolicyRevision
from .governance_schemas import (
    EffectiveNamespaceGovernanceOut,
    NamespaceDailyUsageOut,
    NamespaceGovernancePolicyOut,
    NamespaceGovernancePolicyUpdate,
    NamespaceGovernanceStatusOut,
)
from .models import NamespacePolicy

_CAPTURE_MODE_ORDER = {"metadata_only": 0, "hash_only": 1, "full": 2}
_PLACEHOLDER_REGIONS = {"", "local", "unknown", "unset", "configure-me"}
_MAX_BIGINT = 9_223_372_036_854_775_807


class GovernanceViolation(HTTPException):
    """Structured, stable denial raised before a governed write begins."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        namespace: str,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
                "namespace": namespace,
                **(extra or {}),
            },
            headers=headers,
        )


def deployment_region() -> str:
    """Return the normalized server-owned processing region."""
    return get_settings().deployment_region.strip().lower()


def deployment_region_is_explicit() -> bool:
    return deployment_region() not in _PLACEHOLDER_REGIONS


def estimate_ingest_bytes(value: Any) -> int:
    """Deterministic UTF-8 wire-size estimate without retaining source content."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    document = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return len(document.encode("utf-8"))


def _policy_configured(policy: NamespacePolicy | None) -> bool:
    return bool(policy is not None and policy.governance_status != "unconfigured")


def _policy_active(policy: NamespacePolicy | None) -> bool:
    return bool(policy is not None and policy.governance_status == "active")


def _global_capture_modes() -> list[str]:
    modes = ["metadata_only", "hash_only"]
    if get_settings().recorder_allow_full_capture:
        modes.append("full")
    return modes


def _effective_capture_modes(policy: NamespacePolicy | None) -> list[str]:
    global_modes = set(_global_capture_modes())
    if not _policy_active(policy) or policy.allowed_recorder_capture_modes is None:
        return sorted(global_modes, key=_CAPTURE_MODE_ORDER.__getitem__)
    return sorted(
        global_modes.intersection(policy.allowed_recorder_capture_modes),
        key=_CAPTURE_MODE_ORDER.__getitem__,
    )


def _region_allowed(policy: NamespacePolicy | None) -> bool:
    if not _policy_active(policy) or policy.allowed_processing_regions is None:
        return True
    return deployment_region() in set(policy.allowed_processing_regions)


def _policy_out(
    namespace: str,
    policy: NamespacePolicy | None,
) -> NamespaceGovernancePolicyOut:
    status_value = policy.governance_status if policy is not None else "unconfigured"
    return NamespaceGovernancePolicyOut(
        namespace=namespace,
        configured=_policy_configured(policy),
        status=status_value,
        policy_version=int(policy.policy_version or 0) if policy is not None else 0,
        deployment_region=deployment_region(),
        processing_region_allowed=_region_allowed(policy),
        allowed_processing_regions=(
            list(policy.allowed_processing_regions)
            if policy is not None and policy.allowed_processing_regions is not None
            else None
        ),
        allowed_recorder_capture_modes=(
            list(policy.allowed_recorder_capture_modes)
            if policy is not None and policy.allowed_recorder_capture_modes is not None
            else None
        ),
        effective_recorder_capture_modes=_effective_capture_modes(policy),
        recorder_events_daily_limit=(
            policy.recorder_events_daily_limit if policy is not None else None
        ),
        decision_records_daily_limit=(
            policy.decision_records_daily_limit if policy is not None else None
        ),
        protected_actions_daily_limit=(
            policy.protected_actions_daily_limit if policy is not None else None
        ),
        memory_writes_daily_limit=(
            policy.memory_writes_daily_limit if policy is not None else None
        ),
        recalls_daily_limit=(policy.recalls_daily_limit if policy is not None else None),
        estimated_ingest_bytes_daily_limit=(
            policy.estimated_ingest_bytes_daily_limit if policy is not None else None
        ),
        governance_created_at=(policy.governance_created_at if policy is not None else None),
        governance_created_by=(policy.governance_created_by if policy is not None else None),
        governance_updated_at=(policy.governance_updated_at if policy is not None else None),
        governance_updated_by=(policy.governance_updated_by if policy is not None else None),
    )


def _period(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _remaining(limit: int | None, used: int) -> int | None:
    return None if limit is None else max(0, int(limit) - int(used))


def _usage_out(
    namespace: str,
    day: date,
    usage: NamespaceDailyUsage | None,
    policy: NamespacePolicy | None,
) -> NamespaceDailyUsageOut:
    period_start, reset_at = _period(day)
    recorder_events = int(usage.recorder_events or 0) if usage is not None else 0
    decision_records = int(usage.decision_records or 0) if usage is not None else 0
    protected_actions = int(usage.protected_actions or 0) if usage is not None else 0
    memory_writes = int(usage.memory_writes or 0) if usage is not None else 0
    recalls = int(usage.recalls or 0) if usage is not None else 0
    ingest_bytes = int(usage.estimated_ingest_bytes or 0) if usage is not None else 0
    limits_enabled = _policy_active(policy)
    recorder_limit = policy.recorder_events_daily_limit if limits_enabled else None
    decision_limit = policy.decision_records_daily_limit if limits_enabled else None
    protected_action_limit = policy.protected_actions_daily_limit if limits_enabled else None
    memory_limit = policy.memory_writes_daily_limit if limits_enabled else None
    recall_limit = policy.recalls_daily_limit if limits_enabled else None
    bytes_limit = policy.estimated_ingest_bytes_daily_limit if limits_enabled else None
    return NamespaceDailyUsageOut(
        namespace=namespace,
        usage_date=day,
        period_start=period_start,
        reset_at=reset_at,
        recorder_events=recorder_events,
        decision_records=decision_records,
        protected_actions=protected_actions,
        memory_writes=memory_writes,
        recalls=recalls,
        estimated_ingest_bytes=ingest_bytes,
        recorder_events_remaining=_remaining(recorder_limit, recorder_events),
        decision_records_remaining=_remaining(decision_limit, decision_records),
        protected_actions_remaining=_remaining(protected_action_limit, protected_actions),
        memory_writes_remaining=_remaining(memory_limit, memory_writes),
        recalls_remaining=_remaining(recall_limit, recalls),
        estimated_ingest_bytes_remaining=_remaining(bytes_limit, ingest_bytes),
    )


async def get_effective_governance(
    db: AsyncSession,
    namespace: str,
    *,
    day: date | None = None,
) -> EffectiveNamespaceGovernanceOut:
    usage_day = day or datetime.now(UTC).date()
    policy = await db.get(NamespacePolicy, namespace)
    usage = (
        await db.execute(
            select(NamespaceDailyUsage).where(
                NamespaceDailyUsage.namespace == namespace,
                NamespaceDailyUsage.usage_date == usage_day,
            )
        )
    ).scalar_one_or_none()
    configured = _policy_configured(policy)
    active = _policy_active(policy)
    disclosures = [
        "Processing residency is evaluated from the server DEPLOYMENT_REGION; client headers are ignored.",
        "Usage periods reset at 00:00 UTC and reservations commit atomically with accepted writes.",
    ]
    if not configured:
        disclosures.append(
            "No namespace governance policy is configured; legacy unlimited/global behavior applies."
        )
    elif not active:
        disclosures.append(
            "The namespace policy is disabled; limits and namespace-specific restrictions are not enforced."
        )
    return EffectiveNamespaceGovernanceOut(
        generated_at=datetime.now(UTC),
        policy=_policy_out(namespace, policy),
        usage=_usage_out(namespace, usage_day, usage, policy),
        disclosures=disclosures,
    )


async def _ensure_usage_row(
    db: AsyncSession,
    namespace: str,
    usage_day: date,
) -> NamespaceDailyUsage:
    values = {
        "id": uuid.uuid4(),
        "namespace": namespace,
        "usage_date": usage_day,
        "recorder_events": 0,
        "decision_records": 0,
        "protected_actions": 0,
        "memory_writes": 0,
        "recalls": 0,
        "estimated_ingest_bytes": 0,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert

        statement = dialect_insert(NamespaceDailyUsage).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["namespace", "usage_date"])
        await db.execute(statement)
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        statement = dialect_insert(NamespaceDailyUsage).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["namespace", "usage_date"])
        await db.execute(statement)
    else:
        existing = (
            await db.execute(
                select(NamespaceDailyUsage).where(
                    NamespaceDailyUsage.namespace == namespace,
                    NamespaceDailyUsage.usage_date == usage_day,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            await db.execute(insert(NamespaceDailyUsage).values(**values))

    row = (
        await db.execute(
            select(NamespaceDailyUsage)
            .where(
                NamespaceDailyUsage.namespace == namespace,
                NamespaceDailyUsage.usage_date == usage_day,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise RuntimeError("daily namespace usage row could not be locked")
    return row


def _positive_delta(name: str, value: int) -> int:
    amount = int(value)
    if amount < 0 or amount > _MAX_BIGINT:
        raise ValueError(f"{name} must be between 0 and {_MAX_BIGINT}")
    return amount


async def _lock_policy_boundary(
    db: AsyncSession,
    namespace: str,
    *,
    shared: bool,
) -> None:
    """Serialize first-policy creation with enforcement even before a row exists."""
    if db.get_bind().dialect.name != "postgresql":
        return
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await db.execute(
        text(f"SELECT {function}(hashtextextended(:key, 0))"),
        {"key": f"lians:namespace-governance:{namespace}"},
    )


async def reserve_namespace_usage(
    db: AsyncSession,
    *,
    namespace: str,
    recorder_events: int = 0,
    decision_records: int = 0,
    protected_actions: int = 0,
    memory_writes: int = 0,
    recalls: int = 0,
    estimated_ingest_bytes: int = 0,
    capture_modes: Iterable[str] = (),
) -> NamespaceDailyUsage:
    """Validate policy and reserve counters inside the caller's transaction."""
    delta = {
        "recorder_events": _positive_delta("recorder_events", recorder_events),
        "decision_records": _positive_delta("decision_records", decision_records),
        "protected_actions": _positive_delta("protected_actions", protected_actions),
        "memory_writes": _positive_delta("memory_writes", memory_writes),
        "recalls": _positive_delta("recalls", recalls),
        "estimated_ingest_bytes": _positive_delta("estimated_ingest_bytes", estimated_ingest_bytes),
    }
    normalized_modes = {mode.strip().lower() for mode in capture_modes}
    invalid_modes = normalized_modes.difference(_CAPTURE_MODE_ORDER)
    if invalid_modes:
        raise ValueError(f"unknown capture modes: {sorted(invalid_modes)}")

    await _lock_policy_boundary(db, namespace, shared=True)
    policy = (
        await db.execute(
            select(NamespacePolicy)
            .where(NamespacePolicy.namespace == namespace)
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if _policy_active(policy):
        region = deployment_region()
        if not _region_allowed(policy):
            raise GovernanceViolation(
                status_code=status.HTTP_403_FORBIDDEN,
                code="processing_region_not_allowed",
                message="This deployment region is not allowed by the namespace policy.",
                namespace=namespace,
                extra={
                    "deployment_region": region,
                    "policy_version": int(policy.policy_version),
                },
            )
        allowed_modes = set(_effective_capture_modes(policy))
        rejected_modes = normalized_modes.difference(allowed_modes)
        if rejected_modes:
            raise GovernanceViolation(
                status_code=status.HTTP_403_FORBIDDEN,
                code="recorder_capture_mode_not_allowed",
                message="A requested Recorder capture mode is not allowed by policy.",
                namespace=namespace,
                extra={
                    "rejected_capture_modes": sorted(rejected_modes),
                    "effective_capture_modes": sorted(
                        allowed_modes,
                        key=_CAPTURE_MODE_ORDER.__getitem__,
                    ),
                    "policy_version": int(policy.policy_version),
                },
            )

    usage_day = datetime.now(UTC).date()
    usage = await _ensure_usage_row(db, namespace, usage_day)
    limits = {
        "recorder_events": (policy.recorder_events_daily_limit if _policy_active(policy) else None),
        "decision_records": (
            policy.decision_records_daily_limit if _policy_active(policy) else None
        ),
        "protected_actions": (
            policy.protected_actions_daily_limit if _policy_active(policy) else None
        ),
        "memory_writes": (policy.memory_writes_daily_limit if _policy_active(policy) else None),
        "recalls": (policy.recalls_daily_limit if _policy_active(policy) else None),
        "estimated_ingest_bytes": (
            policy.estimated_ingest_bytes_daily_limit if _policy_active(policy) else None
        ),
    }
    _, reset_at = _period(usage_day)
    for metric, requested in delta.items():
        if requested == 0:
            continue
        current = int(getattr(usage, metric) or 0)
        limit = limits[metric]
        if current + requested > _MAX_BIGINT:
            raise GovernanceViolation(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                code="daily_usage_counter_overflow",
                message="The daily usage counter cannot accept this reservation.",
                namespace=namespace,
                extra={"metric": metric, "reset_at": reset_at.isoformat()},
                headers={
                    "Retry-After": str(max(1, int((reset_at - datetime.now(UTC)).total_seconds())))
                },
            )
        if limit is not None and current + requested > int(limit):
            raise GovernanceViolation(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                code="namespace_daily_quota_exceeded",
                message=f"The namespace daily quota for {metric} would be exceeded.",
                namespace=namespace,
                extra={
                    "metric": metric,
                    "limit": int(limit),
                    "used": current,
                    "requested": requested,
                    "remaining": max(0, int(limit) - current),
                    "reset_at": reset_at.isoformat(),
                    "policy_version": int(policy.policy_version),
                },
                headers={
                    "Retry-After": str(max(1, int((reset_at - datetime.now(UTC)).total_seconds())))
                },
            )

    for metric, requested in delta.items():
        setattr(usage, metric, int(getattr(usage, metric) or 0) + requested)
    usage.updated_at = datetime.now(UTC)
    await db.flush()
    return usage


def _snapshot(policy: NamespacePolicy) -> dict[str, Any]:
    return {
        "namespace": policy.namespace,
        "status": policy.governance_status,
        "policy_version": int(policy.policy_version),
        "allowed_processing_regions": policy.allowed_processing_regions,
        "allowed_recorder_capture_modes": policy.allowed_recorder_capture_modes,
        "recorder_events_daily_limit": policy.recorder_events_daily_limit,
        "decision_records_daily_limit": policy.decision_records_daily_limit,
        "protected_actions_daily_limit": policy.protected_actions_daily_limit,
        "memory_writes_daily_limit": policy.memory_writes_daily_limit,
        "recalls_daily_limit": policy.recalls_daily_limit,
        "estimated_ingest_bytes_daily_limit": policy.estimated_ingest_bytes_daily_limit,
        "governance_created_at": (
            policy.governance_created_at.isoformat() if policy.governance_created_at else None
        ),
        "governance_created_by": policy.governance_created_by,
        "governance_updated_at": (
            policy.governance_updated_at.isoformat() if policy.governance_updated_at else None
        ),
        "governance_updated_by": policy.governance_updated_by,
    }


async def _append_revision(
    db: AsyncSession,
    policy: NamespacePolicy,
    *,
    action: str,
    actor_id: str,
) -> NamespacePolicyRevision:
    snapshot = _snapshot(policy)
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    snapshot_hash = hashlib.sha256(canonical.encode()).hexdigest()
    revision = NamespacePolicyRevision(
        namespace=policy.namespace,
        policy_version=policy.policy_version,
        action=action,
        actor_id=actor_id,
        policy_snapshot=snapshot,
        snapshot_hash=snapshot_hash,
        created_at=datetime.now(UTC),
    )
    db.add(revision)
    await chain_log(
        db,
        namespace=policy.namespace,
        agent_id=actor_id,
        op=f"admin.governance_{action}",
        content_hash=snapshot_hash,
        payload={
            "policy_version": int(policy.policy_version),
            "status": policy.governance_status,
            "snapshot_hash": snapshot_hash,
        },
    )
    return revision


async def put_governance_policy(
    db: AsyncSession,
    namespace: str,
    body: NamespaceGovernancePolicyUpdate,
    actor_id: str,
) -> NamespaceGovernancePolicyOut:
    await _lock_policy_boundary(db, namespace, shared=False)
    policy = (
        await db.execute(
            select(NamespacePolicy).where(NamespacePolicy.namespace == namespace).with_for_update()
        )
    ).scalar_one_or_none()
    current_version = int(policy.policy_version or 0) if policy is not None else 0
    if current_version != body.expected_version:
        raise HTTPException(status_code=409, detail="Governance policy version conflict")
    if policy is None:
        policy = NamespacePolicy(namespace=namespace)
        db.add(policy)
        await db.flush()
    was_configured = _policy_configured(policy)
    now = datetime.now(UTC)
    policy.governance_status = "active"
    policy.allowed_processing_regions = body.allowed_processing_regions
    policy.allowed_recorder_capture_modes = body.allowed_recorder_capture_modes
    policy.recorder_events_daily_limit = body.recorder_events_daily_limit
    policy.decision_records_daily_limit = body.decision_records_daily_limit
    policy.protected_actions_daily_limit = body.protected_actions_daily_limit
    policy.memory_writes_daily_limit = body.memory_writes_daily_limit
    policy.recalls_daily_limit = body.recalls_daily_limit
    policy.estimated_ingest_bytes_daily_limit = body.estimated_ingest_bytes_daily_limit
    policy.policy_version = int(policy.policy_version or 0) + 1
    if policy.governance_created_at is None:
        policy.governance_created_at = now
        policy.governance_created_by = actor_id
    policy.governance_updated_at = now
    policy.governance_updated_by = actor_id
    policy.updated_at = now
    await db.flush()
    await _append_revision(
        db,
        policy,
        action="updated" if was_configured else "created",
        actor_id=actor_id,
    )
    await db.commit()
    await db.refresh(policy)
    return _policy_out(namespace, policy)


async def set_governance_status(
    db: AsyncSession,
    namespace: str,
    target_status: str,
    actor_id: str,
    expected_version: int,
) -> NamespaceGovernancePolicyOut:
    if target_status not in {"active", "disabled"}:
        raise ValueError("target_status must be 'active' or 'disabled'")
    await _lock_policy_boundary(db, namespace, shared=False)
    policy = (
        await db.execute(
            select(NamespacePolicy).where(NamespacePolicy.namespace == namespace).with_for_update()
        )
    ).scalar_one_or_none()
    if not _policy_configured(policy):
        raise HTTPException(status_code=404, detail="Namespace governance policy not found")
    if int(policy.policy_version) != expected_version:
        raise HTTPException(status_code=409, detail="Governance policy version conflict")
    if policy.governance_status == target_status:
        return _policy_out(namespace, policy)
    now = datetime.now(UTC)
    policy.governance_status = target_status
    policy.policy_version = int(policy.policy_version) + 1
    policy.governance_updated_at = now
    policy.governance_updated_by = actor_id
    policy.updated_at = now
    await db.flush()
    action = "enabled" if target_status == "active" else "disabled"
    await _append_revision(db, policy, action=action, actor_id=actor_id)
    await db.commit()
    await db.refresh(policy)
    return _policy_out(namespace, policy)


async def clear_governance_policy(
    db: AsyncSession,
    namespace: str,
    actor_id: str,
    expected_version: int,
) -> bool:
    await _lock_policy_boundary(db, namespace, shared=False)
    policy = (
        await db.execute(
            select(NamespacePolicy).where(NamespacePolicy.namespace == namespace).with_for_update()
        )
    ).scalar_one_or_none()
    if not _policy_configured(policy):
        raise HTTPException(status_code=404, detail="Namespace governance policy not found")
    if int(policy.policy_version) != expected_version:
        raise HTTPException(status_code=409, detail="Governance policy version conflict")
    now = datetime.now(UTC)
    policy.governance_status = "unconfigured"
    policy.allowed_processing_regions = None
    policy.allowed_recorder_capture_modes = None
    policy.recorder_events_daily_limit = None
    policy.decision_records_daily_limit = None
    policy.protected_actions_daily_limit = None
    policy.memory_writes_daily_limit = None
    policy.recalls_daily_limit = None
    policy.estimated_ingest_bytes_daily_limit = None
    policy.policy_version = int(policy.policy_version) + 1
    policy.governance_updated_at = now
    policy.governance_updated_by = actor_id
    policy.updated_at = now
    await db.flush()
    await _append_revision(db, policy, action="cleared", actor_id=actor_id)
    await db.commit()
    return True


async def list_governance_policies(
    db: AsyncSession,
    *,
    include_unconfigured: bool = False,
    limit: int = 200,
    offset: int = 0,
    after_namespace: str | None = None,
) -> list[NamespaceGovernancePolicyOut]:
    statement = select(NamespacePolicy)
    if not include_unconfigured:
        statement = statement.where(NamespacePolicy.governance_status != "unconfigured")
    if after_namespace is not None:
        statement = statement.where(NamespacePolicy.namespace > after_namespace)
    policies = (
        (
            await db.execute(
                statement.order_by(NamespacePolicy.namespace).offset(offset).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [_policy_out(policy.namespace, policy) for policy in policies]


async def governance_status(
    db: AsyncSession,
    namespace: str,
) -> NamespaceGovernanceStatusOut:
    effective = await get_effective_governance(db, namespace)
    revision_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(NamespacePolicyRevision)
                .where(NamespacePolicyRevision.namespace == namespace)
            )
        ).scalar_one()
    )
    latest_hash = (
        await db.execute(
            select(NamespacePolicyRevision.snapshot_hash)
            .where(NamespacePolicyRevision.namespace == namespace)
            .order_by(
                NamespacePolicyRevision.policy_version.desc(),
                NamespacePolicyRevision.created_at.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return NamespaceGovernanceStatusOut(
        effective=effective,
        revision_count=revision_count,
        latest_snapshot_hash=latest_hash,
    )
