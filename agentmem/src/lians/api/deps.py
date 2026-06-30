"""
FastAPI dependencies: API key auth, namespace resolution, DB session, RLS.
"""
from __future__ import annotations
import hashlib
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import ApiKey

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Named roles → scope sets (RBAC). A key's `role`, when set, is merged with any
# explicit `scopes`. "compliance" gets read + admin (audit verify/export/erase)
# but not write — it inspects and certifies, it does not author memories.
ROLE_SCOPES: dict[str, list[str]] = {
    "owner":      ["read", "write", "admin"],
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
    def __init__(self, namespace: str, scopes: list[str]):
        self.namespace = namespace
        self.scopes = scopes

    def require(self, scope: str):
        if scope not in self.scopes:
            raise HTTPException(status_code=403, detail=f"Scope '{scope}' required")


async def _set_rls_namespace(db: AsyncSession, namespace: str) -> None:
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
    try:
        await db.execute(
            text("SELECT set_config('app.current_namespace', :ns, true)"),
            {"ns": namespace},
        )
    except Exception:
        pass  # SQLite or pre-transaction context — application-layer isolation applies


async def get_auth(
    raw_key: Annotated[Optional[str], Security(_api_key_header)],
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

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

    # Enforce namespace isolation at the Postgres layer — any query that runs
    # on this session after this point can only see rows matching the namespace.
    await _set_rls_namespace(db, key_row.namespace)

    return AuthContext(
        namespace=key_row.namespace,
        scopes=_effective_scopes(key_row),
    )
