"""Add durable leases and retry state for autonomous impact processing.

Revision ID: 0049_autonomous_impact_worker
Revises: 0048_observability_indexes
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_autonomous_impact_worker"
down_revision = "0048_observability_indexes"
branch_labels = None
depends_on = None

_TABLE = "decision_impact_assessment_jobs"
_BASE_GUARD_TRIGGER = "trg_decision_impact_assessment_jobs_guard_update"
_WORKER_GUARD_TRIGGER = "trg_decision_impact_assessment_jobs_worker_guard_update"
_CONSTRAINTS = (
    (
        "ck_impact_assessment_attempts",
        "processing_attempts >= 0 AND consecutive_failures >= 0 "
        "AND consecutive_failures <= processing_attempts "
        "AND attempt_limit >= 1 AND attempt_limit <= 100",
    ),
    (
        "ck_impact_assessment_lease_pair",
        "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
        "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
    ),
    (
        "ck_impact_assessment_error_pair",
        "(last_error_code IS NULL AND last_error_digest IS NULL) OR "
        "(last_error_code IS NOT NULL AND last_error_digest IS NOT NULL "
        "AND length(last_error_digest) = 64)",
    ),
    (
        "ck_impact_assessment_failure_state",
        "(status = 'failed' AND failed_at IS NOT NULL "
        "AND failure_code IS NOT NULL) OR "
        "(status <> 'failed' AND failed_at IS NULL)",
    ),
    (
        "ck_impact_assessment_terminal_lease",
        "status NOT IN ('completed','failed') OR "
        "(lease_owner IS NULL AND lease_expires_at IS NULL)",
    ),
)


def _install_postgresql_guard(*, worker_fields: bool) -> None:
    """Keep snapshot identity immutable and progress monotonic at the DB boundary."""

    new_attempt_identity = ", NEW.attempt_limit" if worker_fields else ""
    old_attempt_identity = ", OLD.attempt_limit" if worker_fields else ""
    worker_rules = (
        """
            IF NEW.processing_attempts < OLD.processing_attempts THEN
                RAISE EXCEPTION 'impact assessment attempts cannot move backward';
            END IF;
            IF NEW.consecutive_failures < 0
               OR NEW.consecutive_failures > NEW.processing_attempts
               OR NEW.attempt_limit < 1
               OR NEW.attempt_limit > 100 THEN
                RAISE EXCEPTION 'impact assessment attempt state is invalid';
            END IF;
            IF (NEW.lease_owner IS NULL) <>
               (NEW.lease_expires_at IS NULL) THEN
                RAISE EXCEPTION 'impact assessment lease state is invalid';
            END IF;
            IF NEW.status IN ('completed', 'failed')
               AND (NEW.lease_owner IS NOT NULL
                    OR NEW.lease_expires_at IS NOT NULL) THEN
                RAISE EXCEPTION 'terminal impact assessment cannot retain a lease';
            END IF;
            IF (NEW.last_error_code IS NULL) <>
               (NEW.last_error_digest IS NULL)
               OR (
                    NEW.last_error_digest IS NOT NULL
                    AND length(NEW.last_error_digest) <> 64
               ) THEN
                RAISE EXCEPTION 'impact assessment error state is invalid';
            END IF;
            IF NEW.status = 'failed' AND NEW.failed_at IS NULL THEN
                RAISE EXCEPTION 'failed impact assessment requires failed_at';
            END IF;
            IF NEW.status <> 'failed' AND NEW.failed_at IS NOT NULL THEN
                RAISE EXCEPTION 'nonfailed impact assessment cannot have failed_at';
            END IF;
        """
        if worker_fields
        else ""
    )
    op.execute(
        f"""CREATE OR REPLACE FUNCTION public.lians_impact_job_guard_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF ROW(
                NEW.id,
                NEW.namespace,
                NEW.barrier_group,
                NEW.barrier_scope,
                NEW.idempotency_key_hash,
                NEW.request_fingerprint,
                NEW.dependency_kind,
                NEW.dependency_value,
                NEW.dependency_lookup_hash,
                NEW.change_type,
                NEW.change_occurred_at,
                NEW.note,
                NEW.requested_by_principal_ref,
                NEW.requested_by_auth_method,
                NEW.snapshot_max_coverage_sequence,
                NEW.snapshot_max_link_sequence,
                NEW.record_event,
                NEW.created_at
                {new_attempt_identity}
            ) IS DISTINCT FROM ROW(
                OLD.id,
                OLD.namespace,
                OLD.barrier_group,
                OLD.barrier_scope,
                OLD.idempotency_key_hash,
                OLD.request_fingerprint,
                OLD.dependency_kind,
                OLD.dependency_value,
                OLD.dependency_lookup_hash,
                OLD.change_type,
                OLD.change_occurred_at,
                OLD.note,
                OLD.requested_by_principal_ref,
                OLD.requested_by_auth_method,
                OLD.snapshot_max_coverage_sequence,
                OLD.snapshot_max_link_sequence,
                OLD.record_event,
                OLD.created_at
                {old_attempt_identity}
            ) THEN
                RAISE EXCEPTION 'impact assessment snapshot identity is immutable';
            END IF;
            IF OLD.status IN ('completed', 'failed') THEN
                RAISE EXCEPTION 'terminal impact assessment is immutable';
            END IF;
            IF OLD.status = 'running' AND NEW.status = 'pending' THEN
                RAISE EXCEPTION 'impact assessment status cannot move backward';
            END IF;
            IF NEW.cursor_coverage_sequence < OLD.cursor_coverage_sequence
               OR NEW.decisions_scanned < OLD.decisions_scanned
               OR NEW.fallback_candidates_scanned <
                    OLD.fallback_candidates_scanned
               OR NEW.indexed_decisions_matched < OLD.indexed_decisions_matched
               OR NEW.legacy_decisions_matched < OLD.legacy_decisions_matched
               OR NEW.matches_found < OLD.matches_found
               OR NEW.direct_count < OLD.direct_count
               OR NEW.reachable_count < OLD.reachable_count
               OR NEW.pages_completed < OLD.pages_completed
               OR NEW.updated_at < OLD.updated_at THEN
                RAISE EXCEPTION 'impact assessment progress cannot move backward';
            END IF;
            IF OLD.started_at IS NOT NULL
               AND NEW.started_at IS DISTINCT FROM OLD.started_at THEN
                RAISE EXCEPTION 'impact assessment start time is immutable';
            END IF;
            IF OLD.completion_event_id IS NOT NULL
               AND NEW.completion_event_id IS DISTINCT FROM
                    OLD.completion_event_id THEN
                RAISE EXCEPTION 'impact assessment completion event is immutable';
            END IF;
            IF NEW.status = 'completed' AND (
                NEW.cursor_coverage_sequence <>
                    NEW.snapshot_max_coverage_sequence
                OR NEW.completed_at IS NULL
                OR NEW.matches_found <> NEW.direct_count + NEW.reachable_count
                OR (NEW.record_event AND NEW.completion_event_id IS NULL)
                OR (NOT NEW.record_event AND NEW.completion_event_id IS NOT NULL)
            ) THEN
                RAISE EXCEPTION 'impact assessment completion is inconsistent';
            END IF;
            IF NEW.status <> 'completed' AND (
                NEW.completed_at IS NOT NULL
                OR NEW.completion_event_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'unfinished impact assessment cannot be completed';
            END IF;
            IF NEW.status IN ('running', 'completed', 'failed')
               AND NEW.started_at IS NULL THEN
                RAISE EXCEPTION 'started impact assessment requires start time';
            END IF;
            IF NEW.status = 'failed' AND NEW.failure_code IS NULL THEN
                RAISE EXCEPTION 'failed impact assessment requires failure code';
            END IF;
            {worker_rules}
            RETURN NEW;
        END;
        $$"""
    )


def _install_sqlite_worker_guard() -> None:
    """Add worker-state invariants without replacing the 0043 snapshot guard."""

    op.execute(f"DROP TRIGGER IF EXISTS {_WORKER_GUARD_TRIGGER}")
    op.execute(
        f"""CREATE TRIGGER {_WORKER_GUARD_TRIGGER}
        BEFORE UPDATE ON {_TABLE}
        BEGIN
            SELECT RAISE(
                ABORT,
                'impact assessment attempt limit is immutable'
            ) WHERE NEW.attempt_limit IS NOT OLD.attempt_limit;
            SELECT RAISE(
                ABORT,
                'impact assessment attempts cannot move backward'
            ) WHERE NEW.processing_attempts < OLD.processing_attempts;
            SELECT RAISE(
                ABORT,
                'impact assessment attempt state is invalid'
            ) WHERE
                NEW.consecutive_failures < 0
                OR NEW.consecutive_failures > NEW.processing_attempts
                OR NEW.attempt_limit < 1
                OR NEW.attempt_limit > 100;
            SELECT RAISE(
                ABORT,
                'impact assessment lease state is invalid'
            ) WHERE
                (NEW.lease_owner IS NULL) IS NOT
                (NEW.lease_expires_at IS NULL);
            SELECT RAISE(
                ABORT,
                'terminal impact assessment cannot retain a lease'
            ) WHERE
                NEW.status IN ('completed', 'failed')
                AND (
                    NEW.lease_owner IS NOT NULL
                    OR NEW.lease_expires_at IS NOT NULL
                );
            SELECT RAISE(
                ABORT,
                'impact assessment error state is invalid'
            ) WHERE
                (NEW.last_error_code IS NULL) IS NOT
                    (NEW.last_error_digest IS NULL)
                OR (
                    NEW.last_error_digest IS NOT NULL
                    AND length(NEW.last_error_digest) <> 64
                );
            SELECT RAISE(
                ABORT,
                'impact assessment failure state is invalid'
            ) WHERE
                (NEW.status = 'failed' AND NEW.failed_at IS NULL)
                OR (NEW.status <> 'failed' AND NEW.failed_at IS NOT NULL);
        END"""
    )


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "processing_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column("attempt_limit", sa.Integer(), nullable=False, server_default="8"),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(_TABLE, sa.Column("lease_owner", sa.String(255), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(_TABLE, sa.Column("last_error_code", sa.String(64), nullable=True))
    op.add_column(_TABLE, sa.Column("last_error_digest", sa.String(64), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    dialect = op.get_bind().dialect.name
    sqlite_base_guard_sql: str | None = None
    if dialect == "postgresql":
        # The 0043 guard deliberately makes terminal rows immutable. Temporarily
        # suspend only that guard inside this transactional migration so legacy
        # failed rows can receive the newly required failure timestamp.
        op.execute(
            f"ALTER TABLE public.{_TABLE} DISABLE TRIGGER {_BASE_GUARD_TRIGGER}"
        )
    elif dialect == "sqlite":
        sqlite_base_guard_sql = op.get_bind().execute(
            sa.text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'trigger' AND name = :name"
            ),
            {"name": _BASE_GUARD_TRIGGER},
        ).scalar_one_or_none()
        op.execute(f"DROP TRIGGER IF EXISTS {_BASE_GUARD_TRIGGER}")
    op.execute(
        sa.text(
            "UPDATE decision_impact_assessment_jobs "
            "SET failed_at = updated_at, "
            "failure_code = COALESCE(failure_code, 'legacy_failure') "
            "WHERE status = 'failed'"
        )
    )
    if dialect == "postgresql":
        op.execute(
            f"ALTER TABLE public.{_TABLE} ENABLE TRIGGER {_BASE_GUARD_TRIGGER}"
        )
    elif dialect == "sqlite" and sqlite_base_guard_sql is not None:
        op.execute(sa.text(sqlite_base_guard_sql))
    if dialect != "sqlite":
        for name, expression in _CONSTRAINTS:
            op.create_check_constraint(name, _TABLE, expression)
    op.create_index(
        "ix_impact_assessment_worker_claim",
        _TABLE,
        ["status", "next_attempt_at", "lease_expires_at", "created_at"],
        unique=False,
    )
    if dialect == "postgresql":
        _install_postgresql_guard(worker_fields=True)
    elif dialect == "sqlite":
        _install_sqlite_worker_guard()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _install_postgresql_guard(worker_fields=False)
    elif dialect == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {_WORKER_GUARD_TRIGGER}")
    op.drop_index("ix_impact_assessment_worker_claim", table_name=_TABLE)
    if dialect != "sqlite":
        for name, _expression in reversed(_CONSTRAINTS):
            op.drop_constraint(name, _TABLE, type_="check")
    for column in (
        "failed_at",
        "last_error_digest",
        "last_error_code",
        "last_attempt_at",
        "heartbeat_at",
        "lease_expires_at",
        "lease_owner",
        "next_attempt_at",
        "attempt_limit",
        "consecutive_failures",
        "processing_attempts",
    ):
        op.drop_column(_TABLE, column)
