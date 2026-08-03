"""Durable, namespace-isolated integration outbox persistence models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class IntegrationDestination(Base):
    """A versioned outbound sink whose credentials are envelope-encrypted."""

    __tablename__ = "integration_destinations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String(255), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    destination_type = Column(String(32), nullable=False, index=True)
    url_origin = Column(String(512), nullable=False)
    url_fingerprint = Column(String(64), nullable=False)
    event_patterns = Column(JSON, nullable=False, default=list, server_default="[]")
    payload_profile = Column(
        String(32), nullable=False, default="cloudevents", server_default="cloudevents"
    )
    credential_kind = Column(String(32), nullable=False, default="none", server_default="none")
    custom_header_names = Column(JSON, nullable=False, default=list, server_default="[]")
    secret_config_encrypted = Column(Text, nullable=False)
    secret_fingerprint = Column(String(64), nullable=False)
    max_attempts = Column(Integer, nullable=False, default=12, server_default="12")
    timeout_seconds = Column(Integer, nullable=False, default=10, server_default="10")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    description = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("namespace", "name", name="uq_integration_destination_namespace_name"),
        Index(
            "ix_integration_destination_ns_enabled",
            "namespace",
            "enabled",
            "revoked_at",
        ),
        CheckConstraint(
            "destination_type IN ('generic_http', 'siem', 'grc', 'ticketing', 'billing')",
            name="ck_integration_destination_type",
        ),
        CheckConstraint(
            "payload_profile IN ('cloudevents', 'raw')",
            name="ck_integration_destination_payload_profile",
        ),
        CheckConstraint(
            "credential_kind IN ('none', 'bearer', 'basic', 'api_key_header', 'splunk_hec')",
            name="ck_integration_destination_credential_kind",
        ),
        CheckConstraint(
            "(secret_config_encrypted LIKE 'lians-sealed:v1:%' OR "
            "secret_config_encrypted LIKE 'lians-sealed:v2:%')",
            name="ck_integration_destination_secret_sealed",
        ),
        CheckConstraint(
            "length(url_fingerprint) = 64",
            name="ck_integration_destination_url_fingerprint",
        ),
        CheckConstraint(
            "max_attempts BETWEEN 1 AND 50",
            name="ck_integration_destination_max_attempts",
        ),
        CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 120",
            name="ck_integration_destination_timeout",
        ),
        CheckConstraint("version > 0", name="ck_integration_destination_version"),
    )


class IntegrationOutboxEvent(Base):
    """Append-only domain event committed atomically with the source mutation."""

    __tablename__ = "integration_outbox_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String(255), nullable=True, index=True)
    event_type = Column(String(255), nullable=False, index=True)
    schema_version = Column(String(32), nullable=False, default="1", server_default="1")
    aggregate_type = Column(String(100), nullable=True, index=True)
    aggregate_id = Column(String(512), nullable=True, index=True)
    source_event_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    correlation_id = Column(String(255), nullable=True, index=True)
    idempotency_key = Column(String(255), nullable=False)
    payload_encrypted = Column(Text, nullable=False)
    payload_hash = Column(String(64), nullable=False, index=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    enqueued_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)

    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "idempotency_key",
            name="uq_integration_outbox_namespace_idempotency",
        ),
        Index(
            "ix_integration_outbox_ns_time",
            "namespace",
            "enqueued_at",
            "id",
        ),
        CheckConstraint("length(payload_hash) = 64", name="ck_integration_payload_hash"),
        CheckConstraint(
            "(payload_encrypted LIKE 'lians-sealed:v1:%' OR "
            "payload_encrypted LIKE 'lians-sealed:v2:%')",
            name="ck_integration_payload_sealed",
        ),
    )


class IntegrationDelivery(Base):
    """A leased delivery run; retries mutate this projection, replays create a new run."""

    __tablename__ = "integration_deliveries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String(255), nullable=True, index=True)
    event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("integration_outbox_events.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    destination_id = Column(
        UUID(as_uuid=True),
        ForeignKey("integration_destinations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    run_sequence = Column(Integer, nullable=False, default=1, server_default="1")
    replayed_from_id = Column(
        UUID(as_uuid=True),
        ForeignKey("integration_deliveries.id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    lease_owner = Column(String(255), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    dead_lettered_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    last_status_code = Column(Integer, nullable=True)
    last_error_code = Column(String(100), nullable=True)
    last_error_digest = Column(String(64), nullable=True)
    last_response_digest = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "destination_id",
            "run_sequence",
            name="uq_integration_delivery_event_destination_run",
        ),
        UniqueConstraint("replayed_from_id", name="uq_integration_delivery_replayed_from"),
        Index(
            "ix_integration_delivery_due",
            "status",
            "next_attempt_at",
            "lease_expires_at",
        ),
        Index(
            "ix_integration_delivery_ns_status",
            "namespace",
            "status",
            "updated_at",
        ),
        CheckConstraint("run_sequence > 0", name="ck_integration_delivery_run_sequence"),
        CheckConstraint("attempt_count >= 0", name="ck_integration_delivery_attempt_count"),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_integration_delivery_idempotency_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'leased', 'retry', 'delivered', 'dead_letter', 'cancelled')",
            name="ck_integration_delivery_status",
        ),
        CheckConstraint(
            "(status = 'leased' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status != 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_integration_delivery_lease_shape",
        ),
        CheckConstraint(
            "(status != 'delivered' OR delivered_at IS NOT NULL) AND "
            "(status != 'dead_letter' OR dead_lettered_at IS NOT NULL) AND "
            "(status != 'cancelled' OR cancelled_at IS NOT NULL)",
            name="ck_integration_delivery_terminal_shape",
        ),
    )


class IntegrationDeliveryAttempt(Base):
    """Append-only, response-body-free record of one network attempt."""

    __tablename__ = "integration_delivery_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String(255), nullable=True, index=True)
    delivery_id = Column(
        UUID(as_uuid=True),
        ForeignKey("integration_deliveries.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attempt_number = Column(Integer, nullable=False)
    worker_id = Column(String(255), nullable=False)
    outcome = Column(String(32), nullable=False, index=True)
    status_code = Column(Integer, nullable=True)
    error_code = Column(String(100), nullable=True)
    error_digest = Column(String(64), nullable=True)
    response_digest = Column(String(64), nullable=True)
    duration_ms = Column(Integer, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "delivery_id",
            "attempt_number",
            name="uq_integration_attempt_delivery_number",
        ),
        Index(
            "ix_integration_attempt_ns_time",
            "namespace",
            "finished_at",
        ),
        CheckConstraint("attempt_number > 0", name="ck_integration_attempt_number"),
        CheckConstraint("duration_ms >= 0", name="ck_integration_attempt_duration"),
        CheckConstraint(
            "outcome IN ('delivered', 'retry', 'dead_letter', 'cancelled', 'lease_lost')",
            name="ck_integration_attempt_outcome",
        ),
    )
