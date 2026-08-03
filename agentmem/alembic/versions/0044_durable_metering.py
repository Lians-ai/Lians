"""Replace process-local Stripe metering with a durable delivery ledger.

Revision ID: 0044_durable_metering
Revises: 0043_evidence_impact_jobs
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_durable_metering"
down_revision = "0043_evidence_impact_jobs"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "metering_events",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("event_name", sa.String(length=100), nullable=False),
        sa.Column("customer_id", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("source_identifier_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_identifier", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_limit", sa.Integer(), nullable=False),
        sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_digest", sa.String(length=64), nullable=True),
        sa.Column("last_response_digest", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("id", "namespace", name="uq_metering_event_id_namespace"),
        sa.UniqueConstraint(
            "namespace",
            "event_name",
            "source_identifier_hash",
            name="uq_metering_event_source",
        ),
        sa.UniqueConstraint(
            "provider_identifier",
            name="uq_metering_events_provider_identifier",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_metering_event_quantity"),
        sa.CheckConstraint(
            "length(source_identifier_hash) = 64",
            name="ck_metering_event_source_hash",
        ),
        sa.CheckConstraint("length(request_hash) = 64", name="ck_metering_event_request_hash"),
        sa.CheckConstraint(
            "length(provider_identifier) BETWEEN 1 AND 100",
            name="ck_metering_event_provider_identifier",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'retry', 'delivered', 'dead_letter')",
            name="ck_metering_event_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_metering_event_attempt_count"),
        sa.CheckConstraint(
            "attempt_limit BETWEEN 1 AND 1000 AND attempt_count <= attempt_limit",
            name="ck_metering_event_attempt_limit",
        ),
        sa.CheckConstraint("replay_count >= 0", name="ck_metering_event_replay_count"),
        sa.CheckConstraint(
            "(status = 'leased' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status != 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_metering_event_lease_shape",
        ),
        sa.CheckConstraint(
            "(status = 'delivered') = (delivered_at IS NOT NULL)",
            name="ck_metering_event_delivered_shape",
        ),
        sa.CheckConstraint(
            "(status = 'dead_letter') = (dead_lettered_at IS NOT NULL)",
            name="ck_metering_event_dead_letter_shape",
        ),
        sa.CheckConstraint(
            "last_error_digest IS NULL OR length(last_error_digest) = 64",
            name="ck_metering_event_error_digest",
        ),
        sa.CheckConstraint(
            "last_response_digest IS NULL OR length(last_response_digest) = 64",
            name="ck_metering_event_response_digest",
        ),
    )
    for name, columns in (
        ("ix_metering_events_namespace", ["namespace"]),
        ("ix_metering_events_event_name", ["event_name"]),
        ("ix_metering_events_next_attempt_at", ["next_attempt_at"]),
        ("ix_metering_events_lease_expires_at", ["lease_expires_at"]),
        (
            "ix_metering_event_due",
            ["status", "next_attempt_at", "lease_expires_at"],
        ),
        (
            "ix_metering_event_namespace_status",
            ["namespace", "status", "updated_at"],
        ),
    ):
        op.create_index(name, "metering_events", columns)

    op.create_table(
        "metering_attempt_records",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("event_id", _uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("record_type", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_digest", sa.String(length=64), nullable=True),
        sa.Column("response_digest", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id", "namespace"],
            ["metering_events.id", "metering_events.namespace"],
            ondelete="RESTRICT",
            name="fk_metering_attempt_event_namespace",
        ),
        sa.UniqueConstraint(
            "event_id",
            "attempt_number",
            "record_type",
            name="uq_metering_attempt_event_number_record",
        ),
        sa.CheckConstraint("attempt_number > 0", name="ck_metering_attempt_number"),
        sa.CheckConstraint(
            "record_type IN ('started', 'finished')",
            name="ck_metering_attempt_record_type",
        ),
        sa.CheckConstraint(
            "outcome IN ('started', 'delivered', 'retry', 'dead_letter', 'lease_lost')",
            name="ck_metering_attempt_outcome",
        ),
        sa.CheckConstraint(
            "(record_type = 'started' AND outcome = 'started' AND duration_ms IS NULL) OR "
            "(record_type = 'finished' AND outcome != 'started' AND duration_ms IS NOT NULL)",
            name="ck_metering_attempt_record_shape",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_metering_attempt_duration",
        ),
        sa.CheckConstraint(
            "error_digest IS NULL OR length(error_digest) = 64",
            name="ck_metering_attempt_error_digest",
        ),
        sa.CheckConstraint(
            "response_digest IS NULL OR length(response_digest) = 64",
            name="ck_metering_attempt_response_digest",
        ),
    )
    for name, columns in (
        ("ix_metering_attempt_records_namespace", ["namespace"]),
        ("ix_metering_attempt_records_event_id", ["event_id"]),
        ("ix_metering_attempt_records_outcome", ["outcome"]),
        (
            "ix_metering_attempt_namespace_time",
            ["namespace", "recorded_at"],
        ),
    ):
        op.create_index(name, "metering_attempt_records", columns)

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _install_postgres_guards()
    elif dialect == "sqlite":
        _install_sqlite_guards()


def _install_postgres_guards() -> None:
    for table in ("metering_events", "metering_attempt_records"):
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
        """CREATE FUNCTION lians_metering_event_guard()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'metering_events cannot be deleted';
            END IF;
            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.namespace IS DISTINCT FROM NEW.namespace
               OR OLD.event_name IS DISTINCT FROM NEW.event_name
               OR OLD.customer_id IS DISTINCT FROM NEW.customer_id
               OR OLD.quantity IS DISTINCT FROM NEW.quantity
               OR OLD.source_identifier_hash IS DISTINCT FROM NEW.source_identifier_hash
               OR OLD.request_hash IS DISTINCT FROM NEW.request_hash
               OR OLD.provider_identifier IS DISTINCT FROM NEW.provider_identifier
               OR OLD.occurred_at IS DISTINCT FROM NEW.occurred_at
               OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
                RAISE EXCEPTION 'metering event identity fields are immutable';
            END IF;
            IF OLD.status = 'delivered' AND NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'delivered metering events are immutable';
            END IF;
            IF NOT (
                (OLD.status = 'pending' AND NEW.status IN ('pending', 'leased'))
                OR (OLD.status = 'retry' AND NEW.status IN ('retry', 'leased'))
                OR (OLD.status = 'leased' AND NEW.status IN (
                    'leased', 'retry', 'delivered', 'dead_letter'
                ))
                OR (OLD.status = 'dead_letter' AND NEW.status IN ('dead_letter', 'retry'))
                OR (OLD.status = 'delivered' AND NEW.status = 'delivered')
            ) THEN
                RAISE EXCEPTION 'invalid metering event status transition % -> %',
                    OLD.status, NEW.status;
            END IF;
            IF NEW.attempt_count < OLD.attempt_count
               OR NEW.attempt_count > OLD.attempt_count + 1 THEN
                RAISE EXCEPTION 'metering attempt_count must be monotonic by one';
            END IF;
            IF OLD.first_attempt_at IS NOT NULL
               AND NEW.first_attempt_at IS DISTINCT FROM OLD.first_attempt_at
               AND NOT (
                   OLD.status = 'dead_letter'
                   AND NEW.status = 'retry'
                   AND NEW.first_attempt_at IS NULL
                   AND NEW.attempt_limit > OLD.attempt_limit
                   AND NEW.replay_count = OLD.replay_count + 1
               ) THEN
                RAISE EXCEPTION 'metering first_attempt_at is immutable once set';
            END IF;
            IF OLD.status = 'dead_letter' AND NEW.status = 'retry' THEN
                IF NEW.attempt_limit <= OLD.attempt_limit
                   OR NEW.replay_count != OLD.replay_count + 1
                   OR NEW.attempt_count != OLD.attempt_count
                   OR NEW.first_attempt_at IS NOT NULL
                   OR NEW.last_attempt_at IS NOT NULL
                   OR NEW.last_status_code IS NOT NULL
                   OR NEW.last_error_code IS NOT NULL
                   OR NEW.last_error_digest IS NOT NULL
                   OR NEW.last_response_digest IS NOT NULL THEN
                    RAISE EXCEPTION 'invalid metering dead-letter replay mutation';
                END IF;
            ELSIF NEW.attempt_limit != OLD.attempt_limit
               OR NEW.replay_count != OLD.replay_count THEN
                RAISE EXCEPTION 'metering replay counters can only change during replay';
            END IF;
            IF OLD.status = 'leased'
               AND NEW.status = 'leased'
               AND NEW.lease_owner IS DISTINCT FROM OLD.lease_owner
               AND OLD.lease_expires_at > clock_timestamp() THEN
                RAISE EXCEPTION 'an unexpired metering lease cannot be stolen';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER trg_metering_events_guard
        BEFORE UPDATE OR DELETE ON metering_events
        FOR EACH ROW EXECUTE FUNCTION lians_metering_event_guard()"""
    )
    op.execute(
        """CREATE FUNCTION lians_metering_reject_attempt_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'metering_attempt_records is append-only';
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER trg_metering_attempt_records_append_only
        BEFORE UPDATE OR DELETE ON metering_attempt_records
        FOR EACH ROW EXECUTE FUNCTION lians_metering_reject_attempt_mutation()"""
    )
    op.execute(
        """CREATE FUNCTION lians_metering_reject_truncate()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'durable metering tables cannot be truncated';
        END;
        $$ LANGUAGE plpgsql"""
    )
    for table in ("metering_events", "metering_attempt_records"):
        op.execute(
            f"""CREATE TRIGGER trg_{table}_no_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION lians_metering_reject_truncate()"""
        )

    # 0039 establishes this fixed NOLOGIN/NOBYPASSRLS capability role and
    # migrator-owned default privileges. Narrow these immutable relations
    # explicitly so later grant drift cannot enable deletion or truncation.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON metering_events TO lians_runtime"
    )
    op.execute(
        "GRANT SELECT, INSERT ON metering_attempt_records TO lians_runtime"
    )
    op.execute(
        "REVOKE DELETE, TRUNCATE ON metering_events, metering_attempt_records "
        "FROM PUBLIC, lians_runtime"
    )
    # The audit append migration establishes broad baseline DML for rolling
    # relations.  A narrower GRANT does not remove that inherited privilege,
    # so explicitly close UPDATE on this append-only attempt ledger.
    op.execute(
        "REVOKE UPDATE ON metering_attempt_records FROM PUBLIC, lians_runtime"
    )


def _install_sqlite_guards() -> None:
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"""CREATE TRIGGER trg_metering_attempt_records_no_{operation.lower()}
            BEFORE {operation} ON metering_attempt_records
            BEGIN
                SELECT RAISE(ABORT, 'metering_attempt_records is append-only');
            END"""
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS lians_metering_reject_truncate() CASCADE")
        op.execute("DROP FUNCTION IF EXISTS lians_metering_reject_attempt_mutation() CASCADE")
        op.execute("DROP FUNCTION IF EXISTS lians_metering_event_guard() CASCADE")
    op.drop_table("metering_attempt_records")
    op.drop_table("metering_events")
