"""Add durable fixed-snapshot Recorder evidence indexing.

Revision ID: 0058_recorder_index_jobs
Revises: 0057_decision_auth_snapshot
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0058_recorder_index_jobs"
down_revision = "0057_decision_auth_snapshot"
branch_labels = None
depends_on = None

_TABLE = "recorder_evidence_index_jobs"
_FENCE_HASH_SEED = 1_106_713_909
_REGISTRATION_FENCE_HASH_SEED = 1_279_873_363


def _install_postgresql_boundaries() -> None:
    op.execute(
        f"""CREATE FUNCTION public.lians_recorder_decision_fence()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_decision_id uuid;
        BEGIN
            IF TG_TABLE_NAME = 'decision_records' THEN
                v_decision_id := NEW.id;
            ELSIF TG_TABLE_NAME = 'recorder_events' THEN
                v_decision_id := NEW.decision_id;
            ELSE
                RAISE EXCEPTION 'Recorder decision fence attached incorrectly';
            END IF;
            IF v_decision_id IS NOT NULL THEN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(
                        NEW.namespace,
                        {_REGISTRATION_FENCE_HASH_SEED}
                    )
                );
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(
                        NEW.namespace || ':' || v_decision_id::text,
                        {_FENCE_HASH_SEED}
                    )
                );
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_recorder_decision_fence() "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute(
        """CREATE TRIGGER trg_00_decision_recorder_fence
        BEFORE INSERT ON public.decision_records
        FOR EACH ROW EXECUTE FUNCTION public.lians_recorder_decision_fence()"""
    )
    op.execute(
        """CREATE TRIGGER trg_00_recorder_event_decision_fence
        BEFORE INSERT ON public.recorder_events
        FOR EACH ROW EXECUTE FUNCTION public.lians_recorder_decision_fence()"""
    )
    op.execute(
        """CREATE FUNCTION public.lians_recorder_index_job_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF ROW(
                NEW.id, NEW.namespace, NEW.barrier_group, NEW.barrier_scope,
                NEW.decision_id, NEW.queued_by_principal_ref,
                NEW.queued_by_auth_method, NEW.snapshot_max_recorded_at,
                NEW.snapshot_max_event_id, NEW.snapshot_event_count,
                NEW.attempt_limit, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id, OLD.namespace, OLD.barrier_group, OLD.barrier_scope,
                OLD.decision_id, OLD.queued_by_principal_ref,
                OLD.queued_by_auth_method, OLD.snapshot_max_recorded_at,
                OLD.snapshot_max_event_id, OLD.snapshot_event_count,
                OLD.attempt_limit, OLD.created_at
            ) THEN
                RAISE EXCEPTION 'Recorder indexing snapshot identity is immutable';
            END IF;
            IF OLD.status = 'completed' THEN
                RAISE EXCEPTION 'Completed Recorder indexing jobs are immutable';
            END IF;
            IF OLD.status = 'failed' AND NEW.status <> 'pending' THEN
                RAISE EXCEPTION 'Failed Recorder indexing jobs require an explicit retry';
            END IF;
            IF NEW.events_indexed < OLD.events_indexed
               OR NEW.artifacts_created < OLD.artifacts_created
               OR NEW.links_created < OLD.links_created
               OR NEW.pages_completed < OLD.pages_completed
               OR NEW.processing_attempts < OLD.processing_attempts THEN
                RAISE EXCEPTION 'Recorder indexing progress cannot move backward';
            END IF;
            IF OLD.cursor_recorded_at IS NOT NULL AND (
                NEW.cursor_recorded_at IS NULL
                OR ROW(NEW.cursor_recorded_at, NEW.cursor_event_id)
                   < ROW(OLD.cursor_recorded_at, OLD.cursor_event_id)
            ) THEN
                RAISE EXCEPTION 'Recorder indexing cursor cannot move backward';
            END IF;
            IF OLD.started_at IS NOT NULL
               AND NEW.started_at IS DISTINCT FROM OLD.started_at THEN
                RAISE EXCEPTION 'Recorder indexing start time is immutable';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_recorder_index_job_guard() "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute(
        f"""CREATE TRIGGER trg_recorder_index_job_guard
        BEFORE UPDATE ON public.{_TABLE}
        FOR EACH ROW EXECUTE FUNCTION public.lians_recorder_index_job_guard()"""
    )
    op.execute(
        """CREATE FUNCTION public.lians_recorder_index_job_reject_removal()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION 'Recorder indexing jobs are durable state';
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_recorder_index_job_reject_removal() "
        "FROM PUBLIC, lians_runtime"
    )
    for operation in ("DELETE", "TRUNCATE"):
        level = "ROW" if operation == "DELETE" else "STATEMENT"
        op.execute(
            f"""CREATE TRIGGER trg_recorder_index_job_reject_{operation.lower()}
            BEFORE {operation} ON public.{_TABLE}
            FOR EACH {level} EXECUTE FUNCTION
                public.lians_recorder_index_job_reject_removal()"""
        )


def _install_sqlite_boundaries() -> None:
    op.execute(
        f"""CREATE TRIGGER trg_recorder_index_job_guard
        BEFORE UPDATE ON {_TABLE}
        BEGIN
            SELECT RAISE(ABORT, 'Recorder indexing snapshot identity is immutable')
            WHERE
                NEW.id IS NOT OLD.id
                OR NEW.namespace IS NOT OLD.namespace
                OR NEW.barrier_group IS NOT OLD.barrier_group
                OR NEW.barrier_scope IS NOT OLD.barrier_scope
                OR NEW.decision_id IS NOT OLD.decision_id
                OR NEW.queued_by_principal_ref IS NOT OLD.queued_by_principal_ref
                OR NEW.queued_by_auth_method IS NOT OLD.queued_by_auth_method
                OR NEW.snapshot_max_recorded_at IS NOT OLD.snapshot_max_recorded_at
                OR NEW.snapshot_max_event_id IS NOT OLD.snapshot_max_event_id
                OR NEW.snapshot_event_count IS NOT OLD.snapshot_event_count
                OR NEW.attempt_limit IS NOT OLD.attempt_limit
                OR NEW.created_at IS NOT OLD.created_at;
            SELECT RAISE(ABORT, 'Completed Recorder indexing jobs are immutable')
            WHERE OLD.status = 'completed';
            SELECT RAISE(ABORT, 'Failed Recorder indexing jobs require an explicit retry')
            WHERE OLD.status = 'failed' AND NEW.status <> 'pending';
            SELECT RAISE(ABORT, 'Recorder indexing progress cannot move backward')
            WHERE
                NEW.events_indexed < OLD.events_indexed
                OR NEW.artifacts_created < OLD.artifacts_created
                OR NEW.links_created < OLD.links_created
                OR NEW.pages_completed < OLD.pages_completed
                OR NEW.processing_attempts < OLD.processing_attempts;
            SELECT RAISE(ABORT, 'Recorder indexing cursor cannot move backward')
            WHERE OLD.cursor_recorded_at IS NOT NULL AND (
                NEW.cursor_recorded_at IS NULL
                OR NEW.cursor_recorded_at < OLD.cursor_recorded_at
                OR (
                    NEW.cursor_recorded_at = OLD.cursor_recorded_at
                    AND NEW.cursor_event_id < OLD.cursor_event_id
                )
            );
            SELECT RAISE(ABORT, 'Recorder indexing start time is immutable')
            WHERE OLD.started_at IS NOT NULL AND NEW.started_at IS NOT OLD.started_at;
        END"""
    )
    op.execute(
        f"""CREATE TRIGGER trg_recorder_index_job_reject_delete
        BEFORE DELETE ON {_TABLE}
        BEGIN
            SELECT RAISE(ABORT, 'Recorder indexing jobs are durable state');
        END"""
    )


def _install_postgresql_rls_and_grants() -> None:
    op.execute(f"ALTER TABLE public.{_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY rls_{_TABLE}_namespace ON public.{_TABLE}
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
        f"""CREATE POLICY rls_{_TABLE}_barrier ON public.{_TABLE}
        AS RESTRICTIVE
        USING (
            barrier_group IS NULL
            OR current_setting('app.current_namespace', true) = '__admin__'
            OR current_setting('agentmem.barrier_group', true) = ''
            OR barrier_group = current_setting('agentmem.barrier_group', true)
        )
        WITH CHECK (
            barrier_group IS NULL
            OR current_setting('app.current_namespace', true) = '__admin__'
            OR current_setting('agentmem.barrier_group', true) = ''
            OR barrier_group = current_setting('agentmem.barrier_group', true)
        )"""
    )
    op.execute(f"REVOKE ALL ON TABLE public.{_TABLE} FROM PUBLIC, lians_runtime")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON TABLE public.{_TABLE} TO lians_runtime"
    )


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("barrier_scope", sa.String(64), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("queued_by_principal_ref", sa.String(512), nullable=False),
        sa.Column("queued_by_auth_method", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("snapshot_max_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_max_event_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_event_count", sa.BigInteger(), nullable=False),
        sa.Column("cursor_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_event_id", sa.Uuid(), nullable=True),
        sa.Column("events_indexed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("artifacts_created", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("links_created", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("pages_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_limit", sa.Integer(), nullable=False, server_default="8"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_digest", sa.String(64), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_recorder_index_job_status",
        ),
        sa.CheckConstraint(
            "snapshot_event_count > 500 AND events_indexed >= 0 "
            "AND events_indexed <= snapshot_event_count "
            "AND artifacts_created >= 0 AND links_created >= 0 "
            "AND pages_completed >= 0",
            name="ck_recorder_index_job_progress",
        ),
        sa.CheckConstraint(
            "(cursor_recorded_at IS NULL AND cursor_event_id IS NULL) OR "
            "(cursor_recorded_at IS NOT NULL AND cursor_event_id IS NOT NULL)",
            name="ck_recorder_index_job_cursor_pair",
        ),
        sa.CheckConstraint(
            "processing_attempts >= 0 AND consecutive_failures >= 0 "
            "AND consecutive_failures <= processing_attempts "
            "AND attempt_limit >= 1 AND attempt_limit <= 100",
            name="ck_recorder_index_job_attempts",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_recorder_index_job_lease_pair",
        ),
        sa.CheckConstraint(
            "(last_error_code IS NULL AND last_error_digest IS NULL) OR "
            "(last_error_code IS NOT NULL AND last_error_digest IS NOT NULL "
            "AND length(last_error_digest) = 64)",
            name="ck_recorder_index_job_error_pair",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND events_indexed = snapshot_event_count) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name="ck_recorder_index_job_completion",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND failed_at IS NOT NULL AND failure_code IS NOT NULL) "
            "OR (status <> 'failed' AND failed_at IS NULL AND failure_code IS NULL)",
            name="ck_recorder_index_job_failure",
        ),
        sa.CheckConstraint(
            "status NOT IN ('completed','failed') OR "
            "(lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_recorder_index_job_terminal_lease",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            name="fk_recorder_index_job_decision_namespace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_recorder_index_job_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "decision_id", name="uq_recorder_index_job_decision"
        ),
    )
    op.create_index(
        "ix_recorder_index_job_claim",
        _TABLE,
        ["status", "next_attempt_at", "lease_expires_at", "created_at", "id"],
    )
    op.create_index(
        "ix_recorder_index_job_scope_status",
        _TABLE,
        ["namespace", "barrier_scope", "status", "created_at", "id"],
    )
    op.create_index("ix_recorder_index_jobs_namespace", _TABLE, ["namespace"])
    op.create_index("ix_recorder_index_jobs_barrier_group", _TABLE, ["barrier_group"])
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _install_postgresql_rls_and_grants()
        _install_postgresql_boundaries()
    elif dialect == "sqlite":
        _install_sqlite_boundaries()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for trigger, table in (
            ("trg_00_decision_recorder_fence", "decision_records"),
            ("trg_00_recorder_event_decision_fence", "recorder_events"),
            ("trg_recorder_index_job_guard", _TABLE),
            ("trg_recorder_index_job_reject_delete", _TABLE),
            ("trg_recorder_index_job_reject_truncate", _TABLE),
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON public.{table}")
        for function in (
            "lians_recorder_decision_fence",
            "lians_recorder_index_job_guard",
            "lians_recorder_index_job_reject_removal",
        ):
            op.execute(f"DROP FUNCTION IF EXISTS public.{function}()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_recorder_index_job_guard")
        op.execute("DROP TRIGGER IF EXISTS trg_recorder_index_job_reject_delete")
    op.drop_index("ix_recorder_index_jobs_barrier_group", table_name=_TABLE)
    op.drop_index("ix_recorder_index_jobs_namespace", table_name=_TABLE)
    op.drop_index("ix_recorder_index_job_scope_status", table_name=_TABLE)
    op.drop_index("ix_recorder_index_job_claim", table_name=_TABLE)
    op.drop_table(_TABLE)
