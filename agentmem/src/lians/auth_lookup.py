"""Narrow credential-bootstrap reads for PostgreSQL authentication.

PostgreSQL production callers use the exact SECURITY DEFINER functions added by
0056. Those functions return only the authorization fields needed to establish
the tenant context. SQLite remains an explicitly non-production development
backend and uses equivalent indexed ORM lookups for compatibility.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .identity_models import IdentityBinding
from .models import ApiKey

_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")
_ALLOWED_ROLES = frozenset({"owner", "analyst", "compliance", "readonly"})
_ALLOWED_PRINCIPAL_TYPES = frozenset({"human", "workload"})
_MAX_SCOPES = 50


class AuthLookupInvariantError(Exception):
    """A persisted bootstrap record violates the authenticated data contract."""


@dataclass(frozen=True)
class ApiKeyAuthenticationRecord:
    id: UUID
    namespace: str
    scopes: tuple[str, ...]
    role: str | None
    barrier_group: str | None
    provisioning_source: str
    last_used_at: datetime | None
    authenticated_at: datetime


@dataclass(frozen=True)
class IdentityBindingAuthenticationRecord:
    id: UUID
    namespace: str
    scopes: tuple[str, ...]
    role: str | None
    barrier_group: str | None
    authorized_party: str | None
    principal_type: str


def _validated_scopes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > _MAX_SCOPES:
        raise AuthLookupInvariantError("invalid_scopes")
    result: list[str] = []
    for scope in value:
        if (
            not isinstance(scope, str)
            or not scope
            or len(scope) > 100
            or not all(ch.isalnum() or ch in "_.:-" for ch in scope)
            or scope in result
        ):
            raise AuthLookupInvariantError("invalid_scopes")
        result.append(scope)
    return tuple(result)


def _field(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _validated_common(row: Any) -> tuple[UUID, str, tuple[str, ...], str | None]:
    try:
        record_id = UUID(str(_field(row, "id")))
    except (AttributeError, TypeError, ValueError) as exc:
        raise AuthLookupInvariantError("invalid_record_id") from exc
    namespace = _field(row, "namespace")
    if not isinstance(namespace, str) or _NAMESPACE_RE.fullmatch(namespace) is None:
        raise AuthLookupInvariantError("invalid_namespace")
    role = _field(row, "role")
    if role is not None and role not in _ALLOWED_ROLES:
        raise AuthLookupInvariantError("invalid_role")
    return record_id, namespace, _validated_scopes(_field(row, "scopes")), role


def _validated_barrier(value: Any) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 255
        or value != value.strip()
    ):
        raise AuthLookupInvariantError("invalid_barrier_group")
    return value


def _api_key_record(
    row: Any,
    *,
    fallback_authenticated_at: datetime | None = None,
) -> ApiKeyAuthenticationRecord:
    record_id, namespace, scopes, role = _validated_common(row)
    source = _field(row, "provisioning_source")
    if source not in {"breakglass_admin", "tenant_oidc"}:
        raise AuthLookupInvariantError("invalid_provisioning_source")
    last_used_at = _field(row, "last_used_at")
    if last_used_at is not None and not isinstance(last_used_at, datetime):
        raise AuthLookupInvariantError("invalid_last_used_at")
    authenticated_at = _field(row, "authenticated_at") or fallback_authenticated_at
    if (
        not isinstance(authenticated_at, datetime)
        or authenticated_at.tzinfo is None
        or authenticated_at.utcoffset() is None
    ):
        raise AuthLookupInvariantError("invalid_authenticated_at")
    return ApiKeyAuthenticationRecord(
        id=record_id,
        namespace=namespace,
        scopes=scopes,
        role=role,
        barrier_group=_validated_barrier(_field(row, "barrier_group")),
        provisioning_source=source,
        last_used_at=last_used_at,
        authenticated_at=authenticated_at,
    )


def _identity_record(row: Any) -> IdentityBindingAuthenticationRecord:
    record_id, namespace, scopes, role = _validated_common(row)
    principal_type = _field(row, "principal_type")
    if principal_type not in _ALLOWED_PRINCIPAL_TYPES:
        raise AuthLookupInvariantError("invalid_principal_type")
    authorized_party = _field(row, "authorized_party")
    if authorized_party is not None and (
        not isinstance(authorized_party, str) or len(authorized_party) > 512
    ):
        raise AuthLookupInvariantError("invalid_authorized_party")
    return IdentityBindingAuthenticationRecord(
        id=record_id,
        namespace=namespace,
        scopes=scopes,
        role=role,
        barrier_group=_validated_barrier(_field(row, "barrier_group")),
        authorized_party=authorized_party,
        principal_type=principal_type,
    )


async def lookup_api_key(
    db: AsyncSession,
    *,
    hashed_key: str,
    observed_at: datetime,
) -> ApiKeyAuthenticationRecord | None:
    """Resolve one active key without a PostgreSQL runtime table scan."""

    if db.get_bind().dialect.name == "postgresql":
        rows = (
            await db.execute(
                text(
                    """SELECT *
                       FROM public.lians_auth_lookup_api_key(
                           CAST(:hashed_key AS text)
                       )"""
                ),
                {"hashed_key": hashed_key},
            )
        ).mappings().all()
        if len(rows) > 1:
            raise AuthLookupInvariantError("ambiguous_api_key")
        return None if not rows else _api_key_record(rows[0])

    row = (
        await db.execute(
            select(ApiKey).where(
                and_(
                    ApiKey.hashed_key == hashed_key,
                    ApiKey.revoked_at.is_(None),
                    or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > observed_at),
                )
            )
        )
    ).scalar_one_or_none()
    return (
        None
        if row is None
        else _api_key_record(row, fallback_authenticated_at=observed_at)
    )


async def lookup_identity_binding(
    db: AsyncSession,
    *,
    provider_id: UUID,
    external_subject: str,
) -> IdentityBindingAuthenticationRecord | None:
    """Resolve one active binding after JWT verification, before tenant setup."""

    if db.get_bind().dialect.name == "postgresql":
        rows = (
            await db.execute(
                text(
                    """SELECT *
                       FROM public.lians_auth_lookup_identity_binding(
                           CAST(:provider_id AS uuid),
                           CAST(:external_subject AS text)
                       )"""
                ),
                {
                    "provider_id": str(provider_id),
                    "external_subject": external_subject,
                },
            )
        ).mappings().all()
        if len(rows) > 1:
            raise AuthLookupInvariantError("ambiguous_identity_binding")
        return None if not rows else _identity_record(rows[0])

    row = (
        await db.execute(
            select(IdentityBinding).where(
                and_(
                    IdentityBinding.provider_id == provider_id,
                    IdentityBinding.external_subject == external_subject,
                    IdentityBinding.enabled.is_(True),
                    IdentityBinding.revoked_at.is_(None),
                    or_(
                        IdentityBinding.scim_tenant_config_id.is_(None),
                        IdentityBinding.scim_reconciliation_complete.is_(True),
                    ),
                )
            )
        )
    ).scalar_one_or_none()
    return None if row is None else _identity_record(row)


__all__ = [
    "ApiKeyAuthenticationRecord",
    "AuthLookupInvariantError",
    "IdentityBindingAuthenticationRecord",
    "lookup_api_key",
    "lookup_identity_binding",
]
