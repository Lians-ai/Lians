"""Append-only governance revisions and atomic UTC daily usage ledgers."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _utc_today() -> date:
    return datetime.now(UTC).date()


class NamespaceDailyUsage(Base):
    """One row per namespace and UTC day, locked during quota reservation."""

    __tablename__ = "namespace_daily_usage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    usage_date = Column(Date, nullable=False, default=_utc_today)
    recorder_events = Column(BigInteger, nullable=False, default=0, server_default="0")
    decision_records = Column(BigInteger, nullable=False, default=0, server_default="0")
    protected_actions = Column(BigInteger, nullable=False, default=0, server_default="0")
    memory_writes = Column(BigInteger, nullable=False, default=0, server_default="0")
    recalls = Column(BigInteger, nullable=False, default=0, server_default="0")
    estimated_ingest_bytes = Column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "usage_date",
            name="uq_namespace_daily_usage_namespace_date",
        ),
        CheckConstraint("recorder_events >= 0", name="ck_namespace_usage_recorder"),
        CheckConstraint("decision_records >= 0", name="ck_namespace_usage_decisions"),
        CheckConstraint(
            "protected_actions >= 0",
            name="ck_namespace_usage_protected_actions",
        ),
        CheckConstraint("memory_writes >= 0", name="ck_namespace_usage_memories"),
        CheckConstraint("recalls >= 0", name="ck_namespace_usage_recalls"),
        CheckConstraint(
            "estimated_ingest_bytes >= 0",
            name="ck_namespace_usage_ingest_bytes",
        ),
        Index("ix_namespace_daily_usage_date_namespace", "usage_date", "namespace"),
    )


class NamespacePolicyRevision(Base):
    """Immutable snapshot for every governance policy state transition."""

    __tablename__ = "namespace_policy_revisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    policy_version = Column(BigInteger, nullable=False)
    action = Column(String(32), nullable=False)
    actor_id = Column(String(512), nullable=False)
    policy_snapshot = Column(JSON, nullable=False)
    snapshot_hash = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "policy_version",
            name="uq_namespace_policy_revision_namespace_version",
        ),
        CheckConstraint("policy_version > 0", name="ck_namespace_policy_revision_version"),
        CheckConstraint(
            "action IN ('created', 'updated', 'enabled', 'disabled', 'cleared')",
            name="ck_namespace_policy_revision_action",
        ),
        CheckConstraint(
            "length(snapshot_hash) = 64",
            name="ck_namespace_policy_revision_hash",
        ),
        Index(
            "ix_namespace_policy_revision_namespace_created",
            "namespace",
            "created_at",
        ),
    )
