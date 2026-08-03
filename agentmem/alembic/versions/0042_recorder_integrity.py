"""Bind Recorder events to authenticated provenance and make them append-only.

Revision ID: 0042_recorder_integrity
Revises: 0041a_decision_integrity_idx
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "0042_recorder_integrity"
down_revision = "0041a_decision_integrity_idx"
branch_labels = None
depends_on = None

LEGACY_PRINCIPAL_REF = "lians:principal:v1:legacy-unverified"
LEGACY_AUTH_METHOD = "legacy_unverified"

_CHECK_CONSTRAINTS = (
    (
        "ck_recorder_event_hash_version",
        "event_hash_version IN (1, 2)",
    ),
    (
        "ck_recorder_event_hash_lengths",
        """length(event_hash) = 64
           AND length(source_payload_hash) = 64
           AND event_hash = lower(event_hash)
           AND source_payload_hash = lower(source_payload_hash)""",
    ),
    (
        "ck_recorder_event_actor_attribution",
        "actor_attribution IN ('claimed_unverified', 'not_supplied')",
    ),
    (
        "ck_recorder_event_provenance_state",
        """(
            event_hash_version = 1
            AND ingested_by_principal_ref =
                'lians:principal:v1:legacy-unverified'
            AND ingested_by_auth_method = 'legacy_unverified'
            AND ingested_by_credential_id IS NULL
            AND actor_attribution = 'claimed_unverified'
        ) OR (
            event_hash_version = 2
            AND ingested_by_principal_ref LIKE 'lians:principal:v1:%'
            AND ingested_by_principal_ref <>
                'lians:principal:v1:legacy-unverified'
            AND length(ingested_by_principal_ref) > 20
            AND ingested_by_auth_method IN ('api_key', 'oidc_bearer')
            AND (
                ingested_by_credential_id IS NULL
                OR length(ingested_by_credential_id) BETWEEN 1 AND 128
            )
        )""",
    ),
)


def _set_postgresql_migration_context() -> None:
    op.execute("SELECT set_config('app.current_namespace', '__admin__', true)")
    op.execute("SELECT set_config('agentmem.barrier_group', '', true)")


def _assert_postgresql_boundary_prerequisites() -> None:
    connection = op.get_bind()
    capability_role = connection.execute(
        sa.text(
            """SELECT rolcanlogin, rolsuper, rolbypassrls
               FROM pg_roles
               WHERE rolname = 'lians_runtime'"""
        )
    ).mappings().one_or_none()
    if capability_role is None:
        raise RuntimeError(
            "Recorder integrity requires the pre-provisioned PostgreSQL "
            "capability role lians_runtime"
        )
    if (
        bool(capability_role["rolcanlogin"])
        or bool(capability_role["rolsuper"])
        or bool(capability_role["rolbypassrls"])
    ):
        raise RuntimeError(
            "PostgreSQL role lians_runtime must remain NOLOGIN, NOSUPERUSER, "
            "and NOBYPASSRLS"
        )

    rls_state = {
        str(row["relname"]): (
            bool(row["relrowsecurity"]),
            bool(row["relforcerowsecurity"]),
        )
        for row in connection.execute(
            sa.text(
                """SELECT rel.relname, rel.relrowsecurity, rel.relforcerowsecurity
                   FROM pg_class AS rel
                   JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
                   WHERE ns.nspname = 'public'
                     AND rel.relname IN ('recorder_runs', 'recorder_events')"""
            )
        ).mappings()
    }
    if rls_state != {
        "recorder_runs": (True, True),
        "recorder_events": (True, True),
    }:
        raise RuntimeError(
            "Recorder integrity refused to proceed because the forced-RLS "
            "boundary from migration 0027 is missing or disabled"
        )


def _assert_existing_hashes_are_well_formed() -> None:
    connection = op.get_bind()
    malformed = connection.execute(
        sa.text(
            """SELECT COUNT(*)
               FROM recorder_events
               WHERE event_hash IS NULL
                  OR source_payload_hash IS NULL
                  OR length(event_hash) <> 64
                  OR length(source_payload_hash) <> 64
                  OR event_hash <> lower(event_hash)
                  OR source_payload_hash <> lower(source_payload_hash)"""
        )
    ).scalar_one()
    if malformed:
        raise RuntimeError(
            "Recorder integrity migration refused malformed historical hashes"
        )

    if connection.dialect.name == "postgresql":
        non_hex = connection.execute(
            sa.text(
                """SELECT COUNT(*)
                   FROM recorder_events
                   WHERE event_hash !~ '^[0-9a-f]{64}$'
                      OR source_payload_hash !~ '^[0-9a-f]{64}$'"""
            )
        ).scalar_one()
    elif connection.dialect.name == "sqlite":
        non_hex = connection.execute(
            sa.text(
                """SELECT COUNT(*)
                   FROM recorder_events
                   WHERE event_hash GLOB '*[^0-9a-f]*'
                      OR source_payload_hash GLOB '*[^0-9a-f]*'"""
            )
        ).scalar_one()
    else:
        non_hex = 0
    if non_hex:
        raise RuntimeError(
            "Recorder integrity migration refused non-hexadecimal historical hashes"
        )


def _mark_historical_runs_legacy() -> None:
    """Update only run rows that actually have pre-0042 events."""
    op.execute(
        """UPDATE recorder_runs
           SET ingested_by_principal_refs =
                   '["lians:principal:v1:legacy-unverified"]',
               ingested_by_auth_methods = '["legacy_unverified"]'
           WHERE EXISTS (
               SELECT 1 FROM recorder_events
               WHERE recorder_events.run_id = recorder_runs.id
           )"""
    )


def _install_postgresql_run_provenance_projection() -> None:
    """Keep rolling 0.4.2 run aggregates truthful after each legacy insert."""
    op.execute(
        """CREATE FUNCTION public.lians_recorder_run_provenance_project()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            UPDATE public.recorder_runs AS run
               SET ingested_by_principal_refs = (
                       SELECT jsonb_agg(value ORDER BY value)::json
                       FROM (
                           SELECT DISTINCT value
                           FROM jsonb_array_elements_text(
                               COALESCE(
                                   run.ingested_by_principal_refs::jsonb,
                                   '[]'::jsonb
                               )
                           ) AS existing(value)
                           UNION
                           SELECT NEW.ingested_by_principal_ref
                       ) AS principals
                   ),
                   ingested_by_auth_methods = (
                       SELECT jsonb_agg(value ORDER BY value)::json
                       FROM (
                           SELECT DISTINCT value
                           FROM jsonb_array_elements_text(
                               COALESCE(
                                   run.ingested_by_auth_methods::jsonb,
                                   '[]'::jsonb
                               )
                           ) AS existing(value)
                           UNION
                           SELECT NEW.ingested_by_auth_method
                       ) AS methods
                   )
             WHERE run.id = NEW.run_id
               AND run.namespace = NEW.namespace;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'recorder event has no visible run boundary';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.lians_recorder_run_provenance_project() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER trg_recorder_run_provenance_project
        AFTER INSERT ON public.recorder_events
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_recorder_run_provenance_project()"""
    )


def _install_sqlite_run_provenance_projection() -> None:
    # json_each/json_group_array are part of SQLite's required JSON support for
    # the Recorder local profile. The projection is idempotent for 0.5 writers.
    op.execute(
        """CREATE TRIGGER trg_recorder_run_provenance_project
        AFTER INSERT ON recorder_events
        BEGIN
            UPDATE recorder_runs
               SET ingested_by_principal_refs = (
                       SELECT json_group_array(value)
                       FROM (
                           SELECT DISTINCT value
                           FROM json_each(
                               COALESCE(
                                   recorder_runs.ingested_by_principal_refs,
                                   '[]'
                               )
                           )
                           UNION
                           SELECT NEW.ingested_by_principal_ref
                           ORDER BY value
                       )
                   ),
                   ingested_by_auth_methods = (
                       SELECT json_group_array(value)
                       FROM (
                           SELECT DISTINCT value
                           FROM json_each(
                               COALESCE(
                                   recorder_runs.ingested_by_auth_methods,
                                   '[]'
                               )
                           )
                           UNION
                           SELECT NEW.ingested_by_auth_method
                           ORDER BY value
                       )
                   )
             WHERE id = NEW.run_id AND namespace = NEW.namespace;
            SELECT CASE WHEN changes() <> 1
                THEN RAISE(ABORT, 'recorder event has no run boundary') END;
        END"""
    )


def _install_postgresql_immutability_boundary() -> None:
    op.execute(
        """CREATE FUNCTION public.lians_recorder_event_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION 'recorder_events is append-only; % is forbidden', TG_OP;
        END;
        $$"""
    )
    op.execute(
        """CREATE TRIGGER trg_recorder_event_reject_mutation
        BEFORE UPDATE OR DELETE ON public.recorder_events
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_recorder_event_reject_mutation()"""
    )
    op.execute(
        """CREATE TRIGGER trg_recorder_event_reject_truncate
        BEFORE TRUNCATE ON public.recorder_events
        FOR EACH STATEMENT EXECUTE FUNCTION
            public.lians_recorder_event_reject_mutation()"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.lians_recorder_event_reject_mutation() FROM PUBLIC"
    )

    # The runtime can append and read events. It cannot rewrite or remove
    # history. Trigger enforcement also protects against table owners and
    # accidentally broadened grants.
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.recorder_events FROM PUBLIC"
    )
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.recorder_events "
        "FROM lians_runtime"
    )
    op.execute(
        "GRANT SELECT, INSERT ON TABLE public.recorder_events TO lians_runtime"
    )


def _install_sqlite_immutability_boundary() -> None:
    op.execute(
        """CREATE TRIGGER trg_recorder_event_reject_mutation
        BEFORE UPDATE ON recorder_events
        BEGIN
            SELECT RAISE(ABORT, 'recorder_events is append-only; UPDATE is forbidden');
        END"""
    )
    op.execute(
        """CREATE TRIGGER trg_recorder_event_reject_delete
        BEFORE DELETE ON recorder_events
        BEGIN
            SELECT RAISE(ABORT, 'recorder_events is append-only; DELETE is forbidden');
        END"""
    )


def upgrade() -> None:
    connection = op.get_bind()
    dialect = connection.dialect.name
    if dialect == "postgresql":
        _set_postgresql_migration_context()
        if not context.is_offline_mode():
            _assert_postgresql_boundary_prerequisites()

    if context.is_offline_mode():
        if dialect == "postgresql":
            op.execute(
                """DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM public.recorder_events
                         WHERE event_hash IS NULL
                            OR source_payload_hash IS NULL
                            OR event_hash !~ '^[0-9a-f]{64}$'
                            OR source_payload_hash !~ '^[0-9a-f]{64}$'
                    ) THEN
                        RAISE EXCEPTION
                            'Recorder integrity migration refused malformed hashes';
                    END IF;
                END;
                $$"""
            )
    else:
        _assert_existing_hashes_are_well_formed()

    op.add_column(
        "recorder_runs",
        sa.Column(
            "ingested_by_principal_refs",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "recorder_runs",
        sa.Column(
            "ingested_by_auth_methods",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "recorder_events",
        sa.Column(
            "event_hash_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "recorder_events",
        sa.Column(
            "ingested_by_principal_ref",
            sa.String(512),
            nullable=False,
            server_default=LEGACY_PRINCIPAL_REF,
        ),
    )
    op.add_column(
        "recorder_events",
        sa.Column(
            "ingested_by_auth_method",
            sa.String(64),
            nullable=False,
            server_default=LEGACY_AUTH_METHOD,
        ),
    )
    op.add_column(
        "recorder_events",
        sa.Column("ingested_by_credential_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "recorder_events",
        sa.Column(
            "actor_attribution",
            sa.String(32),
            nullable=False,
            server_default="claimed_unverified",
        ),
    )

    if dialect == "postgresql":
        for name, expression in _CHECK_CONSTRAINTS:
            op.execute(
                f"ALTER TABLE public.recorder_events ADD CONSTRAINT {name} "
                f"CHECK ({expression}) NOT VALID"
            )
        op.execute(
            "ALTER TABLE public.recorder_events "
            + ", ".join(
                f"VALIDATE CONSTRAINT {name}"
                for name, _expression in _CHECK_CONSTRAINTS
            )
        )
        _install_postgresql_run_provenance_projection()
        _install_postgresql_immutability_boundary()
        # Assert rather than recreate the 0027 policies: this migration must
        # preserve both namespace and restrictive information-barrier RLS.
        if not context.is_offline_mode():
            _assert_postgresql_boundary_prerequisites()
    else:
        with op.batch_alter_table("recorder_events") as batch:
            for name, expression in _CHECK_CONSTRAINTS:
                batch.create_check_constraint(name, expression)
    if dialect == "sqlite":
        _install_sqlite_run_provenance_projection()
        _install_sqlite_immutability_boundary()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_recorder_run_provenance_project "
            "ON public.recorder_events"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "public.lians_recorder_run_provenance_project()"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_recorder_event_reject_truncate "
            "ON public.recorder_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_recorder_event_reject_mutation "
            "ON public.recorder_events"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_recorder_event_reject_mutation()"
        )
        # Restore the ordinary table permissions established by migration 0039.
        # TRUNCATE remains revoked, as it was before this migration.
        op.execute(
            "GRANT UPDATE, DELETE ON TABLE public.recorder_events TO lians_runtime"
        )
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_recorder_run_provenance_project")
        op.execute("DROP TRIGGER IF EXISTS trg_recorder_event_reject_mutation")
        op.execute("DROP TRIGGER IF EXISTS trg_recorder_event_reject_delete")

    with op.batch_alter_table("recorder_events") as batch:
        batch.drop_constraint(
            "ck_recorder_event_provenance_state",
            type_="check",
        )
        batch.drop_constraint(
            "ck_recorder_event_actor_attribution",
            type_="check",
        )
        batch.drop_constraint(
            "ck_recorder_event_hash_lengths",
            type_="check",
        )
        batch.drop_constraint(
            "ck_recorder_event_hash_version",
            type_="check",
        )
        batch.drop_column("actor_attribution")
        batch.drop_column("ingested_by_credential_id")
        batch.drop_column("ingested_by_auth_method")
        batch.drop_column("ingested_by_principal_ref")
        batch.drop_column("event_hash_version")

    op.drop_column("recorder_runs", "ingested_by_auth_methods")
    op.drop_column("recorder_runs", "ingested_by_principal_refs")
