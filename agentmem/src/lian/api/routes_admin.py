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
from ..models import ApiKey, AgentBarrierGroup, NamespacePolicy
from ..schemas import (
    ApiKeyCreate, ApiKeyCreated, ApiKeyOut,
    BarrierGroupAssign, BarrierGroupOut,
    RetentionPolicyIn, RetentionPolicyOut, RetentionPruneResult,
    AuditChainVerifyResult, AuditExportResult,
    NamespaceBillingIn, NamespaceBillingOut,
)
from ..memory_service import get_retention_policy, set_retention_policy, prune_expired_content
from ..audit_chain import verify_chain, export_audit_log, chain_log

router = APIRouter(prefix="/v1/admin", tags=["admin"])

_admin_header = APIKeyHeader(name="X-Admin-Secret", auto_error=False)
_ADMIN_AGENT = "__admin__"


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
    await db.flush()
    await chain_log(
        db, namespace=body.namespace, agent_id=_ADMIN_AGENT,
        op="admin.key_provision",
        payload={"key_id": str(row.id), "label": body.label, "scopes": list(body.scopes)},
    )
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
    await chain_log(
        db, namespace=row.namespace, agent_id=_ADMIN_AGENT,
        op="admin.key_revoke",
        payload={"key_id": str(key_id), "label": row.label},
    )
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
    await db.flush()
    await chain_log(
        db, namespace=old.namespace, agent_id=_ADMIN_AGENT,
        op="admin.key_rotate",
        payload={"old_key_id": str(key_id), "new_key_id": str(new_row.id), "label": old.label},
    )
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


# ── Information Barrier Group Management ────────────────────────────────────

@router.post(
    "/barriers",
    response_model=BarrierGroupOut,
    status_code=status.HTTP_201_CREATED,
    summary="Assign an agent to an information barrier group",
)
async def assign_barrier_group(
    body: BarrierGroupAssign,
    namespace: str = Query(..., description="Target namespace"),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> BarrierGroupOut:
    """
    Assign an agent to a Chinese-wall barrier group.

    After this call, the agent can only recall memories tagged with the same
    group_name (or untagged public memories).  Memories written by this agent
    will be tagged with group_name automatically.

    To grant compliance-officer access (see all memories), do NOT assign the
    agent to any group — unassigned agents see everything in the namespace.

    Example barrier groups:  equity_desk, fixed_income, investment_banking
    """
    # Upsert: if the agent already has a group, replace it
    existing = await db.get(AgentBarrierGroup, body.agent_id)
    if existing and existing.namespace == namespace:
        existing.group_name = body.group_name
        row = existing
    else:
        row = AgentBarrierGroup(
            agent_id=body.agent_id,
            namespace=namespace,
            group_name=body.group_name,
        )
        db.add(row)
    await chain_log(
        db, namespace=namespace, agent_id=_ADMIN_AGENT,
        op="admin.barrier_assign",
        payload={"agent_id": body.agent_id, "group_name": body.group_name},
    )
    await db.commit()
    await db.refresh(row)
    return BarrierGroupOut.model_validate(row)


@router.get(
    "/barriers",
    response_model=list[BarrierGroupOut],
    summary="List information barrier group assignments",
)
async def list_barrier_groups(
    namespace: str = Query(..., description="Target namespace"),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[BarrierGroupOut]:
    stmt = select(AgentBarrierGroup).where(AgentBarrierGroup.namespace == namespace)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [BarrierGroupOut.model_validate(r) for r in rows]


@router.delete(
    "/barriers/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an agent from its barrier group (grants full-namespace access)",
)
async def remove_barrier_group(
    agent_id: str,
    namespace: str = Query(..., description="Target namespace"),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    row = await db.get(AgentBarrierGroup, agent_id)
    if row is None or row.namespace != namespace:
        raise HTTPException(status_code=404, detail="Barrier group assignment not found")
    await db.delete(row)
    await chain_log(
        db, namespace=namespace, agent_id=_ADMIN_AGENT,
        op="admin.barrier_remove",
        payload={"agent_id": agent_id},
    )
    await db.commit()
    return Response(status_code=204)


# ── Retention & Compliance Policy ───────────────────────────────────────────

@router.get(
    "/retention/{namespace}",
    response_model=RetentionPolicyOut,
    summary="Get the retention policy for a namespace",
)
async def get_retention(
    namespace: str,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> RetentionPolicyOut:
    """
    Return the content TTL and audit retention settings for a namespace.

    Default policy (auto-created on first fetch):
      - content_ttl_days: None (retain forever)
      - audit_retention_days: 1825 (5 years — CFTC swap dealer minimum)
      - legal_hold: False
    """
    return await get_retention_policy(db, namespace)


@router.put(
    "/retention/{namespace}",
    response_model=RetentionPolicyOut,
    summary="Set or update the retention policy for a namespace",
)
async def set_retention(
    namespace: str,
    body: RetentionPolicyIn,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> RetentionPolicyOut:
    """
    Upsert the retention policy.

    Setting `legal_hold: true` blocks any automated pruning on this namespace
    until the hold is explicitly lifted (litigation hold pattern).

    Setting `content_ttl_days` to a value means memories older than N days will
    have their content erased by the next prune run.  The content_hash audit
    record is preserved (SEC 17a-4 / CFTC compliance).
    """
    return await set_retention_policy(db, namespace, body)


@router.post(
    "/retention/{namespace}/prune",
    response_model=RetentionPruneResult,
    summary="Immediately prune expired memory content for a namespace",
)
async def run_prune(
    namespace: str,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> RetentionPruneResult:
    """
    Erase the encrypted content of memories whose age exceeds content_ttl_days.

    Returns 409 if the namespace is under legal hold.
    Returns 0 memories_pruned if content_ttl_days is not set.

    Each pruned memory writes a `retention_prune` event to the immutable audit
    log so regulators can confirm the content was destroyed per policy.
    """
    return await prune_expired_content(db, namespace)


# ── Audit chain verification ─────────────────────────────────────────────────

@router.get(
    "/audit/verify",
    response_model=AuditChainVerifyResult,
    summary="Verify the SEC 17a-4 tamper-evidence hash chain for a namespace",
)
async def verify_audit_chain(
    namespace: str = Query(..., description="Namespace to verify"),
    limit: int = Query(default=50_000, ge=1, le=500_000, description="Max rows to inspect"),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AuditChainVerifyResult:
    """
    Walk the event_log hash chain for *namespace* and report any tampering.

    For each row the verifier recomputes SHA-256(prev_hash || row fields) and
    compares it to the stored row_hash.  A mismatch means the row was modified
    after it was written.  An orphaned prev_hash means a row was deleted from
    the middle of the chain.

    Returns `{"status": "ok"}` when the chain is intact.
    Returns `{"status": "tampered", "violations": [...]}` with details
    identifying every broken link — suitable for regulatory examination.

    Rows written before migration 0006 (which added the hash columns) have
    NULL hashes and are skipped rather than reported as violations.
    """
    report = await verify_chain(db, namespace=namespace, limit=limit)
    return AuditChainVerifyResult(**report)


# ── Audit log export ─────────────────────────────────────────────────────────

@router.get(
    "/audit/export",
    response_model=AuditExportResult,
    summary="Export the full audit log for a namespace (for SEC/FINRA/CFTC examiners)",
)
async def export_audit(
    namespace: str = Query(..., description="Namespace to export"),
    from_: Optional[datetime] = Query(
        default=None, alias="from",
        description="Lower bound on created_at (inclusive).  ISO-8601 UTC.",
    ),
    to: Optional[datetime] = Query(
        default=None,
        description="Upper bound on created_at (inclusive).  ISO-8601 UTC.",
    ),
    limit: int = Query(
        default=100_000, ge=1, le=1_000_000,
        description="Hard cap on rows returned.  Paginate via from/to if needed.",
    ),
    verify: bool = Query(
        default=False,
        description=(
            "When true, also runs the hash-chain verifier and includes chain_status "
            "and chain_violations in the response.  Adds one extra table scan."
        ),
    ),
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AuditExportResult:
    """
    Export event_log rows for *namespace* ordered chronologically.

    Designed for regulatory examiners (SEC, FINRA, CFTC) who need the full
    immutable audit trail for a namespace within a date range.

    **Pagination:** The default limit is 100 000 rows.  For namespaces with more
    activity, paginate by setting `from` to the `created_at` of the last row
    in the previous response.

    **Chain verification:** Pass `verify=true` to include a tamper-evidence
    report alongside the export data.  This runs `verify_chain()` over the full
    namespace (not just the exported window) so the examiner gets a complete
    chain-of-custody verdict.

    **Output fields per event:**
    - `id`, `namespace`, `agent_id`, `op` — who did what
    - `memory_id`, `content_hash` — which memory row was affected
    - `payload` — operation-specific context (e.g. superseded_by, query_hash)
    - `created_at` — when the event was ingested (UTC)
    - `prev_hash`, `row_hash` — hash-chain links for independent verification
    """
    data = await export_audit_log(
        db,
        namespace=namespace,
        from_dt=from_,
        to_dt=to,
        limit=limit,
        include_chain_status=verify,
    )
    return AuditExportResult(**data)


# ── Stripe usage metering ────────────────────────────────────────────────────

@router.get(
    "/billing/{namespace}",
    response_model=NamespaceBillingOut,
    summary="Get the Stripe customer ID assigned to a namespace",
)
async def get_billing(
    namespace: str,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> NamespaceBillingOut:
    """
    Return the Stripe customer ID wired to *namespace*.

    When stripe_customer_id is null the namespace is not metered — writes and
    recalls are not reported to Stripe regardless of STRIPE_API_KEY.
    """
    pol = await db.get(NamespacePolicy, namespace)
    return NamespaceBillingOut(
        namespace=namespace,
        stripe_customer_id=pol.stripe_customer_id if pol else None,
    )


@router.put(
    "/billing/{namespace}",
    response_model=NamespaceBillingOut,
    summary="Set or clear the Stripe customer ID for a namespace",
)
async def set_billing(
    namespace: str,
    body: NamespaceBillingIn,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> NamespaceBillingOut:
    """
    Assign a Stripe Customer ID to *namespace*.

    After this call all memory writes and recalls in the namespace are metered
    via Stripe Meters API.  Set stripe_customer_id to null to stop billing.

    The customer ID is cached for up to 60 s in each worker process; billing
    starts within one minute of this call without requiring a restart.
    """
    from ..metering import invalidate_customer_cache

    pol = await db.get(NamespacePolicy, namespace)
    if pol is None:
        pol = NamespacePolicy(namespace=namespace)
        db.add(pol)
    pol.stripe_customer_id = body.stripe_customer_id
    await chain_log(
        db, namespace=namespace, agent_id=_ADMIN_AGENT,
        op="admin.billing_set",
        payload={"stripe_customer_id": body.stripe_customer_id},
    )
    await db.commit()
    await db.refresh(pol)
    invalidate_customer_cache(namespace)
    return NamespaceBillingOut(
        namespace=namespace,
        stripe_customer_id=pol.stripe_customer_id,
    )
