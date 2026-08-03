"""Add durable fixed-snapshot SCIM tenant reconciliation jobs.

Revision ID: 0062_scim_reconciliation_jobs
Revises: 0061_inventory_page_indexes
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0062_scim_reconciliation_jobs"
down_revision = "0061_inventory_page_indexes"
branch_labels = None
depends_on = None

_TABLE = "scim_tenant_reconciliation_jobs"


def _install_activation_fence_auth_lookup() -> None:
    op.execute(
        """CREATE OR REPLACE FUNCTION
        public.lians_auth_lookup_identity_binding(
            p_provider_id uuid,
            p_external_subject text
        ) RETURNS TABLE (
            id uuid,
            namespace text,
            scopes jsonb,
            role text,
            barrier_group text,
            authorized_party text,
            principal_type text
        )
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        SET row_security = off
        AS $$
        BEGIN
            IF NOT pg_catalog.pg_has_role(
                session_user, 'lians_runtime', 'MEMBER'
            ) AND session_user <> current_user THEN
                RAISE EXCEPTION 'authentication lookup is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            IF p_provider_id IS NULL
               OR p_external_subject IS NULL
               OR length(p_external_subject) NOT BETWEEN 1 AND 512 THEN
                RETURN;
            END IF;
            RETURN QUERY
            SELECT binding.id,
                   binding.namespace::text,
                   binding.scopes::jsonb,
                   binding.role::text,
                   binding.barrier_group::text,
                   binding.authorized_party::text,
                   binding.principal_type::text
              FROM public.identity_bindings AS binding
             WHERE binding.provider_id = p_provider_id
               AND binding.external_subject = p_external_subject
               AND binding.enabled IS TRUE
               AND binding.revoked_at IS NULL
               AND (
                   binding.scim_tenant_config_id IS NULL
                   OR binding.scim_reconciliation_complete IS TRUE
               );
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.lians_auth_lookup_identity_binding(uuid,text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "public.lians_auth_lookup_identity_binding(uuid,text) TO lians_runtime"
    )
    op.execute(
        """DO $$
        BEGIN
            IF position(
                'scim_reconciliation_complete' IN (
                    SELECT function.prosrc
                      FROM pg_catalog.pg_proc AS function
                     WHERE function.oid = to_regprocedure(
                         'public.lians_auth_lookup_identity_binding(uuid,text)'
                     )
                )
            ) = 0 THEN
                RAISE EXCEPTION 'SCIM activation fence is absent from identity lookup';
            END IF;
        END;
        $$"""
    )


def _restore_legacy_auth_lookup() -> None:
    op.execute(
        """CREATE OR REPLACE FUNCTION
        public.lians_auth_lookup_identity_binding(
            p_provider_id uuid,
            p_external_subject text
        ) RETURNS TABLE (
            id uuid,
            namespace text,
            scopes jsonb,
            role text,
            barrier_group text,
            authorized_party text,
            principal_type text
        )
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        SET row_security = off
        AS $$
        BEGIN
            IF NOT pg_catalog.pg_has_role(
                session_user, 'lians_runtime', 'MEMBER'
            ) AND session_user <> current_user THEN
                RAISE EXCEPTION 'authentication lookup is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            IF p_provider_id IS NULL
               OR p_external_subject IS NULL
               OR length(p_external_subject) NOT BETWEEN 1 AND 512 THEN
                RETURN;
            END IF;
            RETURN QUERY
            SELECT binding.id,
                   binding.namespace::text,
                   binding.scopes::jsonb,
                   binding.role::text,
                   binding.barrier_group::text,
                   binding.authorized_party::text,
                   binding.principal_type::text
              FROM public.identity_bindings AS binding
             WHERE binding.provider_id = p_provider_id
               AND binding.external_subject = p_external_subject
               AND binding.enabled IS TRUE
               AND binding.revoked_at IS NULL;
        END;
        $$"""
    )


def _install_postgresql_boundaries() -> None:
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
    op.execute(f"REVOKE ALL ON TABLE public.{_TABLE} FROM PUBLIC, lians_runtime")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON TABLE public.{_TABLE} TO lians_runtime"
    )
    op.execute(
        """CREATE FUNCTION public.lians_scim_reconciliation_job_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF ROW(
                NEW.id, NEW.tenant_config_id, NEW.namespace,
                NEW.target_config_version, NEW.target_enabled,
                NEW.target_revoked_at, NEW.requested_by_principal_ref,
                NEW.snapshot_max_created_at, NEW.snapshot_max_user_id,
                NEW.snapshot_user_count, NEW.attempt_limit, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id, OLD.tenant_config_id, OLD.namespace,
                OLD.target_config_version, OLD.target_enabled,
                OLD.target_revoked_at, OLD.requested_by_principal_ref,
                OLD.snapshot_max_created_at, OLD.snapshot_max_user_id,
                OLD.snapshot_user_count, OLD.attempt_limit, OLD.created_at
            ) THEN
                RAISE EXCEPTION 'SCIM reconciliation snapshot identity is immutable';
            END IF;
            IF OLD.status IN ('completed', 'superseded') THEN
                RAISE EXCEPTION 'Terminal SCIM reconciliation jobs are immutable';
            END IF;
            IF OLD.status = 'failed' AND NEW.status <> 'pending' THEN
                RAISE EXCEPTION 'Failed SCIM reconciliation jobs require explicit retry';
            END IF;
            IF NEW.users_reconciled < OLD.users_reconciled
               OR NEW.pages_completed < OLD.pages_completed
               OR NEW.processing_attempts < OLD.processing_attempts THEN
                RAISE EXCEPTION 'SCIM reconciliation progress cannot move backward';
            END IF;
            IF OLD.cursor_created_at IS NOT NULL AND (
                NEW.cursor_created_at IS NULL
                OR ROW(NEW.cursor_created_at, NEW.cursor_user_id)
                    < ROW(OLD.cursor_created_at, OLD.cursor_user_id)
            ) THEN
                RAISE EXCEPTION 'SCIM reconciliation cursor cannot move backward';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_scim_reconciliation_job_guard() "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute(
        f"""CREATE TRIGGER trg_scim_reconciliation_job_guard
        BEFORE UPDATE ON public.{_TABLE}
        FOR EACH ROW EXECUTE FUNCTION public.lians_scim_reconciliation_job_guard()"""
    )
    op.execute(
        """CREATE FUNCTION public.lians_scim_reconciliation_reject_removal()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION 'SCIM reconciliation jobs are durable audit state';
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_scim_reconciliation_reject_removal() "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute(
        f"""CREATE TRIGGER trg_scim_reconciliation_job_reject_delete
        BEFORE DELETE ON public.{_TABLE}
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_scim_reconciliation_reject_removal()"""
    )
    op.execute(
        f"""CREATE TRIGGER trg_scim_reconciliation_job_reject_truncate
        BEFORE TRUNCATE ON public.{_TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION
            public.lians_scim_reconciliation_reject_removal()"""
    )


def _install_sqlite_boundaries() -> None:
    op.execute(
        f"""CREATE TRIGGER trg_scim_reconciliation_job_guard
        BEFORE UPDATE ON {_TABLE}
        BEGIN
            SELECT RAISE(ABORT, 'SCIM reconciliation snapshot identity is immutable')
            WHERE NEW.id IS NOT OLD.id
               OR NEW.tenant_config_id IS NOT OLD.tenant_config_id
               OR NEW.namespace IS NOT OLD.namespace
               OR NEW.target_config_version IS NOT OLD.target_config_version
               OR NEW.target_enabled IS NOT OLD.target_enabled
               OR NEW.target_revoked_at IS NOT OLD.target_revoked_at
               OR NEW.requested_by_principal_ref IS NOT OLD.requested_by_principal_ref
               OR NEW.snapshot_max_created_at IS NOT OLD.snapshot_max_created_at
               OR NEW.snapshot_max_user_id IS NOT OLD.snapshot_max_user_id
               OR NEW.snapshot_user_count IS NOT OLD.snapshot_user_count
               OR NEW.attempt_limit IS NOT OLD.attempt_limit
               OR NEW.created_at IS NOT OLD.created_at;
            SELECT RAISE(ABORT, 'Terminal SCIM reconciliation jobs are immutable')
            WHERE OLD.status IN ('completed', 'superseded');
            SELECT RAISE(ABORT, 'Failed SCIM reconciliation jobs require explicit retry')
            WHERE OLD.status = 'failed' AND NEW.status <> 'pending';
            SELECT RAISE(ABORT, 'SCIM reconciliation progress cannot move backward')
            WHERE NEW.users_reconciled < OLD.users_reconciled
               OR NEW.pages_completed < OLD.pages_completed
               OR NEW.processing_attempts < OLD.processing_attempts;
            SELECT RAISE(ABORT, 'SCIM reconciliation cursor cannot move backward')
            WHERE OLD.cursor_created_at IS NOT NULL
              AND (
                  NEW.cursor_created_at IS NULL
                  OR NEW.cursor_created_at < OLD.cursor_created_at
                  OR (
                      NEW.cursor_created_at = OLD.cursor_created_at
                      AND NEW.cursor_user_id < OLD.cursor_user_id
                  )
              );
        END"""
    )
    op.execute(
        f"""CREATE TRIGGER trg_scim_reconciliation_job_reject_delete
        BEFORE DELETE ON {_TABLE}
        BEGIN
            SELECT RAISE(ABORT, 'SCIM reconciliation jobs are durable audit state');
        END"""
    )


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.add_column(
            "identity_bindings",
            sa.Column("scim_tenant_config_id", sa.Uuid(), nullable=True),
        )
        op.add_column(
            "identity_bindings",
            sa.Column("scim_tenant_config_version", sa.Integer(), nullable=True),
        )
        op.add_column(
            "identity_bindings",
            sa.Column("scim_reconciliation_complete", sa.Boolean(), nullable=True)
        )
        op.execute(
            """ALTER TABLE public.identity_bindings
            ADD CONSTRAINT fk_identity_binding_scim_tenant_namespace
            FOREIGN KEY (scim_tenant_config_id, namespace)
            REFERENCES public.scim_tenant_configs (id, namespace)
            ON DELETE RESTRICT NOT VALID"""
        )
        op.execute(
            """ALTER TABLE public.identity_bindings
            ADD CONSTRAINT ck_identity_binding_scim_activation_fence
            CHECK (
                (scim_tenant_config_id IS NULL
                 AND scim_tenant_config_version IS NULL
                 AND scim_reconciliation_complete IS NULL)
                OR
                (scim_tenant_config_id IS NOT NULL
                 AND scim_tenant_config_version >= 1
                 AND scim_reconciliation_complete IS NOT NULL)
            ) NOT VALID"""
        )
        # Validation avoids the ACCESS EXCLUSIVE validation posture of adding
        # already-valid constraints directly while still proving all legacy
        # rows before the release is stamped.
        op.execute(
            "ALTER TABLE public.identity_bindings VALIDATE CONSTRAINT "
            "fk_identity_binding_scim_tenant_namespace"
        )
        op.execute(
            "ALTER TABLE public.identity_bindings VALIDATE CONSTRAINT "
            "ck_identity_binding_scim_activation_fence"
        )
    else:
        with op.batch_alter_table("identity_bindings") as batch:
            batch.add_column(
                sa.Column("scim_tenant_config_id", sa.Uuid(), nullable=True)
            )
            batch.add_column(
                sa.Column("scim_tenant_config_version", sa.Integer(), nullable=True)
            )
            batch.add_column(
                sa.Column("scim_reconciliation_complete", sa.Boolean(), nullable=True)
            )
            batch.create_foreign_key(
                "fk_identity_binding_scim_tenant_namespace",
                "scim_tenant_configs",
                ["scim_tenant_config_id", "namespace"],
                ["id", "namespace"],
                ondelete="RESTRICT",
            )
            batch.create_check_constraint(
                "ck_identity_binding_scim_activation_fence",
                "(scim_tenant_config_id IS NULL "
                "AND scim_tenant_config_version IS NULL "
                "AND scim_reconciliation_complete IS NULL) OR "
                "(scim_tenant_config_id IS NOT NULL "
                "AND scim_tenant_config_version >= 1 "
                "AND scim_reconciliation_complete IS NOT NULL)",
            )
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_config_id", sa.Uuid(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("target_config_version", sa.Integer(), nullable=False),
        sa.Column("target_enabled", sa.Boolean(), nullable=False),
        sa.Column("target_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("snapshot_max_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_max_user_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot_user_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cursor_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_user_id", sa.Uuid(), nullable=True),
        sa.Column("users_reconciled", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("pages_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_limit", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_digest", sa.String(length=64), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed','superseded')",
            name="ck_scim_reconciliation_job_status",
        ),
        sa.CheckConstraint(
            "target_config_version >= 1 AND "
            "(target_enabled = false OR target_revoked_at IS NULL)",
            name="ck_scim_reconciliation_job_target",
        ),
        sa.CheckConstraint(
            "snapshot_user_count >= 0 AND users_reconciled >= 0 "
            "AND users_reconciled <= snapshot_user_count AND pages_completed >= 0",
            name="ck_scim_reconciliation_job_progress",
        ),
        sa.CheckConstraint(
            "((snapshot_user_count = 0 AND snapshot_max_created_at IS NULL "
            "AND snapshot_max_user_id IS NULL) OR "
            "(snapshot_user_count > 0 AND snapshot_max_created_at IS NOT NULL "
            "AND snapshot_max_user_id IS NOT NULL))",
            name="ck_scim_reconciliation_job_snapshot_boundary",
        ),
        sa.CheckConstraint(
            "(cursor_created_at IS NULL AND cursor_user_id IS NULL) OR "
            "(cursor_created_at IS NOT NULL AND cursor_user_id IS NOT NULL)",
            name="ck_scim_reconciliation_job_cursor_pair",
        ),
        sa.CheckConstraint(
            "processing_attempts >= 0 AND consecutive_failures >= 0 "
            "AND consecutive_failures <= processing_attempts "
            "AND attempt_limit BETWEEN 1 AND 100",
            name="ck_scim_reconciliation_job_attempts",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_scim_reconciliation_job_lease_pair",
        ),
        sa.CheckConstraint(
            "(last_error_code IS NULL AND last_error_digest IS NULL) OR "
            "(last_error_code IS NOT NULL AND last_error_digest IS NOT NULL "
            "AND length(last_error_digest) = 64)",
            name="ck_scim_reconciliation_job_error_pair",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND users_reconciled = snapshot_user_count) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name="ck_scim_reconciliation_job_completion",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND failed_at IS NOT NULL AND failure_code IS NOT NULL) "
            "OR (status <> 'failed' AND failed_at IS NULL AND failure_code IS NULL)",
            name="ck_scim_reconciliation_job_failure",
        ),
        sa.CheckConstraint(
            "(status = 'superseded' AND superseded_at IS NOT NULL) OR "
            "(status <> 'superseded' AND superseded_at IS NULL)",
            name="ck_scim_reconciliation_job_superseded",
        ),
        sa.CheckConstraint(
            "status NOT IN ('completed','failed','superseded') OR "
            "(lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_scim_reconciliation_job_terminal_lease",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_config_id", "namespace"],
            ["scim_tenant_configs.id", "scim_tenant_configs.namespace"],
            ondelete="RESTRICT",
            name="fk_scim_reconciliation_job_tenant_namespace",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_scim_reconciliation_job_id_namespace"),
        sa.UniqueConstraint(
            "tenant_config_id",
            "target_config_version",
            name="uq_scim_reconciliation_job_tenant_version",
        ),
    )
    op.create_index(
        "ix_scim_reconciliation_job_claim",
        _TABLE,
        ["status", "next_attempt_at", "lease_expires_at", "created_at", "id"],
    )
    op.create_index(
        "ix_scim_reconciliation_job_tenant_page",
        _TABLE,
        ["tenant_config_id", "created_at", "id"],
    )
    op.create_index(
        "uq_scim_reconciliation_job_one_active",
        _TABLE,
        ["tenant_config_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','running')"),
        sqlite_where=sa.text("status IN ('pending','running')"),
    )
    if op.get_bind().dialect.name == "postgresql":
        _install_postgresql_boundaries()
        _install_activation_fence_auth_lookup()
    else:
        _install_sqlite_boundaries()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _restore_legacy_auth_lookup()
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_scim_reconciliation_job_reject_truncate "
            f"ON public.{_TABLE}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_scim_reconciliation_job_reject_delete "
            f"ON public.{_TABLE}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_scim_reconciliation_job_guard ON public.{_TABLE}"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_scim_reconciliation_reject_removal()"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_scim_reconciliation_job_guard()"
        )
    op.drop_index("uq_scim_reconciliation_job_one_active", table_name=_TABLE)
    op.drop_index("ix_scim_reconciliation_job_tenant_page", table_name=_TABLE)
    op.drop_index("ix_scim_reconciliation_job_claim", table_name=_TABLE)
    op.drop_table(_TABLE)
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "ck_identity_binding_scim_activation_fence",
            "identity_bindings",
            type_="check",
        )
        op.drop_constraint(
            "fk_identity_binding_scim_tenant_namespace",
            "identity_bindings",
            type_="foreignkey",
        )
        op.drop_column("identity_bindings", "scim_reconciliation_complete")
        op.drop_column("identity_bindings", "scim_tenant_config_version")
        op.drop_column("identity_bindings", "scim_tenant_config_id")
    else:
        with op.batch_alter_table("identity_bindings") as batch:
            batch.drop_constraint(
                "ck_identity_binding_scim_activation_fence",
                type_="check",
            )
            batch.drop_constraint(
                "fk_identity_binding_scim_tenant_namespace",
                type_="foreignkey",
            )
            batch.drop_column("scim_reconciliation_complete")
            batch.drop_column("scim_tenant_config_version")
            batch.drop_column("scim_tenant_config_id")
