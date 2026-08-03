"""Persistence for tenant-scoped SCIM 2.0 enterprise provisioning.

The provisioning domain is intentionally separate from request authentication.
SCIM credentials are high-entropy bearer secrets whose one-way digest is the
only token material persisted.  Provisioned users are linked to native
``IdentityBinding`` rows so the normal authorization path remains authoritative.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ScimTenantConfig(Base):
    """One isolated SCIM service-provider configuration per Lians tenant."""

    __tablename__ = "scim_tenant_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(255), nullable=False)
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("trusted_identity_providers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_attribute = Column(String(32), nullable=False, default="externalId")
    enabled = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("namespace", name="uq_scim_tenant_config_namespace"),
        UniqueConstraint("id", "namespace", name="uq_scim_tenant_id_namespace"),
        CheckConstraint(
            "subject_attribute IN ('externalId', 'userName')",
            name="ck_scim_tenant_subject_attribute",
        ),
        Index("ix_scim_tenant_provider", "provider_id"),
        Index("ix_scim_tenant_active", "enabled", "revoked_at"),
    )


class ScimBearerCredential(Base):
    """Rotatable SCIM credential; plaintext is never represented by this model."""

    __tablename__ = "scim_bearer_credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_config_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )
    namespace = Column(String(255), nullable=False)
    token_hash = Column(String(64), nullable=False)
    token_hint = Column(String(24), nullable=False)
    label = Column(String(200), nullable=True)
    rotated_from_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scim_bearer_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    replaced_by_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scim_bearer_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="RESTRICT",
            name="fk_scim_credential_tenant_namespace",
        ),
        UniqueConstraint("token_hash", name="uq_scim_bearer_token_hash"),
        CheckConstraint("length(token_hash) = 64", name="ck_scim_token_hash_length"),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_scim_credential_expiry",
        ),
        Index("ix_scim_credential_tenant", "tenant_config_id"),
        Index("ix_scim_credential_namespace", "namespace"),
        Index("ix_scim_credential_active", "tenant_config_id", "revoked_at", "expires_at"),
    )


class ScimUser(Base):
    """Tenant-isolated SCIM user and its authoritative identity-binding link."""

    __tablename__ = "scim_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_config_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )
    namespace = Column(String(255), nullable=False)
    external_id = Column(String(512), nullable=True)
    user_name = Column(String(512), nullable=False)
    display_name = Column(String(512), nullable=True)
    name = Column(JSON, nullable=False, default=dict)
    emails = Column(JSON, nullable=False, default=list)
    active = Column(Boolean, nullable=False, default=True)
    identity_binding_id = Column(
        UUID(as_uuid=True),
        ForeignKey("identity_bindings.id", ondelete="SET NULL"),
        nullable=True,
    )
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="RESTRICT",
            name="fk_scim_user_tenant_namespace",
        ),
        UniqueConstraint(
            "id", "tenant_config_id", "namespace", name="uq_scim_user_tenant_identity"
        ),
        UniqueConstraint(
            "tenant_config_id", "user_name", name="uq_scim_user_tenant_username"
        ),
        UniqueConstraint(
            "tenant_config_id", "external_id", name="uq_scim_user_tenant_external_id"
        ),
        UniqueConstraint("identity_binding_id", name="uq_scim_user_identity_binding"),
        Index("ix_scim_user_namespace", "namespace"),
        Index("ix_scim_user_tenant_active", "tenant_config_id", "active", "deleted_at"),
        Index("ix_scim_user_binding", "identity_binding_id"),
        Index(
            "ix_scim_user_reconciliation_page",
            "tenant_config_id",
            "created_at",
            "id",
        ),
    )


class ScimGroup(Base):
    """A tenant-scoped SCIM group."""

    __tablename__ = "scim_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_config_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )
    namespace = Column(String(255), nullable=False)
    external_id = Column(String(512), nullable=True)
    display_name = Column(String(512), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="RESTRICT",
            name="fk_scim_group_tenant_namespace",
        ),
        UniqueConstraint(
            "id", "tenant_config_id", "namespace", name="uq_scim_group_tenant_identity"
        ),
        UniqueConstraint(
            "tenant_config_id", "display_name", name="uq_scim_group_tenant_display_name"
        ),
        UniqueConstraint(
            "tenant_config_id", "external_id", name="uq_scim_group_tenant_external_id"
        ),
        Index("ix_scim_group_namespace", "namespace"),
        Index("ix_scim_group_tenant_active", "tenant_config_id", "deleted_at"),
    )


class ScimGroupMember(Base):
    """Tenant-bound membership edge with database-enforced per-User capacity."""

    __tablename__ = "scim_group_members"

    group_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    tenant_config_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )
    namespace = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="CASCADE",
            name="fk_scim_member_tenant_namespace",
        ),
        ForeignKeyConstraint(
            ["group_id", "tenant_config_id", "namespace"],
            ["scim_groups.id", "scim_groups.tenant_config_id", "scim_groups.namespace"],
            ondelete="CASCADE",
            name="fk_scim_member_group_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_config_id", "namespace"],
            ["scim_users.id", "scim_users.tenant_config_id", "scim_users.namespace"],
            ondelete="CASCADE",
            name="fk_scim_member_user_tenant",
        ),
        Index("ix_scim_membership_user", "user_id"),
        Index("ix_scim_membership_tenant", "tenant_config_id"),
        Index("ix_scim_membership_namespace", "namespace"),
    )


class ScimGroupEntitlement(Base):
    """Deterministic authorization contribution for membership in one group."""

    __tablename__ = "scim_group_entitlements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_config_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )
    namespace = Column(String(255), nullable=False)
    group_id = Column(
        UUID(as_uuid=True),
        nullable=False,
    )
    role = Column(String(64), nullable=True)
    scopes = Column(JSON, nullable=False, default=list)
    barrier_group = Column(String(255), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="CASCADE",
            name="fk_scim_entitlement_tenant_namespace",
        ),
        ForeignKeyConstraint(
            ["group_id", "tenant_config_id", "namespace"],
            ["scim_groups.id", "scim_groups.tenant_config_id", "scim_groups.namespace"],
            ondelete="CASCADE",
            name="fk_scim_entitlement_group_tenant",
        ),
        UniqueConstraint("group_id", name="uq_scim_group_entitlement_group"),
        CheckConstraint(
            "role IS NULL OR role IN ('owner', 'analyst', 'compliance', 'readonly')",
            name="ck_scim_entitlement_role",
        ),
        Index("ix_scim_entitlement_tenant", "tenant_config_id"),
        Index("ix_scim_entitlement_namespace", "namespace"),
    )


class ScimTenantReconciliationJob(Base):
    """Durable fixed-user-snapshot reconciliation for one tenant version."""

    __tablename__ = "scim_tenant_reconciliation_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_config_id = Column(UUID(as_uuid=True), nullable=False)
    namespace = Column(String(255), nullable=False)
    target_config_version = Column(Integer, nullable=False)
    target_enabled = Column(Boolean, nullable=False)
    target_revoked_at = Column(DateTime(timezone=True), nullable=True)
    requested_by_principal_ref = Column(String(512), nullable=False)

    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    snapshot_max_created_at = Column(DateTime(timezone=True), nullable=True)
    snapshot_max_user_id = Column(UUID(as_uuid=True), nullable=True)
    snapshot_user_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    cursor_created_at = Column(DateTime(timezone=True), nullable=True)
    cursor_user_id = Column(UUID(as_uuid=True), nullable=True)
    users_reconciled = Column(BigInteger, nullable=False, default=0, server_default="0")
    pages_completed = Column(Integer, nullable=False, default=0, server_default="0")

    processing_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    consecutive_failures = Column(Integer, nullable=False, default=0, server_default="0")
    attempt_limit = Column(Integer, nullable=False, default=8, server_default="8")
    next_attempt_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    lease_owner = Column(String(255), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_digest = Column(String(64), nullable=True)
    failure_code = Column(String(64), nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    superseded_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="RESTRICT",
            name="fk_scim_reconciliation_job_tenant_namespace",
        ),
        UniqueConstraint(
            "tenant_config_id",
            "target_config_version",
            name="uq_scim_reconciliation_job_tenant_version",
        ),
        UniqueConstraint(
            "id", "namespace", name="uq_scim_reconciliation_job_id_namespace"
        ),
        CheckConstraint(
            "status IN ('pending','running','completed','failed','superseded')",
            name="ck_scim_reconciliation_job_status",
        ),
        CheckConstraint(
            "target_config_version >= 1 AND "
            "(target_enabled = false OR target_revoked_at IS NULL)",
            name="ck_scim_reconciliation_job_target",
        ),
        CheckConstraint(
            "snapshot_user_count >= 0 AND users_reconciled >= 0 "
            "AND users_reconciled <= snapshot_user_count AND pages_completed >= 0",
            name="ck_scim_reconciliation_job_progress",
        ),
        CheckConstraint(
            "((snapshot_user_count = 0 AND snapshot_max_created_at IS NULL "
            "AND snapshot_max_user_id IS NULL) OR "
            "(snapshot_user_count > 0 AND snapshot_max_created_at IS NOT NULL "
            "AND snapshot_max_user_id IS NOT NULL))",
            name="ck_scim_reconciliation_job_snapshot_boundary",
        ),
        CheckConstraint(
            "(cursor_created_at IS NULL AND cursor_user_id IS NULL) OR "
            "(cursor_created_at IS NOT NULL AND cursor_user_id IS NOT NULL)",
            name="ck_scim_reconciliation_job_cursor_pair",
        ),
        CheckConstraint(
            "processing_attempts >= 0 AND consecutive_failures >= 0 "
            "AND consecutive_failures <= processing_attempts "
            "AND attempt_limit BETWEEN 1 AND 100",
            name="ck_scim_reconciliation_job_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_scim_reconciliation_job_lease_pair",
        ),
        CheckConstraint(
            "(last_error_code IS NULL AND last_error_digest IS NULL) OR "
            "(last_error_code IS NOT NULL AND last_error_digest IS NOT NULL "
            "AND length(last_error_digest) = 64)",
            name="ck_scim_reconciliation_job_error_pair",
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND users_reconciled = snapshot_user_count) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name="ck_scim_reconciliation_job_completion",
        ),
        CheckConstraint(
            "(status = 'failed' AND failed_at IS NOT NULL AND failure_code IS NOT NULL) "
            "OR (status <> 'failed' AND failed_at IS NULL AND failure_code IS NULL)",
            name="ck_scim_reconciliation_job_failure",
        ),
        CheckConstraint(
            "(status = 'superseded' AND superseded_at IS NOT NULL) OR "
            "(status <> 'superseded' AND superseded_at IS NULL)",
            name="ck_scim_reconciliation_job_superseded",
        ),
        CheckConstraint(
            "status NOT IN ('completed','failed','superseded') OR "
            "(lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_scim_reconciliation_job_terminal_lease",
        ),
        Index(
            "ix_scim_reconciliation_job_claim",
            "status",
            "next_attempt_at",
            "lease_expires_at",
            "created_at",
            "id",
        ),
        Index(
            "ix_scim_reconciliation_job_tenant_page",
            "tenant_config_id",
            "created_at",
            "id",
        ),
        Index(
            "uq_scim_reconciliation_job_one_active",
            "tenant_config_id",
            unique=True,
            postgresql_where=text("status IN ('pending','running')"),
            sqlite_where=text("status IN ('pending','running')"),
        ),
    )
