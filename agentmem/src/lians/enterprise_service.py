"""Security-sensitive services for SCIM credential and identity provisioning."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import chain_log
from .barrier_policy import is_reserved_barrier_group
from .db import set_current_barrier_group, set_current_namespace
from .enterprise_models import (
    ScimBearerCredential,
    ScimGroup,
    ScimGroupEntitlement,
    ScimGroupMember,
    ScimTenantConfig,
    ScimTenantReconciliationJob,
    ScimUser,
)
from .enterprise_schemas import (
    SCIM_EFFECTIVE_SCOPE_LIMIT,
    SCIM_GROUP_MEMBER_LIMIT,
    SCIM_USER_GROUP_LIMIT,
    ScimGroupOut,
    ScimGroupWrite,
    ScimMeta,
    ScimPatchRequest,
    ScimUserOut,
    ScimUserWrite,
)
from .identity_models import IdentityBinding, TrustedIdentityProvider


_SCIM_AGENT = "__scim__"
_SCIM_DB_BATCH = 400
_FILTER_RE = re.compile(
    r'^\s*(userName|externalId|displayName)\s+eq\s+("(?:[^"\\]|\\.)*")\s*$',
    re.IGNORECASE,
)
_MEMBER_FILTER_RE = re.compile(
    r'^members\s*\[\s*value\s+eq\s+("(?:[^"\\]|\\.)*")\s*\]\s*$',
    re.IGNORECASE,
)


class ProvisioningError(Exception):
    """Safe, protocol-neutral provisioning failure."""

    def __init__(self, status_code: int, detail: str, *, scim_type: str | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.scim_type = scim_type


@dataclass(frozen=True)
class ScimContext:
    tenant_id: UUID
    namespace: str
    provider_id: UUID
    subject_attribute: str
    credential_id: UUID


@dataclass(frozen=True)
class EffectiveProvisionedAuthorization:
    role: str | None
    scopes: tuple[str, ...]
    barrier_group: str | None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def subject_fingerprint(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()


def generate_bearer_token() -> str:
    return "lians_scim_" + secrets.token_urlsafe(48)


def make_credential(
    config: ScimTenantConfig,
    *,
    label: str | None,
    expires_at: datetime | None,
    rotated_from_id: UUID | None = None,
) -> tuple[ScimBearerCredential, str]:
    raw = generate_bearer_token()
    digest = token_hash(raw)
    credential = ScimBearerCredential(
        tenant_config_id=config.id,
        namespace=config.namespace,
        token_hash=digest,
        # A digest hint supports operator identification without retaining even
        # a fragment of the bearer secret.
        token_hint=f"sha256:{digest[:12]}",
        label=label,
        expires_at=expires_at,
        rotated_from_id=rotated_from_id,
    )
    return credential, raw


async def authenticate_scim(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    raw_token: str | None,
) -> ScimContext:
    """Authenticate without allowing a token to select any other tenant.

    SCIM tables use FORCE RLS. Authentication briefly establishes the internal
    admin sentinel solely to find the tenant-and-digest pair, ends that
    transaction, then constrains every subsequent transaction to the resolved
    namespace. The untrusted caller never supplies the namespace context.
    """
    if not raw_token or len(raw_token) > 1024 or not raw_token.startswith("lians_scim_"):
        raise ProvisioningError(401, "Invalid or missing SCIM bearer credential")

    digest = token_hash(raw_token)
    set_current_namespace("__admin__")
    set_current_barrier_group(None)
    resolved: dict[str, Any] | None = None
    try:
        stmt = (
            select(ScimBearerCredential, ScimTenantConfig)
            .join(
                ScimTenantConfig,
                ScimTenantConfig.id == ScimBearerCredential.tenant_config_id,
            )
            .where(
                ScimBearerCredential.tenant_config_id == tenant_id,
                ScimBearerCredential.token_hash == digest,
            )
        )
        pair = (await db.execute(stmt)).one_or_none()
        if pair is not None:
            credential, config = pair
            resolved = {
                "credential_id": credential.id,
                "credential_hash": credential.token_hash,
                "credential_namespace": credential.namespace,
                "credential_revoked_at": credential.revoked_at,
                "credential_expires_at": credential.expires_at,
                "tenant_id": config.id,
                "namespace": config.namespace,
                "provider_id": config.provider_id,
                "subject_attribute": config.subject_attribute,
                "enabled": config.enabled,
                "tenant_revoked_at": config.revoked_at,
            }
    finally:
        # End the privileged lookup transaction before changing its RLS context.
        await db.rollback()

    if resolved is None:
        set_current_namespace(None)
        raise ProvisioningError(401, "Invalid or missing SCIM bearer credential")
    now = utcnow()
    valid = (
        secrets.compare_digest(resolved["credential_hash"], digest)
        and resolved["credential_namespace"] == resolved["namespace"]
        and resolved["credential_revoked_at"] is None
        and (
            resolved["credential_expires_at"] is None
            or _aware(resolved["credential_expires_at"]) > now
        )
        and resolved["enabled"]
        and resolved["tenant_revoked_at"] is None
    )
    if not valid:
        set_current_namespace(None)
        raise ProvisioningError(401, "Invalid or missing SCIM bearer credential")

    set_current_namespace(resolved["namespace"])
    set_current_barrier_group(None)
    return ScimContext(
        tenant_id=resolved["tenant_id"],
        namespace=resolved["namespace"],
        provider_id=resolved["provider_id"],
        subject_attribute=resolved["subject_attribute"],
        credential_id=resolved["credential_id"],
    )


def parse_filter(raw_filter: str | None, *, resource_type: str) -> tuple[str, str] | None:
    if raw_filter is None:
        return None
    match = _FILTER_RE.fullmatch(raw_filter)
    if match is None:
        raise ProvisioningError(
            400,
            "Only equality filters for userName, externalId, and displayName are supported",
            scim_type="invalidFilter",
        )
    field = match.group(1)
    canonical = {
        "username": "userName",
        "externalid": "externalId",
        "displayname": "displayName",
    }[field.casefold()]
    if resource_type == "Group" and canonical == "userName":
        raise ProvisioningError(
            400,
            "userName is not a Group attribute",
            scim_type="invalidFilter",
        )
    try:
        value = json.loads(match.group(2))
    except json.JSONDecodeError as exc:
        raise ProvisioningError(400, "Malformed filter string", scim_type="invalidFilter") from exc
    if len(value) > 512:
        raise ProvisioningError(400, "Filter value exceeds the size limit", scim_type="invalidFilter")
    return canonical, value


async def effective_authorization(
    db: AsyncSession,
    *,
    config: ScimTenantConfig,
    user_id: UUID,
) -> EffectiveProvisionedAuthorization:
    membership_total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ScimGroupMember)
                .where(
                    ScimGroupMember.user_id == user_id,
                    ScimGroupMember.tenant_config_id == config.id,
                )
            )
        ).scalar_one()
    )
    if membership_total > SCIM_USER_GROUP_LIMIT:
        raise ProvisioningError(
            413,
            "User Group membership exceeds the complete authorization "
            f"reconciliation limit of {SCIM_USER_GROUP_LIMIT}; no Group "
            "contribution was omitted",
            scim_type="tooMany",
        )
    stmt = (
        select(ScimGroupEntitlement)
        .join(ScimGroupMember, ScimGroupMember.group_id == ScimGroupEntitlement.group_id)
        .join(ScimGroup, ScimGroup.id == ScimGroupEntitlement.group_id)
        .where(
            ScimGroupMember.user_id == user_id,
            ScimGroupMember.tenant_config_id == config.id,
            ScimGroupEntitlement.tenant_config_id == config.id,
            ScimGroup.deleted_at.is_(None),
        )
        .order_by(ScimGroupEntitlement.group_id)
    )
    mappings = list(
        (await db.execute(stmt.limit(SCIM_USER_GROUP_LIMIT + 1))).scalars()
    )
    if len(mappings) > SCIM_USER_GROUP_LIMIT:
        raise ProvisioningError(
            413,
            "User Group entitlements exceed the complete authorization "
            f"reconciliation limit of {SCIM_USER_GROUP_LIMIT}; no Group "
            "contribution was omitted",
            scim_type="tooMany",
        )
    role_set: set[str] = set()
    barrier_set: set[str] = set()
    for mapping in mappings:
        if mapping.role is not None:
            if mapping.role not in {"owner", "analyst", "compliance", "readonly"}:
                raise ProvisioningError(
                    409,
                    "A Group entitlement has an invalid role mapping",
                    scim_type="invalidValue",
                )
            role_set.add(mapping.role)
        if mapping.barrier_group is not None:
            barrier = mapping.barrier_group
            if (
                not isinstance(barrier, str)
                or not barrier
                or len(barrier) > 255
                or barrier != barrier.strip()
                or is_reserved_barrier_group(barrier)
            ):
                raise ProvisioningError(
                    409,
                    "A Group entitlement has an invalid information-barrier mapping",
                    scim_type="invalidValue",
                )
            barrier_set.add(barrier)
    roles = sorted(role_set)
    barriers = sorted(barrier_set)
    if len(roles) > 1:
        raise ProvisioningError(
            409,
            "Group membership produces conflicting role mappings",
            scim_type="uniqueness",
        )
    if len(barriers) > 1:
        raise ProvisioningError(
            409,
            "Group membership produces conflicting information-barrier mappings",
            scim_type="uniqueness",
        )
    scope_set: set[str] = set()
    for mapping in mappings:
        mapping_scopes = mapping.scopes or []
        if (
            not isinstance(mapping_scopes, list)
            or len(mapping_scopes) > SCIM_EFFECTIVE_SCOPE_LIMIT
        ):
            raise ProvisioningError(
                409,
                "A Group entitlement has an invalid scope mapping",
                scim_type="invalidValue",
            )
        for scope in mapping_scopes:
            if (
                not isinstance(scope, str)
                or not scope
                or len(scope) > 100
                or not all(ch.isalnum() or ch in "_.:-" for ch in scope)
            ):
                raise ProvisioningError(
                    409,
                    "A Group entitlement has an invalid scope mapping",
                    scim_type="invalidValue",
                )
            scope_set.add(scope)
            if len(scope_set) > SCIM_EFFECTIVE_SCOPE_LIMIT:
                raise ProvisioningError(
                    409,
                    "Group membership produces more than "
                    f"{SCIM_EFFECTIVE_SCOPE_LIMIT} distinct scope mappings; "
                    "no authorization contribution was omitted",
                    scim_type="tooMany",
                )
    scopes = sorted(scope_set)
    return EffectiveProvisionedAuthorization(
        role=roles[0] if roles else None,
        scopes=tuple(scopes),
        barrier_group=barriers[0] if barriers else None,
    )


async def _audit_binding_change(
    db: AsyncSession,
    *,
    config: ScimTenantConfig,
    user: ScimUser,
    binding: IdentityBinding,
    operation: str,
    subject: str,
) -> None:
    await chain_log(
        db,
        namespace=config.namespace,
        agent_id=_SCIM_AGENT,
        op=operation,
        payload={
            "scim_user_id": str(user.id),
            "binding_id": str(binding.id),
            "provider_id": str(config.provider_id),
            "subject_sha256": subject_fingerprint(subject),
            "binding_version": binding.version,
        },
    )


async def sync_user_binding(
    db: AsyncSession,
    *,
    config: ScimTenantConfig,
    user: ScimUser,
    provider: TrustedIdentityProvider | None = None,
    provider_loaded: bool = False,
    activation_fence_complete: bool | None = None,
) -> None:
    """Reconcile exactly one SCIM user into the native identity binding table."""
    if not provider_loaded:
        provider = await db.get(TrustedIdentityProvider, config.provider_id)
    desired_subject = (
        user.external_id if config.subject_attribute == "externalId" else user.user_name
    )
    current = None
    if user.identity_binding_id:
        current = (
            await db.execute(
                select(IdentityBinding)
                .where(IdentityBinding.id == user.identity_binding_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    should_be_active = bool(
        user.active
        and user.deleted_at is None
        and config.enabled
        and config.revoked_at is None
    )
    if provider is None or not provider.enabled or provider.revoked_at is not None:
        should_be_active = False

    if not should_be_active:
        if current is not None and current.enabled:
            current.enabled = False
            current.version += 1
            current.updated_at = utcnow()
            await _audit_binding_change(
                db,
                config=config,
                user=user,
                binding=current,
                operation="scim.binding_disable",
                subject=current.external_subject,
            )
        return

    if not desired_subject:
        raise ProvisioningError(
            409,
            f"An active user requires {config.subject_attribute} for identity binding",
            scim_type="invalidValue",
        )

    if activation_fence_complete is None:
        reconciliation_status = (
            await db.execute(
                select(ScimTenantReconciliationJob.status).where(
                    ScimTenantReconciliationJob.tenant_config_id == config.id,
                    ScimTenantReconciliationJob.target_config_version
                    == config.version,
                )
            )
        ).scalar_one_or_none()
        # No job means a pre-0062 or never-version-mutated tenant. Preserve that
        # compatibility boundary; once a job exists, only completed activates.
        activation_fence_complete = reconciliation_status in {None, "completed"}

    # A subject-attribute change must remove access from the old subject first.
    if current is not None and (
        current.provider_id != config.provider_id
        or current.external_subject != desired_subject
        or current.namespace != config.namespace
    ):
        if current.enabled:
            current.enabled = False
            current.version += 1
            current.updated_at = utcnow()
            await _audit_binding_change(
                db,
                config=config,
                user=user,
                binding=current,
                operation="scim.binding_subject_replaced",
                subject=current.external_subject,
            )
        user.identity_binding_id = None
        current = None

    target = (
        await db.execute(
            select(IdentityBinding)
            .where(
                IdentityBinding.provider_id == config.provider_id,
                IdentityBinding.external_subject == desired_subject,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if target is not None:
        if (
            target.namespace != config.namespace
            or target.principal_type != "human"
            or target.authorized_party is not None
        ):
            raise ProvisioningError(
                409,
                "The external subject is already bound to an incompatible principal",
                scim_type="uniqueness",
            )
        if target.revoked_at is not None:
            raise ProvisioningError(
                409,
                "The external subject has a revoked identity binding",
                scim_type="invalidValue",
            )
        other_user = (
            await db.execute(
                select(ScimUser.id).where(
                    ScimUser.identity_binding_id == target.id,
                    ScimUser.id != user.id,
                    ScimUser.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if other_user is not None:
            raise ProvisioningError(
                409,
                "The external subject is already mapped to another SCIM user",
                scim_type="uniqueness",
            )
    authorization = await effective_authorization(db, config=config, user_id=user.id)
    authorized = bool(authorization.role or authorization.scopes)
    if target is None:
        target = IdentityBinding(
            provider_id=config.provider_id,
            external_subject=desired_subject,
            namespace=config.namespace,
            principal_type="human",
            display_name=user.display_name or user.user_name,
            role=authorization.role,
            scopes=list(authorization.scopes),
            barrier_group=authorization.barrier_group,
            authorized_party=None,
            scim_tenant_config_id=config.id,
            scim_tenant_config_version=config.version,
            scim_reconciliation_complete=activation_fence_complete,
            enabled=authorized,
        )
        db.add(target)
        await db.flush()
        user.identity_binding_id = target.id
        await _audit_binding_change(
            db,
            config=config,
            user=user,
            binding=target,
            operation="scim.binding_create",
            subject=desired_subject,
        )
        return

    user.identity_binding_id = target.id
    desired = {
        "display_name": user.display_name or user.user_name,
        "role": authorization.role,
        "scopes": list(authorization.scopes),
        "barrier_group": authorization.barrier_group,
        "enabled": authorized,
        "scim_tenant_config_id": config.id,
        "scim_tenant_config_version": config.version,
        "scim_reconciliation_complete": activation_fence_complete,
    }
    changed = any(getattr(target, field) != value for field, value in desired.items())
    if changed:
        for field, value in desired.items():
            setattr(target, field, value)
        target.revoked_at = None
        target.version += 1
        target.updated_at = utcnow()
        await _audit_binding_change(
            db,
            config=config,
            user=user,
            binding=target,
            operation="scim.binding_reconcile",
            subject=desired_subject,
        )


async def set_group_members(
    db: AsyncSession,
    *,
    config: ScimTenantConfig,
    group: ScimGroup,
    desired_ids: Iterable[UUID],
) -> None:
    desired: set[UUID] = set()
    for member_id in desired_ids:
        desired.add(member_id)
        if len(desired) > SCIM_GROUP_MEMBER_LIMIT:
            raise ProvisioningError(
                400,
                f"Group membership exceeds the limit of {SCIM_GROUP_MEMBER_LIMIT}",
                scim_type="invalidValue",
            )
    existing_rows = list(
        (
            await db.execute(
                select(ScimGroupMember.user_id)
                .where(
                    ScimGroupMember.group_id == group.id,
                    ScimGroupMember.tenant_config_id == config.id,
                )
                .order_by(ScimGroupMember.user_id)
                .limit(SCIM_GROUP_MEMBER_LIMIT + 1)
            )
        ).scalars()
    )
    if len(existing_rows) > SCIM_GROUP_MEMBER_LIMIT:
        raise ProvisioningError(
            413,
            "Existing Group membership exceeds the complete-resource response "
            f"limit of {SCIM_GROUP_MEMBER_LIMIT}; no members were omitted",
            scim_type="tooMany",
        )
    existing = set(existing_rows)
    if desired:
        valid: set[UUID] = set()
        ordered_desired = sorted(desired, key=str)
        for start in range(0, len(ordered_desired), _SCIM_DB_BATCH):
            valid.update(
                (
                    await db.execute(
                        select(ScimUser.id).where(
                            ScimUser.id.in_(
                                ordered_desired[start : start + _SCIM_DB_BATCH]
                            ),
                            ScimUser.tenant_config_id == config.id,
                            ScimUser.deleted_at.is_(None),
                        )
                    )
                ).scalars()
            )
        if valid != desired:
            raise ProvisioningError(
                400,
                "One or more Group members are not active Users in this tenant",
                scim_type="invalidValue",
            )
    remove_ids = existing - desired
    add_ids = desired - existing
    if add_ids:
        # Every supported SCIM mutation already holds the tenant configuration
        # row as its transaction mutex.  This exact count therefore gives the
        # caller a protocol-shaped failure before writes, while the PostgreSQL
        # trigger installed by 0056b remains the authoritative defense against
        # non-HTTP and concurrent writers.
        ordered_add = sorted(add_ids, key=str)
        for start in range(0, len(ordered_add), _SCIM_DB_BATCH):
            counts = (
                await db.execute(
                    select(
                        ScimGroupMember.user_id,
                        func.count().label("membership_total"),
                    )
                    .where(
                        ScimGroupMember.tenant_config_id == config.id,
                        ScimGroupMember.user_id.in_(
                            ordered_add[start : start + _SCIM_DB_BATCH]
                        ),
                    )
                    .group_by(ScimGroupMember.user_id)
                )
            ).all()
            if any(
                int(row.membership_total) >= SCIM_USER_GROUP_LIMIT
                for row in counts
            ):
                raise ProvisioningError(
                    409,
                    "Adding this Group would exceed the per-User Group "
                    f"membership limit of {SCIM_USER_GROUP_LIMIT}; no "
                    "membership was changed",
                    scim_type="tooMany",
                )
    if remove_ids:
        ordered_remove = sorted(remove_ids, key=str)
        for start in range(0, len(ordered_remove), _SCIM_DB_BATCH):
            await db.execute(
                delete(ScimGroupMember).where(
                    ScimGroupMember.group_id == group.id,
                    ScimGroupMember.user_id.in_(
                        ordered_remove[start : start + _SCIM_DB_BATCH]
                    ),
                )
            )
    for user_id in sorted(add_ids, key=str):
        db.add(
            ScimGroupMember(
                group_id=group.id,
                user_id=user_id,
                tenant_config_id=config.id,
                namespace=config.namespace,
            )
        )
    await db.flush()

    affected_ids = existing | desired
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
        ordered_affected = sorted(affected_ids, key=str)
        for start in range(0, len(ordered_affected), _SCIM_DB_BATCH):
            affected = list(
                (
                    await db.execute(
                        select(ScimUser)
                        .where(
                            ScimUser.id.in_(
                                ordered_affected[start : start + _SCIM_DB_BATCH]
                            )
                        )
                        .order_by(ScimUser.id)
                        .with_for_update()
                    )
                ).scalars()
            )
            for user in affected:
                await sync_user_binding(
                    db,
                    config=config,
                    user=user,
                    provider=provider,
                    provider_loaded=True,
                    activation_fence_complete=activation_fence_complete,
                )


async def group_member_documents(
    db: AsyncSession, *, config_id: UUID, group_id: UUID
) -> list[dict[str, Any]]:
    stmt = (
        select(ScimUser.id, ScimUser.display_name, ScimUser.user_name)
        .join(ScimGroupMember, ScimGroupMember.user_id == ScimUser.id)
        .where(
            ScimGroupMember.group_id == group_id,
            ScimGroupMember.tenant_config_id == config_id,
            ScimUser.deleted_at.is_(None),
        )
        .order_by(ScimUser.id)
    )
    rows = (await db.execute(stmt.limit(SCIM_GROUP_MEMBER_LIMIT + 1))).all()
    if len(rows) > SCIM_GROUP_MEMBER_LIMIT:
        raise ProvisioningError(
            413,
            "Group membership exceeds the complete-resource response limit of "
            f"{SCIM_GROUP_MEMBER_LIMIT}; no members were omitted",
            scim_type="tooMany",
        )
    return [
        {
            "value": str(row.id),
            "$ref": f"/scim/v2/{config_id}/Users/{row.id}",
            "display": row.display_name or row.user_name,
            "type": "User",
        }
        for row in rows
    ]


async def batch_group_member_documents(
    db: AsyncSession,
    *,
    config_id: UUID,
    group_ids: Iterable[UUID],
    cumulative_row_limit: int,
    cumulative_byte_limit: int,
) -> tuple[dict[UUID, list[dict[str, Any]]], int]:
    """Load a complete page's Group members without per-Group queries.

    Per-Group counts and the cumulative page count are preflighted before any
    member projection is returned. The byte counter covers the exact compact
    UTF-8 JSON representation of every expanded member document, including
    array separators, so callers either receive all members or an error.
    """

    ordered_group_ids = tuple(dict.fromkeys(group_ids))
    documents: dict[UUID, list[dict[str, Any]]] = {
        group_id: [] for group_id in ordered_group_ids
    }
    if not ordered_group_ids:
        return documents, 0
    if len(ordered_group_ids) > 100:
        raise ProvisioningError(
            413,
            "Group page exceeds the bounded membership expansion contract",
            scim_type="tooMany",
        )
    counts = (
        await db.execute(
            select(
                ScimGroupMember.group_id,
                func.count(ScimGroupMember.user_id).label("member_count"),
            )
            .join(ScimUser, ScimUser.id == ScimGroupMember.user_id)
            .where(
                ScimGroupMember.tenant_config_id == config_id,
                ScimGroupMember.group_id.in_(ordered_group_ids),
                ScimUser.deleted_at.is_(None),
            )
            .group_by(ScimGroupMember.group_id)
        )
    ).all()
    total = 0
    for row in counts:
        count = int(row.member_count)
        if count > SCIM_GROUP_MEMBER_LIMIT:
            raise ProvisioningError(
                413,
                "A Group in this page exceeds the complete-resource response "
                f"limit of {SCIM_GROUP_MEMBER_LIMIT}; no members were omitted",
                scim_type="tooMany",
            )
        total += count
    if total > cumulative_row_limit:
        raise ProvisioningError(
            413,
            "The requested Group page contains too many members for one complete "
            f"response ({total} > {cumulative_row_limit}); request a smaller page",
            scim_type="tooMany",
        )
    rows = (
        await db.execute(
            select(
                ScimGroupMember.group_id,
                ScimUser.id,
                ScimUser.display_name,
                ScimUser.user_name,
            )
            .join(ScimUser, ScimUser.id == ScimGroupMember.user_id)
            .where(
                ScimGroupMember.tenant_config_id == config_id,
                ScimGroupMember.group_id.in_(ordered_group_ids),
                ScimUser.deleted_at.is_(None),
            )
            .order_by(ScimGroupMember.group_id, ScimUser.id)
            .limit(cumulative_row_limit + 1)
        )
    ).all()
    if len(rows) != total:
        raise ProvisioningError(
            409,
            "Group membership changed while the complete page was expanded; retry the read",
        )
    serialized_bytes = 0
    seen_by_group: dict[UUID, int] = {group_id: 0 for group_id in ordered_group_ids}
    for row in rows:
        document = {
            "value": str(row.id),
            "$ref": f"/scim/v2/{config_id}/Users/{row.id}",
            "display": row.display_name or row.user_name,
            "type": "User",
        }
        encoded_size = len(
            json.dumps(
                document,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        # One byte accounts for the comma between adjacent array entries.
        group_id = row.group_id
        if seen_by_group[group_id]:
            encoded_size += 1
        serialized_bytes += encoded_size
        if serialized_bytes > cumulative_byte_limit:
            raise ProvisioningError(
                413,
                "The requested Group page exceeds the complete membership byte "
                "budget; request a smaller page",
                scim_type="tooMany",
            )
        documents[group_id].append(document)
        seen_by_group[group_id] += 1
    return documents, serialized_bytes


async def group_member_ids(
    db: AsyncSession, *, config_id: UUID, group_id: UUID
) -> list[UUID]:
    """Load a complete, contract-bounded Group membership for PATCH."""

    rows = list(
        (
            await db.execute(
                select(ScimGroupMember.user_id)
                .where(
                    ScimGroupMember.group_id == group_id,
                    ScimGroupMember.tenant_config_id == config_id,
                )
                .order_by(ScimGroupMember.user_id)
                .limit(SCIM_GROUP_MEMBER_LIMIT + 1)
            )
        ).scalars()
    )
    if len(rows) > SCIM_GROUP_MEMBER_LIMIT:
        raise ProvisioningError(
            413,
            "Group membership exceeds the complete-resource mutation limit of "
            f"{SCIM_GROUP_MEMBER_LIMIT}; no members were omitted",
            scim_type="tooMany",
        )
    return rows


async def user_group_ids(
    db: AsyncSession, *, config_id: UUID, user_id: UUID
) -> list[UUID]:
    """Load one User's complete, contract-bounded Group membership."""

    rows = list(
        (
            await db.execute(
                select(ScimGroupMember.group_id)
                .where(
                    ScimGroupMember.user_id == user_id,
                    ScimGroupMember.tenant_config_id == config_id,
                )
                .order_by(ScimGroupMember.group_id)
                .limit(SCIM_USER_GROUP_LIMIT + 1)
            )
        ).scalars()
    )
    if len(rows) > SCIM_USER_GROUP_LIMIT:
        raise ProvisioningError(
            413,
            "User Group membership exceeds the complete-resource mutation "
            f"limit of {SCIM_USER_GROUP_LIMIT}; no membership was omitted",
            scim_type="tooMany",
        )
    return rows


def etag(version: int) -> str:
    return f'W/"{version}"'


def assert_if_match(raw: str | None, current_version: int) -> None:
    if raw is None:
        raise ProvisioningError(
            428,
            "If-Match is required for this mutation",
            scim_type="invalidVers",
        )
    accepted = {etag(current_version), f'"{current_version}"', str(current_version)}
    if raw.strip() not in accepted:
        raise ProvisioningError(
            412,
            "Resource version does not match If-Match",
            scim_type="invalidVers",
        )


def user_document(user: ScimUser, base_path: str) -> ScimUserOut:
    return ScimUserOut(
        id=user.id,
        externalId=user.external_id,
        userName=user.user_name,
        displayName=user.display_name,
        name=dict(user.name or {}),
        emails=list(user.emails or []),
        active=bool(user.active and user.deleted_at is None),
        meta=ScimMeta(
            resourceType="User",
            created=user.created_at,
            lastModified=user.updated_at,
            version=etag(user.version),
            location=f"{base_path}/Users/{user.id}",
        ),
    )


def group_document(
    group: ScimGroup, base_path: str, members: list[dict[str, Any]]
) -> ScimGroupOut:
    return ScimGroupOut(
        id=group.id,
        externalId=group.external_id,
        displayName=group.display_name,
        members=members,
        meta=ScimMeta(
            resourceType="Group",
            created=group.created_at,
            lastModified=group.updated_at,
            version=etag(group.version),
            location=f"{base_path}/Groups/{group.id}",
        ),
    )


def _user_state(user: ScimUser) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "externalId": user.external_id,
        "userName": user.user_name,
        "displayName": user.display_name,
        "name": dict(user.name or {}),
        "emails": list(user.emails or []),
        "active": user.active,
    }


def apply_user_patch(user: ScimUser, patch: ScimPatchRequest) -> ScimUserWrite:
    state = _user_state(user)
    field_names = {
        "externalid": "externalId",
        "username": "userName",
        "displayname": "displayName",
        "active": "active",
        "name": "name",
        "emails": "emails",
    }
    for operation in patch.Operations:
        if operation.path is None:
            if operation.op == "remove" or not isinstance(operation.value, dict):
                raise ProvisioningError(400, "A pathless operation requires an object value")
            for raw_key, value in operation.value.items():
                key = field_names.get(str(raw_key).casefold())
                if key:
                    state[key] = value
            continue
        raw_path = operation.path.strip()
        parts = raw_path.split(".", 1)
        field = field_names.get(parts[0].casefold())
        if field is None:
            raise ProvisioningError(400, f"Unsupported User PATCH path: {raw_path}")
        if len(parts) == 2:
            if field != "name":
                raise ProvisioningError(400, f"Unsupported User PATCH path: {raw_path}")
            subfield_map = {
                "formatted": "formatted",
                "familyname": "familyName",
                "givenname": "givenName",
                "middlename": "middleName",
                "honorificprefix": "honorificPrefix",
                "honorificsuffix": "honorificSuffix",
            }
            subfield = subfield_map.get(parts[1].casefold())
            if subfield is None:
                raise ProvisioningError(400, f"Unsupported User PATCH path: {raw_path}")
            name = dict(state.get("name") or {})
            if operation.op == "remove":
                name.pop(subfield, None)
            else:
                name[subfield] = operation.value
            state["name"] = name
            continue
        if operation.op == "remove":
            if field == "userName":
                raise ProvisioningError(400, "userName cannot be removed", scim_type="mutability")
            state[field] = True if field == "active" else ([] if field == "emails" else None)
        elif operation.op == "add" and field == "emails":
            incoming = operation.value if isinstance(operation.value, list) else [operation.value]
            state["emails"] = [*(state.get("emails") or []), *incoming]
        else:
            state[field] = operation.value
    try:
        return ScimUserWrite.model_validate(state)
    except ValueError as exc:
        raise ProvisioningError(400, str(exc), scim_type="invalidValue") from exc


def _member_values(value: Any) -> set[UUID]:
    items = value if isinstance(value, list) else [value]
    result: set[UUID] = set()
    for item in items:
        raw = item.get("value") if isinstance(item, dict) else item
        try:
            result.add(UUID(str(raw)))
        except (TypeError, ValueError) as exc:
            raise ProvisioningError(
                400, "Group member values must be SCIM User IDs", scim_type="invalidValue"
            ) from exc
        if len(result) > SCIM_GROUP_MEMBER_LIMIT:
            raise ProvisioningError(
                400,
                f"Group membership exceeds the limit of {SCIM_GROUP_MEMBER_LIMIT}",
                scim_type="invalidValue",
            )
    return result


def _assert_group_member_limit(members: set[UUID]) -> None:
    if len(members) > SCIM_GROUP_MEMBER_LIMIT:
        raise ProvisioningError(
            400,
            f"Group membership exceeds the limit of {SCIM_GROUP_MEMBER_LIMIT}",
            scim_type="invalidValue",
        )


def apply_group_patch(
    group: ScimGroup,
    current_members: Iterable[UUID],
    patch: ScimPatchRequest,
) -> ScimGroupWrite:
    state: dict[str, Any] = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "externalId": group.external_id,
        "displayName": group.display_name,
    }
    members = set(current_members)
    for operation in patch.Operations:
        if operation.path is None:
            if operation.op == "remove" or not isinstance(operation.value, dict):
                raise ProvisioningError(400, "A pathless operation requires an object value")
            for key, value in operation.value.items():
                lowered = str(key).casefold()
                if lowered == "displayname":
                    state["displayName"] = value
                elif lowered == "externalid":
                    state["externalId"] = value
                elif lowered == "members":
                    incoming = _member_values(value)
                    members = members | incoming if operation.op == "add" else incoming
            _assert_group_member_limit(members)
            continue
        raw_path = operation.path.strip()
        member_match = _MEMBER_FILTER_RE.fullmatch(raw_path)
        if member_match:
            try:
                member_id = UUID(json.loads(member_match.group(1)))
            except (ValueError, json.JSONDecodeError) as exc:
                raise ProvisioningError(400, "Invalid member filter", scim_type="invalidPath") from exc
            if operation.op != "remove":
                raise ProvisioningError(400, "Filtered member paths support remove only")
            members.discard(member_id)
            continue
        lowered = raw_path.casefold()
        if lowered == "members":
            incoming = (
                _member_values(operation.value)
                if operation.op != "remove" or operation.value is not None
                else set()
            )
            if operation.op == "add":
                members.update(incoming)
            elif operation.op == "remove" and operation.value is not None:
                members.difference_update(incoming)
            else:
                members = incoming
            _assert_group_member_limit(members)
        elif lowered == "displayname":
            if operation.op == "remove":
                raise ProvisioningError(400, "displayName cannot be removed", scim_type="mutability")
            state["displayName"] = operation.value
        elif lowered == "externalid":
            state["externalId"] = None if operation.op == "remove" else operation.value
        else:
            raise ProvisioningError(400, f"Unsupported Group PATCH path: {raw_path}")
    state["members"] = [{"value": str(member_id)} for member_id in sorted(members, key=str)]
    try:
        return ScimGroupWrite.model_validate(state)
    except ValueError as exc:
        raise ProvisioningError(400, str(exc), scim_type="invalidValue") from exc


async def paginated_users(
    db: AsyncSession,
    *,
    config_id: UUID,
    parsed_filter: tuple[str, str] | None,
    start_index: int,
    count: int,
) -> tuple[int, list[ScimUser]]:
    conditions = [ScimUser.tenant_config_id == config_id, ScimUser.deleted_at.is_(None)]
    if parsed_filter:
        field, value = parsed_filter
        column = {
            "userName": ScimUser.user_name,
            "externalId": ScimUser.external_id,
            "displayName": ScimUser.display_name,
        }[field]
        conditions.append(column == value)
    total = int(
        (await db.execute(select(func.count()).select_from(ScimUser).where(*conditions))).scalar_one()
    )
    rows = list(
        (
            await db.execute(
                select(ScimUser)
                .where(*conditions)
                .order_by(ScimUser.id)
                .offset(start_index - 1)
                .limit(count)
            )
        ).scalars()
    )
    return total, rows


async def paginated_groups(
    db: AsyncSession,
    *,
    config_id: UUID,
    parsed_filter: tuple[str, str] | None,
    start_index: int,
    count: int,
) -> tuple[int, list[ScimGroup]]:
    conditions = [ScimGroup.tenant_config_id == config_id, ScimGroup.deleted_at.is_(None)]
    if parsed_filter:
        field, value = parsed_filter
        column = {
            "externalId": ScimGroup.external_id,
            "displayName": ScimGroup.display_name,
        }[field]
        conditions.append(column == value)
    total = int(
        (await db.execute(select(func.count()).select_from(ScimGroup).where(*conditions))).scalar_one()
    )
    rows = list(
        (
            await db.execute(
                select(ScimGroup)
                .where(*conditions)
                .order_by(ScimGroup.id)
                .offset(start_index - 1)
                .limit(count)
            )
        ).scalars()
    )
    return total, rows
