"""Durable, namespace-isolated Stripe usage-meter delivery models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class MeteringEvent(Base):
    """A billing fact plus its leased, retryable Stripe delivery projection."""

    __tablename__ = "metering_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    event_name = Column(String(100), nullable=False, index=True)
    customer_id = Column(String(255), nullable=False)
    quantity = Column(BigInteger, nullable=False)
    source_identifier_hash = Column(String(64), nullable=False)
    request_hash = Column(String(64), nullable=False)
    provider_identifier = Column(String(100), nullable=False, unique=True)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    attempt_limit = Column(Integer, nullable=False)
    replay_count = Column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    lease_owner = Column(String(255), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    first_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    dead_lettered_at = Column(DateTime(timezone=True), nullable=True)
    last_status_code = Column(Integer, nullable=True)
    last_error_code = Column(String(100), nullable=True)
    last_error_digest = Column(String(64), nullable=True)
    last_response_digest = Column(String(64), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("id", "namespace", name="uq_metering_event_id_namespace"),
        UniqueConstraint(
            "namespace",
            "event_name",
            "source_identifier_hash",
            name="uq_metering_event_source",
        ),
        Index(
            "ix_metering_event_due",
            "status",
            "next_attempt_at",
            "lease_expires_at",
        ),
        Index(
            "ix_metering_event_namespace_status",
            "namespace",
            "status",
            "updated_at",
        ),
        Index(
            "ix_metering_event_admin_all_page",
            "updated_at",
            "id",
        ),
        Index(
            "ix_metering_event_admin_namespace_page",
            "namespace",
            "updated_at",
            "id",
        ),
        Index(
            "ix_metering_event_admin_status_page",
            "status",
            "updated_at",
            "id",
        ),
        CheckConstraint("quantity > 0", name="ck_metering_event_quantity"),
        CheckConstraint(
            "length(source_identifier_hash) = 64",
            name="ck_metering_event_source_hash",
        ),
        CheckConstraint("length(request_hash) = 64", name="ck_metering_event_request_hash"),
        CheckConstraint(
            "length(provider_identifier) BETWEEN 1 AND 100",
            name="ck_metering_event_provider_identifier",
        ),
        CheckConstraint(
            "status IN ('pending', 'leased', 'retry', 'delivered', 'dead_letter')",
            name="ck_metering_event_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_metering_event_attempt_count"),
        CheckConstraint(
            "attempt_limit BETWEEN 1 AND 1000 AND attempt_count <= attempt_limit",
            name="ck_metering_event_attempt_limit",
        ),
        CheckConstraint("replay_count >= 0", name="ck_metering_event_replay_count"),
        CheckConstraint(
            "(status = 'leased' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status != 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_metering_event_lease_shape",
        ),
        CheckConstraint(
            "(status = 'delivered') = (delivered_at IS NOT NULL)",
            name="ck_metering_event_delivered_shape",
        ),
        CheckConstraint(
            "(status = 'dead_letter') = (dead_lettered_at IS NOT NULL)",
            name="ck_metering_event_dead_letter_shape",
        ),
        CheckConstraint(
            "last_error_digest IS NULL OR length(last_error_digest) = 64",
            name="ck_metering_event_error_digest",
        ),
        CheckConstraint(
            "last_response_digest IS NULL OR length(last_response_digest) = 64",
            name="ck_metering_event_response_digest",
        ),
    )


class MeteringAttemptRecord(Base):
    """Append-only intent/result record for one provider attempt."""

    __tablename__ = "metering_attempt_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    event_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    record_type = Column(String(16), nullable=False)
    outcome = Column(String(32), nullable=False, index=True)
    worker_id = Column(String(255), nullable=False)
    status_code = Column(Integer, nullable=True)
    error_code = Column(String(100), nullable=True)
    error_digest = Column(String(64), nullable=True)
    response_digest = Column(String(64), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["event_id", "namespace"],
            ["metering_events.id", "metering_events.namespace"],
            ondelete="RESTRICT",
            name="fk_metering_attempt_event_namespace",
        ),
        UniqueConstraint(
            "event_id",
            "attempt_number",
            "record_type",
            name="uq_metering_attempt_event_number_record",
        ),
        Index(
            "ix_metering_attempt_namespace_time",
            "namespace",
            "recorded_at",
        ),
        CheckConstraint("attempt_number > 0", name="ck_metering_attempt_number"),
        CheckConstraint(
            "record_type IN ('started', 'finished')",
            name="ck_metering_attempt_record_type",
        ),
        CheckConstraint(
            "outcome IN ('started', 'delivered', 'retry', 'dead_letter', 'lease_lost')",
            name="ck_metering_attempt_outcome",
        ),
        CheckConstraint(
            "(record_type = 'started' AND outcome = 'started' AND duration_ms IS NULL) OR "
            "(record_type = 'finished' AND outcome != 'started' AND duration_ms IS NOT NULL)",
            name="ck_metering_attempt_record_shape",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_metering_attempt_duration",
        ),
        CheckConstraint(
            "error_digest IS NULL OR length(error_digest) = 64",
            name="ck_metering_attempt_error_digest",
        ),
        CheckConstraint(
            "response_digest IS NULL OR length(response_digest) = 64",
            name="ck_metering_attempt_response_digest",
        ),
    )
