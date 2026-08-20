"""
FastAPI dependencies: API key auth, namespace resolution, DB session, RLS.
"""
from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db, set_current_barrier_group, set_current_namespace
from ..models import ApiKey

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_bearer_header = HTTPBearer(auto_error=False)

# Named roles → scope sets (RBAC). A key's `role`, when set, is merged with any
# explicit `scopes`. "compliance" gets read + admin (audit verify/export/erase)
# but not write - it inspects and certifies, it does not author memories.
ROLE_SCOPES: dict[str, list[str]] = {
    "owner":      ["read", "write", "sync", "admin"],
    "analyst":    ["read", "write"],
    "compliance": ["read", "admin"],
    "readonly":   ["read"],
}


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _effective_scopes(key_row: ApiKey) -> list[str]:
    scopes = set(key_row.scopes or [])
    role = getattr(key_row, "role", None)
    if role:
        scopes.update(ROLE_SCOPES.get(role, []))
    return sorted(scopes)


class AuthContext:
    def __init__(self, namespace: str, scopes: list[str], barrier_group: str | None = None):
        self.namespace = namespace
        self.scopes = scopes
        # Information barrier the calling key is scoped to (None = unbarriered).
        self.barrier_group = barrier_group

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
    barrier_group: str | None,
) -> None:
    """
    Set the PostgreSQL session variable used by Row-Level Security policies.

    SET LOCAL is transaction-scoped - it resets when the transaction ends,
    so there is no risk of a connection-pool reuse leaking one tenant's
    namespace into another tenant's query.

    On SQLite (unit tests) the statement fails silently; RLS is enforced
    by application-level WHERE clauses in that environment.

    Uses ``set_config(..., is_local => true)`` rather than ``SET LOCAL ... = :ns``
    because PostgreSQL's ``SET`` does not accept bind parameters - under asyncpg a
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
        pass  # SQLite or pre-transaction context - application-layer isolation applies


async def _authenticate_api_key(raw_key: str, db: AsyncSession) -> AuthContext:
    hashed = _hash_key(raw_key)
    stmt = select(ApiKey).where(
        and_(
            ApiKey.hashed_key == hashed,
            ApiKey.revoked_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    key_row = result.scalar_one_or_none()

    if key_row is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    # Enforce namespace isolation at the Postgres layer - any query that runs
    # on this session after this point can only see rows matching the namespace.
    # _set_rls_namespace covers the current (already-open) transaction;
    # set_current_namespace lets the db "begin" listener re-apply it to any
    # later transaction autobegun after a mid-request commit().
    barrier_group = getattr(key_row, "barrier_group", None)
    await _set_rls_context(db, key_row.namespace, barrier_group)
    set_current_namespace(key_row.namespace)
    set_current_barrier_group(barrier_group)

    return AuthContext(
        namespace=key_row.namespace,
        scopes=_effective_scopes(key_row),
        barrier_group=barrier_group,
    )


async def get_auth(
    raw_key: Annotated[str | None, Security(_api_key_header)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthContext:
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    return await _authenticate_api_key(raw_key, db)


async def get_sync_auth(
    raw_key: Annotated[str | None, Security(_api_key_header)],
    bearer: Annotated[
        HTTPAuthorizationCredentials | None, Security(_bearer_header)
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthContext:
    """Accept developer API keys or consumer OAuth, never both ambiguously."""

    if raw_key and bearer is not None:
        raise HTTPException(status_code=400, detail="Use one Lians credential type per request")
    if raw_key:
        return await _authenticate_api_key(raw_key, db)
    if bearer is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to Lians or provide an X-API-Key",
            headers={"WWW-Authenticate": 'Bearer scope="memory:sync"'},
        )

    from ..cloud_sync_oauth import (
        SYNC_OAUTH_SCOPE,
        get_cloud_sync_oauth_runtime,
        principal_from_sync_access_token,
    )
    from ..config import get_settings

    runtime = get_cloud_sync_oauth_runtime()
    if runtime is None:
        raise HTTPException(
            status_code=401,
            detail="Consumer Lians sign-in is not enabled",
            headers={"WWW-Authenticate": f'Bearer scope="{SYNC_OAUTH_SCOPE}"'},
        )
    token = await runtime.verifier.verify_token(bearer.credentials)
    if token is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired Lians sign-in",
            headers={"WWW-Authenticate": f'Bearer scope="{SYNC_OAUTH_SCOPE}"'},
        )
    try:
        principal = principal_from_sync_access_token(token, get_settings().api_secret_seed)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Relink your Lians account",
            headers={"WWW-Authenticate": f'Bearer scope="{SYNC_OAUTH_SCOPE}"'},
        ) from None

    await _set_rls_context(db, principal.namespace, None)
    set_current_namespace(principal.namespace)
    set_current_barrier_group(None)
    scopes = ["sync"] if SYNC_OAUTH_SCOPE in token.scopes else []
    return AuthContext(namespace=principal.namespace, scopes=scopes, barrier_group=None)
