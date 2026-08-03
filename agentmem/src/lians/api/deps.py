"""
FastAPI dependencies: API key auth, namespace resolution, DB session, RLS.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, or_, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth_lookup import (
    ApiKeyAuthenticationRecord,
    AuthLookupInvariantError,
    lookup_api_key,
)
from ..authz import api_key_principal_ref, effective_scopes
from ..barrier_policy import is_reserved_barrier_group
from ..config import get_settings
from ..db import get_db, set_current_barrier_group, set_current_namespace
from ..identity_service import IdentityAuthenticationError, authenticate_bearer
from ..models import ApiKey
from ..principal_rate_limit import (
    PrincipalRateLimitBackendUnavailable,
    PrincipalRateLimitExceeded,
    enforce_principal_rate_limit,
)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_bearer_header = HTTPBearer(auto_error=False)

# Named roles → scope sets (RBAC). A key's `role`, when set, is merged with any
# explicit `scopes`. "compliance" gets read + admin (audit verify/export/erase)
# but not write — it inspects and certifies, it does not author memories.
def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _effective_scopes(key_row: ApiKey | ApiKeyAuthenticationRecord) -> list[str]:
    return effective_scopes(getattr(key_row, "role", None), key_row.scopes)


class AuthContext:
    def __init__(
        self,
        namespace: str,
        scopes: list[str],
        barrier_group: Optional[str] = None,
        *,
        principal_id: Optional[str] = None,
        principal_type: Optional[str] = None,
        role: Optional[str] = None,
        auth_method: str = "api_key",
        credential_id: Optional[str] = None,
    ):
        self.namespace = namespace
        self.scopes = scopes
        # Information barrier the calling key is scoped to (None = unbarriered).
        self.barrier_group = barrier_group
        self.principal_id = principal_id
        self.principal_type = principal_type
        # Named authorization role is always derived from the authenticated
        # credential/binding.  Callers must never assert this value in a body.
        self.role = role
        self.auth_method = auth_method
        self.credential_id = credential_id

    def require(self, scope: str):
        if scope not in self.scopes:
            raise HTTPException(status_code=403, detail=f"Scope '{scope}' required")

    def require_unbarriered(self) -> None:
        if self.barrier_group is not None:
            raise HTTPException(
                status_code=403,
                detail="An unbarriered compliance/owner key is required",
            )


async def _set_rls_context(
    db: AsyncSession,
    namespace: str,
    barrier_group: Optional[str],
) -> None:
    """
    Set the PostgreSQL session variable used by Row-Level Security policies.

    SET LOCAL is transaction-scoped — it resets when the transaction ends,
    so there is no risk of a connection-pool reuse leaking one tenant's
    namespace into another tenant's query.

    On SQLite (unit tests) the statement fails silently; RLS is enforced
    by application-level WHERE clauses in that environment.

    Uses ``set_config(..., is_local => true)`` rather than ``SET LOCAL ... = :ns``
    because PostgreSQL's ``SET`` does not accept bind parameters — under asyncpg a
    parameterized ``SET LOCAL`` raises a syntax error, which previously meant the
    namespace variable was never set and namespace RLS silently never engaged for
    non-superuser roles. ``set_config`` is the parameterizable equivalent.
    """
    # PostgreSQL errors intentionally propagate. Silently continuing here would
    # turn a broken RLS configuration into an authorization bypass.
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT set_config('app.current_namespace', :ns, true)"),
            {"ns": namespace},
        )
        await db.execute(
            text("SELECT set_config('agentmem.barrier_group', :bg, true)"),
            {"bg": barrier_group or ""},
        )
    else:
        pass  # SQLite or pre-transaction context — application-layer isolation applies


async def get_auth(
    request: Request,
    raw_key: Annotated[Optional[str], Security(_api_key_header)],
    bearer: Annotated[Optional[HTTPAuthorizationCredentials], Security(_bearer_header)],
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    if raw_key and bearer:
        raise HTTPException(
            status_code=400,
            detail="Supply exactly one credential: X-API-Key or Authorization Bearer",
        )
    if bearer:
        try:
            principal = await authenticate_bearer(db, bearer.credentials)
        except IdentityAuthenticationError as exc:
            raise HTTPException(
                status_code=401,
                detail="Invalid or unauthorized bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        await _enforce_authenticated_rate_limit(
            request,
            namespace=principal.namespace,
            principal_id=principal.principal_id,
        )
        await _set_rls_context(db, principal.namespace, principal.barrier_group)
        set_current_namespace(principal.namespace)
        set_current_barrier_group(principal.barrier_group)
        return AuthContext(
            namespace=principal.namespace,
            scopes=principal.scopes,
            barrier_group=principal.barrier_group,
            principal_id=principal.principal_id,
            principal_type=principal.principal_type,
            role=principal.role,
            auth_method="oidc_bearer",
            credential_id=principal.credential_id,
        )
    if not raw_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key or Authorization Bearer credential",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if len(raw_key) > 1_024:
        raise HTTPException(status_code=401, detail="Invalid, revoked, or expired API key")

    now = datetime.now(UTC)
    hashed = _hash_key(raw_key)
    try:
        key_row = await lookup_api_key(db, hashed_key=hashed, observed_at=now)
    except AuthLookupInvariantError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid, revoked, or expired API key",
        ) from exc

    if key_row is None:
        raise HTTPException(status_code=401, detail="Invalid, revoked, or expired API key")
    now = key_row.authenticated_at
    barrier_group = getattr(key_row, "barrier_group", None)
    if is_reserved_barrier_group(barrier_group):
        # Legacy provenance sentinels are deliberately visible only to an
        # unbarriered compliance/admin context, never as an assumable desk.
        raise HTTPException(status_code=401, detail="Invalid, revoked, or expired API key")

    scopes = _effective_scopes(key_row)
    if not scopes:
        raise HTTPException(status_code=401, detail="Invalid, revoked, or expired API key")
    stable_principal_id = api_key_principal_ref(key_row.id)
    await _enforce_authenticated_rate_limit(
        request,
        namespace=key_row.namespace,
        principal_id=stable_principal_id,
    )

    # The exact pre-authentication function has resolved the authoritative
    # tenant and barrier. Establish RLS before the first direct-table operation
    # (the optional last-use metadata update), not only before route handling.
    await _set_rls_context(db, key_row.namespace, barrier_group)
    set_current_namespace(key_row.namespace)
    set_current_barrier_group(barrier_group)

    # Approximate usage metadata is useful for credential hygiene, but updating
    # it on every request would make one hot credential a database lock hotspot.
    # The timestamp observed above avoids most writes; the conditional UPDATE is
    # the race-safe second guard when many workers simultaneously observe a stale
    # value. This metadata never increments the lifecycle version.
    interval_seconds = max(
        60,
        int(get_settings().workload_credential_last_used_write_interval_seconds),
    )
    threshold = now - timedelta(seconds=interval_seconds)
    last_used = getattr(key_row, "last_used_at", None)
    if last_used is None or (
        last_used.replace(tzinfo=UTC) if last_used.tzinfo is None else last_used
    ) <= threshold:
        updated = await db.execute(
            update(ApiKey)
            .where(
                and_(
                    ApiKey.id == key_row.id,
                    ApiKey.revoked_at.is_(None),
                    or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
                    or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at <= threshold),
                )
            )
            .values(last_used_at=now)
        )
        if updated.rowcount:
            # Authentication has not run any application operation yet. Commit
            # only this independent metadata update; the task-local context
            # re-establishes RLS on the next transaction.
            await db.commit()

    workload_credential = key_row.provisioning_source == "tenant_oidc"
    return AuthContext(
        namespace=key_row.namespace,
        scopes=scopes,
        barrier_group=barrier_group,
        principal_id=stable_principal_id,
        principal_type="workload" if workload_credential else "api_key",
        role=getattr(key_row, "role", None),
        auth_method="api_key",
        credential_id=str(key_row.id),
    )


async def _enforce_authenticated_rate_limit(
    request: Request,
    *,
    namespace: str,
    principal_id: str,
) -> None:
    try:
        limit, remaining = await enforce_principal_rate_limit(
            namespace,
            principal_id,
            admin=request.url.path.startswith("/v1/admin/"),
        )
    except PrincipalRateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({exc.limit} requests/minute).",
            headers={
                "Retry-After": "60",
                "X-RateLimit-Limit": str(exc.limit),
                "X-RateLimit-Remaining": "0",
            },
        ) from exc
    except PrincipalRateLimitBackendUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Rate-limit backend unavailable; request denied by policy",
            headers={"Retry-After": "5"},
        ) from exc
    request.state.principal_rate_limit = {
        "limit": limit,
        "remaining": remaining,
    }
