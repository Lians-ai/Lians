"""Durable enterprise integration outbox and delivery ledger.

Revision ID: 0033_integration_outbox
Revises: 0032_immutable_attestations
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_integration_outbox"
down_revision = "0032_immutable_attestations"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True)


_TABLES = (
    "integration_destinations",
    "integration_outbox_events",
    "integration_deliveries",
    "integration_delivery_attempts",
)
_APPEND_ONLY_TABLES = (
    "integration_outbox_events",
    "integration_delivery_attempts",
)


def upgrade() -> None:
    op.create_table(
        "integration_destinations",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("destination_type", sa.String(length=32), nullable=False),
        sa.Column("url_origin", sa.String(length=512), nullable=False),
        sa.Column("url_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("event_patterns", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "payload_profile", sa.String(length=32), nullable=False, server_default="cloudevents"
        ),
        sa.Column("credential_kind", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("custom_header_names", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("secret_config_encrypted", sa.Text(), nullable=False),
        sa.Column("secret_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("namespace", "name", name="uq_integration_destination_namespace_name"),
        sa.CheckConstraint(
            "destination_type IN ('generic_http', 'siem', 'grc', 'ticketing', 'billing')",
            name="ck_integration_destination_type",
        ),
        sa.CheckConstraint(
            "payload_profile IN ('cloudevents', 'raw')",
            name="ck_integration_destination_payload_profile",
        ),
        sa.CheckConstraint(
            "credential_kind IN ('none', 'bearer', 'basic', 'api_key_header', 'splunk_hec')",
            name="ck_integration_destination_credential_kind",
        ),
        sa.CheckConstraint(
            "secret_config_encrypted LIKE 'lians-sealed:v1:%'",
            name="ck_integration_destination_secret_sealed",
        ),
        sa.CheckConstraint(
            "length(url_fingerprint) = 64",
            name="ck_integration_destination_url_fingerprint",
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 50", name="ck_integration_destination_max_attempts"
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 120", name="ck_integration_destination_timeout"
        ),
        sa.CheckConstraint("version > 0", name="ck_integration_destination_version"),
    )
    op.create_index(
        "ix_integration_destinations_namespace", "integration_destinations", ["namespace"]
    )
    op.create_index(
        "ix_integration_destinations_barrier_group", "integration_destinations", ["barrier_group"]
    )
    op.create_index(
        "ix_integration_destinations_destination_type",
        "integration_destinations",
        ["destination_type"],
    )
    op.create_index(
        "ix_integration_destination_ns_enabled",
        "integration_destinations",
        ["namespace", "enabled", "revoked_at"],
    )

    op.create_table(
        "integration_outbox_events",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=255), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False, server_default="1"),
        sa.Column("aggregate_type", sa.String(length=100), nullable=True),
        sa.Column("aggregate_id", sa.String(length=512), nullable=True),
        sa.Column("source_event_id", _uuid(), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "namespace", "idempotency_key", name="uq_integration_outbox_namespace_idempotency"
        ),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_integration_payload_hash"),
        sa.CheckConstraint(
            "payload_encrypted LIKE 'lians-sealed:v1:%'",
            name="ck_integration_payload_sealed",
        ),
    )
    for name, columns in (
        ("ix_integration_outbox_events_namespace", ["namespace"]),
        ("ix_integration_outbox_events_barrier_group", ["barrier_group"]),
        ("ix_integration_outbox_events_event_type", ["event_type"]),
        ("ix_integration_outbox_events_aggregate_type", ["aggregate_type"]),
        ("ix_integration_outbox_events_aggregate_id", ["aggregate_id"]),
        ("ix_integration_outbox_events_source_event_id", ["source_event_id"]),
        ("ix_integration_outbox_events_correlation_id", ["correlation_id"]),
        ("ix_integration_outbox_events_payload_hash", ["payload_hash"]),
        ("ix_integration_outbox_events_enqueued_at", ["enqueued_at"]),
        ("ix_integration_outbox_ns_time", ["namespace", "enqueued_at", "id"]),
    ):
        op.create_index(name, "integration_outbox_events", columns)

    op.create_table(
        "integration_deliveries",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column(
            "event_id",
            _uuid(),
            sa.ForeignKey("integration_outbox_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            _uuid(),
            sa.ForeignKey("integration_destinations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("run_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "replayed_from_id",
            _uuid(),
            sa.ForeignKey("integration_deliveries.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_digest", sa.String(length=64), nullable=True),
        sa.Column("last_response_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "event_id",
            "destination_id",
            "run_sequence",
            name="uq_integration_delivery_event_destination_run",
        ),
        sa.UniqueConstraint("replayed_from_id", name="uq_integration_delivery_replayed_from"),
        sa.CheckConstraint("run_sequence > 0", name="ck_integration_delivery_run_sequence"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_integration_delivery_attempt_count"),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_integration_delivery_idempotency_key",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'retry', 'delivered', 'dead_letter', 'cancelled')",
            name="ck_integration_delivery_status",
        ),
        sa.CheckConstraint(
            "(status = 'leased' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status != 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_integration_delivery_lease_shape",
        ),
        sa.CheckConstraint(
            "(status != 'delivered' OR delivered_at IS NOT NULL) AND "
            "(status != 'dead_letter' OR dead_lettered_at IS NOT NULL) AND "
            "(status != 'cancelled' OR cancelled_at IS NOT NULL)",
            name="ck_integration_delivery_terminal_shape",
        ),
    )
    for name, columns in (
        ("ix_integration_deliveries_namespace", ["namespace"]),
        ("ix_integration_deliveries_barrier_group", ["barrier_group"]),
        ("ix_integration_deliveries_event_id", ["event_id"]),
        ("ix_integration_deliveries_destination_id", ["destination_id"]),
        ("ix_integration_deliveries_idempotency_key", ["idempotency_key"]),
        ("ix_integration_deliveries_next_attempt_at", ["next_attempt_at"]),
        ("ix_integration_deliveries_lease_expires_at", ["lease_expires_at"]),
        ("ix_integration_delivery_due", ["status", "next_attempt_at", "lease_expires_at"]),
        ("ix_integration_delivery_ns_status", ["namespace", "status", "updated_at"]),
    ):
        op.create_index(name, "integration_deliveries", columns)

    op.create_table(
        "integration_delivery_attempts",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column(
            "delivery_id",
            _uuid(),
            sa.ForeignKey("integration_deliveries.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_digest", sa.String(length=64), nullable=True),
        sa.Column("response_digest", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "delivery_id", "attempt_number", name="uq_integration_attempt_delivery_number"
        ),
        sa.CheckConstraint("attempt_number > 0", name="ck_integration_attempt_number"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_integration_attempt_duration"),
        sa.CheckConstraint(
            "outcome IN ('delivered', 'retry', 'dead_letter', 'cancelled', 'lease_lost')",
            name="ck_integration_attempt_outcome",
        ),
    )
    for name, columns in (
        ("ix_integration_delivery_attempts_namespace", ["namespace"]),
        ("ix_integration_delivery_attempts_barrier_group", ["barrier_group"]),
        ("ix_integration_delivery_attempts_delivery_id", ["delivery_id"]),
        ("ix_integration_delivery_attempts_outcome", ["outcome"]),
        ("ix_integration_attempt_ns_time", ["namespace", "finished_at"]),
    ):
        op.create_index(name, "integration_delivery_attempts", columns)

    if op.get_bind().dialect.name == "postgresql":
        _install_postgres_guards()
    elif op.get_bind().dialect.name == "sqlite":
        _install_sqlite_guards()


def _install_postgres_guards() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY rls_{table}_namespace ON {table}
            USING (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )
            WITH CHECK (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )"""
        )
        op.execute(
            f"""CREATE POLICY rls_{table}_barrier ON {table} AS RESTRICTIVE
            USING (
                barrier_group IS NULL
                OR current_setting('agentmem.barrier_group', true) IS NULL
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )
            WITH CHECK (
                barrier_group IS NULL
                OR current_setting('agentmem.barrier_group', true) IS NULL
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )"""
        )

    op.execute(
        """CREATE FUNCTION lians_integration_reject_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql"""
    )
    for table in _APPEND_ONLY_TABLES:
        op.execute(
            f"""CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION lians_integration_reject_mutation()"""
        )

    op.execute(
        """CREATE FUNCTION lians_integration_destination_boundary()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.namespace IS DISTINCT FROM NEW.namespace
               OR OLD.barrier_group IS DISTINCT FROM NEW.barrier_group
               OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
                RAISE EXCEPTION 'integration destination boundary fields are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER trg_integration_destination_boundary
        BEFORE UPDATE ON integration_destinations
        FOR EACH ROW EXECUTE FUNCTION lians_integration_destination_boundary()"""
    )

    op.execute(
        """CREATE FUNCTION lians_integration_delivery_boundary()
        RETURNS trigger AS $$
        DECLARE outbox integration_outbox_events%ROWTYPE;
        DECLARE destination integration_destinations%ROWTYPE;
        DECLARE prior integration_deliveries%ROWTYPE;
        BEGIN
            IF TG_OP = 'UPDATE' AND (
                OLD.id IS DISTINCT FROM NEW.id
                OR OLD.namespace IS DISTINCT FROM NEW.namespace
                OR OLD.barrier_group IS DISTINCT FROM NEW.barrier_group
                OR OLD.event_id IS DISTINCT FROM NEW.event_id
                OR OLD.destination_id IS DISTINCT FROM NEW.destination_id
                OR OLD.run_sequence IS DISTINCT FROM NEW.run_sequence
                OR OLD.replayed_from_id IS DISTINCT FROM NEW.replayed_from_id
                OR OLD.idempotency_key IS DISTINCT FROM NEW.idempotency_key
                OR OLD.created_at IS DISTINCT FROM NEW.created_at
            ) THEN
                RAISE EXCEPTION 'integration delivery identity fields are immutable';
            END IF;
            SELECT * INTO outbox FROM integration_outbox_events WHERE id = NEW.event_id;
            SELECT * INTO destination FROM integration_destinations WHERE id = NEW.destination_id;
            IF NOT FOUND OR outbox.id IS NULL OR destination.id IS NULL
               OR outbox.namespace IS DISTINCT FROM NEW.namespace
               OR destination.namespace IS DISTINCT FROM NEW.namespace
               OR NEW.barrier_group IS DISTINCT FROM COALESCE(
                    outbox.barrier_group, destination.barrier_group
               )
               OR (
                    outbox.barrier_group IS NOT NULL
                    AND destination.barrier_group IS NOT NULL
                    AND outbox.barrier_group IS DISTINCT FROM destination.barrier_group
               ) THEN
                RAISE EXCEPTION 'integration delivery crosses a namespace or barrier boundary';
            END IF;
            IF NEW.replayed_from_id IS NULL AND NEW.run_sequence != 1 THEN
                RAISE EXCEPTION 'an initial integration delivery must use run_sequence 1';
            ELSIF NEW.replayed_from_id IS NOT NULL THEN
                SELECT * INTO prior FROM integration_deliveries WHERE id = NEW.replayed_from_id;
                IF NOT FOUND
                   OR prior.namespace IS DISTINCT FROM NEW.namespace
                   OR prior.event_id IS DISTINCT FROM NEW.event_id
                   OR prior.destination_id IS DISTINCT FROM NEW.destination_id
                   OR prior.run_sequence + 1 IS DISTINCT FROM NEW.run_sequence
                   OR prior.idempotency_key IS DISTINCT FROM NEW.idempotency_key
                   OR prior.status NOT IN ('dead_letter', 'cancelled') THEN
                    RAISE EXCEPTION 'invalid integration delivery replay predecessor';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER trg_integration_delivery_boundary
        BEFORE INSERT OR UPDATE ON integration_deliveries
        FOR EACH ROW EXECUTE FUNCTION lians_integration_delivery_boundary()"""
    )
    op.execute(
        """CREATE FUNCTION lians_integration_attempt_boundary()
        RETURNS trigger AS $$
        DECLARE delivery integration_deliveries%ROWTYPE;
        BEGIN
            SELECT * INTO delivery FROM integration_deliveries WHERE id = NEW.delivery_id;
            IF NOT FOUND
               OR delivery.namespace IS DISTINCT FROM NEW.namespace
               OR delivery.barrier_group IS DISTINCT FROM NEW.barrier_group THEN
                RAISE EXCEPTION 'integration attempt crosses a delivery boundary';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER trg_integration_attempt_boundary
        BEFORE INSERT ON integration_delivery_attempts
        FOR EACH ROW EXECUTE FUNCTION lians_integration_attempt_boundary()"""
    )


def _install_sqlite_guards() -> None:
    for table in _APPEND_ONLY_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"""CREATE TRIGGER trg_{table}_no_{operation.lower()}
                BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"""
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS lians_integration_attempt_boundary() CASCADE")
        op.execute("DROP FUNCTION IF EXISTS lians_integration_delivery_boundary() CASCADE")
        op.execute("DROP FUNCTION IF EXISTS lians_integration_destination_boundary() CASCADE")
        op.execute("DROP FUNCTION IF EXISTS lians_integration_reject_mutation() CASCADE")
    op.drop_table("integration_delivery_attempts")
    op.drop_table("integration_deliveries")
    op.drop_table("integration_outbox_events")
    op.drop_table("integration_destinations")
