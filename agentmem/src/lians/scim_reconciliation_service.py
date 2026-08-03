"""Durable, leased SCIM tenant-binding reconciliation.

Tenant configuration mutations freeze an exact `(created_at, id)` User
snapshot. Workers reconcile that fixed set in bounded commits while fencing
every page on the immutable target tenant version. A newer tenant version can
therefore supersede stale work but can never be overwritten by it.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .audit_chain import chain_log
from .config import Settings, get_settings
from .db import set_current_barrier_group, set_current_namespace
from .enterprise_models import (
    ScimTenantConfig,
    ScimTenantReconciliationJob,
    ScimUser,
)
from .enterprise_service import ProvisioningError, sync_user_binding
from .identity_models import IdentityBinding, TrustedIdentityProvider

logger = logging.getLogger("lians.scim_reconciliation_worker")

_worker_last_heartbeat_at: datetime | None = None
_worker_last_iteration_healthy = False


class ScimReconciliationLeaseConflict(RuntimeError):
    """The page owner no longer has a valid lease."""


class ScimReconciliationInvariantError(RuntimeError):
    """Persisted fixed-snapshot state is internally inconsistent."""


@dataclass(frozen=True)
class ClaimedScimReconciliationJob:
    job_id: UUID
    tenant_config_id: UUID
    namespace: str


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _database_now(db: AsyncSession) -> datetime:
    value = (await db.execute(select(func.now()))).scalar_one()
    return _utc(value)


def _error_digest(exc: BaseException) -> str:
    identity = f"{type(exc).__module__}.{type(exc).__qualname__}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _error_code(exc: BaseException) -> tuple[str, bool]:
    if isinstance(exc, ScimReconciliationLeaseConflict):
        return "lease_lost", False
    if isinstance(exc, ScimReconciliationInvariantError):
        return "snapshot_invariant_failed", True
    if isinstance(exc, ProvisioningError):
        return "provisioning_conflict", True
    if isinstance(exc, (OperationalError, DBAPIError)):
        return "database_retryable", False
    return "processing_error", False


def _backoff_seconds(job_id: UUID, failures: int) -> float:
    settings = get_settings()
    ceiling = min(
        settings.scim_reconciliation_worker_retry_max_seconds,
        settings.scim_reconciliation_worker_retry_base_seconds
        * (2 ** max(0, failures - 1)),
    )
    jitter = int(hashlib.sha256(job_id.bytes).hexdigest()[:8], 16) / 0xFFFFFFFF
    return max(0.05, ceiling * (0.75 + 0.25 * jitter))


def scim_reconciliation_job_dict(
    job: ScimTenantReconciliationJob,
) -> dict[str, object]:
    total = int(job.snapshot_user_count)
    reconciled = int(job.users_reconciled)
    return {
        "id": job.id,
        "tenant_config_id": job.tenant_config_id,
        "namespace": job.namespace,
        "target_config_version": int(job.target_config_version),
        "target_enabled": bool(job.target_enabled),
        "target_revoked_at": job.target_revoked_at,
        "status": job.status,
        "snapshot_max_created_at": job.snapshot_max_created_at,
        "snapshot_max_user_id": job.snapshot_max_user_id,
        "snapshot_user_count": total,
        "cursor_created_at": job.cursor_created_at,
        "cursor_user_id": job.cursor_user_id,
        "users_reconciled": reconciled,
        "pages_completed": int(job.pages_completed),
        "processing_attempts": int(job.processing_attempts),
        "consecutive_failures": int(job.consecutive_failures),
        "attempt_limit": int(job.attempt_limit),
        "next_attempt_at": job.next_attempt_at,
        "lease_expires_at": job.lease_expires_at,
        "heartbeat_at": job.heartbeat_at,
        "last_attempt_at": job.last_attempt_at,
        "last_error_code": job.last_error_code,
        "last_error_digest": job.last_error_digest,
        "failure_code": job.failure_code,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
        "failed_at": job.failed_at,
        "superseded_at": job.superseded_at,
        "snapshot_complete": job.status == "completed",
        "progress_complete": reconciled == total,
        "completion_scope": "tenant_user_created_at_id_snapshot",
    }


async def fence_tenant_bindings(
    db: AsyncSession,
    *,
    config: ScimTenantConfig,
) -> tuple[int, int]:
    """Fence linked bindings and immediately close them for inactive targets.

    This is executed in the disable/revoke transaction. It closes access before
    the asynchronous evidence-rich per-User reconciliation starts.
    """

    binding_ids = select(ScimUser.identity_binding_id).where(
        ScimUser.tenant_config_id == config.id,
        ScimUser.identity_binding_id.is_not(None),
    )
    tenant_binding = or_(
        IdentityBinding.scim_tenant_config_id == config.id,
        # Legacy pre-fence bindings have no tenant tag yet, so retain the
        # relationship traversal while converging them onto the durable tag.
        IdentityBinding.id.in_(binding_ids),
    )
    now = await _database_now(db)
    inactive_target = not config.enabled or config.revoked_at is not None
    disabled = 0
    if inactive_target:
        disabled = int(
            (
                await db.execute(
                    select(func.count(IdentityBinding.id)).where(
                        tenant_binding,
                        IdentityBinding.namespace == config.namespace,
                        IdentityBinding.enabled.is_(True),
                    )
                )
            ).scalar_one()
        )
    values: dict[str, object] = {
        "scim_tenant_config_id": config.id,
        "scim_tenant_config_version": config.version,
        "scim_reconciliation_complete": False,
        "version": IdentityBinding.version + 1,
        "updated_at": now,
    }
    if inactive_target:
        values["enabled"] = False
    result = await db.execute(
        update(IdentityBinding)
        .where(
            tenant_binding,
            IdentityBinding.namespace == config.namespace,
        )
        .values(**values)
    )
    fenced = int(result.rowcount or 0)
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id="__admin__",
        op="scim.binding_activation_fenced",
        payload={
            "tenant_config_id": str(config.id),
            "target_config_version": int(config.version),
            "bindings_fenced": fenced,
            "bindings_disabled": disabled,
        },
    )
    return fenced, disabled


async def enqueue_scim_reconciliation(
    db: AsyncSession,
    *,
    config: ScimTenantConfig,
    requested_by_principal_ref: str,
) -> ScimTenantReconciliationJob:
    """Freeze and enqueue one exact User snapshot for the locked tenant row."""

    now = await _database_now(db)
    active = list(
        (
            await db.execute(
                select(ScimTenantReconciliationJob)
                .where(
                    ScimTenantReconciliationJob.tenant_config_id == config.id,
                    ScimTenantReconciliationJob.status.in_(("pending", "running")),
                )
                .order_by(ScimTenantReconciliationJob.created_at)
                .limit(2)
                .with_for_update()
            )
        ).scalars()
    )
    if len(active) > 1:
        raise ScimReconciliationInvariantError(
            "More than one active reconciliation exists for a tenant"
        )
    if active:
        stale = active[0]
        stale.status = "superseded"
        stale.superseded_at = now
        stale.lease_owner = None
        stale.lease_expires_at = None
        stale.updated_at = now
        # Persist the terminal transition before adding the replacement.  This
        # makes the partial unique active-job index an ordering guarantee even
        # when session autoflush settings change.
        await db.flush()
        await chain_log(
            db,
            namespace=config.namespace,
            agent_id=requested_by_principal_ref,
            op="scim.binding_reconciliation_superseded",
            payload={
                "job_id": str(stale.id),
                "tenant_config_id": str(config.id),
                "superseded_target_version": int(stale.target_config_version),
                "replacement_target_version": int(config.version),
                "users_reconciled": int(stale.users_reconciled),
                "snapshot_user_count": int(stale.snapshot_user_count),
            },
        )

    boundary = (
        await db.execute(
            select(ScimUser.created_at, ScimUser.id)
            .where(ScimUser.tenant_config_id == config.id)
            .order_by(ScimUser.created_at.desc(), ScimUser.id.desc())
            .limit(1)
        )
    ).one_or_none()
    if boundary is None:
        snapshot_count = 0
        snapshot_created_at = None
        snapshot_user_id = None
    else:
        snapshot_created_at = boundary.created_at
        snapshot_user_id = boundary.id
        snapshot_count = int(
            (
                await db.execute(
                    select(func.count(ScimUser.id)).where(
                        ScimUser.tenant_config_id == config.id,
                        or_(
                            ScimUser.created_at < snapshot_created_at,
                            and_(
                                ScimUser.created_at == snapshot_created_at,
                                ScimUser.id <= snapshot_user_id,
                            ),
                        ),
                    )
                )
            ).scalar_one()
        )

    completed = snapshot_count == 0
    job = ScimTenantReconciliationJob(
        tenant_config_id=config.id,
        namespace=config.namespace,
        target_config_version=config.version,
        target_enabled=config.enabled,
        target_revoked_at=config.revoked_at,
        requested_by_principal_ref=requested_by_principal_ref,
        status="completed" if completed else "pending",
        snapshot_max_created_at=snapshot_created_at,
        snapshot_max_user_id=snapshot_user_id,
        snapshot_user_count=snapshot_count,
        attempt_limit=get_settings().scim_reconciliation_worker_max_attempts,
        next_attempt_at=now,
        completed_at=now if completed else None,
    )
    db.add(job)
    await db.flush()
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id=requested_by_principal_ref,
        op=(
            "scim.binding_reconciliation_completed"
            if completed
            else "scim.binding_reconciliation_queued"
        ),
        payload={
            "job_id": str(job.id),
            "tenant_config_id": str(config.id),
            "target_config_version": int(config.version),
            "target_enabled": bool(config.enabled),
            "target_revoked": config.revoked_at is not None,
            "snapshot_user_count": snapshot_count,
            "users_reconciled": 0,
            "completion_scope": "tenant_user_created_at_id_snapshot",
        },
    )
    return job


async def get_scim_reconciliation_job(
    db: AsyncSession,
    *,
    tenant_config_id: UUID,
    job_id: UUID,
) -> ScimTenantReconciliationJob | None:
    return (
        await db.execute(
            select(ScimTenantReconciliationJob)
            .where(
                ScimTenantReconciliationJob.id == job_id,
                ScimTenantReconciliationJob.tenant_config_id == tenant_config_id,
            )
            # Request-driven advance processes the page in a separate session.
            # Refresh an identity-map resident claim so the response never
            # reports the pre-page status or cursor.
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def retry_scim_reconciliation_job(
    db: AsyncSession,
    *,
    tenant_config_id: UUID,
    job_id: UUID,
) -> ScimTenantReconciliationJob | None:
    config = (
        await db.execute(
            select(ScimTenantConfig)
            .where(ScimTenantConfig.id == tenant_config_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    job = (
        await db.execute(
            select(ScimTenantReconciliationJob)
            .where(
                ScimTenantReconciliationJob.id == job_id,
                ScimTenantReconciliationJob.tenant_config_id == tenant_config_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        return None
    if job.status != "failed":
        raise ProvisioningError(409, "Only a failed reconciliation can be retried")
    if config is None or config.version != job.target_config_version:
        raise ProvisioningError(
            409,
            "The failed reconciliation target was superseded by a tenant version change",
        )
    now = await _database_now(db)
    job.status = "pending"
    job.consecutive_failures = 0
    job.next_attempt_at = now
    job.last_error_code = None
    job.last_error_digest = None
    job.failure_code = None
    job.failed_at = None
    job.updated_at = now
    await chain_log(
        db,
        namespace=job.namespace,
        agent_id="__admin__",
        op="scim.binding_reconciliation_retry",
        payload={
            "job_id": str(job.id),
            "tenant_config_id": str(job.tenant_config_id),
            "target_config_version": int(job.target_config_version),
            "users_reconciled": int(job.users_reconciled),
            "snapshot_user_count": int(job.snapshot_user_count),
        },
    )
    await db.commit()
    await db.refresh(job)
    return job


async def claim_due_scim_reconciliation_jobs(
    db: AsyncSession,
    *,
    worker_id: str,
    batch_size: int,
    lease_seconds: int,
    tenant_config_id: UUID | None = None,
    job_id: UUID | None = None,
) -> list[ClaimedScimReconciliationJob]:
    now = await _database_now(db)
    filters = [
        ScimTenantReconciliationJob.status.in_(("pending", "running")),
        ScimTenantReconciliationJob.next_attempt_at <= now,
        or_(
            ScimTenantReconciliationJob.lease_owner.is_(None),
            ScimTenantReconciliationJob.lease_expires_at <= now,
        ),
    ]
    if tenant_config_id is not None:
        filters.append(ScimTenantReconciliationJob.tenant_config_id == tenant_config_id)
    if job_id is not None:
        filters.append(ScimTenantReconciliationJob.id == job_id)
    rows = list(
        (
            await db.execute(
                select(ScimTenantReconciliationJob)
                .where(*filters)
                .order_by(
                    ScimTenantReconciliationJob.next_attempt_at,
                    ScimTenantReconciliationJob.created_at,
                    ScimTenantReconciliationJob.id,
                )
                .limit(max(1, min(100, batch_size)))
                .with_for_update(skip_locked=db.get_bind().dialect.name == "postgresql")
            )
        ).scalars()
    )
    claims: list[ClaimedScimReconciliationJob] = []
    for job in rows:
        if job.lease_owner is not None:
            job.consecutive_failures += 1
            job.last_error_code = "lease_expired"
            job.last_error_digest = hashlib.sha256(b"lease_expired").hexdigest()
            if job.consecutive_failures >= job.attempt_limit:
                job.status = "failed"
                job.failure_code = "worker_attempt_limit_exhausted"
                job.failed_at = now
                job.lease_owner = None
                job.lease_expires_at = None
                await chain_log(
                    db,
                    namespace=job.namespace,
                    agent_id=job.requested_by_principal_ref,
                    op="scim.binding_reconciliation_failed",
                    payload={
                        "job_id": str(job.id),
                        "tenant_config_id": str(job.tenant_config_id),
                        "target_config_version": int(job.target_config_version),
                        "failure_code": job.failure_code,
                        "users_reconciled": int(job.users_reconciled),
                        "snapshot_user_count": int(job.snapshot_user_count),
                    },
                )
                continue
        job.status = "running"
        job.processing_attempts += 1
        job.last_attempt_at = now
        job.heartbeat_at = now
        job.lease_owner = worker_id
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        if job.started_at is None:
            job.started_at = now
        claims.append(
            ClaimedScimReconciliationJob(
                job_id=job.id,
                tenant_config_id=job.tenant_config_id,
                namespace=job.namespace,
            )
        )
    await db.commit()
    return claims


def _snapshot_filter(job: ScimTenantReconciliationJob):
    if job.snapshot_max_created_at is None or job.snapshot_max_user_id is None:
        raise ScimReconciliationInvariantError("A non-empty snapshot has no boundary")
    return or_(
        ScimUser.created_at < job.snapshot_max_created_at,
        and_(
            ScimUser.created_at == job.snapshot_max_created_at,
            ScimUser.id <= job.snapshot_max_user_id,
        ),
    )


def _cursor_filter(job: ScimTenantReconciliationJob):
    if job.cursor_created_at is None or job.cursor_user_id is None:
        return None
    return or_(
        ScimUser.created_at > job.cursor_created_at,
        and_(
            ScimUser.created_at == job.cursor_created_at,
            ScimUser.id > job.cursor_user_id,
        ),
    )


async def _mark_superseded(
    db: AsyncSession,
    *,
    job: ScimTenantReconciliationJob,
    current_version: int | None,
    now: datetime,
) -> None:
    job.status = "superseded"
    job.superseded_at = now
    job.lease_owner = None
    job.lease_expires_at = None
    job.updated_at = now
    await chain_log(
        db,
        namespace=job.namespace,
        agent_id=job.requested_by_principal_ref,
        op="scim.binding_reconciliation_superseded",
        payload={
            "job_id": str(job.id),
            "tenant_config_id": str(job.tenant_config_id),
            "target_config_version": int(job.target_config_version),
            "current_config_version": current_version,
            "users_reconciled": int(job.users_reconciled),
            "snapshot_user_count": int(job.snapshot_user_count),
        },
    )


async def _advance_one_page(
    db: AsyncSession,
    *,
    claim: ClaimedScimReconciliationJob,
    worker_id: str,
    page_size: int,
    lease_seconds: int,
) -> bool:
    # Tenant mutations take this same row first. Keeping a canonical lock order
    # (tenant, then job, then Users/bindings) prevents update/worker deadlocks.
    config = (
        await db.execute(
            select(ScimTenantConfig)
            .where(ScimTenantConfig.id == claim.tenant_config_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    job = (
        await db.execute(
            select(ScimTenantReconciliationJob)
            .where(
                ScimTenantReconciliationJob.id == claim.job_id,
                ScimTenantReconciliationJob.namespace == claim.namespace,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        job is None
        or job.status != "running"
        or job.lease_owner != worker_id
        or job.lease_expires_at is None
    ):
        raise ScimReconciliationLeaseConflict("SCIM reconciliation lease was lost")
    now = await _database_now(db)
    if _utc(job.lease_expires_at) <= now:
        raise ScimReconciliationLeaseConflict("SCIM reconciliation lease expired")

    target_matches = bool(
        config is not None
        and config.namespace == job.namespace
        and config.version == job.target_config_version
        and config.enabled == job.target_enabled
        and config.revoked_at == job.target_revoked_at
    )
    if not target_matches:
        await _mark_superseded(
            db,
            job=job,
            current_version=int(config.version) if config is not None else None,
            now=now,
        )
        await db.commit()
        return True
    if config is None:
        raise ScimReconciliationInvariantError(
            "SCIM reconciliation target matched without a tenant configuration"
        )

    provider = (
        await db.execute(
            select(TrustedIdentityProvider).where(
                TrustedIdentityProvider.id == config.provider_id
            )
        )
    ).scalar_one_or_none()
    filters = [
        ScimUser.tenant_config_id == job.tenant_config_id,
        _snapshot_filter(job),
    ]
    cursor = _cursor_filter(job)
    if cursor is not None:
        filters.append(cursor)
    users = list(
        (
            await db.execute(
                select(ScimUser)
                .where(*filters)
                .order_by(ScimUser.created_at, ScimUser.id)
                .limit(page_size)
                .with_for_update()
            )
        ).scalars()
    )
    if not users:
        raise ScimReconciliationInvariantError(
            "SCIM reconciliation snapshot ended before its exact count"
        )
    for user in users:
        await sync_user_binding(
            db,
            config=config,
            user=user,
            provider=provider,
            provider_loaded=True,
            activation_fence_complete=False,
        )
    job.users_reconciled += len(users)
    job.pages_completed += 1
    job.cursor_created_at = users[-1].created_at
    job.cursor_user_id = users[-1].id
    job.heartbeat_at = now
    job.updated_at = now
    job.consecutive_failures = 0
    job.last_error_code = None
    job.last_error_digest = None
    if job.users_reconciled > job.snapshot_user_count:
        raise ScimReconciliationInvariantError(
            "SCIM reconciliation exceeded its exact snapshot count"
        )
    completed = job.users_reconciled == job.snapshot_user_count
    if completed:
        if (
            _utc(job.cursor_created_at) != _utc(job.snapshot_max_created_at)
            or job.cursor_user_id != job.snapshot_max_user_id
        ):
            raise ScimReconciliationInvariantError(
                "SCIM reconciliation terminal cursor differs from its frozen boundary"
            )
        job.status = "completed"
        binding_ids = select(ScimUser.identity_binding_id).where(
            ScimUser.tenant_config_id == job.tenant_config_id,
            ScimUser.identity_binding_id.is_not(None),
        )
        activation = await db.execute(
            update(IdentityBinding)
            .where(
                IdentityBinding.id.in_(binding_ids),
                IdentityBinding.namespace == job.namespace,
                IdentityBinding.scim_tenant_config_id == job.tenant_config_id,
                IdentityBinding.scim_tenant_config_version
                == job.target_config_version,
                IdentityBinding.scim_reconciliation_complete.is_(False),
            )
            .values(
                scim_reconciliation_complete=True,
                version=IdentityBinding.version + 1,
                updated_at=now,
            )
        )
        bindings_fence_completed = int(activation.rowcount or 0)
        job.completed_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        await chain_log(
            db,
            namespace=job.namespace,
            agent_id=job.requested_by_principal_ref,
            op="scim.binding_reconciliation_completed",
            payload={
                "job_id": str(job.id),
                "tenant_config_id": str(job.tenant_config_id),
                "target_config_version": int(job.target_config_version),
                "users_reconciled": int(job.users_reconciled),
                "snapshot_user_count": int(job.snapshot_user_count),
                "pages_completed": int(job.pages_completed),
                "bindings_fence_completed": bindings_fence_completed,
                "completion_scope": "tenant_user_created_at_id_snapshot",
            },
        )
    else:
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    await db.commit()
    return completed


async def _record_failure(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claim: ClaimedScimReconciliationJob,
    worker_id: str,
    exc: BaseException,
) -> None:
    code, inherently_terminal = _error_code(exc)
    if code == "lease_lost":
        return
    set_current_namespace(claim.namespace)
    set_current_barrier_group(None)
    async with session_factory() as db:
        job = (
            await db.execute(
                select(ScimTenantReconciliationJob)
                .where(
                    ScimTenantReconciliationJob.id == claim.job_id,
                    ScimTenantReconciliationJob.namespace == claim.namespace,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None or job.status not in {"pending", "running"}:
            return
        if job.lease_owner not in {None, worker_id}:
            return
        now = await _database_now(db)
        job.consecutive_failures += 1
        job.last_error_code = code
        job.last_error_digest = _error_digest(exc)
        terminal = inherently_terminal or job.consecutive_failures >= job.attempt_limit
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = now
        job.updated_at = now
        if terminal:
            job.status = "failed"
            job.failure_code = (
                code if inherently_terminal else "worker_attempt_limit_exhausted"
            )
            job.failed_at = now
            await chain_log(
                db,
                namespace=job.namespace,
                agent_id=job.requested_by_principal_ref,
                op="scim.binding_reconciliation_failed",
                payload={
                    "job_id": str(job.id),
                    "tenant_config_id": str(job.tenant_config_id),
                    "target_config_version": int(job.target_config_version),
                    "failure_code": job.failure_code,
                    "users_reconciled": int(job.users_reconciled),
                    "snapshot_user_count": int(job.snapshot_user_count),
                },
            )
        else:
            job.status = "pending"
            job.next_attempt_at = now + timedelta(
                seconds=_backoff_seconds(job.id, job.consecutive_failures)
            )
        await db.commit()


async def process_scim_reconciliation_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claim: ClaimedScimReconciliationJob,
    worker_id: str,
    page_size: int,
    max_pages: int,
    lease_seconds: int,
) -> None:
    set_current_namespace(claim.namespace)
    set_current_barrier_group(None)
    try:
        for _ in range(max_pages):
            async with session_factory() as db:
                if await _advance_one_page(
                    db,
                    claim=claim,
                    worker_id=worker_id,
                    page_size=page_size,
                    lease_seconds=lease_seconds,
                ):
                    return
        async with session_factory() as db:
            job = (
                await db.execute(
                    select(ScimTenantReconciliationJob)
                    .where(
                        ScimTenantReconciliationJob.id == claim.job_id,
                        ScimTenantReconciliationJob.namespace == claim.namespace,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is not None and job.status == "running" and job.lease_owner == worker_id:
                job.lease_owner = None
                job.lease_expires_at = None
                job.next_attempt_at = await _database_now(db)
                await db.commit()
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        await _record_failure(
            session_factory,
            claim=claim,
            worker_id=worker_id,
            exc=exc,
        )
    finally:
        set_current_namespace(None)
        set_current_barrier_group(None)


def validate_scim_reconciliation_worker_configuration(
    settings: Settings,
    *,
    production: bool,
) -> list[str]:
    errors: list[str] = []
    if production and not settings.scim_reconciliation_worker_enabled:
        errors.append("SCIM_RECONCILIATION_WORKER_ENABLED must be true in production")
    if not 1 <= settings.scim_reconciliation_worker_batch_size <= 100:
        errors.append("SCIM_RECONCILIATION_WORKER_BATCH_SIZE must be between 1 and 100")
    if not 1 <= settings.scim_reconciliation_worker_concurrency <= 32:
        errors.append("SCIM_RECONCILIATION_WORKER_CONCURRENCY must be between 1 and 32")
    if not 30 <= settings.scim_reconciliation_worker_lease_seconds <= 3_600:
        errors.append("SCIM_RECONCILIATION_WORKER_LEASE_SECONDS must be between 30 and 3600")
    if not 1 <= settings.scim_reconciliation_worker_page_size <= 500:
        errors.append("SCIM_RECONCILIATION_WORKER_PAGE_SIZE must be between 1 and 500")
    if not 1 <= settings.scim_reconciliation_worker_max_pages_per_claim <= 20:
        errors.append(
            "SCIM_RECONCILIATION_WORKER_MAX_PAGES_PER_CLAIM must be between 1 and 20"
        )
    if not (
        0 < settings.scim_reconciliation_worker_retry_base_seconds
        <= settings.scim_reconciliation_worker_retry_max_seconds
        <= 3_600
    ):
        errors.append("SCIM reconciliation retry seconds must be positive, ordered, and <= 3600")
    if not 1 <= settings.scim_reconciliation_worker_max_attempts <= 100:
        errors.append("SCIM_RECONCILIATION_WORKER_MAX_ATTEMPTS must be between 1 and 100")
    return errors


def scim_reconciliation_worker_status() -> tuple[bool, datetime | None]:
    settings = get_settings()
    if not settings.scim_reconciliation_worker_enabled:
        return False, _worker_last_heartbeat_at
    threshold = max(30.0, settings.scim_reconciliation_worker_poll_seconds * 5)
    healthy = bool(
        _worker_last_iteration_healthy
        and _worker_last_heartbeat_at is not None
        and (datetime.now(UTC) - _worker_last_heartbeat_at).total_seconds() <= threshold
    )
    return healthy, _worker_last_heartbeat_at


def refresh_scim_reconciliation_worker_process_metrics() -> None:
    from .metrics import set_scim_reconciliation_worker_state

    healthy, heartbeat = scim_reconciliation_worker_status()
    set_scim_reconciliation_worker_state(
        enabled=get_settings().scim_reconciliation_worker_enabled,
        healthy=healthy,
        heartbeat_at=heartbeat,
    )


async def scim_reconciliation_inventory(
    db: AsyncSession,
    *,
    namespace: str | None = None,
) -> dict[str, object]:
    filters = []
    if namespace is not None:
        filters.append(ScimTenantReconciliationJob.namespace == namespace)
    counts = {
        str(status): int(count)
        for status, count in (
            await db.execute(
                select(
                    ScimTenantReconciliationJob.status,
                    func.count(ScimTenantReconciliationJob.id),
                )
                .where(*filters)
                .group_by(ScimTenantReconciliationJob.status)
            )
        ).all()
    }
    active = (
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
                *filters,
                ScimTenantReconciliationJob.status.in_(("pending", "running")),
            )
        )
    ).one()
    healthy, heartbeat = scim_reconciliation_worker_status()
    return {
        "counts": counts,
        "users_reconciled": int(active[0] or 0),
        "snapshot_users": int(active[1] or 0),
        "oldest_active_at": active[2],
        "worker_enabled": get_settings().scim_reconciliation_worker_enabled,
        "worker_healthy": healthy,
        "worker_heartbeat_at": heartbeat,
    }


async def run_scim_reconciliation_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    global _worker_last_heartbeat_at, _worker_last_iteration_healthy
    settings = get_settings()
    if not settings.scim_reconciliation_worker_enabled:
        return
    worker_id = f"scim-reconciliation:{uuid.uuid4()}"
    logger.info("Durable SCIM binding reconciliation worker started")
    try:
        while True:
            _worker_last_heartbeat_at = datetime.now(UTC)
            try:
                set_current_namespace("__admin__")
                set_current_barrier_group(None)
                async with session_factory() as db:
                    claims = await claim_due_scim_reconciliation_jobs(
                        db,
                        worker_id=worker_id,
                        batch_size=min(
                            settings.scim_reconciliation_worker_batch_size,
                            settings.scim_reconciliation_worker_concurrency,
                        ),
                        lease_seconds=settings.scim_reconciliation_worker_lease_seconds,
                    )
                set_current_namespace(None)
                set_current_barrier_group(None)
                await asyncio.gather(
                    *(
                        process_scim_reconciliation_job(
                            session_factory,
                            claim=claim,
                            worker_id=worker_id,
                            page_size=settings.scim_reconciliation_worker_page_size,
                            max_pages=settings.scim_reconciliation_worker_max_pages_per_claim,
                            lease_seconds=settings.scim_reconciliation_worker_lease_seconds,
                        )
                        for claim in claims
                    )
                )
                _worker_last_iteration_healthy = True
            except asyncio.CancelledError:
                raise
            except Exception:
                _worker_last_iteration_healthy = False
                logger.warning("SCIM binding reconciliation poll failed")
            finally:
                set_current_namespace(None)
                set_current_barrier_group(None)
                _worker_last_heartbeat_at = datetime.now(UTC)
            await asyncio.sleep(settings.scim_reconciliation_worker_poll_seconds)
    finally:
        set_current_namespace("__admin__")
        set_current_barrier_group(None)
        try:
            async with session_factory() as db:
                rows = list(
                    (
                        await db.execute(
                            select(ScimTenantReconciliationJob)
                            .where(ScimTenantReconciliationJob.lease_owner == worker_id)
                            .with_for_update(
                                skip_locked=db.get_bind().dialect.name == "postgresql"
                            )
                        )
                    ).scalars()
                )
                now = await _database_now(db)
                for job in rows:
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.next_attempt_at = now
                    job.updated_at = now
                await db.commit()
        finally:
            set_current_namespace(None)
            set_current_barrier_group(None)
