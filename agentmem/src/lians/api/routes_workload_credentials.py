"""Tenant-scoped workload credential lifecycle for verified OIDC administrators.

This router is intentionally separate from ``/v1/admin/api-keys``. The latter
is the deployment break-glass surface protected by ``X-Admin-Secret``; these
routes derive tenant, actor, and barrier exclusively from a verified bearer.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log
from ..authz import ROLE_SCOPES, effective_scopes
from ..config import get_settings
from ..db import get_db
from ..models import ApiKey
from ..mutation_safety import reject_non_replayable_idempotency_key
from ..workload_credential_schemas import (
    WorkloadCredentialCreate,
    WorkloadCredentialCreated,
    WorkloadCredentialOut,
    WorkloadCredentialRotate,
)
from .deps import AuthContext, get_auth

router = APIRouter(
    prefix="/v1/identity/workload-credentials",
    tags=["identity", "workload credentials"],
)

_PROVISIONING_SOURCE = "tenant_oidc"
_ROLE_DELEGATION: dict[str, frozenset[str]] = {
    "owner": frozenset(ROLE_SCOPES),
    "analyst": frozenset({"analyst", "readonly"}),
    "compliance": frozenset({"compliance", "readonly"}),
    "readonly": frozenset({"readonly"}),
}
_RESERVED_SCOPES = {
    "*",
    "__admin__",
    "breakglass",
    "global_admin",
    "platform_admin",
}

_WORKLOAD_CREDENTIAL_LIST_RESPONSES = {
    200: {
        "description": "Secret-free workload credential inventory page",
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
        },
    }
}


def _set_workload_credential_page_headers(
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
        response.headers["X-Lians-Next-Before-Created-At"] = (
            next_created_at.isoformat()
        )
        response.headers["X-Lians-Next-Before-Id"] = str(next_id)


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _generate_secret() -> str:
    return "lians_wk_" + secrets.token_urlsafe(32)


def _actor_reference(auth: AuthContext) -> str:
    # The binding UUID is stable and audit-useful without persisting a raw OIDC
    # subject, email address, access token, or other identity-provider claim.
    if not auth.credential_id:
        raise HTTPException(status_code=403, detail="OIDC identity binding is required")
    return f"identity_binding:{auth.credential_id}"


def _require_tenant_credential_admin(auth: AuthContext) -> str:
    if auth.auth_method != "oidc_bearer" or auth.principal_type != "human":
        raise HTTPException(
            status_code=403,
            detail="A human OIDC tenant administrator is required; API keys and workload tokens cannot manage credentials",
        )
    auth.require("admin")
    return _actor_reference(auth)


def _ttl_expiry(ttl_seconds: int) -> datetime:
    settings = get_settings()
    minimum = settings.workload_credential_min_ttl_seconds
    maximum = settings.workload_credential_max_ttl_seconds
    if not (60 <= minimum <= maximum <= 31_536_000):
        # Do not silently fall back to an unbounded or operator-unintended TTL.
        raise HTTPException(
            status_code=503,
            detail="Workload credential TTL policy is invalid; an operator must repair it",
        )
    if not minimum <= ttl_seconds <= maximum:
        raise HTTPException(
            status_code=422,
            detail=f"ttl_seconds must be between {minimum} and {maximum}",
        )
    return _now() + timedelta(seconds=ttl_seconds)


def _scope_is_reserved(scope: str) -> bool:
    normalized = scope.lower()
    return (
        normalized in _RESERVED_SCOPES
        or normalized.startswith(("breakglass:", "global:", "platform:"))
    )


def _authorized_barrier(auth: AuthContext, requested: str | None) -> str | None:
    if auth.barrier_group is None:
        return requested
    if requested is not None and requested != auth.barrier_group:
        raise HTTPException(
            status_code=403,
            detail="A barrier-scoped administrator may issue credentials only for the same barrier",
        )
    # Omitted/null never widens a scoped caller to an unbarriered credential.
    return auth.barrier_group


def _authorize_grants(
    auth: AuthContext,
    *,
    role: str | None,
    scopes: list[str],
    barrier_group: str | None,
) -> tuple[list[str], str | None]:
    if any(_scope_is_reserved(scope) for scope in scopes):
        raise HTTPException(
            status_code=403,
            detail="Break-glass, global-administrator, wildcard, and platform scopes cannot be delegated",
        )

    target_effective = set(effective_scopes(role, scopes))
    caller_effective = set(auth.scopes)
    if not target_effective or not target_effective.issubset(caller_effective):
        raise HTTPException(
            status_code=403,
            detail="Requested role and scopes must be a subset of the caller's effective grants",
        )

    # Named roles carry semantics beyond their scope expansion (for example,
    # role-bound Gate approvals). A scope-equivalent but roleless caller cannot
    # manufacture that identity assurance.
    if role is not None:
        delegated = _ROLE_DELEGATION.get(auth.role or "", frozenset())
        if role not in delegated:
            raise HTTPException(
                status_code=403,
                detail="The caller's verified role cannot delegate the requested named role",
            )

    return sorted(target_effective), _authorized_barrier(auth, barrier_group)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _status(row: ApiKey, now: datetime | None = None) -> str:
    if row.rotated_at is not None:
        return "rotated"
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at is not None and _as_utc(row.expires_at) <= (now or _now()):
        return "expired"
    return "active"


def _out(row: ApiKey, *, secret: str | None = None) -> WorkloadCredentialOut:
    payload = {
        "id": row.id,
        "namespace": row.namespace,
        "label": row.label,
        "scopes": list(row.scopes or []),
        "effective_scopes": effective_scopes(row.role, row.scopes),
        "role": row.role,
        "barrier_group": row.barrier_group,
        "provisioning_source": row.provisioning_source,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
        "rotated_from_id": row.rotated_from_id,
        "rotated_at": row.rotated_at,
        "revoked_at": row.revoked_at,
        "version": row.version,
        "status": _status(row),
    }
    if secret is not None:
        return WorkloadCredentialCreated(**payload, secret=secret)
    return WorkloadCredentialOut(**payload)


async def _tenant_row_for_update(
    db: AsyncSession,
    auth: AuthContext,
    credential_id: UUID,
) -> ApiKey:
    result = await db.execute(
        select(ApiKey)
        .where(
            and_(
                ApiKey.id == credential_id,
                ApiKey.namespace == auth.namespace,
                ApiKey.provisioning_source == _PROVISIONING_SOURCE,
            )
        )
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Workload credential not found")
    if auth.barrier_group is not None and row.barrier_group != auth.barrier_group:
        # Use the same response as absence to avoid cross-barrier enumeration.
        raise HTTPException(status_code=404, detail="Workload credential not found")
    return row


@router.post(
    "",
    response_model=WorkloadCredentialCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create an expiring credential for this OIDC principal's tenant",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def create_workload_credential(
    body: WorkloadCredentialCreate,
    response: Response,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkloadCredentialCreated:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    actor = _require_tenant_credential_admin(auth)
    _, barrier_group = _authorize_grants(
        auth,
        role=body.role,
        scopes=body.scopes,
        barrier_group=body.barrier_group,
    )
    expires_at = _ttl_expiry(body.ttl_seconds)
    secret = _generate_secret()
    row = ApiKey(
        hashed_key=_hash(secret),
        namespace=auth.namespace,
        label=body.label,
        scopes=list(body.scopes),
        role=body.role,
        barrier_group=barrier_group,
        provisioning_source=_PROVISIONING_SOURCE,
        created_by=actor,
        expires_at=expires_at,
        version=1,
    )
    db.add(row)
    await db.flush()
    await chain_log(
        db,
        namespace=auth.namespace,
        agent_id=actor,
        op="identity.workload_credential_create",
        payload={
            "credential_id": str(row.id),
            "label_sha256": _hash(body.label) if body.label else None,
            "role": body.role,
            "scopes": list(body.scopes),
            "effective_scopes": effective_scopes(body.role, body.scopes),
            "barrier_group": barrier_group,
            "expires_at": expires_at.isoformat(),
            "created_by": actor,
        },
    )
    await db.commit()
    await db.refresh(row)
    return _out(row, secret=secret)  # type: ignore[return-value]


@router.get(
    "",
    response_model=list[WorkloadCredentialOut],
    responses=_WORKLOAD_CREDENTIAL_LIST_RESPONSES,
    summary="List tenant-managed workload credentials in this OIDC principal's boundary",
)
async def list_workload_credentials(
    response: Response,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    include_revoked: bool = Query(default=False),
    include_expired: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    before_created_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
) -> list[WorkloadCredentialOut]:
    _require_tenant_credential_admin(auth)
    if (before_created_at is None) != (before_id is None):
        raise HTTPException(
            status_code=422,
            detail="before_created_at and before_id must be supplied together",
        )
    now = _now()
    conditions = [
        ApiKey.namespace == auth.namespace,
        ApiKey.provisioning_source == _PROVISIONING_SOURCE,
    ]
    if auth.barrier_group is not None:
        conditions.append(ApiKey.barrier_group == auth.barrier_group)
    if not include_revoked:
        conditions.append(ApiKey.revoked_at.is_(None))
    if not include_expired:
        conditions.append(or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now))
    total = int(
        (
            await db.execute(select(func.count(ApiKey.id)).where(and_(*conditions)))
        ).scalar_one()
        or 0
    )
    page_conditions = list(conditions)
    if before_created_at is not None and before_id is not None:
        page_conditions.append(
            or_(
                ApiKey.created_at < before_created_at,
                and_(ApiKey.created_at == before_created_at, ApiKey.id < before_id),
            )
        )
    fetched = list(
        (
            await db.execute(
                select(ApiKey)
                .where(and_(*page_conditions))
                .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
                .limit(limit + 1)
            )
        ).scalars()
    )
    has_more = len(fetched) > limit
    rows = fetched[:limit]
    next_row = rows[-1] if has_more and rows else None
    _set_workload_credential_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(rows),
        has_more=has_more,
        cursor_supplied=before_created_at is not None,
        next_created_at=next_row.created_at if next_row is not None else None,
        next_id=next_row.id if next_row is not None else None,
    )
    return [_out(row) for row in rows]


@router.get(
    "/{credential_id}",
    response_model=WorkloadCredentialOut,
    summary="Get one tenant-managed workload credential",
)
async def get_workload_credential(
    credential_id: UUID,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkloadCredentialOut:
    _require_tenant_credential_admin(auth)
    result = await db.execute(
        select(ApiKey).where(
            and_(
                ApiKey.id == credential_id,
                ApiKey.namespace == auth.namespace,
                ApiKey.provisioning_source == _PROVISIONING_SOURCE,
            )
        )
    )
    row = result.scalar_one_or_none()
    if row is None or (
        auth.barrier_group is not None and row.barrier_group != auth.barrier_group
    ):
        raise HTTPException(status_code=404, detail="Workload credential not found")
    return _out(row)


@router.post(
    "/{credential_id}/rotate",
    response_model=WorkloadCredentialCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Atomically replace an expiring tenant-managed workload credential",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def rotate_workload_credential(
    credential_id: UUID,
    body: WorkloadCredentialRotate,
    response: Response,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkloadCredentialCreated:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    actor = _require_tenant_credential_admin(auth)
    old = await _tenant_row_for_update(db, auth, credential_id)
    if old.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Workload credential is already inactive")
    if old.version != body.expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict; current version is {old.version}",
        )
    _authorize_grants(
        auth,
        role=old.role,
        scopes=list(old.scopes or []),
        barrier_group=old.barrier_group,
    )

    now = _now()
    expires_at = _ttl_expiry(body.ttl_seconds)
    old.rotated_at = now
    old.revoked_at = now
    old.version += 1

    secret = _generate_secret()
    successor = ApiKey(
        hashed_key=_hash(secret),
        namespace=old.namespace,
        label=old.label,
        scopes=list(old.scopes or []),
        role=old.role,
        barrier_group=old.barrier_group,
        provisioning_source=_PROVISIONING_SOURCE,
        created_by=actor,
        expires_at=expires_at,
        rotated_from_id=old.id,
        version=1,
    )
    db.add(successor)
    await db.flush()
    await chain_log(
        db,
        namespace=auth.namespace,
        agent_id=actor,
        op="identity.workload_credential_rotate",
        payload={
            "old_credential_id": str(old.id),
            "old_version": old.version,
            "new_credential_id": str(successor.id),
            "new_version": successor.version,
            "barrier_group": old.barrier_group,
            "expires_at": expires_at.isoformat(),
            "created_by": actor,
        },
    )
    await db.commit()
    await db.refresh(successor)
    return _out(successor, secret=secret)  # type: ignore[return-value]


@router.delete(
    "/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a tenant-managed workload credential immediately",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def revoke_workload_credential(
    credential_id: UUID,
    auth: Annotated[AuthContext, Depends(get_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    expected_version: int = Query(..., ge=1),
) -> Response:
    actor = _require_tenant_credential_admin(auth)
    row = await _tenant_row_for_update(db, auth, credential_id)
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Workload credential is already inactive")
    if row.version != expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict; current version is {row.version}",
        )
    row.revoked_at = _now()
    row.version += 1
    await chain_log(
        db,
        namespace=auth.namespace,
        agent_id=actor,
        op="identity.workload_credential_revoke",
        payload={
            "credential_id": str(row.id),
            "version": row.version,
            "barrier_group": row.barrier_group,
            "created_by": actor,
        },
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
