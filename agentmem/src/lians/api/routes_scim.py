"""Tenant-isolated SCIM 2.0 provisioning and administrative bootstrap APIs."""
from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import chain_log
from ..config import get_settings
from ..db import (
    AsyncSessionLocal,
    get_db,
    set_current_barrier_group,
    set_current_namespace,
)
from ..enterprise_models import (
    ScimBearerCredential,
    ScimGroup,
    ScimGroupEntitlement,
    ScimGroupMember,
    ScimTenantConfig,
    ScimTenantReconciliationJob,
    ScimUser,
)
from ..enterprise_schemas import (
    SCIM_GROUP_SCHEMA,
    SCIM_GROUP_LIST_MEMBER_ROW_LIMIT,
    SCIM_GROUP_LIST_RESPONSE_BYTES,
    SCIM_SERVICE_PROVIDER_SCHEMA,
    SCIM_USER_SCHEMA,
    ScimCredentialCreated,
    ScimCredentialOut,
    ScimCredentialRotate,
    ScimEntitlementOut,
    ScimEntitlementUpsert,
    ScimErrorBody,
    ScimGroupOut,
    ScimGroupWrite,
    ScimListResponse,
    ScimPatchRequest,
    ScimTenantCreate,
    ScimTenantCreated,
    ScimTenantOut,
    ScimTenantPatch,
    ScimTenantReconciliationOut,
    ScimUserOut,
    ScimUserWrite,
)
from ..enterprise_service import (
    ProvisioningError,
    ScimContext,
    apply_group_patch,
    apply_user_patch,
    assert_if_match,
    authenticate_scim,
    batch_group_member_documents,
    etag,
    group_document,
    group_member_ids,
    group_member_documents,
    make_credential,
    paginated_groups,
    paginated_users,
    parse_filter,
    set_group_members,
    subject_fingerprint,
    sync_user_binding,
    user_group_ids,
    user_document,
    utcnow,
)
from ..identity_models import TrustedIdentityProvider
from ..mutation_safety import reject_non_replayable_idempotency_key
from ..scim_reconciliation_service import (
    claim_due_scim_reconciliation_jobs,
    enqueue_scim_reconciliation,
    fence_tenant_bindings,
    get_scim_reconciliation_job,
    process_scim_reconciliation_job,
    retry_scim_reconciliation_job,
    scim_reconciliation_job_dict,
)
from .routes_admin import _require_admin

_ADMIN_AGENT = "__admin__"
_SCIM_AGENT = "__scim__"

_RECONCILIATION_HEADERS = {
    "Location": {
        "description": "Canonical administrative reconciliation status resource",
        "schema": {"type": "string"},
    },
    "X-Lians-Reconciliation-Job-Id": {
        "description": "Durable fixed-snapshot job identifier",
        "schema": {"type": "string", "format": "uuid"},
    },
    "X-Lians-Reconciliation-Status": {
        "description": "Job state committed with this tenant mutation",
        "schema": {
            "type": "string",
            "enum": ["pending", "completed"],
        },
    },
}

_EXACT_INVENTORY_HEADERS = {
    "X-Lians-Total-Count": {"schema": {"type": "integer", "minimum": 0}},
    "X-Lians-Page-Limit": {"schema": {"type": "integer", "minimum": 1}},
    "X-Lians-Page-Returned": {"schema": {"type": "integer", "minimum": 0}},
    "X-Lians-Has-More": {"schema": {"type": "boolean"}},
    "X-Lians-Page-Complete": {"schema": {"type": "boolean"}},
    "X-Lians-Collection-Complete": {"schema": {"type": "boolean"}},
}


def _inventory_responses(*cursor_headers: str) -> dict[int, dict[str, object]]:
    headers = dict(_EXACT_INVENTORY_HEADERS)
    headers.update(
        {
            name: {"schema": {"type": "string", "format": "uuid"}}
            for name in cursor_headers
        }
    )
    return {
        200: {
            "description": "Exact SCIM administrative inventory page",
            "headers": headers,
        }
    }


_ID_INVENTORY_RESPONSES = _inventory_responses(
    "X-Lians-Next-After-Id",
    "X-Lians-Next-Id",
)
_GROUP_ID_INVENTORY_RESPONSES = _inventory_responses(
    "X-Lians-Next-After-Group-Id",
    "X-Lians-Next-Group-Id",
)


def _set_inventory_headers(
    response: Response,
    *,
    total: int,
    limit: int,
    returned: int,
    has_more: bool,
    cursor_supplied: bool,
    next_cursor: UUID | None,
    next_header: str,
    compatibility_header: str,
) -> None:
    response.headers["X-Lians-Total-Count"] = str(total)
    response.headers["X-Lians-Page-Limit"] = str(limit)
    response.headers["X-Lians-Page-Returned"] = str(returned)
    response.headers["X-Lians-Has-More"] = str(has_more).lower()
    response.headers["X-Lians-Page-Complete"] = str(not has_more).lower()
    response.headers["X-Lians-Collection-Complete"] = str(
        not cursor_supplied and not has_more and total == returned
    ).lower()
    if has_more and next_cursor is not None:
        encoded = str(next_cursor)
        response.headers[next_header] = encoded
        response.headers[compatibility_header] = encoded


class ScimJSONResponse(JSONResponse):
    media_type = "application/scim+json"


class ScimRoute(APIRoute):
    """Render service failures as RFC 7644-shaped errors, including dependencies."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except ProvisioningError as exc:
                body = ScimErrorBody(
                    status=str(exc.status_code),
                    scimType=exc.scim_type,
                    detail=exc.detail,
                )
                headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
                return ScimJSONResponse(
                    status_code=exc.status_code,
                    content=jsonable_encoder(body, exclude_none=True),
                    headers=headers,
                )
            except RequestValidationError:
                body = ScimErrorBody(
                    status="400",
                    scimType="invalidValue",
                    detail="Request body or parameters failed SCIM schema validation",
                )
                return ScimJSONResponse(
                    status_code=400,
                    content=jsonable_encoder(body, exclude_none=True),
                )
            except HTTPException as exc:
                body = ScimErrorBody(
                    status=str(exc.status_code),
                    detail=str(exc.detail),
                )
                return ScimJSONResponse(
                    status_code=exc.status_code,
                    content=jsonable_encoder(body, exclude_none=True),
                    headers=exc.headers,
                )

        return handler


class AdminProvisioningRoute(APIRoute):
    """Keep safe provisioning conflicts from surfacing as administrator 500s."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except ProvisioningError as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                )

        return handler


admin_router = APIRouter(
    prefix="/v1/admin/enterprise/scim",
    tags=["admin", "enterprise", "scim"],
    route_class=AdminProvisioningRoute,
)
router = APIRouter(
    prefix="/scim/v2/{tenant_id}",
    tags=["scim"],
    route_class=ScimRoute,
    default_response_class=ScimJSONResponse,
)

# ``auto_error=False`` preserves the SCIM layer's RFC 7644-shaped authentication
# failures. The dependency is also the canonical OpenAPI declaration: using a
# plain optional Header made every provisioning operation appear unsecured to
# client generators and security scanners even though runtime auth was strict.
_scim_bearer = HTTPBearer(auto_error=False)


def _bearer_value(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not value.strip():
        return None
    return value.strip()


async def _scim_context(
    tenant_id: UUID,
    request: Request,
    _bearer_credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(_scim_bearer),
    ],
    db: AsyncSession = Depends(get_db),
) -> ScimContext:
    # Keep the original parser as the runtime authority so whitespace, scheme
    # matching, and missing-token behavior remain byte-for-byte compatible.
    authorization = request.headers.get("Authorization")
    return await authenticate_scim(
        db,
        tenant_id=tenant_id,
        raw_token=_bearer_value(authorization),
    )


async def _tenant_for_update(db: AsyncSession, tenant_id: UUID) -> ScimTenantConfig:
    row = (
        await db.execute(
            select(ScimTenantConfig)
            .where(ScimTenantConfig.id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="SCIM tenant configuration not found")
    return row


async def _tenant_for_inventory(
    db: AsyncSession, tenant_id: UUID
) -> ScimTenantConfig:
    row = (
        await db.execute(
            select(ScimTenantConfig)
            .where(ScimTenantConfig.id == tenant_id)
            # Tenant-scoped writers take FOR UPDATE on this same row. A shared
            # lock keeps count and page queries on one complete collection
            # while allowing concurrent inventory readers.
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="SCIM tenant configuration not found")
    return row


async def _credential_for_update(
    db: AsyncSession, tenant_id: UUID, credential_id: UUID
) -> ScimBearerCredential:
    row = (
        await db.execute(
            select(ScimBearerCredential)
            .where(
                ScimBearerCredential.id == credential_id,
                ScimBearerCredential.tenant_config_id == tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="SCIM credential not found")
    return row


async def _admin_group_for_update(
    db: AsyncSession, tenant_id: UUID, group_id: UUID
) -> ScimGroup:
    row = (
        await db.execute(
            select(ScimGroup)
            .where(
                ScimGroup.id == group_id,
                ScimGroup.tenant_config_id == tenant_id,
                ScimGroup.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="SCIM group not found")
    return row


async def _config_for_context(
    db: AsyncSession,
    ctx: ScimContext,
    *,
    shared: bool = False,
) -> ScimTenantConfig:
    # The tenant row is the provisioning transaction mutex. Every SCIM
    # mutation takes it before resource locks, giving deterministic lock order
    # across user, group, entitlement, credential, and disable workflows.
    row = (
        await db.execute(
            select(ScimTenantConfig)
            .where(ScimTenantConfig.id == ctx.tenant_id)
            # Complete multi-query reads share the tenant mutex with each
            # other, while every mutation continues to take the exclusive
            # form. PostgreSQL therefore prevents membership drift without
            # serializing concurrent Group list readers.
            .with_for_update(read=shared)
        )
    ).scalar_one_or_none()
    if (
        row is None
        or row.namespace != ctx.namespace
        or not row.enabled
        or row.revoked_at is not None
    ):
        raise ProvisioningError(401, "SCIM tenant is unavailable")
    return row


async def _user_for_update(
    db: AsyncSession, ctx: ScimContext, user_id: UUID
) -> ScimUser:
    row = (
        await db.execute(
            select(ScimUser)
            .where(
                ScimUser.id == user_id,
                ScimUser.tenant_config_id == ctx.tenant_id,
                ScimUser.namespace == ctx.namespace,
                ScimUser.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProvisioningError(404, "User not found")
    return row


async def _group_for_update(
    db: AsyncSession, ctx: ScimContext, group_id: UUID
) -> ScimGroup:
    row = (
        await db.execute(
            select(ScimGroup)
            .where(
                ScimGroup.id == group_id,
                ScimGroup.tenant_config_id == ctx.tenant_id,
                ScimGroup.namespace == ctx.namespace,
                ScimGroup.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProvisioningError(404, "Group not found")
    return row


async def _audit_scim_user(
    db: AsyncSession,
    *,
    config: ScimTenantConfig,
    user: ScimUser,
    operation: str,
) -> None:
    raw_subject = (
        user.external_id if config.subject_attribute == "externalId" else user.user_name
    )
    payload: dict[str, Any] = {
        "scim_user_id": str(user.id),
        "version": user.version,
        "active": bool(user.active and user.deleted_at is None),
    }
    if raw_subject:
        payload["subject_sha256"] = subject_fingerprint(raw_subject)
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id=_SCIM_AGENT,
        op=operation,
        payload=payload,
    )


async def _reconcile_group_users(
    db: AsyncSession, *, config: ScimTenantConfig, group_id: UUID
) -> None:
    user_ids = await group_member_ids(
        db,
        config_id=config.id,
        group_id=group_id,
    )
    if not user_ids:
        return
    provider = await db.get(TrustedIdentityProvider, config.provider_id)
    reconciliation_status = (
        await db.execute(
            select(ScimTenantReconciliationJob.status).where(
                ScimTenantReconciliationJob.tenant_config_id == config.id,
                ScimTenantReconciliationJob.target_config_version == config.version,
            )
        )
    ).scalar_one_or_none()
    activation_fence_complete = reconciliation_status in {None, "completed"}
    for start in range(0, len(user_ids), 400):
        users = list(
            (
                await db.execute(
                    select(ScimUser)
                    .where(ScimUser.id.in_(user_ids[start : start + 400]))
                    .order_by(ScimUser.id)
                    .with_for_update()
                )
            ).scalars()
        )
        for user in users:
            await sync_user_binding(
                db,
                config=config,
                user=user,
                provider=provider,
                provider_loaded=True,
                activation_fence_complete=activation_fence_complete,
            )


def _is_user_group_capacity_error(exc: IntegrityError) -> bool:
    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == "ck_scim_user_group_capacity":
        return True
    # SQLite has no structured constraint diagnostic. Match only the fixed
    # server-owned trigger message, never arbitrary database detail.
    return "SCIM User Group membership capacity exceeded" in str(original)


@admin_router.post(
    "/tenants",
    response_model=ScimTenantCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant SCIM service provider and its first credential",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def create_scim_tenant(
    body: ScimTenantCreate,
    response: Response,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ScimTenantCreated:
    provider = (
        await db.execute(
            select(TrustedIdentityProvider)
            .where(TrustedIdentityProvider.id == body.provider_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if provider is None or not provider.enabled or provider.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Active trusted identity provider not found")
    config = ScimTenantConfig(
        namespace=body.namespace,
        provider_id=body.provider_id,
        subject_attribute=body.subject_attribute,
        enabled=body.enabled,
    )
    db.add(config)
    try:
        await db.flush()
        credential, raw_token = make_credential(
            config,
            label=body.credential_label,
            expires_at=body.credential_expires_at,
        )
        db.add(credential)
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="A SCIM configuration already exists for this tenant"
        ) from exc
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id=_ADMIN_AGENT,
        op="scim.tenant_create",
        payload={
            "tenant_config_id": str(config.id),
            "provider_id": str(config.provider_id),
            "subject_attribute": config.subject_attribute,
            "credential_id": str(credential.id),
        },
    )
    await db.commit()
    await db.refresh(config)
    await db.refresh(credential)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return ScimTenantCreated(
        tenant=ScimTenantOut.model_validate(config),
        credential=ScimCredentialOut.model_validate(credential),
        bearer_token=raw_token,
        scim_base_path=f"/scim/v2/{config.id}",
    )


@admin_router.get(
    "/tenants",
    response_model=list[ScimTenantOut],
    responses=_ID_INVENTORY_RESPONSES,
)
async def list_scim_tenants(
    response: Response,
    namespace: str | None = Query(default=None),
    include_revoked: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    after_id: UUID | None = Query(default=None),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ScimTenantOut]:
    filters = []
    if namespace:
        filters.append(ScimTenantConfig.namespace == namespace)
    if not include_revoked:
        filters.append(ScimTenantConfig.revoked_at.is_(None))
    total = int(
        (
            await db.execute(
                select(func.count(ScimTenantConfig.id)).where(*filters)
            )
        ).scalar_one()
    )
    stmt = select(ScimTenantConfig).where(*filters)
    if after_id is not None:
        stmt = stmt.where(ScimTenantConfig.id > after_id)
    fetched = list(
        (
            await db.execute(stmt.order_by(ScimTenantConfig.id.asc()).limit(limit + 1))
        ).scalars()
    )
    has_more = len(fetched) > limit
    rows = fetched[:limit]
    _set_inventory_headers(
        response,
        total=total,
        limit=limit,
        returned=len(rows),
        has_more=has_more,
        cursor_supplied=after_id is not None,
        next_cursor=rows[-1].id if rows else None,
        next_header="X-Lians-Next-After-Id",
        compatibility_header="X-Lians-Next-Id",
    )
    return [ScimTenantOut.model_validate(row) for row in rows]


@admin_router.get("/tenants/{tenant_id}", response_model=ScimTenantOut)
async def get_scim_tenant(
    tenant_id: UUID,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ScimTenantOut:
    row = await db.get(ScimTenantConfig, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SCIM tenant configuration not found")
    return ScimTenantOut.model_validate(row)


@admin_router.patch(
    "/tenants/{tenant_id}",
    response_model=ScimTenantOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {
            "description": "Tenant mutation committed and reconciliation queued",
            "headers": _RECONCILIATION_HEADERS,
        }
    },
)
async def update_scim_tenant(
    tenant_id: UUID,
    body: ScimTenantPatch,
    response: Response,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ScimTenantOut:
    config = await _tenant_for_update(db, tenant_id)
    if config.revoked_at is not None:
        raise HTTPException(status_code=409, detail="SCIM tenant configuration is revoked")
    if config.version != body.expected_version:
        raise HTTPException(
            status_code=409, detail=f"Version conflict; current version is {config.version}"
        )
    if body.enabled:
        provider = (
            await db.execute(
                select(TrustedIdentityProvider)
                .where(TrustedIdentityProvider.id == config.provider_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if provider is None or not provider.enabled or provider.revoked_at is not None:
            raise HTTPException(
                status_code=409,
                detail="The configured trusted identity provider is not active",
            )
    config.enabled = body.enabled
    config.version += 1
    config.updated_at = utcnow()
    await fence_tenant_bindings(db, config=config)
    job = await enqueue_scim_reconciliation(
        db,
        config=config,
        requested_by_principal_ref=_ADMIN_AGENT,
    )
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id=_ADMIN_AGENT,
        op="scim.tenant_update",
        payload={
            "tenant_config_id": str(config.id),
            "version": config.version,
            "enabled": config.enabled,
        },
    )
    await db.commit()
    await db.refresh(config)
    response.headers["Location"] = (
        f"/v1/admin/enterprise/scim/tenants/{tenant_id}/"
        f"binding-reconciliations/{job.id}"
    )
    response.headers["X-Lians-Reconciliation-Job-Id"] = str(job.id)
    response.headers["X-Lians-Reconciliation-Status"] = str(job.status)
    return ScimTenantOut.model_validate(config)


@admin_router.delete(
    "/tenants/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
    responses={
        204: {
            "description": "Tenant revoked and reconciliation queued",
            "headers": _RECONCILIATION_HEADERS,
        }
    },
)
async def revoke_scim_tenant(
    tenant_id: UUID,
    expected_version: int = Query(..., ge=1),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    config = await _tenant_for_update(db, tenant_id)
    if config.revoked_at is not None:
        raise HTTPException(status_code=409, detail="SCIM tenant configuration is already revoked")
    if config.version != expected_version:
        raise HTTPException(
            status_code=409, detail=f"Version conflict; current version is {config.version}"
        )
    config.enabled = False
    config.revoked_at = utcnow()
    config.version += 1
    config.updated_at = utcnow()
    await fence_tenant_bindings(db, config=config)
    job = await enqueue_scim_reconciliation(
        db,
        config=config,
        requested_by_principal_ref=_ADMIN_AGENT,
    )
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id=_ADMIN_AGENT,
        op="scim.tenant_revoke",
        payload={"tenant_config_id": str(config.id), "version": config.version},
    )
    await db.commit()
    location = (
        f"/v1/admin/enterprise/scim/tenants/{tenant_id}/"
        f"binding-reconciliations/{job.id}"
    )
    return Response(
        status_code=204,
        headers={
            "Location": location,
            "X-Lians-Reconciliation-Job-Id": str(job.id),
            "X-Lians-Reconciliation-Status": str(job.status),
        },
    )


@admin_router.get(
    "/tenants/{tenant_id}/binding-reconciliations/{job_id}",
    response_model=ScimTenantReconciliationOut,
    summary="Inspect exact fixed-snapshot SCIM binding reconciliation progress",
)
async def scim_reconciliation_status(
    tenant_id: UUID,
    job_id: UUID,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ScimTenantReconciliationOut:
    job = await get_scim_reconciliation_job(
        db,
        tenant_config_id=tenant_id,
        job_id=job_id,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="SCIM reconciliation job not found")
    return ScimTenantReconciliationOut(**scim_reconciliation_job_dict(job))


@admin_router.post(
    "/tenants/{tenant_id}/binding-reconciliations/{job_id}/retry",
    response_model=ScimTenantReconciliationOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
    summary="Retry a failed fixed SCIM binding snapshot after remediation",
)
async def retry_scim_reconciliation(
    tenant_id: UUID,
    job_id: UUID,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ScimTenantReconciliationOut:
    job = await retry_scim_reconciliation_job(
        db,
        tenant_config_id=tenant_id,
        job_id=job_id,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="SCIM reconciliation job not found")
    return ScimTenantReconciliationOut(**scim_reconciliation_job_dict(job))


@admin_router.post(
    "/tenants/{tenant_id}/binding-reconciliations/{job_id}/advance",
    response_model=ScimTenantReconciliationOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
    summary="Lease and advance at most one configured reconciliation page",
)
async def advance_scim_reconciliation(
    tenant_id: UUID,
    job_id: UUID,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ScimTenantReconciliationOut:
    settings = get_settings()
    worker_id = f"request:{job_id}"
    claims = await claim_due_scim_reconciliation_jobs(
        db,
        worker_id=worker_id,
        batch_size=1,
        lease_seconds=settings.scim_reconciliation_worker_lease_seconds,
        tenant_config_id=tenant_id,
        job_id=job_id,
    )
    if claims:
        await process_scim_reconciliation_job(
            AsyncSessionLocal,
            claim=claims[0],
            worker_id=worker_id,
            page_size=settings.scim_reconciliation_worker_page_size,
            max_pages=1,
            lease_seconds=settings.scim_reconciliation_worker_lease_seconds,
        )
        set_current_namespace("__admin__")
        set_current_barrier_group(None)
    job = await get_scim_reconciliation_job(
        db,
        tenant_config_id=tenant_id,
        job_id=job_id,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="SCIM reconciliation job not found")
    if not claims and job.status in {"pending", "running"}:
        detail = (
            "SCIM reconciliation job is already leased"
            if job.lease_expires_at is not None
            else "SCIM reconciliation job is waiting for its durable retry window"
        )
        raise HTTPException(status_code=409, detail=detail)
    return ScimTenantReconciliationOut(**scim_reconciliation_job_dict(job))


@admin_router.get(
    "/tenants/{tenant_id}/credentials",
    response_model=list[ScimCredentialOut],
    responses=_ID_INVENTORY_RESPONSES,
)
async def list_scim_credentials(
    tenant_id: UUID,
    response: Response,
    include_revoked: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    after_id: UUID | None = Query(default=None),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ScimCredentialOut]:
    await _tenant_for_inventory(db, tenant_id)
    filters = [ScimBearerCredential.tenant_config_id == tenant_id]
    if not include_revoked:
        filters.append(ScimBearerCredential.revoked_at.is_(None))
    total = int(
        (
            await db.execute(
                select(func.count(ScimBearerCredential.id)).where(*filters)
            )
        ).scalar_one()
    )
    stmt = select(ScimBearerCredential).where(*filters)
    if after_id is not None:
        stmt = stmt.where(ScimBearerCredential.id > after_id)
    fetched = list(
        (
            await db.execute(stmt.order_by(ScimBearerCredential.id.asc()).limit(limit + 1))
        ).scalars()
    )
    has_more = len(fetched) > limit
    rows = fetched[:limit]
    _set_inventory_headers(
        response,
        total=total,
        limit=limit,
        returned=len(rows),
        has_more=has_more,
        cursor_supplied=after_id is not None,
        next_cursor=rows[-1].id if rows else None,
        next_header="X-Lians-Next-After-Id",
        compatibility_header="X-Lians-Next-Id",
    )
    return [ScimCredentialOut.model_validate(row) for row in rows]


@admin_router.post(
    "/tenants/{tenant_id}/credentials/{credential_id}/rotate",
    response_model=ScimCredentialCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def rotate_scim_credential(
    tenant_id: UUID,
    credential_id: UUID,
    body: ScimCredentialRotate,
    response: Response,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ScimCredentialCreated:
    config = await _tenant_for_update(db, tenant_id)
    if config.revoked_at is not None:
        raise HTTPException(status_code=409, detail="SCIM tenant configuration is revoked")
    old = await _credential_for_update(db, tenant_id, credential_id)
    if old.version != body.expected_version:
        raise HTTPException(
            status_code=409, detail=f"Version conflict; current version is {old.version}"
        )
    new, raw_token = make_credential(
        config,
        label=body.label if body.label is not None else old.label,
        expires_at=body.expires_at,
        rotated_from_id=old.id,
    )
    db.add(new)
    await db.flush()
    old.version += 1
    if body.revoke_prior:
        old.revoked_at = utcnow()
        old.replaced_by_id = new.id
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id=_ADMIN_AGENT,
        op="scim.credential_rotate",
        payload={
            "tenant_config_id": str(config.id),
            "old_credential_id": str(old.id),
            "new_credential_id": str(new.id),
            "prior_revoked": body.revoke_prior,
            "prior_version": old.version,
        },
    )
    await db.commit()
    await db.refresh(new)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return ScimCredentialCreated(
        credential=ScimCredentialOut.model_validate(new),
        bearer_token=raw_token,
    )


@admin_router.delete(
    "/tenants/{tenant_id}/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def revoke_scim_credential(
    tenant_id: UUID,
    credential_id: UUID,
    expected_version: int = Query(..., ge=1),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    config = await _tenant_for_update(db, tenant_id)
    credential = await _credential_for_update(db, tenant_id, credential_id)
    if credential.revoked_at is not None:
        raise HTTPException(status_code=409, detail="SCIM credential is already revoked")
    if credential.version != expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict; current version is {credential.version}",
        )
    credential.revoked_at = utcnow()
    credential.version += 1
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id=_ADMIN_AGENT,
        op="scim.credential_revoke",
        payload={
            "tenant_config_id": str(config.id),
            "credential_id": str(credential.id),
            "version": credential.version,
        },
    )
    await db.commit()
    return Response(status_code=204)


@admin_router.get(
    "/tenants/{tenant_id}/entitlements",
    response_model=list[ScimEntitlementOut],
    responses=_GROUP_ID_INVENTORY_RESPONSES,
)
async def list_entitlements(
    tenant_id: UUID,
    response: Response,
    limit: int = Query(default=100, ge=1, le=500),
    after_group_id: UUID | None = Query(default=None),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ScimEntitlementOut]:
    await _tenant_for_inventory(db, tenant_id)
    filters = [ScimGroupEntitlement.tenant_config_id == tenant_id]
    total = int(
        (
            await db.execute(
                select(func.count(ScimGroupEntitlement.id)).where(*filters)
            )
        ).scalar_one()
    )
    stmt = select(ScimGroupEntitlement).where(*filters)
    if after_group_id is not None:
        stmt = stmt.where(ScimGroupEntitlement.group_id > after_group_id)
    fetched = list(
        (
            await db.execute(
                stmt.order_by(ScimGroupEntitlement.group_id).limit(limit + 1)
            )
        ).scalars()
    )
    has_more = len(fetched) > limit
    rows = fetched[:limit]
    _set_inventory_headers(
        response,
        total=total,
        limit=limit,
        returned=len(rows),
        has_more=has_more,
        cursor_supplied=after_group_id is not None,
        next_cursor=rows[-1].group_id if rows else None,
        next_header="X-Lians-Next-After-Group-Id",
        compatibility_header="X-Lians-Next-Group-Id",
    )
    return [ScimEntitlementOut.model_validate(row) for row in rows]


@admin_router.put(
    "/tenants/{tenant_id}/groups/{group_id}/entitlement",
    response_model=ScimEntitlementOut,
)
async def upsert_group_entitlement(
    tenant_id: UUID,
    group_id: UUID,
    body: ScimEntitlementUpsert,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> ScimEntitlementOut:
    config = await _tenant_for_update(db, tenant_id)
    if config.revoked_at is not None:
        raise HTTPException(status_code=409, detail="SCIM tenant configuration is revoked")
    await _admin_group_for_update(db, tenant_id, group_id)
    mapping = (
        await db.execute(
            select(ScimGroupEntitlement)
            .where(ScimGroupEntitlement.group_id == group_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if mapping is None:
        if body.expected_version is not None:
            raise HTTPException(status_code=409, detail="Entitlement does not yet exist")
        mapping = ScimGroupEntitlement(
            tenant_config_id=config.id,
            namespace=config.namespace,
            group_id=group_id,
            role=body.role,
            scopes=body.scopes,
            barrier_group=body.barrier_group,
        )
        db.add(mapping)
        operation = "scim.entitlement_create"
    else:
        if body.expected_version is None or mapping.version != body.expected_version:
            raise HTTPException(
                status_code=409,
                detail=f"Version conflict; current version is {mapping.version}",
            )
        mapping.role = body.role
        mapping.scopes = body.scopes
        mapping.barrier_group = body.barrier_group
        mapping.version += 1
        mapping.updated_at = utcnow()
        operation = "scim.entitlement_update"
    await db.flush()
    await _reconcile_group_users(db, config=config, group_id=group_id)
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id=_ADMIN_AGENT,
        op=operation,
        payload={
            "tenant_config_id": str(config.id),
            "group_id": str(group_id),
            "entitlement_id": str(mapping.id),
            "version": mapping.version,
            "role": mapping.role,
            "scopes": list(mapping.scopes),
            "barrier_group": mapping.barrier_group,
        },
    )
    await db.commit()
    await db.refresh(mapping)
    return ScimEntitlementOut.model_validate(mapping)


@admin_router.delete(
    "/tenants/{tenant_id}/groups/{group_id}/entitlement",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_group_entitlement(
    tenant_id: UUID,
    group_id: UUID,
    expected_version: int = Query(..., ge=1),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    config = await _tenant_for_update(db, tenant_id)
    await _admin_group_for_update(db, tenant_id, group_id)
    mapping = (
        await db.execute(
            select(ScimGroupEntitlement)
            .where(ScimGroupEntitlement.group_id == group_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if mapping is None:
        raise HTTPException(status_code=404, detail="Group entitlement not found")
    if mapping.version != expected_version:
        raise HTTPException(
            status_code=409, detail=f"Version conflict; current version is {mapping.version}"
        )
    mapping_id = mapping.id
    await db.delete(mapping)
    await db.flush()
    await _reconcile_group_users(db, config=config, group_id=group_id)
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id=_ADMIN_AGENT,
        op="scim.entitlement_delete",
        payload={
            "tenant_config_id": str(config.id),
            "group_id": str(group_id),
            "entitlement_id": str(mapping_id),
            "version": expected_version,
        },
    )
    await db.commit()
    return Response(status_code=204)


@router.get("/ServiceProviderConfig", summary="SCIM service-provider capabilities")
async def service_provider_config(
    tenant_id: UUID,
    _: ScimContext = Depends(_scim_context),
) -> dict[str, Any]:
    return {
        "schemas": [SCIM_SERVICE_PROVIDER_SCHEMA],
        "documentationUri": "/docs/enterprise-provisioning",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 100},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": True},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "Bearer Token",
                "description": "Tenant-scoped rotatable SCIM bearer credential",
                "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
                "primary": True,
            }
        ],
        "meta": {
            "resourceType": "ServiceProviderConfig",
            "location": f"/scim/v2/{tenant_id}/ServiceProviderConfig",
        },
    }


@router.get("/ResourceTypes", summary="SCIM resource types")
async def resource_types(
    tenant_id: UUID,
    _: ScimContext = Depends(_scim_context),
) -> ScimListResponse:
    base = f"/scim/v2/{tenant_id}"
    resources = [
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "User",
            "name": "User",
            "endpoint": "/Users",
            "schema": SCIM_USER_SCHEMA,
            "meta": {"resourceType": "ResourceType", "location": f"{base}/ResourceTypes/User"},
        },
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "Group",
            "name": "Group",
            "endpoint": "/Groups",
            "schema": SCIM_GROUP_SCHEMA,
            "meta": {"resourceType": "ResourceType", "location": f"{base}/ResourceTypes/Group"},
        },
    ]
    return ScimListResponse(
        totalResults=2, startIndex=1, itemsPerPage=2, Resources=resources
    )


@router.get("/Schemas", summary="Supported SCIM core schemas")
async def schemas(
    tenant_id: UUID,
    _: ScimContext = Depends(_scim_context),
) -> ScimListResponse:
    base = f"/scim/v2/{tenant_id}"
    resources = [
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
            "id": SCIM_USER_SCHEMA,
            "name": "User",
            "description": "Lians provisioned human identity",
            "attributes": [
                {
                    "name": "userName",
                    "type": "string",
                    "multiValued": False,
                    "required": True,
                    "caseExact": True,
                    "mutability": "readWrite",
                    "returned": "default",
                    "uniqueness": "server",
                },
                {
                    "name": "externalId",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "caseExact": True,
                    "mutability": "readWrite",
                    "returned": "default",
                    "uniqueness": "server",
                },
                {
                    "name": "displayName",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readWrite",
                    "returned": "default",
                },
                {
                    "name": "name",
                    "type": "complex",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readWrite",
                    "returned": "default",
                    "subAttributes": [
                        {"name": field, "type": "string", "multiValued": False}
                        for field in (
                            "formatted",
                            "familyName",
                            "givenName",
                            "middleName",
                            "honorificPrefix",
                            "honorificSuffix",
                        )
                    ],
                },
                {
                    "name": "emails",
                    "type": "complex",
                    "multiValued": True,
                    "required": False,
                    "mutability": "readWrite",
                    "returned": "default",
                    "subAttributes": [
                        {"name": "value", "type": "string", "multiValued": False},
                        {"name": "type", "type": "string", "multiValued": False},
                        {"name": "primary", "type": "boolean", "multiValued": False},
                        {"name": "display", "type": "string", "multiValued": False},
                    ],
                },
                {
                    "name": "active",
                    "type": "boolean",
                    "multiValued": False,
                    "required": False,
                    "mutability": "readWrite",
                    "returned": "default",
                },
            ],
            "meta": {"resourceType": "Schema", "location": f"{base}/Schemas/{SCIM_USER_SCHEMA}"},
        },
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
            "id": SCIM_GROUP_SCHEMA,
            "name": "Group",
            "description": "Lians authorization group",
            "attributes": [
                {
                    "name": "displayName",
                    "type": "string",
                    "multiValued": False,
                    "required": True,
                    "caseExact": True,
                    "mutability": "readWrite",
                    "returned": "default",
                    "uniqueness": "server",
                },
                {
                    "name": "externalId",
                    "type": "string",
                    "multiValued": False,
                    "required": False,
                    "caseExact": True,
                    "mutability": "readWrite",
                    "returned": "default",
                    "uniqueness": "server",
                },
                {
                    "name": "members",
                    "type": "complex",
                    "multiValued": True,
                    "required": False,
                    "mutability": "readWrite",
                    "returned": "default",
                    "subAttributes": [
                        {"name": "value", "type": "string", "multiValued": False},
                        {"name": "$ref", "type": "reference", "multiValued": False},
                        {"name": "display", "type": "string", "multiValued": False},
                        {"name": "type", "type": "string", "multiValued": False},
                    ],
                },
            ],
            "meta": {"resourceType": "Schema", "location": f"{base}/Schemas/{SCIM_GROUP_SCHEMA}"},
        },
    ]
    return ScimListResponse(
        totalResults=2, startIndex=1, itemsPerPage=2, Resources=resources
    )


@router.post("/Users", response_model=ScimUserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    tenant_id: UUID,
    body: ScimUserWrite,
    response: Response,
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> ScimUserOut:
    config = await _config_for_context(db, ctx)
    if config.subject_attribute == "externalId" and body.externalId is None and body.active:
        raise ProvisioningError(
            400,
            "externalId is required for active users in this tenant",
            scim_type="invalidValue",
        )
    user = ScimUser(
        tenant_config_id=config.id,
        namespace=config.namespace,
        external_id=body.externalId,
        user_name=body.userName,
        display_name=body.displayName,
        name=body.name.model_dump(exclude_none=True) if body.name else {},
        emails=[email.model_dump(exclude_none=True) for email in body.emails],
        active=body.active,
    )
    db.add(user)
    try:
        await db.flush()
        await sync_user_binding(db, config=config, user=user)
        await _audit_scim_user(db, config=config, user=user, operation="scim.user_create")
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ProvisioningError(
            409, "userName or externalId already exists", scim_type="uniqueness"
        ) from exc
    await db.refresh(user)
    base = f"/scim/v2/{tenant_id}"
    response.headers["ETag"] = etag(user.version)
    response.headers["Location"] = f"{base}/Users/{user.id}"
    return user_document(user, base)


@router.get("/Users", response_model=ScimListResponse)
async def list_users(
    tenant_id: UUID,
    filter: str | None = Query(default=None),
    startIndex: int = Query(default=1, ge=1, le=100_000),
    count: int = Query(default=100, ge=0, le=100),
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> ScimListResponse:
    parsed = parse_filter(filter, resource_type="User")
    total, rows = await paginated_users(
        db,
        config_id=ctx.tenant_id,
        parsed_filter=parsed,
        start_index=startIndex,
        count=count,
    )
    base = f"/scim/v2/{tenant_id}"
    resources = [user_document(row, base).model_dump(mode="json") for row in rows]
    return ScimListResponse(
        totalResults=total,
        startIndex=startIndex,
        itemsPerPage=len(resources),
        Resources=resources,
    )


@router.get("/Users/{user_id}", response_model=ScimUserOut)
async def get_user(
    tenant_id: UUID,
    user_id: UUID,
    response: Response,
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> ScimUserOut:
    user = await _user_for_update(db, ctx, user_id)
    response.headers["ETag"] = etag(user.version)
    return user_document(user, f"/scim/v2/{tenant_id}")


async def _replace_user(
    *,
    tenant_id: UUID,
    user_id: UUID,
    body: ScimUserWrite,
    if_match: str | None,
    response: Response,
    ctx: ScimContext,
    db: AsyncSession,
    operation: str,
) -> ScimUserOut:
    config = await _config_for_context(db, ctx)
    user = await _user_for_update(db, ctx, user_id)
    assert_if_match(if_match, user.version)
    if config.subject_attribute == "externalId" and body.externalId is None and body.active:
        raise ProvisioningError(
            400,
            "externalId is required for active users in this tenant",
            scim_type="invalidValue",
        )
    user.external_id = body.externalId
    user.user_name = body.userName
    user.display_name = body.displayName
    user.name = body.name.model_dump(exclude_none=True) if body.name else {}
    user.emails = [email.model_dump(exclude_none=True) for email in body.emails]
    user.active = body.active
    user.version += 1
    user.updated_at = utcnow()
    try:
        await db.flush()
        await sync_user_binding(db, config=config, user=user)
        await _audit_scim_user(db, config=config, user=user, operation=operation)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ProvisioningError(
            409, "userName or externalId already exists", scim_type="uniqueness"
        ) from exc
    await db.refresh(user)
    response.headers["ETag"] = etag(user.version)
    return user_document(user, f"/scim/v2/{tenant_id}")


@router.put("/Users/{user_id}", response_model=ScimUserOut)
async def replace_user(
    tenant_id: UUID,
    user_id: UUID,
    body: ScimUserWrite,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> ScimUserOut:
    return await _replace_user(
        tenant_id=tenant_id,
        user_id=user_id,
        body=body,
        if_match=if_match,
        response=response,
        ctx=ctx,
        db=db,
        operation="scim.user_replace",
    )


@router.patch("/Users/{user_id}", response_model=ScimUserOut)
async def patch_user(
    tenant_id: UUID,
    user_id: UUID,
    body: ScimPatchRequest,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> ScimUserOut:
    await _config_for_context(db, ctx)
    user = await _user_for_update(db, ctx, user_id)
    patched = apply_user_patch(user, body)
    # The replacement helper locks again within the same transaction safely.
    return await _replace_user(
        tenant_id=tenant_id,
        user_id=user_id,
        body=patched,
        if_match=if_match,
        response=response,
        ctx=ctx,
        db=db,
        operation="scim.user_patch",
    )


@router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    config = await _config_for_context(db, ctx)
    user = await _user_for_update(db, ctx, user_id)
    assert_if_match(if_match, user.version)
    membership_group_ids = await user_group_ids(
        db,
        config_id=config.id,
        user_id=user.id,
    )
    groups: list[ScimGroup] = []
    if membership_group_ids:
        groups = list(
            (
                await db.execute(
                    select(ScimGroup)
                    .where(
                        ScimGroup.id.in_(membership_group_ids),
                        ScimGroup.tenant_config_id == config.id,
                        ScimGroup.deleted_at.is_(None),
                    )
                    .order_by(ScimGroup.id)
                    .limit(len(membership_group_ids))
                    .with_for_update()
                )
            ).scalars()
        )
        await db.execute(
            delete(ScimGroupMember).where(
                ScimGroupMember.user_id == user.id,
                ScimGroupMember.tenant_config_id == config.id,
            )
        )
        now = utcnow()
        for group in groups:
            group.version += 1
            group.updated_at = now
        await chain_log(
            db,
            namespace=config.namespace,
            agent_id=_SCIM_AGENT,
            op="scim.user_memberships_remove",
            payload={
                "scim_user_id": str(user.id),
                "group_count": len(membership_group_ids),
            },
        )
    user.active = False
    user.deleted_at = utcnow()
    user.updated_at = utcnow()
    user.version += 1
    await sync_user_binding(db, config=config, user=user)
    await _audit_scim_user(db, config=config, user=user, operation="scim.user_delete")
    await db.commit()
    return Response(status_code=204)


@router.post("/Groups", response_model=ScimGroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    tenant_id: UUID,
    body: ScimGroupWrite,
    response: Response,
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> ScimGroupOut:
    config = await _config_for_context(db, ctx)
    group = ScimGroup(
        tenant_config_id=config.id,
        namespace=config.namespace,
        external_id=body.externalId,
        display_name=body.displayName,
    )
    db.add(group)
    try:
        await db.flush()
        await set_group_members(
            db,
            config=config,
            group=group,
            desired_ids=[member.value for member in body.members],
        )
        await chain_log(
            db,
            namespace=config.namespace,
            agent_id=_SCIM_AGENT,
            op="scim.group_create",
            payload={"scim_group_id": str(group.id), "version": group.version},
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_user_group_capacity_error(exc):
            raise ProvisioningError(
                409,
                "Adding this Group would exceed the per-User Group membership "
                "limit; no membership was changed",
                scim_type="tooMany",
            ) from exc
        raise ProvisioningError(
            409, "displayName or externalId already exists", scim_type="uniqueness"
        ) from exc
    await db.refresh(group)
    members = await group_member_documents(db, config_id=config.id, group_id=group.id)
    base = f"/scim/v2/{tenant_id}"
    response.headers["ETag"] = etag(group.version)
    response.headers["Location"] = f"{base}/Groups/{group.id}"
    return group_document(group, base, members)


@router.get("/Groups", response_model=ScimListResponse)
async def list_groups(
    tenant_id: UUID,
    filter: str | None = Query(default=None),
    startIndex: int = Query(default=1, ge=1, le=100_000),
    count: int = Query(default=100, ge=0, le=100),
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> ScimListResponse:
    config = await _config_for_context(db, ctx, shared=True)
    parsed = parse_filter(filter, resource_type="Group")
    total, rows = await paginated_groups(
        db,
        config_id=config.id,
        parsed_filter=parsed,
        start_index=startIndex,
        count=count,
    )
    base = f"/scim/v2/{tenant_id}"
    settings = get_settings()
    response_byte_limit = min(
        SCIM_GROUP_LIST_RESPONSE_BYTES,
        settings.scim_group_list_response_bytes,
    )
    members_by_group, _ = await batch_group_member_documents(
        db,
        config_id=config.id,
        group_ids=(row.id for row in rows),
        cumulative_row_limit=min(
            SCIM_GROUP_LIST_MEMBER_ROW_LIMIT,
            settings.scim_group_list_member_row_limit,
        ),
        cumulative_byte_limit=response_byte_limit,
    )
    resources = [
        group_document(row, base, members_by_group[row.id]).model_dump(mode="json")
        for row in rows
    ]
    result = ScimListResponse(
        totalResults=total,
        startIndex=startIndex,
        itemsPerPage=len(resources),
        Resources=resources,
    )
    response_bytes = len(
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if response_bytes > response_byte_limit:
        raise ProvisioningError(
            413,
            "The requested Group page exceeds the complete response byte budget; "
            "request a smaller page",
            scim_type="tooMany",
        )
    return result


@router.get("/Groups/{group_id}", response_model=ScimGroupOut)
async def get_group(
    tenant_id: UUID,
    group_id: UUID,
    response: Response,
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> ScimGroupOut:
    group = await _group_for_update(db, ctx, group_id)
    members = await group_member_documents(db, config_id=ctx.tenant_id, group_id=group.id)
    response.headers["ETag"] = etag(group.version)
    return group_document(group, f"/scim/v2/{tenant_id}", members)


async def _replace_group(
    *,
    tenant_id: UUID,
    group_id: UUID,
    body: ScimGroupWrite,
    if_match: str | None,
    response: Response,
    ctx: ScimContext,
    db: AsyncSession,
    operation: str,
) -> ScimGroupOut:
    config = await _config_for_context(db, ctx)
    group = await _group_for_update(db, ctx, group_id)
    assert_if_match(if_match, group.version)
    group.external_id = body.externalId
    group.display_name = body.displayName
    group.version += 1
    group.updated_at = utcnow()
    try:
        await db.flush()
        await set_group_members(
            db,
            config=config,
            group=group,
            desired_ids=[member.value for member in body.members],
        )
        await chain_log(
            db,
            namespace=config.namespace,
            agent_id=_SCIM_AGENT,
            op=operation,
            payload={"scim_group_id": str(group.id), "version": group.version},
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_user_group_capacity_error(exc):
            raise ProvisioningError(
                409,
                "Replacing this Group would exceed the per-User Group membership "
                "limit; no membership was changed",
                scim_type="tooMany",
            ) from exc
        raise ProvisioningError(
            409, "displayName or externalId already exists", scim_type="uniqueness"
        ) from exc
    await db.refresh(group)
    members = await group_member_documents(db, config_id=config.id, group_id=group.id)
    response.headers["ETag"] = etag(group.version)
    return group_document(group, f"/scim/v2/{tenant_id}", members)


@router.put("/Groups/{group_id}", response_model=ScimGroupOut)
async def replace_group(
    tenant_id: UUID,
    group_id: UUID,
    body: ScimGroupWrite,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> ScimGroupOut:
    return await _replace_group(
        tenant_id=tenant_id,
        group_id=group_id,
        body=body,
        if_match=if_match,
        response=response,
        ctx=ctx,
        db=db,
        operation="scim.group_replace",
    )


@router.patch("/Groups/{group_id}", response_model=ScimGroupOut)
async def patch_group(
    tenant_id: UUID,
    group_id: UUID,
    body: ScimPatchRequest,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> ScimGroupOut:
    await _config_for_context(db, ctx)
    group = await _group_for_update(db, ctx, group_id)
    member_ids = await group_member_ids(
        db,
        config_id=ctx.tenant_id,
        group_id=group.id,
    )
    patched = apply_group_patch(group, member_ids, body)
    return await _replace_group(
        tenant_id=tenant_id,
        group_id=group_id,
        body=patched,
        if_match=if_match,
        response=response,
        ctx=ctx,
        db=db,
        operation="scim.group_patch",
    )


@router.delete("/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: UUID,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ctx: ScimContext = Depends(_scim_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    config = await _config_for_context(db, ctx)
    group = await _group_for_update(db, ctx, group_id)
    assert_if_match(if_match, group.version)
    affected_ids = await group_member_ids(
        db,
        config_id=config.id,
        group_id=group.id,
    )
    mapping = (
        await db.execute(
            select(ScimGroupEntitlement)
            .where(ScimGroupEntitlement.group_id == group.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if mapping is not None:
        mapping_id = mapping.id
        mapping_version = mapping.version
        await db.delete(mapping)
        await chain_log(
            db,
            namespace=config.namespace,
            agent_id=_SCIM_AGENT,
            op="scim.entitlement_delete_by_group",
            payload={
                "scim_group_id": str(group.id),
                "entitlement_id": str(mapping_id),
                "version": mapping_version,
            },
        )
    await db.execute(delete(ScimGroupMember).where(ScimGroupMember.group_id == group.id))
    group.deleted_at = utcnow()
    group.updated_at = utcnow()
    group.version += 1
    await db.flush()
    if affected_ids:
        provider = await db.get(TrustedIdentityProvider, config.provider_id)
        reconciliation_status = (
            await db.execute(
                select(ScimTenantReconciliationJob.status).where(
                    ScimTenantReconciliationJob.tenant_config_id == config.id,
                    ScimTenantReconciliationJob.target_config_version
                    == config.version,
                )
            )
        ).scalar_one_or_none()
        activation_fence_complete = reconciliation_status in {None, "completed"}
        for start in range(0, len(affected_ids), 400):
            users = list(
                (
                    await db.execute(
                        select(ScimUser)
                        .where(ScimUser.id.in_(affected_ids[start : start + 400]))
                        .order_by(ScimUser.id)
                        .with_for_update()
                    )
                ).scalars()
            )
            for user in users:
                await sync_user_binding(
                    db,
                    config=config,
                    user=user,
                    provider=provider,
                    provider_loaded=True,
                    activation_fence_complete=activation_fence_complete,
                )
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id=_SCIM_AGENT,
        op="scim.group_delete",
        payload={"scim_group_id": str(group.id), "version": group.version},
    )
    await db.commit()
    return Response(status_code=204)
