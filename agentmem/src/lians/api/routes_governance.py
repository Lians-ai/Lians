"""Admin policy lifecycle and tenant read-only governance visibility."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..governance_models import NamespacePolicyRevision
from ..governance_schemas import (
    EffectiveNamespaceGovernanceOut,
    NamespaceDailyUsageOut,
    NamespaceGovernancePolicyOut,
    NamespaceGovernancePolicyUpdate,
    NamespaceGovernanceStatusOut,
    NamespaceGovernanceStatusUpdate,
    NamespacePolicyRevisionOut,
)
from ..governance_service import (
    clear_governance_policy,
    get_effective_governance,
    governance_status,
    list_governance_policies,
    put_governance_policy,
    set_governance_status,
)
from ..models import NamespacePolicy
from .deps import AuthContext, get_auth
from .routes_admin import _ADMIN_AGENT, _require_admin

router = APIRouter(prefix="/v1/governance", tags=["namespace-governance"])
admin_router = APIRouter(prefix="/v1/admin/governance", tags=["admin", "governance"])
_MAX_LIST_OFFSET = 50_000
NamespacePath = Annotated[
    str,
    Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$"),
]


def _actor(value: str | None) -> str:
    actor = (value or _ADMIN_AGENT).strip()
    if not actor or len(actor) > 512 or any(character in actor for character in "\r\n"):
        raise HTTPException(
            status_code=422,
            detail="X-Admin-Actor must contain 1 to 512 characters without line breaks",
        )
    return actor


def _set_page_headers(
    response: Response,
    *,
    total: int,
    limit: int,
    returned: int,
    has_more: bool,
    offset: int | None = None,
    next_cursor: dict[str, str] | None = None,
) -> None:
    response.headers["X-Lians-Total-Count"] = str(total)
    response.headers["X-Lians-Page-Limit"] = str(limit)
    response.headers["X-Lians-Page-Returned"] = str(returned)
    response.headers["X-Lians-Page-Complete"] = str(not has_more).lower()
    response.headers["X-Lians-Has-More"] = str(has_more).lower()
    if offset is not None:
        response.headers["X-Lians-Page-Offset"] = str(offset)
    if has_more and next_cursor:
        for name, value in next_cursor.items():
            header_name = "-".join(part.capitalize() for part in name.split("_"))
            response.headers[f"X-Lians-Next-{header_name}"] = value


@router.get(
    "/effective",
    response_model=EffectiveNamespaceGovernanceOut,
    summary="Read the effective namespace policy and today's reserved usage",
)
async def effective_governance(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> EffectiveNamespaceGovernanceOut:
    auth.require("read")
    return await get_effective_governance(db, auth.namespace)


@router.get(
    "/usage",
    response_model=NamespaceDailyUsageOut,
    summary="Read today's namespace quota counters and remaining capacity",
)
async def daily_usage(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> NamespaceDailyUsageOut:
    auth.require("read")
    return (await get_effective_governance(db, auth.namespace)).usage


@admin_router.get(
    "/policies",
    response_model=list[NamespaceGovernancePolicyOut],
    summary="List namespace governance policies",
)
async def admin_list_policies(
    response: Response,
    include_unconfigured: bool = Query(False),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=_MAX_LIST_OFFSET),
    after_namespace: str | None = Query(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$",
    ),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[NamespaceGovernancePolicyOut]:
    if after_namespace is not None and offset:
        raise HTTPException(status_code=422, detail="offset cannot be combined with a cursor")
    count_statement = select(func.count()).select_from(NamespacePolicy)
    if not include_unconfigured:
        count_statement = count_statement.where(
            NamespacePolicy.governance_status != "unconfigured"
        )
    total = int((await db.execute(count_statement)).scalar_one())
    rows = await list_governance_policies(
        db,
        include_unconfigured=include_unconfigured,
        limit=limit + 1,
        offset=offset,
        after_namespace=after_namespace,
    )
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        next_cursor = {"after_namespace": page[-1].namespace}
    _set_page_headers(
        response,
        total=total,
        offset=offset,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        next_cursor=next_cursor,
    )
    return page


@admin_router.get(
    "/policies/{namespace}",
    response_model=NamespaceGovernancePolicyOut,
    summary="Get one configured namespace governance policy",
)
async def admin_get_policy(
    namespace: NamespacePath,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> NamespaceGovernancePolicyOut:
    policy = (await get_effective_governance(db, namespace)).policy
    if not policy.configured:
        raise HTTPException(status_code=404, detail="Namespace governance policy not found")
    return policy


@admin_router.put(
    "/policies/{namespace}",
    response_model=NamespaceGovernancePolicyOut,
    summary="Create or completely replace a namespace governance policy",
)
async def admin_put_policy(
    namespace: NamespacePath,
    body: NamespaceGovernancePolicyUpdate,
    admin_actor: Annotated[str | None, Header(alias="X-Admin-Actor")] = None,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> NamespaceGovernancePolicyOut:
    return await put_governance_policy(db, namespace, body, _actor(admin_actor))


@admin_router.put(
    "/policies/{namespace}/status",
    response_model=NamespaceGovernancePolicyOut,
    summary="Enable or disable a configured namespace governance policy",
)
async def admin_set_policy_status(
    namespace: NamespacePath,
    body: NamespaceGovernanceStatusUpdate,
    admin_actor: Annotated[str | None, Header(alias="X-Admin-Actor")] = None,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> NamespaceGovernancePolicyOut:
    return await set_governance_status(
        db,
        namespace,
        body.status,
        _actor(admin_actor),
        body.expected_version,
    )


@admin_router.delete(
    "/policies/{namespace}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear governance fields while preserving retention and billing policy",
)
async def admin_clear_policy(
    namespace: NamespacePath,
    expected_version: int = Query(..., ge=1),
    admin_actor: Annotated[str | None, Header(alias="X-Admin-Actor")] = None,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await clear_governance_policy(
        db,
        namespace,
        _actor(admin_actor),
        expected_version,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.get(
    "/status/{namespace}",
    response_model=NamespaceGovernanceStatusOut,
    summary="Inspect effective enforcement, usage, and immutable revision state",
)
async def admin_governance_status(
    namespace: NamespacePath,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> NamespaceGovernanceStatusOut:
    return await governance_status(db, namespace)


@admin_router.get(
    "/policies/{namespace}/revisions",
    response_model=list[NamespacePolicyRevisionOut],
    summary="List immutable governance policy revisions",
)
async def admin_list_policy_revisions(
    namespace: NamespacePath,
    response: Response,
    limit: int = Query(100, ge=1, le=1000),
    before_policy_version: int | None = Query(default=None, ge=1),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[NamespacePolicyRevisionOut]:
    filters = [NamespacePolicyRevision.namespace == namespace]
    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(NamespacePolicyRevision)
                .where(*filters)
            )
        ).scalar_one()
    )
    if before_policy_version is not None:
        filters.append(NamespacePolicyRevision.policy_version < before_policy_version)
    rows = (
        (
            await db.execute(
                select(NamespacePolicyRevision)
                .where(*filters)
                .order_by(NamespacePolicyRevision.policy_version.desc())
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        next_cursor = {"before_policy_version": str(page[-1].policy_version)}
    _set_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        next_cursor=next_cursor,
    )
    return [NamespacePolicyRevisionOut.model_validate(row) for row in page]
