"""Administration and introspection APIs for native OIDC identity federation."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log
from ..config import get_settings
from ..db import get_db
from ..identity_models import IdentityBinding, TrustedIdentityProvider
from ..identity_schemas import (
    IdentityBindingCreate,
    IdentityBindingOut,
    IdentityBindingPatch,
    IdentityProviderCreate,
    IdentityProviderOut,
    IdentityProviderPatch,
    IdentityProviderProbe,
    PrincipalOut,
)
from ..identity_service import (
    IdentityProviderFetchError,
    clear_jwks_cache,
    probe_provider,
)
from ..mutation_safety import reject_non_replayable_idempotency_key
from .deps import AuthContext, get_auth
from .routes_admin import (
    _CREATED_AT_INVENTORY_RESPONSES,
    _require_admin,
    _set_created_at_page_headers,
)

router = APIRouter(prefix="/v1/identity", tags=["identity"])
admin_router = APIRouter(prefix="/v1/admin/identity", tags=["admin", "identity"])
_PLATFORM_AUDIT_NAMESPACE = "__platform__"
_ADMIN_AGENT = "__admin__"


def _production() -> bool:
    return get_settings().deployment_environment.strip().lower() in {"prod", "production"}


def _subject_fingerprint(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()


async def _provider_for_update(db: AsyncSession, provider_id: UUID) -> TrustedIdentityProvider:
    result = await db.execute(
        select(TrustedIdentityProvider)
        .where(TrustedIdentityProvider.id == provider_id)
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Identity provider not found")
    return row


async def _binding_for_update(db: AsyncSession, binding_id: UUID) -> IdentityBinding:
    result = await db.execute(
        select(IdentityBinding).where(IdentityBinding.id == binding_id).with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Identity binding not found")
    return row


@admin_router.post(
    "/providers",
    response_model=IdentityProviderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Trust an OIDC issuer",
)
async def create_provider(
    body: IdentityProviderCreate,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> IdentityProviderOut:
    if _production() and body.allow_insecure_http:
        raise HTTPException(
            status_code=422,
            detail="Insecure HTTP JWKS endpoints are forbidden in production",
        )
    row = TrustedIdentityProvider(**body.model_dump())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Issuer is already configured") from exc
    await chain_log(
        db,
        namespace=_PLATFORM_AUDIT_NAMESPACE,
        agent_id=_ADMIN_AGENT,
        op="identity.provider_create",
        payload={
            "provider_id": str(row.id),
            "name": row.name,
            "issuer": row.issuer,
            "allowed_algorithms": list(row.allowed_algorithms),
        },
    )
    await db.commit()
    await db.refresh(row)
    return IdentityProviderOut.model_validate(row)


@admin_router.get(
    "/providers",
    response_model=list[IdentityProviderOut],
    responses=_CREATED_AT_INVENTORY_RESPONSES,
    summary="List trusted OIDC issuers",
)
async def list_providers(
    response: Response,
    include_revoked: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    before_created_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[IdentityProviderOut]:
    conditions = []
    if not include_revoked:
        conditions.append(TrustedIdentityProvider.revoked_at.is_(None))
    if (before_created_at is None) != (before_id is None):
        raise HTTPException(422, "before_created_at and before_id must be supplied together")
    total_statement = select(func.count(TrustedIdentityProvider.id))
    if conditions:
        total_statement = total_statement.where(and_(*conditions))
    total = int((await db.execute(total_statement)).scalar_one() or 0)
    page_conditions = list(conditions)
    if before_created_at is not None and before_id is not None:
        page_conditions.append(
            or_(
                TrustedIdentityProvider.created_at < before_created_at,
                and_(
                    TrustedIdentityProvider.created_at == before_created_at,
                    TrustedIdentityProvider.id < before_id,
                ),
            )
        )
    stmt = select(TrustedIdentityProvider)
    if page_conditions:
        stmt = stmt.where(and_(*page_conditions))
    fetched = list(
        (
            await db.execute(
                stmt.order_by(
                    TrustedIdentityProvider.created_at.desc(),
                    TrustedIdentityProvider.id.desc(),
                ).limit(limit + 1)
            )
        ).scalars()
    )
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
    return [IdentityProviderOut.model_validate(row) for row in rows]


@admin_router.get(
    "/providers/{provider_id}",
    response_model=IdentityProviderOut,
    summary="Get a trusted OIDC issuer",
)
async def get_provider(
    provider_id: UUID,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> IdentityProviderOut:
    row = await db.get(TrustedIdentityProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Identity provider not found")
    return IdentityProviderOut.model_validate(row)


@admin_router.patch(
    "/providers/{provider_id}",
    response_model=IdentityProviderOut,
    summary="Update an OIDC verification policy with optimistic concurrency",
)
async def update_provider(
    provider_id: UUID,
    body: IdentityProviderPatch,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> IdentityProviderOut:
    row = await _provider_for_update(db, provider_id)
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Identity provider is revoked")
    if row.version != body.expected_version:
        raise HTTPException(status_code=409, detail=f"Version conflict; current version is {row.version}")

    changes = body.model_dump(exclude_unset=True)
    changes.pop("expected_version", None)
    candidate = {
        "name": row.name,
        "issuer": row.issuer,
        "jwks_uri": row.jwks_uri,
        "audiences": list(row.audiences),
        "allowed_algorithms": list(row.allowed_algorithms),
        "required_claims": list(row.required_claims),
        "required_typ": row.required_typ,
        "clock_skew_seconds": row.clock_skew_seconds,
        "max_token_age_seconds": row.max_token_age_seconds,
        "jwks_cache_seconds": row.jwks_cache_seconds,
        "allow_private_network": row.allow_private_network,
        "allow_insecure_http": row.allow_insecure_http,
        "enabled": row.enabled,
    }
    candidate.update(changes)
    try:
        validated = IdentityProviderCreate(**candidate)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if _production() and validated.allow_insecure_http:
        raise HTTPException(
            status_code=422,
            detail="Insecure HTTP JWKS endpoints are forbidden in production",
        )
    for field, value in validated.model_dump(exclude={"issuer"}).items():
        setattr(row, field, value)
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    clear_jwks_cache(provider_id)
    await chain_log(
        db,
        namespace=_PLATFORM_AUDIT_NAMESPACE,
        agent_id=_ADMIN_AGENT,
        op="identity.provider_update",
        payload={
            "provider_id": str(row.id),
            "version": row.version,
            "changed_fields": sorted(changes),
        },
    )
    await db.commit()
    await db.refresh(row)
    return IdentityProviderOut.model_validate(row)


@admin_router.delete(
    "/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an OIDC issuer immediately",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def revoke_provider(
    provider_id: UUID,
    expected_version: int = Query(..., ge=1),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    row = await _provider_for_update(db, provider_id)
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Identity provider is already revoked")
    if row.version != expected_version:
        raise HTTPException(status_code=409, detail=f"Version conflict; current version is {row.version}")
    row.enabled = False
    row.revoked_at = datetime.now(timezone.utc)
    row.version += 1
    clear_jwks_cache(provider_id)
    await chain_log(
        db,
        namespace=_PLATFORM_AUDIT_NAMESPACE,
        agent_id=_ADMIN_AGENT,
        op="identity.provider_revoke",
        payload={"provider_id": str(row.id), "version": row.version},
    )
    await db.commit()
    return Response(status_code=204)


@admin_router.post(
    "/providers/{provider_id}/probe",
    response_model=IdentityProviderProbe,
    summary="Verify JWKS reachability and force a safe key refresh",
)
async def check_provider(
    provider_id: UUID,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> IdentityProviderProbe:
    row = await db.get(TrustedIdentityProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Identity provider not found")
    try:
        key_count = await probe_provider(row)
    except IdentityProviderFetchError as exc:
        return IdentityProviderProbe(
            provider_id=row.id,
            issuer=row.issuer,
            reachable=False,
            error=str(exc),
        )
    return IdentityProviderProbe(
        provider_id=row.id,
        issuer=row.issuer,
        reachable=True,
        signing_key_count=key_count,
    )


@admin_router.post(
    "/bindings",
    response_model=IdentityBindingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Bind a verified OIDC subject to a Lians tenant and role",
)
async def create_binding(
    body: IdentityBindingCreate,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> IdentityBindingOut:
    provider = (
        await db.execute(
            select(TrustedIdentityProvider)
            .where(TrustedIdentityProvider.id == body.provider_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if provider is None or provider.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Active identity provider not found")
    row = IdentityBinding(**body.model_dump())
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This provider subject already has a binding",
        ) from exc
    await chain_log(
        db,
        namespace=row.namespace,
        agent_id=_ADMIN_AGENT,
        op="identity.binding_create",
        payload={
            "binding_id": str(row.id),
            "provider_id": str(row.provider_id),
            "subject_sha256": _subject_fingerprint(row.external_subject),
            "principal_type": row.principal_type,
            "role": row.role,
            "scopes": list(row.scopes),
            "barrier_group": row.barrier_group,
        },
    )
    await db.commit()
    await db.refresh(row)
    return IdentityBindingOut.model_validate(row)


@admin_router.get(
    "/bindings",
    response_model=list[IdentityBindingOut],
    responses=_CREATED_AT_INVENTORY_RESPONSES,
    summary="List OIDC subject bindings",
)
async def list_bindings(
    response: Response,
    namespace: str | None = Query(default=None),
    provider_id: UUID | None = Query(default=None),
    include_revoked: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    before_created_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[IdentityBindingOut]:
    conditions = []
    if namespace:
        conditions.append(IdentityBinding.namespace == namespace)
    if provider_id:
        conditions.append(IdentityBinding.provider_id == provider_id)
    if not include_revoked:
        conditions.append(IdentityBinding.revoked_at.is_(None))
    if (before_created_at is None) != (before_id is None):
        raise HTTPException(422, "before_created_at and before_id must be supplied together")
    total_statement = select(func.count(IdentityBinding.id))
    if conditions:
        total_statement = total_statement.where(and_(*conditions))
    total = int((await db.execute(total_statement)).scalar_one() or 0)
    page_conditions = list(conditions)
    if before_created_at is not None and before_id is not None:
        page_conditions.append(
            or_(
                IdentityBinding.created_at < before_created_at,
                and_(
                    IdentityBinding.created_at == before_created_at,
                    IdentityBinding.id < before_id,
                ),
            )
        )
    stmt = select(IdentityBinding)
    if page_conditions:
        stmt = stmt.where(and_(*page_conditions))
    fetched = list(
        (
            await db.execute(
                stmt.order_by(
                    IdentityBinding.created_at.desc(),
                    IdentityBinding.id.desc(),
                ).limit(limit + 1)
            )
        ).scalars()
    )
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
    return [IdentityBindingOut.model_validate(row) for row in rows]


@admin_router.patch(
    "/bindings/{binding_id}",
    response_model=IdentityBindingOut,
    summary="Update a subject binding with optimistic concurrency",
)
async def update_binding(
    binding_id: UUID,
    body: IdentityBindingPatch,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> IdentityBindingOut:
    row = await _binding_for_update(db, binding_id)
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Identity binding is revoked")
    if row.version != body.expected_version:
        raise HTTPException(status_code=409, detail=f"Version conflict; current version is {row.version}")
    changes = body.model_dump(exclude_unset=True)
    changes.pop("expected_version", None)
    next_role = changes.get("role", row.role)
    next_scopes = changes.get("scopes", row.scopes)
    if next_role is None and not next_scopes:
        raise HTTPException(status_code=422, detail="A binding must retain a role or scope")
    for field, value in changes.items():
        setattr(row, field, value)
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    await chain_log(
        db,
        namespace=row.namespace,
        agent_id=_ADMIN_AGENT,
        op="identity.binding_update",
        payload={
            "binding_id": str(row.id),
            "version": row.version,
            "changed_fields": sorted(changes),
        },
    )
    await db.commit()
    await db.refresh(row)
    return IdentityBindingOut.model_validate(row)


@admin_router.delete(
    "/bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a subject binding immediately",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def revoke_binding(
    binding_id: UUID,
    expected_version: int = Query(..., ge=1),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    row = await _binding_for_update(db, binding_id)
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Identity binding is already revoked")
    if row.version != expected_version:
        raise HTTPException(status_code=409, detail=f"Version conflict; current version is {row.version}")
    row.enabled = False
    row.revoked_at = datetime.now(timezone.utc)
    row.version += 1
    await chain_log(
        db,
        namespace=row.namespace,
        agent_id=_ADMIN_AGENT,
        op="identity.binding_revoke",
        payload={"binding_id": str(row.id), "version": row.version},
    )
    await db.commit()
    return Response(status_code=204)


@router.get("/whoami", response_model=PrincipalOut, summary="Inspect the active principal")
async def whoami(auth: AuthContext = Depends(get_auth)) -> PrincipalOut:
    return PrincipalOut(
        namespace=auth.namespace,
        scopes=list(auth.scopes),
        barrier_group=auth.barrier_group,
        role=auth.role,
        principal_id=auth.principal_id,
        principal_type=auth.principal_type,
        auth_method=auth.auth_method,
        credential_id=auth.credential_id,
    )
