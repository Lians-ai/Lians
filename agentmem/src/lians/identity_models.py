"""Persistence models for native OIDC human and workload identity federation."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TrustedIdentityProvider(Base):
    """An administrator-approved OIDC issuer and its verification policy.

    Issuers are globally unique. A single enterprise issuer can serve many Lians
    namespaces; the verified ``(provider_id, sub)`` binding selects exactly one.
    No tenant or authorization data is ever accepted from unverified JWT claims.
    """

    __tablename__ = "trusted_identity_providers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    issuer = Column(String(2048), nullable=False)
    jwks_uri = Column(String(2048), nullable=False)
    audiences = Column(JSON, nullable=False)
    allowed_algorithms = Column(JSON, nullable=False)
    required_claims = Column(JSON, nullable=False)
    required_typ = Column(String(100), nullable=True)
    clock_skew_seconds = Column(Integer, nullable=False, default=30)
    max_token_age_seconds = Column(Integer, nullable=False, default=900)
    jwks_cache_seconds = Column(Integer, nullable=False, default=300)
    allow_private_network = Column(Boolean, nullable=False, default=False)
    allow_insecure_http = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("issuer", name="uq_trusted_identity_provider_issuer"),
        CheckConstraint("version >= 1", name="ck_identity_provider_version"),
        CheckConstraint(
            "clock_skew_seconds >= 0 AND clock_skew_seconds <= 300",
            name="ck_identity_provider_clock_skew",
        ),
        CheckConstraint(
            "max_token_age_seconds >= 30 AND max_token_age_seconds <= 86400",
            name="ck_identity_provider_token_age",
        ),
        Index("ix_trusted_identity_provider_enabled", "enabled", "revoked_at"),
        Index(
            "ix_trusted_identity_provider_inventory_page",
            "created_at",
            "id",
        ),
    )


class IdentityBinding(Base):
    """Maps one verified external subject to one Lians authorization context."""

    __tablename__ = "identity_bindings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("trusted_identity_providers.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_subject = Column(String(512), nullable=False)
    namespace = Column(String(255), nullable=False)
    principal_type = Column(String(32), nullable=False)
    display_name = Column(String(255), nullable=True)
    role = Column(String(64), nullable=True)
    scopes = Column(JSON, nullable=False)
    barrier_group = Column(String(255), nullable=True)
    # Optional OAuth client/workload binding. When set, token azp/client_id must
    # match; this prevents a subject token minted for a different client from use.
    authorized_party = Column(String(512), nullable=True)
    # Null for manual/legacy bindings. SCIM-managed bindings are denied by both
    # authentication lookup paths until the exact tenant-version snapshot has
    # completed; this prevents incremental activation if a worker later fails.
    scim_tenant_config_id = Column(UUID(as_uuid=True), nullable=True)
    scim_tenant_config_version = Column(Integer, nullable=True)
    scim_reconciliation_complete = Column(Boolean, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["scim_tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="RESTRICT",
            name="fk_identity_binding_scim_tenant_namespace",
        ),
        UniqueConstraint(
            "provider_id",
            "external_subject",
            name="uq_identity_binding_provider_subject",
        ),
        CheckConstraint(
            "principal_type IN ('human','workload')",
            name="ck_identity_binding_principal_type",
        ),
        CheckConstraint(
            "role IS NULL OR role IN ('owner','analyst','compliance','readonly')",
            name="ck_identity_binding_role",
        ),
        CheckConstraint("version >= 1", name="ck_identity_binding_version"),
        CheckConstraint(
            "(scim_tenant_config_id IS NULL "
            "AND scim_tenant_config_version IS NULL "
            "AND scim_reconciliation_complete IS NULL) OR "
            "(scim_tenant_config_id IS NOT NULL "
            "AND scim_tenant_config_version >= 1 "
            "AND scim_reconciliation_complete IS NOT NULL)",
            name="ck_identity_binding_scim_activation_fence",
        ),
        Index("ix_identity_binding_namespace", "namespace"),
        Index("ix_identity_binding_active", "provider_id", "enabled", "revoked_at"),
        Index("ix_identity_binding_inventory_page", "created_at", "id"),
        Index(
            "ix_identity_binding_namespace_inventory_page",
            "namespace",
            "created_at",
            "id",
        ),
        Index(
            "ix_identity_binding_provider_inventory_page",
            "provider_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_identity_binding_scim_activation_fence",
            "scim_tenant_config_id",
            "scim_tenant_config_version",
            "scim_reconciliation_complete",
            "id",
        ),
    )
