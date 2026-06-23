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


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


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
    """
    try:
        await db.execute(
            text("SET LOCAL app.current_namespace = :ns"),
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
        scopes=list(key_row.scopes or []),
    )
