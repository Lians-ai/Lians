"""
Break-glass admin API: provision, list, revoke, and rotate API keys.

Protected by X-Admin-Secret header (separate from per-namespace API keys).
The plaintext key is returned ONCE at creation or rotation and never stored.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import (
    AuditCapacityExceeded,
    AuditCursorInvalid,
    chain_log,
    export_audit_log,
    verify_chain,
)
from ..config import get_settings
from ..db import get_db, set_current_barrier_group, set_current_namespace
from ..memory_service import (
    _acquire_pg_advisory_lock,
    get_retention_policy,
    prune_expired_content,
    set_retention_policy,
)
from ..metering_schemas import (
    MeteringEventOut,
    MeteringInventoryOut,
    MeteringReplayRequest,
    MeteringStatus,
)
from ..models import AgentBarrierGroup, ApiKey, NamespacePolicy
from ..mutation_safety import (
    MutationVersionConflict,
    assert_expected_updated_at,
    reject_non_replayable_idempotency_key,
)
from ..schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    AuditChainVerifyResult,
    AuditExportResult,
    BarrierGroupAssign,
    BarrierGroupOut,
    NamespaceBillingIn,
    NamespaceBillingOut,
    RetentionPolicyIn,
    RetentionPolicyOut,
    RetentionPruneResult,
)

router = APIRouter(prefix="/v1/admin", tags=["admin"])

_admin_header = APIKeyHeader(name="X-Admin-Secret", auto_error=False)
_ADMIN_AGENT = "__admin__"
_MAX_USAGE_OPERATION_GROUPS = 500

_CREATED_AT_INVENTORY_RESPONSES = {
    200: {
        "description": "Exact, secret-free inventory page",
        "headers": {
            "X-Lians-Total-Count": {"schema": {"type": "integer", "minimum": 0}},
            "X-Lians-Page-Limit": {"schema": {"type": "integer", "minimum": 1}},
            "X-Lians-Page-Returned": {
                "schema": {"type": "integer", "minimum": 0}
            },
            "X-Lians-Has-More": {"schema": {"type": "boolean"}},
            "X-Lians-Page-Complete": {"schema": {"type": "boolean"}},
            "X-Lians-Collection-Complete": {"schema": {"type": "boolean"}},
            "X-Lians-Next-Before-Created-At": {
                "schema": {"type": "string", "format": "date-time"}
            },
            "X-Lians-Next-Before-Id": {
                "schema": {"type": "string", "format": "uuid"}
            },
            "X-Lians-Next-Created-At": {
                "schema": {"type": "string", "format": "date-time"}
            },
            "X-Lians-Next-Id": {
                "schema": {"type": "string", "format": "uuid"}
            },
        },
    }
}

_BARRIER_INVENTORY_RESPONSES = {
    200: {
        "description": "Exact information-barrier assignment page",
        "headers": {
            "X-Lians-Total-Count": {"schema": {"type": "integer", "minimum": 0}},
            "X-Lians-Page-Limit": {"schema": {"type": "integer", "minimum": 1}},
            "X-Lians-Page-Returned": {
                "schema": {"type": "integer", "minimum": 0}
            },
            "X-Lians-Has-More": {"schema": {"type": "boolean"}},
            "X-Lians-Page-Complete": {"schema": {"type": "boolean"}},
            "X-Lians-Collection-Complete": {"schema": {"type": "boolean"}},
            "X-Lians-Next-After-Agent-Id": {"schema": {"type": "string"}},
            "X-Lians-Next-Agent-Id": {"schema": {"type": "string"}},
        },
    }
}

_METERING_EVENT_LIST_RESPONSES = {
    200: {
        "description": "Secret-free durable metering event inventory page",
        "headers": {
            "X-Lians-Total-Count": {"schema": {"type": "integer", "minimum": 0}},
            "X-Lians-Page-Limit": {"schema": {"type": "integer", "minimum": 1}},
            "X-Lians-Page-Returned": {
                "schema": {"type": "integer", "minimum": 0}
            },
            "X-Lians-Has-More": {"schema": {"type": "boolean"}},
            "X-Lians-Page-Complete": {"schema": {"type": "boolean"}},
            "X-Lians-Collection-Complete": {"schema": {"type": "boolean"}},
            "X-Lians-Next-Before-Updated-At": {
                "schema": {"type": "string", "format": "date-time"}
            },
            "X-Lians-Next-Before-Id": {
                "schema": {"type": "string", "format": "uuid"}
            },
        },
    }
}


def _set_metering_event_page_headers(
    response: Response,
    *,
    total: int,
    limit: int,
    returned: int,
    has_more: bool,
    cursor_supplied: bool,
    next_updated_at: datetime | None,
    next_id: UUID | None,
) -> None:
    response.headers["X-Lians-Total-Count"] = str(total)
    response.headers["X-Lians-Page-Limit"] = str(limit)
    response.headers["X-Lians-Page-Returned"] = str(returned)
    response.headers["X-Lians-Has-More"] = str(has_more).lower()
    response.headers["X-Lians-Page-Complete"] = str(not has_more).lower()
    response.headers["X-Lians-Collection-Complete"] = str(
        not cursor_supplied and not has_more and total == returned
    ).lower()
    if has_more and next_updated_at is not None and next_id is not None:
        response.headers["X-Lians-Next-Before-Updated-At"] = (
            next_updated_at.isoformat()
        )
        response.headers["X-Lians-Next-Before-Id"] = str(next_id)


def _set_created_at_page_headers(
    response: Response,
    *,
    total: int,
    limit: int,
    returned: int,
    has_more: bool,
    cursor_supplied: bool,
    next_created_at: datetime | None,
    next_id: UUID | None,
) -> None:
    response.headers["X-Lians-Total-Count"] = str(total)
    response.headers["X-Lians-Page-Limit"] = str(limit)
    response.headers["X-Lians-Page-Returned"] = str(returned)
    response.headers["X-Lians-Has-More"] = str(has_more).lower()
    response.headers["X-Lians-Page-Complete"] = str(not has_more).lower()
    response.headers["X-Lians-Collection-Complete"] = str(
        not cursor_supplied and not has_more and total == returned
    ).lower()
    if has_more and next_created_at is not None and next_id is not None:
        encoded_created_at = next_created_at.isoformat()
        encoded_id = str(next_id)
        response.headers["X-Lians-Next-Before-Created-At"] = encoded_created_at
        response.headers["X-Lians-Next-Before-Id"] = encoded_id
        # Rolling compatibility with the original cursor header names.
        response.headers["X-Lians-Next-Created-At"] = encoded_created_at
        response.headers["X-Lians-Next-Id"] = encoded_id


def _set_barrier_page_headers(
    response: Response,
    *,
    total: int,
    limit: int,
    returned: int,
    has_more: bool,
    cursor_supplied: bool,
    next_agent_id: str | None,
) -> None:
    response.headers["X-Lians-Total-Count"] = str(total)
    response.headers["X-Lians-Page-Limit"] = str(limit)
    response.headers["X-Lians-Page-Returned"] = str(returned)
    response.headers["X-Lians-Has-More"] = str(has_more).lower()
    response.headers["X-Lians-Page-Complete"] = str(not has_more).lower()
    response.headers["X-Lians-Collection-Complete"] = str(
        not cursor_supplied and not has_more and total == returned
    ).lower()
    if has_more and next_agent_id is not None:
        response.headers["X-Lians-Next-After-Agent-Id"] = next_agent_id
        response.headers["X-Lians-Next-Agent-Id"] = next_agent_id


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _generate_key() -> str:
    return "agentmem_" + secrets.token_urlsafe(32)


def _api_key_payload(row: ApiKey) -> dict:
    return {
        "id": row.id,
        "namespace": row.namespace,
        "label": row.label,
        "scopes": list(row.scopes),
        "role": row.role,
        "barrier_group": row.barrier_group,
        "created_at": row.created_at,
        "rotated_at": row.rotated_at,
        "revoked_at": row.revoked_at,
        "provisioning_source": row.provisioning_source,
        "created_by": row.created_by,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
        "rotated_from_id": row.rotated_from_id,
        "version": row.version,
    }


async def _api_key_for_update(db: AsyncSession, key_id: UUID) -> ApiKey:
    row = (
        await db.execute(select(ApiKey).where(ApiKey.id == key_id).with_for_update())
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    return row


async def _lock_barrier_assignment_boundary(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
) -> None:
    """Serialize assignment creation too, where no row exists to lock yet."""
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"lians:barrier-assignment:{namespace}:{agent_id}"},
        )


async def _require_admin(
    secret: Annotated[Optional[str], Security(_admin_header)],
) -> None:
    expected = get_settings().admin_secret
    if not secret or not secrets.compare_digest(secret, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Secret")
    # Admin routes do not use get_auth(), so establish the RLS bypass sentinel
    # before their DB dependency opens a transaction. get_db() clears it at
    # request teardown.
    set_current_namespace("__admin__")
    set_current_barrier_group(None)


@router.post(
    "/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Break-glass provision a global-admin-managed API key",
)
async def provision_key(
    body: ApiKeyCreate,
    response: Response,
    _retry_contract: None = Depends(reject_non_replayable_idempotency_key),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    raw = _generate_key()
    row = ApiKey(
        hashed_key=_hash(raw),
        namespace=body.namespace,
        label=body.label,
        scopes=body.scopes,
        role=body.role,
        barrier_group=body.barrier_group,
        provisioning_source="breakglass_admin",
        created_by="breakglass_admin:X-Admin-Secret",
    )
    db.add(row)
    await db.flush()
    await chain_log(
        db, namespace=body.namespace, agent_id=_ADMIN_AGENT,
        op="admin.key_provision",
        payload={
            "key_id": str(row.id),
            "label_hash": _hash(body.label) if body.label else None,
            "scopes": list(body.scopes),
            "role": body.role,
            "barrier_group": body.barrier_group,
        },
    )
    await db.commit()
    await db.refresh(row)
    return ApiKeyCreated(**_api_key_payload(row), key=raw)


@router.get(
    "/api-keys",
    response_model=list[ApiKeyOut],
    responses=_CREATED_AT_INVENTORY_RESPONSES,
    summary="Break-glass list all API keys, optionally filtered by namespace",
)
async def list_keys(
    response: Response,
    namespace: Optional[str] = Query(default=None),
    include_revoked: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    before_created_at: Optional[datetime] = Query(default=None),
    before_id: Optional[UUID] = Query(default=None),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyOut]:
    conditions = []
    if namespace:
        conditions.append(ApiKey.namespace == namespace)
    if not include_revoked:
        conditions.append(ApiKey.revoked_at.is_(None))
    if (before_created_at is None) != (before_id is None):
        raise HTTPException(422, "before_created_at and before_id must be supplied together")
    total_statement = select(func.count(ApiKey.id))
    if conditions:
        total_statement = total_statement.where(and_(*conditions))
    total = int((await db.execute(total_statement)).scalar_one() or 0)
    page_conditions = list(conditions)
    if before_created_at is not None and before_id is not None:
        page_conditions.append(
            or_(
                ApiKey.created_at < before_created_at,
                and_(ApiKey.created_at == before_created_at, ApiKey.id < before_id),
            )
        )
    stmt = select(ApiKey)
    if page_conditions:
        stmt = stmt.where(and_(*page_conditions))
    result = await db.execute(
        stmt.order_by(ApiKey.created_at.desc(), ApiKey.id.desc()).limit(limit + 1)
    )
    fetched = result.scalars().all()
    has_more = len(fetched) > limit
    rows = fetched[:limit]
    next_row = rows[-1] if has_more and rows else None
    _set_created_at_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(rows),
        has_more=has_more,
        cursor_supplied=before_created_at is not None,
        next_created_at=next_row.created_at if next_row is not None else None,
        next_id=next_row.id if next_row is not None else None,
    )
    return [
        ApiKeyOut(**_api_key_payload(r))
        for r in rows
    ]


@router.delete(
    "/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Break-glass revoke any API key immediately",
)
async def revoke_key(
    key_id: UUID,
    expected_version: int = Query(..., ge=1),
    _retry_contract: None = Depends(reject_non_replayable_idempotency_key),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    row = await _api_key_for_update(db, key_id)
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="API key already revoked")
    if row.version != expected_version:
        raise HTTPException(status_code=409, detail="API key version conflict")
    row.revoked_at = datetime.now(timezone.utc)
    row.version = (row.version or 1) + 1
    await chain_log(
        db, namespace=row.namespace, agent_id=_ADMIN_AGENT,
        op="admin.key_revoke",
        payload={
            "key_id": str(key_id),
            "label_hash": _hash(row.label) if row.label else None,
            "role": row.role,
            "barrier_group": row.barrier_group,
        },
    )
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/api-keys/{key_id}/rotate",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Break-glass rotate an API key — old key is revoked, new key is returned",
)
async def rotate_key(
    key_id: UUID,
    response: Response,
    expected_version: int = Query(..., ge=1),
    _retry_contract: None = Depends(reject_non_replayable_idempotency_key),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    old = await _api_key_for_update(db, key_id)
    if old.revoked_at is not None:
        raise HTTPException(status_code=409, detail="API key already revoked")
    if old.version != expected_version:
        raise HTTPException(status_code=409, detail="API key version conflict")

    now = datetime.now(timezone.utc)
    old.rotated_at = now
    old.revoked_at = now
    old.version = (old.version or 1) + 1

    raw = _generate_key()
    new_row = ApiKey(
        hashed_key=_hash(raw),
        namespace=old.namespace,
        label=old.label,
        scopes=old.scopes,
        role=old.role,
        barrier_group=old.barrier_group,
        provisioning_source="breakglass_admin",
        created_by="breakglass_admin:X-Admin-Secret",
        rotated_from_id=old.id,
    )
    db.add(new_row)
    await db.flush()
    await chain_log(
        db, namespace=old.namespace, agent_id=_ADMIN_AGENT,
        op="admin.key_rotate",
        payload={
            "old_key_id": str(key_id),
            "new_key_id": str(new_row.id),
            "label_hash": _hash(old.label) if old.label else None,
            "role": old.role,
            "barrier_group": old.barrier_group,
        },
    )
    await db.commit()
    await db.refresh(new_row)
    return ApiKeyCreated(**_api_key_payload(new_row), key=raw)


# ── Information Barrier Group Management ────────────────────────────────────

@router.post(
    "/barriers",
    response_model=BarrierGroupOut,
    status_code=status.HTTP_201_CREATED,
    summary="Assign an agent to an information barrier group",
)
async def assign_barrier_group(
    body: BarrierGroupAssign,
    namespace: str = Query(..., description="Target namespace"),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> BarrierGroupOut:
    """
    Assign an agent to a Chinese-wall barrier group.

    After this call, the agent can only recall memories tagged with the same
    group_name (or untagged public memories).  Memories written by this agent
    will be tagged with group_name automatically.

    To grant compliance-officer access (see all memories), do NOT assign the
    agent to any group — unassigned agents see everything in the namespace.

    Example barrier groups:  equity_desk, fixed_income, investment_banking
    """
    await _lock_barrier_assignment_boundary(db, namespace, body.agent_id)
    await _acquire_pg_advisory_lock(db, namespace, body.agent_id)
    existing = (
        await db.execute(
            select(AgentBarrierGroup)
            .where(
                AgentBarrierGroup.namespace == namespace,
                AgentBarrierGroup.agent_id == body.agent_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    current_group = existing.group_name if existing is not None else None
    if current_group != body.expected_group_name:
        raise HTTPException(status_code=409, detail="Barrier assignment version conflict")
    if existing:
        existing.group_name = body.group_name
        row = existing
    else:
        row = AgentBarrierGroup(
            agent_id=body.agent_id,
            namespace=namespace,
            group_name=body.group_name,
        )
        db.add(row)
    await chain_log(
        db, namespace=namespace, agent_id=_ADMIN_AGENT,
        op="admin.barrier_assign",
        payload={"agent_id": body.agent_id, "group_name": body.group_name},
    )
    await db.commit()
    await db.refresh(row)
    return BarrierGroupOut.model_validate(row)


@router.get(
    "/barriers",
    response_model=list[BarrierGroupOut],
    responses=_BARRIER_INVENTORY_RESPONSES,
    summary="List information barrier group assignments",
)
async def list_barrier_groups(
    response: Response,
    namespace: str = Query(..., description="Target namespace"),
    limit: int = Query(default=100, ge=1, le=500),
    after_agent_id: Optional[str] = Query(default=None, max_length=255),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[BarrierGroupOut]:
    base_condition = AgentBarrierGroup.namespace == namespace
    total = int(
        (
            await db.execute(
                select(func.count(AgentBarrierGroup.agent_id)).where(base_condition)
            )
        ).scalar_one()
        or 0
    )
    stmt = select(AgentBarrierGroup).where(base_condition)
    if after_agent_id is not None:
        stmt = stmt.where(AgentBarrierGroup.agent_id > after_agent_id)
    result = await db.execute(
        stmt.order_by(AgentBarrierGroup.agent_id.asc()).limit(limit + 1)
    )
    fetched = result.scalars().all()
    has_more = len(fetched) > limit
    rows = fetched[:limit]
    _set_barrier_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(rows),
        has_more=has_more,
        cursor_supplied=after_agent_id is not None,
        next_agent_id=rows[-1].agent_id if has_more and rows else None,
    )
    return [BarrierGroupOut.model_validate(r) for r in rows]


@router.delete(
    "/barriers/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an agent from its barrier group (grants full-namespace access)",
)
async def remove_barrier_group(
    agent_id: str,
    namespace: str = Query(..., description="Target namespace"),
    expected_group_name: str = Query(..., min_length=1, max_length=255),
    _retry_contract: None = Depends(reject_non_replayable_idempotency_key),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _lock_barrier_assignment_boundary(db, namespace, agent_id)
    await _acquire_pg_advisory_lock(db, namespace, agent_id)
    row = (
        await db.execute(
            select(AgentBarrierGroup)
            .where(
                AgentBarrierGroup.namespace == namespace,
                AgentBarrierGroup.agent_id == agent_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Barrier group assignment not found")
    if row.group_name != expected_group_name:
        raise HTTPException(status_code=409, detail="Barrier assignment version conflict")
    await db.delete(row)
    await chain_log(
        db, namespace=namespace, agent_id=_ADMIN_AGENT,
        op="admin.barrier_remove",
        payload={"agent_id": agent_id},
    )
    await db.commit()
    return Response(status_code=204)


# ── Retention & Compliance Policy ───────────────────────────────────────────

@router.get(
    "/retention/{namespace}",
    response_model=RetentionPolicyOut,
    summary="Get the retention policy for a namespace",
)
async def get_retention(
    namespace: str,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> RetentionPolicyOut:
    """
    Return the content TTL and audit retention settings for a namespace.

    Virtual default policy (a GET does not create a row):
      - content_ttl_days: None (retain forever)
      - audit_retention_days: 1825 (5 years — CFTC swap dealer minimum)
      - legal_hold: False
    """
    return await get_retention_policy(db, namespace)


@router.put(
    "/retention/{namespace}",
    response_model=RetentionPolicyOut,
    summary="Set or update the retention policy for a namespace",
)
async def set_retention(
    namespace: str,
    body: RetentionPolicyIn,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> RetentionPolicyOut:
    """
    Upsert the retention policy.

    Setting `legal_hold: true` blocks any automated pruning on this namespace
    until the hold is explicitly lifted (litigation hold pattern).

    Setting `content_ttl_days` to a value means memories older than N days will
    have their content erased by the next prune run.  The content_hash audit
    record is preserved (SEC 17a-4 / CFTC compliance).
    """
    try:
        return await set_retention_policy(db, namespace, body)
    except MutationVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/retention/{namespace}/prune",
    response_model=RetentionPruneResult,
    summary="Immediately prune expired memory content for a namespace",
)
async def run_prune(
    namespace: str,
    batch_limit: int = Query(default=500, ge=1, le=1000),
    _retry_contract: None = Depends(reject_non_replayable_idempotency_key),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> RetentionPruneResult:
    """
    Erase the encrypted content of memories whose age exceeds content_ttl_days.

    Returns 409 if the namespace is under legal hold.
    Returns 0 memories_pruned if content_ttl_days is not set.

    Each pruned memory writes a `retention_prune` event to the immutable audit
    log so regulators can confirm the content was destroyed per policy.
    """
    return await prune_expired_content(db, namespace, batch_limit=batch_limit)


# ── Audit chain verification ─────────────────────────────────────────────────

@router.get(
    "/audit/verify",
    response_model=AuditChainVerifyResult,
    summary="Verify the SEC 17a-4 tamper-evidence hash chain for a namespace",
)
async def verify_audit_chain(
    namespace: str = Query(..., description="Namespace to verify"),
    limit: int = Query(default=50_000, ge=1, le=50_000, description="Max rows to inspect"),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AuditChainVerifyResult:
    """
    Walk the event_log hash chain for *namespace* and report any tampering.

    For each row the verifier recomputes SHA-256(prev_hash || row fields) and
    compares it to the stored row_hash.  A mismatch means the row was modified
    after it was written.  An orphaned prev_hash means a row was deleted from
    the middle of the chain.

    Returns `{"status": "ok"}` when the chain is intact.
    Returns `{"status": "tampered", "violations": [...]}` with details
    identifying every broken link — suitable for regulatory examination.

    Missing, malformed, or unknown-version hashes are reported as integrity
    violations; unverifiable history is never converted into an ``ok`` result.
    """
    try:
        report = await verify_chain(
            db,
            namespace=namespace,
            limit=limit,
            max_response_bytes=get_settings().audit_export_page_bytes_limit,
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
    return AuditChainVerifyResult(**report)


# ── Audit log export ─────────────────────────────────────────────────────────

@router.get(
    "/audit/export",
    response_model=AuditExportResult,
    summary="Export one exact-count, keyset-paginated audit-log page",
)
async def export_audit(
    namespace: str = Query(..., description="Namespace to export"),
    from_: Optional[datetime] = Query(
        default=None, alias="from",
        description="Lower bound on created_at (inclusive).  ISO-8601 UTC.",
    ),
    to: Optional[datetime] = Query(
        default=None,
        description="Upper bound on created_at (inclusive).  ISO-8601 UTC.",
    ),
    limit: int = Query(
        default=10_000, ge=1, le=10_000,
        description="Bounded rows returned per page.",
    ),
    after_chain_position: Optional[int] = Query(
        default=None,
        ge=0,
        description="Exclusive keyset cursor returned by the previous page.",
    ),
    through_chain_position: Optional[int] = Query(
        default=None,
        ge=0,
        description=(
            "Fixed snapshot watermark returned as snapshot_max_chain_position; "
            "retain it on every continuation request."
        ),
    ),
    verify: bool = Query(
        default=False,
        description=(
            "When true, also runs the hash-chain verifier and includes chain_status "
            "and chain_violations in the response.  Adds one extra table scan."
        ),
    ),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AuditExportResult:
    """
    Export event_log rows for *namespace* ordered chronologically.

    Designed for regulatory examiners who need a bounded page of the immutable
    audit trail plus exact cardinality and explicit completeness.

    **Pagination:** The default and maximum limit is 10 000 rows. Continue with
    `next_chain_position`. ``has_more`` describes the continuation after this
    cursor; ``complete`` is true only when the uncursored response contains the
    entire filtered collection. Retain ``snapshot_max_chain_position`` as
    ``through_chain_position`` across every continuation request.

    **Chain verification:** Pass `verify=true` to include a tamper-evidence
    report alongside the export data. Verification starts at the namespace
    genesis and is independently row- and byte-bounded; ``partial`` means the
    caller must request a smaller standalone verification window or use an
    offline examiner workflow.

    **Output fields per event:**
    - `id`, `namespace`, `agent_id`, `op` — who did what
    - `memory_id`, `content_hash` — which memory row was affected
    - `payload` — operation-specific context (e.g. superseded_by, query_hash)
    - `created_at` — when the event was ingested (UTC)
    - `prev_hash`, `row_hash` — hash-chain links for independent verification
    """
    try:
        data = await export_audit_log(
            db,
            namespace=namespace,
            from_dt=from_,
            to_dt=to,
            limit=limit,
            include_chain_status=verify,
            after_chain_position=after_chain_position,
            through_chain_position=through_chain_position,
            max_page_bytes=get_settings().audit_export_page_bytes_limit,
        )
    except AuditCursorInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
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
    return AuditExportResult(**data)


# ── Stripe usage metering ────────────────────────────────────────────────────

@router.get(
    "/billing/{namespace}",
    response_model=NamespaceBillingOut,
    summary="Get the Stripe customer ID assigned to a namespace",
)
async def get_billing(
    namespace: str,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> NamespaceBillingOut:
    """
    Return the Stripe customer ID wired to *namespace*.

    When stripe_customer_id is null the namespace is not metered — authoritative
    decisions, protected actions, and compatibility memory activity are not
    reported to Stripe regardless of STRIPE_API_KEY.
    """
    pol = await db.get(NamespacePolicy, namespace)
    return NamespaceBillingOut(
        namespace=namespace,
        stripe_customer_id=pol.stripe_customer_id if pol else None,
        updated_at=pol.updated_at if pol else None,
    )


@router.put(
    "/billing/{namespace}",
    response_model=NamespaceBillingOut,
    summary="Set or clear the Stripe customer ID for a namespace",
)
async def set_billing(
    namespace: str,
    body: NamespaceBillingIn,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> NamespaceBillingOut:
    """
    Assign a Stripe Customer ID to *namespace*.

    After this call authoritative decision creation, successful Gate permit
    consumption, and compatibility memory writes/recalls in the namespace are
    metered via Stripe Meters API. Set stripe_customer_id to null to stop billing.

    The customer ID is read transactionally for each new usage fact. Existing
    outbox rows retain their original customer snapshot.
    """
    from ..metering import invalidate_customer_cache

    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"lians:namespace-governance:{namespace}"},
        )
    pol = (
        await db.execute(
            select(NamespacePolicy)
            .where(NamespacePolicy.namespace == namespace)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if pol is None:
        if body.expected_updated_at is not None:
            raise HTTPException(status_code=409, detail="Resource version conflict")
        pol = NamespacePolicy(namespace=namespace)
        db.add(pol)
    else:
        if body.expected_updated_at is None:
            raise HTTPException(status_code=409, detail="Resource version conflict")
        try:
            assert_expected_updated_at(pol.updated_at, body.expected_updated_at)
        except MutationVersionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    pol.stripe_customer_id = body.stripe_customer_id
    pol.updated_at = datetime.now(timezone.utc)
    await chain_log(
        db, namespace=namespace, agent_id=_ADMIN_AGENT,
        op="admin.billing_set",
        payload={"stripe_customer_id": body.stripe_customer_id},
    )
    await db.commit()
    await db.refresh(pol)
    invalidate_customer_cache(namespace)
    return NamespaceBillingOut(
        namespace=namespace,
        stripe_customer_id=pol.stripe_customer_id,
        updated_at=pol.updated_at,
    )


@router.get(
    "/billing-metering/status",
    response_model=MeteringInventoryOut,
    summary="Inspect the durable Stripe metering worker and backlog",
)
async def get_metering_status(
    namespace: str | None = Query(default=None, min_length=1, max_length=255),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> MeteringInventoryOut:
    from ..metering import metering_inventory

    return MeteringInventoryOut(**(await metering_inventory(db, namespace=namespace)))


@router.get(
    "/billing-metering/events",
    response_model=list[MeteringEventOut],
    responses=_METERING_EVENT_LIST_RESPONSES,
    summary="List secret-free durable metering delivery projections",
)
async def get_metering_events(
    response: Response,
    delivery_status: MeteringStatus | None = Query(default=None, alias="status"),
    namespace: str | None = Query(default=None, min_length=1, max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    before_updated_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MeteringEventOut]:
    from ..metering import list_metering_events_page

    if (before_updated_at is None) != (before_id is None):
        raise HTTPException(
            status_code=422,
            detail="before_updated_at and before_id must be supplied together",
        )
    total, fetched = await list_metering_events_page(
        db,
        status=delivery_status,
        namespace=namespace,
        limit=limit,
        before_updated_at=before_updated_at,
        before_id=before_id,
    )
    has_more = len(fetched) > limit
    rows = fetched[:limit]
    next_row = rows[-1] if has_more and rows else None
    _set_metering_event_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(rows),
        has_more=has_more,
        cursor_supplied=before_updated_at is not None,
        next_updated_at=next_row.updated_at if next_row is not None else None,
        next_id=next_row.id if next_row is not None else None,
    )
    return [MeteringEventOut.model_validate(row) for row in rows]


@router.post(
    "/billing-metering/events/{event_id}/replay",
    response_model=MeteringEventOut,
    summary="Replay one reconciled durable metering dead letter",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def replay_metering_event(
    event_id: UUID,
    body: MeteringReplayRequest,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> MeteringEventOut:
    from ..metering import MeteringConflictError, replay_dead_letter_event

    try:
        row = await replay_dead_letter_event(
            db,
            event_id,
            reconciliation=body.reconciliation,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MeteringConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await chain_log(
        db,
        namespace=row.namespace,
        agent_id=_ADMIN_AGENT,
        op="admin.billing_meter_replay",
        payload={
            "metering_event_id": str(row.id),
            "event_name": row.event_name,
            "provider_identifier": row.provider_identifier,
            "attempt_count": row.attempt_count,
            "attempt_limit": row.attempt_limit,
            "replay_count": row.replay_count,
            "reconciliation": body.reconciliation,
            "reconciliation_reference_hash": _hash(body.reconciliation_reference),
        },
    )
    await db.commit()
    await db.refresh(row)
    return MeteringEventOut.model_validate(row)


@router.get("/usage/{namespace}")
async def get_usage_summary(
    namespace: str,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Current-calendar-month write/recall counts for a namespace — powers the
    console's usage meters. Reads the append-only event_log (the same table the
    audit endpoints read), so no RLS context is required.
    """
    from sqlalchemy import func

    from ..models import EventLog

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(EventLog.op, func.count())
        .where(EventLog.namespace == namespace, EventLog.created_at >= month_start)
        .group_by(EventLog.op)
        .order_by(EventLog.op)
        .limit(_MAX_USAGE_OPERATION_GROUPS + 1)
    )
    rows = list(result.all())
    if len(rows) > _MAX_USAGE_OPERATION_GROUPS:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "usage_operation_cardinality_exceeded",
                "message": "Usage operation cardinality exceeds the reporting capacity",
            },
        )
    counts = {op: int(n) for op, n in rows}
    return {
        "namespace": namespace,
        "period_start": month_start.isoformat(),
        "writes": counts.get("add", 0) + counts.get("supersede", 0),
        "recalls": counts.get("recall", 0),
        "by_op": counts,
    }
