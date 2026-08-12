"""Least-privilege API-key provisioning surface for the public website broker."""
from __future__ import annotations

import secrets
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db, set_current_barrier_group, set_current_namespace
from ..models import ApiKey
from ..schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut, ApiKeyScopesUpdate
from .routes_admin import (
    get_usage_summary as admin_get_usage_summary,
    list_keys as admin_list_keys,
    provision_key as admin_provision_key,
    revoke_key as admin_revoke_key,
    rotate_key as admin_rotate_key,
    update_key_scopes as admin_update_key_scopes,
)

router = APIRouter(prefix="/v1/provisioning", tags=["provisioning"])

_provisioning_header = APIKeyHeader(name="X-Provisioning-Secret", auto_error=False)
_namespace_header = APIKeyHeader(name="X-Lians-Namespace", auto_error=False)


async def _require_provisioner(
    secret: Annotated[Optional[str], Security(_provisioning_header)],
) -> None:
    expected = get_settings().provisioning_secret
    if not expected:
        raise HTTPException(status_code=503, detail="Provisioning API is not configured")
    if not secret or not secrets.compare_digest(secret, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Provisioning-Secret")
    set_current_namespace("__admin__")
    set_current_barrier_group(None)


def _require_namespace(namespace: str) -> str:
    normalized = namespace.strip()
    if not normalized.startswith("ns_") or len(normalized) > 200:
        raise HTTPException(status_code=400, detail="A valid website namespace is required")
    return normalized


def _match_namespace(header: Optional[str], expected: str) -> str:
    namespace = _require_namespace(header or "")
    if not secrets.compare_digest(namespace, expected):
        raise HTTPException(status_code=404, detail="API key not found")
    return namespace


async def _owned_key(db: AsyncSession, key_id: UUID, namespace: str) -> ApiKey:
    row = await db.get(ApiKey, key_id)
    if row is None or not secrets.compare_digest(row.namespace, namespace):
        raise HTTPException(status_code=404, detail="API key not found")
    return row


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def provision_key(
    body: ApiKeyCreate,
    namespace: Annotated[Optional[str], Security(_namespace_header)],
    _: None = Depends(_require_provisioner),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    _match_namespace(namespace, _require_namespace(body.namespace))
    return await admin_provision_key(body, None, db)


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_keys(
    namespace: str,
    namespace_header: Annotated[Optional[str], Security(_namespace_header)],
    _: None = Depends(_require_provisioner),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyOut]:
    normalized = _require_namespace(namespace)
    _match_namespace(namespace_header, normalized)
    return await admin_list_keys(normalized, False, None, db)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: UUID,
    namespace: Annotated[Optional[str], Security(_namespace_header)],
    _: None = Depends(_require_provisioner),
    db: AsyncSession = Depends(get_db),
):
    normalized = _require_namespace(namespace or "")
    await _owned_key(db, key_id, normalized)
    return await admin_revoke_key(key_id, None, db)


@router.patch("/api-keys/{key_id}", response_model=ApiKeyOut)
async def update_key_scopes(
    key_id: UUID,
    body: ApiKeyScopesUpdate,
    namespace: Annotated[Optional[str], Security(_namespace_header)],
    _: None = Depends(_require_provisioner),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyOut:
    normalized = _require_namespace(namespace or "")
    await _owned_key(db, key_id, normalized)
    return await admin_update_key_scopes(key_id, body, None, db)


@router.post("/api-keys/{key_id}/rotate", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def rotate_key(
    key_id: UUID,
    namespace: Annotated[Optional[str], Security(_namespace_header)],
    _: None = Depends(_require_provisioner),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    normalized = _require_namespace(namespace or "")
    await _owned_key(db, key_id, normalized)
    return await admin_rotate_key(key_id, None, db)


@router.get("/usage/{namespace}")
async def get_usage_summary(
    namespace: str,
    namespace_header: Annotated[Optional[str], Security(_namespace_header)],
    _: None = Depends(_require_provisioner),
    db: AsyncSession = Depends(get_db),
):
    normalized = _require_namespace(namespace)
    _match_namespace(namespace_header, normalized)
    return await admin_get_usage_summary(normalized, None, db)
