"""Add durable fixed-snapshot subject-erasure jobs and bounded evidence.

Revision ID: 0059_subject_erasure_jobs
Revises: 0058a_live_supersession_indexes
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0059_subject_erasure_jobs"
down_revision = "0058a_live_supersession_indexes"
branch_labels = None
depends_on = None

_JOB_TABLE = "subject_erasure_jobs"
_EVIDENCE_TABLE = "subject_erasure_memory_evidence"


def _install_postgresql_boundaries() -> None:
    op.execute(
        f"""CREATE TRIGGER trg_subject_erasure_jobs_master_key_fence
        BEFORE INSERT OR UPDATE OF subject_locator_encrypted
        ON public.{_JOB_TABLE}
        FOR EACH ROW EXECUTE FUNCTION public.lians_master_key_fence_sealed(
            'subject_locator_encrypted', 'nullable'
        )"""
    )
    op.execute(
        """CREATE FUNCTION public.lians_subject_erasure_job_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF ROW(
                NEW.id, NEW.namespace, NEW.subject_ref, NEW.request_ref,
                NEW.queued_by_principal_ref, NEW.queued_by_auth_method,
                NEW.key_destroyed_at, NEW.cache_fenced_at,
                NEW.snapshot_memory_count, NEW.snapshot_memory_max_id,
                NEW.snapshot_live_fact_count, NEW.snapshot_live_fact_max_id,
                NEW.snapshot_relationship_count, NEW.snapshot_relationship_max_id,
                NEW.snapshot_pending_admission_count,
                NEW.snapshot_pending_admission_max_id,
                NEW.attempt_limit, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id, OLD.namespace, OLD.subject_ref, OLD.request_ref,
                OLD.queued_by_principal_ref, OLD.queued_by_auth_method,
                OLD.key_destroyed_at, OLD.cache_fenced_at,
                OLD.snapshot_memory_count, OLD.snapshot_memory_max_id,
                OLD.snapshot_live_fact_count, OLD.snapshot_live_fact_max_id,
                OLD.snapshot_relationship_count, OLD.snapshot_relationship_max_id,
                OLD.snapshot_pending_admission_count,
                OLD.snapshot_pending_admission_max_id,
                OLD.attempt_limit, OLD.created_at
            ) THEN
                RAISE EXCEPTION 'Subject-erasure snapshot identity is immutable';
            END IF;
            IF OLD.status = 'completed' THEN
                RAISE EXCEPTION 'Completed subject-erasure jobs are immutable';
            END IF;
            IF OLD.status = 'failed' AND NEW.status <> 'pending' THEN
                RAISE EXCEPTION 'Failed subject-erasure jobs require explicit retry';
            END IF;
            IF NEW.memories_scrubbed < OLD.memories_scrubbed
               OR NEW.live_facts_scrubbed < OLD.live_facts_scrubbed
               OR NEW.relationships_scrubbed < OLD.relationships_scrubbed
               OR NEW.pending_admissions_scrubbed < OLD.pending_admissions_scrubbed
               OR NEW.pages_completed < OLD.pages_completed
               OR NEW.processing_attempts < OLD.processing_attempts THEN
                RAISE EXCEPTION 'Subject-erasure progress cannot move backward';
            END IF;
            IF OLD.memory_cursor_id IS NOT NULL AND (
                NEW.memory_cursor_id IS NULL OR NEW.memory_cursor_id < OLD.memory_cursor_id
            ) THEN
                RAISE EXCEPTION 'Subject-erasure memory cursor cannot move backward';
            END IF;
            IF OLD.live_fact_cursor_id IS NOT NULL AND (
                NEW.live_fact_cursor_id IS NULL
                OR NEW.live_fact_cursor_id < OLD.live_fact_cursor_id
            ) THEN
                RAISE EXCEPTION 'Subject-erasure live-fact cursor cannot move backward';
            END IF;
            IF OLD.relationship_cursor_id IS NOT NULL AND (
                NEW.relationship_cursor_id IS NULL
                OR NEW.relationship_cursor_id < OLD.relationship_cursor_id
            ) THEN
                RAISE EXCEPTION 'Subject-erasure relationship cursor cannot move backward';
            END IF;
            IF OLD.pending_admission_cursor_id IS NOT NULL AND (
                NEW.pending_admission_cursor_id IS NULL
                OR NEW.pending_admission_cursor_id < OLD.pending_admission_cursor_id
            ) THEN
                RAISE EXCEPTION 'Subject-erasure admission cursor cannot move backward';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_subject_erasure_job_guard() "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute(
        f"""CREATE TRIGGER trg_subject_erasure_job_guard
        BEFORE UPDATE ON public.{_JOB_TABLE}
        FOR EACH ROW EXECUTE FUNCTION public.lians_subject_erasure_job_guard()"""
    )
    op.execute(
        """CREATE FUNCTION public.lians_subject_erasure_reject_removal()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION 'Subject-erasure evidence is durable state';
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_subject_erasure_reject_removal() "
        "FROM PUBLIC, lians_runtime"
    )
    for table_name in (_JOB_TABLE, _EVIDENCE_TABLE):
        op.execute(
            f"""CREATE TRIGGER trg_{table_name}_reject_delete
            BEFORE DELETE ON public.{table_name}
            FOR EACH ROW EXECUTE FUNCTION public.lians_subject_erasure_reject_removal()"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_{table_name}_reject_truncate
            BEFORE TRUNCATE ON public.{table_name}
            FOR EACH STATEMENT EXECUTE FUNCTION
                public.lians_subject_erasure_reject_removal()"""
        )
    op.execute(
        """CREATE FUNCTION public.lians_subject_erasure_evidence_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION 'Subject-erasure memory evidence is immutable';
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_subject_erasure_evidence_immutable() "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute(
        f"""CREATE TRIGGER trg_subject_erasure_evidence_immutable
        BEFORE UPDATE ON public.{_EVIDENCE_TABLE}
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_subject_erasure_evidence_immutable()"""
    )


def _install_sqlite_boundaries() -> None:
    op.execute(
        f"""CREATE TRIGGER trg_subject_erasure_job_guard
        BEFORE UPDATE ON {_JOB_TABLE}
        BEGIN
            SELECT RAISE(ABORT, 'Subject-erasure snapshot identity is immutable')
            WHERE NEW.id IS NOT OLD.id
               OR NEW.namespace IS NOT OLD.namespace
               OR NEW.subject_ref IS NOT OLD.subject_ref
               OR NEW.request_ref IS NOT OLD.request_ref
               OR NEW.key_destroyed_at IS NOT OLD.key_destroyed_at
               OR NEW.cache_fenced_at IS NOT OLD.cache_fenced_at
               OR NEW.snapshot_memory_count IS NOT OLD.snapshot_memory_count
               OR NEW.snapshot_memory_max_id IS NOT OLD.snapshot_memory_max_id
               OR NEW.snapshot_live_fact_count IS NOT OLD.snapshot_live_fact_count
               OR NEW.snapshot_live_fact_max_id IS NOT OLD.snapshot_live_fact_max_id
               OR NEW.snapshot_relationship_count IS NOT OLD.snapshot_relationship_count
               OR NEW.snapshot_relationship_max_id IS NOT OLD.snapshot_relationship_max_id
               OR NEW.snapshot_pending_admission_count
                    IS NOT OLD.snapshot_pending_admission_count
               OR NEW.snapshot_pending_admission_max_id
                    IS NOT OLD.snapshot_pending_admission_max_id
               OR NEW.attempt_limit IS NOT OLD.attempt_limit
               OR NEW.created_at IS NOT OLD.created_at;
            SELECT RAISE(ABORT, 'Completed subject-erasure jobs are immutable')
            WHERE OLD.status = 'completed';
            SELECT RAISE(ABORT, 'Subject-erasure progress cannot move backward')
            WHERE NEW.memories_scrubbed < OLD.memories_scrubbed
               OR NEW.live_facts_scrubbed < OLD.live_facts_scrubbed
               OR NEW.relationships_scrubbed < OLD.relationships_scrubbed
               OR NEW.pending_admissions_scrubbed < OLD.pending_admissions_scrubbed
               OR NEW.pages_completed < OLD.pages_completed
               OR NEW.processing_attempts < OLD.processing_attempts;
        END"""
    )
    for table_name in (_JOB_TABLE, _EVIDENCE_TABLE):
        op.execute(
            f"""CREATE TRIGGER trg_{table_name}_reject_delete
            BEFORE DELETE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'Subject-erasure evidence is durable state');
            END"""
        )
    op.execute(
        f"""CREATE TRIGGER trg_subject_erasure_evidence_immutable
        BEFORE UPDATE ON {_EVIDENCE_TABLE}
        BEGIN
            SELECT RAISE(ABORT, 'Subject-erasure memory evidence is immutable');
        END"""
    )


def _install_postgresql_rls_and_grants() -> None:
    for table_name in (_JOB_TABLE, _EVIDENCE_TABLE):
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY rls_{table_name}_namespace ON public.{table_name}
            USING (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )
            WITH CHECK (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )"""
        )
        op.execute(f"REVOKE ALL ON TABLE public.{table_name} FROM PUBLIC, lians_runtime")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON TABLE public.{_JOB_TABLE} TO lians_runtime"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE public.{_EVIDENCE_TABLE} TO lians_runtime"
    )


def upgrade() -> None:
    op.create_table(
        _JOB_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("subject_ref", sa.String(192), nullable=False),
        sa.Column("request_ref", sa.String(192), nullable=False),
        sa.Column("subject_locator_encrypted", sa.String(4096), nullable=True),
        sa.Column("queued_by_principal_ref", sa.String(512), nullable=False),
        sa.Column("queued_by_auth_method", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("phase", sa.String(32), nullable=False, server_default="memories"),
        sa.Column("key_destroyed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cache_fenced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_memory_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("snapshot_memory_max_id", sa.Uuid(), nullable=True),
        sa.Column("memory_cursor_id", sa.Uuid(), nullable=True),
        sa.Column("memories_scrubbed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("snapshot_live_fact_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("snapshot_live_fact_max_id", sa.Uuid(), nullable=True),
        sa.Column("live_fact_cursor_id", sa.Uuid(), nullable=True),
        sa.Column("live_facts_scrubbed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("snapshot_relationship_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("snapshot_relationship_max_id", sa.Uuid(), nullable=True),
        sa.Column("relationship_cursor_id", sa.Uuid(), nullable=True),
        sa.Column("relationships_scrubbed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("snapshot_pending_admission_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("snapshot_pending_admission_max_id", sa.Uuid(), nullable=True),
        sa.Column("pending_admission_cursor_id", sa.Uuid(), nullable=True),
        sa.Column("pending_admissions_scrubbed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("pages_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("completion_event_id", sa.Uuid(), nullable=True),
        sa.Column("completion_event_hash", sa.String(64), nullable=True),
        sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_limit", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_digest", sa.String(64), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending','running','completed','failed')", name="ck_subject_erasure_job_status"),
        sa.CheckConstraint("phase IN ('memories','live_facts','relationships','pending_admissions','finalizing','completed')", name="ck_subject_erasure_job_phase"),
        sa.CheckConstraint(
            "snapshot_memory_count >= 0 AND memories_scrubbed BETWEEN 0 AND snapshot_memory_count AND "
            "snapshot_live_fact_count >= 0 AND live_facts_scrubbed BETWEEN 0 AND snapshot_live_fact_count AND "
            "snapshot_relationship_count >= 0 AND relationships_scrubbed BETWEEN 0 AND snapshot_relationship_count AND "
            "snapshot_pending_admission_count >= 0 AND pending_admissions_scrubbed BETWEEN 0 AND snapshot_pending_admission_count AND pages_completed >= 0",
            name="ck_subject_erasure_job_progress",
        ),
        sa.CheckConstraint(
            "((snapshot_memory_count = 0 AND snapshot_memory_max_id IS NULL) OR (snapshot_memory_count > 0 AND snapshot_memory_max_id IS NOT NULL)) AND "
            "((snapshot_live_fact_count = 0 AND snapshot_live_fact_max_id IS NULL) OR (snapshot_live_fact_count > 0 AND snapshot_live_fact_max_id IS NOT NULL)) AND "
            "((snapshot_relationship_count = 0 AND snapshot_relationship_max_id IS NULL) OR (snapshot_relationship_count > 0 AND snapshot_relationship_max_id IS NOT NULL)) AND "
            "((snapshot_pending_admission_count = 0 AND snapshot_pending_admission_max_id IS NULL) OR (snapshot_pending_admission_count > 0 AND snapshot_pending_admission_max_id IS NOT NULL))",
            name="ck_subject_erasure_job_snapshot_bounds",
        ),
        sa.CheckConstraint("processing_attempts >= 0 AND consecutive_failures BETWEEN 0 AND processing_attempts AND attempt_limit BETWEEN 1 AND 100", name="ck_subject_erasure_job_attempts"),
        sa.CheckConstraint("(lease_owner IS NULL AND lease_expires_at IS NULL) OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)", name="ck_subject_erasure_job_lease_pair"),
        sa.CheckConstraint("(last_error_code IS NULL AND last_error_digest IS NULL) OR (last_error_code IS NOT NULL AND last_error_digest IS NOT NULL AND length(last_error_digest) = 64)", name="ck_subject_erasure_job_error_pair"),
        sa.CheckConstraint("length(manifest_sha256) = 64", name="ck_subject_erasure_job_manifest_hash"),
        sa.CheckConstraint("cache_fenced_at <= key_destroyed_at", name="ck_subject_erasure_job_privacy_boundary_order"),
        sa.CheckConstraint("(completion_event_id IS NULL AND completion_event_hash IS NULL) OR (completion_event_id IS NOT NULL AND completion_event_hash IS NOT NULL AND length(completion_event_hash) = 64)", name="ck_subject_erasure_job_completion_event_pair"),
        sa.CheckConstraint("(status = 'completed' AND completed_at IS NOT NULL AND phase = 'completed' AND subject_locator_encrypted IS NULL AND completion_event_id IS NOT NULL) OR (status <> 'completed' AND completed_at IS NULL AND phase <> 'completed' AND subject_locator_encrypted IS NOT NULL)", name="ck_subject_erasure_job_completion"),
        sa.CheckConstraint("(status = 'failed' AND failed_at IS NOT NULL AND failure_code IS NOT NULL) OR (status <> 'failed' AND failed_at IS NULL AND failure_code IS NULL)", name="ck_subject_erasure_job_failure"),
        sa.CheckConstraint("status NOT IN ('completed','failed') OR (lease_owner IS NULL AND lease_expires_at IS NULL)", name="ck_subject_erasure_job_terminal_lease"),
        sa.PrimaryKeyConstraint("id", name="pk_subject_erasure_jobs"),
        sa.UniqueConstraint("id", "namespace", name="uq_subject_erasure_job_id_namespace"),
        sa.UniqueConstraint("namespace", "subject_ref", name="uq_subject_erasure_job_subject"),
    )
    op.create_index("ix_subject_erasure_job_namespace", _JOB_TABLE, ["namespace"])
    op.create_index("ix_subject_erasure_job_claim", _JOB_TABLE, ["status", "next_attempt_at", "lease_expires_at", "created_at", "id"])
    op.create_index("ix_subject_erasure_job_namespace_status", _JOB_TABLE, ["namespace", "status", "created_at", "id"])

    op.create_table(
        _EVIDENCE_TABLE,
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(content_hash) = 64 AND content_hash = lower(content_hash)", name="ck_subject_erasure_evidence_content_hash"),
        sa.ForeignKeyConstraint(["job_id", "namespace"], ["subject_erasure_jobs.id", "subject_erasure_jobs.namespace"], ondelete="RESTRICT", name="fk_subject_erasure_evidence_job_namespace"),
        sa.PrimaryKeyConstraint("job_id", "memory_id", name="pk_subject_erasure_memory_evidence"),
    )
    op.create_index("ix_subject_erasure_evidence_page", _EVIDENCE_TABLE, ["namespace", "job_id", "memory_id"])

    if op.get_bind().dialect.name == "postgresql":
        _install_postgresql_boundaries()
        _install_postgresql_rls_and_grants()
    else:
        _install_sqlite_boundaries()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_subject_erasure_evidence_immutable() CASCADE"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_subject_erasure_reject_removal() CASCADE"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_subject_erasure_job_guard() CASCADE"
        )
    op.drop_table(_EVIDENCE_TABLE)
    op.drop_table(_JOB_TABLE)
