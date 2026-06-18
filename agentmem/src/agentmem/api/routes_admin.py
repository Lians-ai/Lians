"""
Admin API: provision, list, revoke, and rotate API keys.

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import ApiKey
from ..schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut

router = APIRouter(prefix="/v1/admin", tags=["admin"])

_admin_header = APIKeyHeader(name="X-Admin-Secret", auto_error=False)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _generate_key() -> str:
    return "agentmem_" + secrets.token_urlsafe(32)


async def _require_admin(
    secret: Annotated[Optional[str], Security(_admin_header)],
) -> None:
    if not secret or secret != get_settings().admin_secret:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Secret")


@router.post(
    "/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a new API key",
)
async def provision_key(
    body: ApiKeyCreate,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    raw = _generate_key()
    row = ApiKey(
        hashed_key=_hash(raw),
        namespace=body.namespace,
        label=body.label,
        scopes=body.scopes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return ApiKeyCreated(
        id=row.id,
        namespace=row.namespace,
        label=row.label,
        scopes=list(row.scopes),
        created_at=row.created_at,
        rotated_at=row.rotated_at,
        revoked_at=row.revoked_at,
        key=raw,
    )


@router.get(
    "/api-keys",
    response_model=list[ApiKeyOut],
    summary="List API keys, optionally filtered by namespace",
)
async def list_keys(
    namespace: Optional[str] = Query(default=None),
    include_revoked: bool = Query(default=False),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyOut]:
    stmt = select(ApiKey)
    if namespace:
        stmt = stmt.where(ApiKey.namespace == namespace)
    if not include_revoked:
        stmt = stmt.where(ApiKey.revoked_at.is_(None))
    result = await db.execute(stmt.order_by(ApiKey.created_at.desc()))
    rows = result.scalars().all()
    return [
        ApiKeyOut(
            id=r.id,
            namespace=r.namespace,
            label=r.label,
            scopes=list(r.scopes),
            created_at=r.created_at,
            rotated_at=r.rotated_at,
            revoked_at=r.revoked_at,
        )
        for r in rows
    ]


@router.delete(
    "/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key immediately",
)
async def revoke_key(
    key_id: UUID,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    row = await db.get(ApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="API key already revoked")
    row.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/api-keys/{key_id}/rotate",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Rotate an API key — old key is revoked, new key is returned",
)
async def rotate_key(
    key_id: UUID,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    old = await db.get(ApiKey, key_id)
    if old is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if old.revoked_at is not None:
        raise HTTPException(status_code=409, detail="API key already revoked")

    now = datetime.now(timezone.utc)
    old.rotated_at = now
    old.revoked_at = now

    raw = _generate_key()
    new_row = ApiKey(
        hashed_key=_hash(raw),
        namespace=old.namespace,
        label=old.label,
        scopes=old.scopes,
    )
    db.add(new_row)
    await db.commit()
    await db.refresh(new_row)
    return ApiKeyCreated(
        id=new_row.id,
        namespace=new_row.namespace,
        label=new_row.label,
        scopes=list(new_row.scopes),
        created_at=new_row.created_at,
        rotated_at=new_row.rotated_at,
        revoked_at=new_row.revoked_at,
        key=raw,
    )
