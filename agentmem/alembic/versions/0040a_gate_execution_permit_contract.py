"""Backfill and contract the mediated Gate execution boundary.

Revision ID: 0040a_gate_permit_contract
Revises: 0040_gate_execution_permits

The preceding expand revision keeps legacy writers live with nullable execution
claims plus a fail-closed compatibility trigger.  This revision retires legacy
selector-only policies and fills historical target references in committed,
bounded pages before validating the final contract.  Indexes on the existing
Gate decision table are built concurrently and repaired after interruption.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "0040a_gate_permit_contract"
down_revision = "0040_gate_execution_permits"
branch_labels = None
depends_on = None

_BATCH_SIZE = 2_000
_HOT_INDEXES = (
    (
        "ix_gate_decision_records_target_ref",
        "target_ref",
    ),
    (
        "ix_gate_decision_records_enforcement_principal_id",
        "enforcement_principal_id",
    ),
    (
        "ix_gate_decision_records_execution_request_hash",
        "execution_request_hash",
    ),
)


def _install_migration_append_boundary() -> None:
    op.execute(
        """CREATE OR REPLACE FUNCTION lians_control_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            table_owner name;
        BEGIN
            SELECT pg_get_userbyid(relation.relowner)
            INTO table_owner
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = TG_TABLE_SCHEMA
              AND relation.relname = TG_TABLE_NAME;
            IF TG_TABLE_SCHEMA = 'public'
               AND TG_TABLE_NAME = 'gate_decision_records'
               AND TG_OP = 'UPDATE'
               AND current_setting(
                    'lians.migration_gate_decision_backfill', true
               ) = '0040a_gate_permit_contract'
               AND pg_has_role(current_user, table_owner, 'USAGE') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION lians_control_reject_mutation() FROM PUBLIC"
    )


def _restore_strict_append_boundary() -> None:
    op.execute(
        """CREATE OR REPLACE FUNCTION lians_control_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION lians_control_reject_mutation() FROM PUBLIC"
    )


def _index_valid(index_name: str) -> bool | None:
    return op.get_bind().execute(
        sa.text(
            """SELECT index.indisvalid
               FROM pg_index AS index
               JOIN pg_class AS relation ON relation.oid = index.indexrelid
               JOIN pg_namespace AS namespace
                 ON namespace.oid = relation.relnamespace
               WHERE namespace.nspname = 'public'
                 AND relation.relname = :index_name"""
        ),
        {"index_name": index_name},
    ).scalar_one_or_none()


def _create_or_repair_hot_indexes() -> None:
    for index_name, column in _HOT_INDEXES:
        valid = _index_valid(index_name)
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")
            valid = None
        if valid is None:
            op.execute(
                f"CREATE INDEX CONCURRENTLY {index_name} "
                f"ON public.gate_decision_records ({column})"
            )


def _drain(statement: sa.TextClause) -> None:
    bind = op.get_bind()
    while bind.execute(statement, {"batch_size": _BATCH_SIZE}).first() is not None:
        pass


def _backfill_postgresql() -> None:
    _drain(
        sa.text(
            """WITH target AS (
                   SELECT id
                   FROM public.gate_decision_records
                   WHERE target_ref IS NULL
                   ORDER BY id
                   LIMIT :batch_size
                   FOR UPDATE SKIP LOCKED
               )
               UPDATE public.gate_decision_records AS decision
               SET target_ref = COALESCE(
                   NULLIF(decision.input_snapshot ->> 'target_ref', ''),
                   'lians:legacy-unbound'
               )
               FROM target
               WHERE decision.id = target.id
               RETURNING decision.id"""
        )
    )
    _drain(
        sa.text(
            """WITH target AS (
                   SELECT id
                   FROM public.gate_policy_sets
                   WHERE status = 'active'
                     AND jsonb_typeof(enforcement_principal_ids::jsonb) = 'array'
                     AND jsonb_array_length(enforcement_principal_ids::jsonb) = 0
                   ORDER BY id
                   LIMIT :batch_size
                   FOR UPDATE SKIP LOCKED
               )
               UPDATE public.gate_policy_sets AS policy
               SET status = 'retired',
                   retired_at = COALESCE(policy.retired_at, clock_timestamp())
               FROM target
               WHERE policy.id = target.id
               RETURNING policy.id"""
        )
    )
    remaining = op.get_bind().execute(
        sa.text(
            """SELECT EXISTS (
                   SELECT 1
                   FROM public.gate_decision_records
                   WHERE target_ref IS NULL
               ) OR EXISTS (
                   SELECT 1
                   FROM public.gate_policy_sets
                   WHERE status = 'active'
                     AND jsonb_typeof(enforcement_principal_ids::jsonb) = 'array'
                     AND jsonb_array_length(enforcement_principal_ids::jsonb) = 0
               )"""
        )
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            "0040a_gate_permit_contract could not drain every legacy "
            "row; a row remains locked. Let the owning transaction finish and "
            "rerun the online migration."
        )


def _upgrade_postgresql() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("SELECT set_config('app.current_namespace', '__admin__', false)")
        )
        op.execute(
            sa.text("SELECT set_config('agentmem.barrier_group', '', false)")
        )
        op.execute(
            sa.text(
                "SELECT set_config("
                "'lians.migration_gate_decision_backfill', "
                "'0040a_gate_permit_contract', false)"
            )
        )
        _backfill_postgresql()
        _create_or_repair_hot_indexes()
    _restore_strict_append_boundary()
    op.execute(
        """ALTER TABLE public.gate_policy_sets
        VALIDATE CONSTRAINT ck_gate_policy_permit_ttl"""
    )
    op.execute(
        """ALTER TABLE public.gate_decision_records
        VALIDATE CONSTRAINT ck_0040_gate_target_ref_present"""
    )
    op.alter_column(
        "gate_decision_records",
        "target_ref",
        existing_type=sa.String(length=2048),
        nullable=False,
    )
    op.drop_constraint(
        "ck_0040_gate_target_ref_present",
        "gate_decision_records",
        type_="check",
    )


def _upgrade_non_postgresql(dialect: str) -> None:
    if dialect == "sqlite":
        op.execute(
            """UPDATE gate_decision_records
            SET target_ref = COALESCE(
                NULLIF(json_extract(input_snapshot, '$.target_ref'), ''),
                'lians:legacy-unbound'
            )
            WHERE target_ref IS NULL"""
        )
        op.execute(
            """UPDATE gate_policy_sets
            SET status = 'retired',
                retired_at = COALESCE(retired_at, CURRENT_TIMESTAMP)
            WHERE status = 'active'
              AND json_array_length(enforcement_principal_ids) = 0"""
        )
        with op.batch_alter_table("gate_decision_records") as batch:
            batch.alter_column(
                "target_ref",
                existing_type=sa.String(length=2048),
                nullable=False,
            )
    else:
        op.execute(
            """UPDATE gate_decision_records
            SET target_ref = 'lians:legacy-unbound'
            WHERE target_ref IS NULL"""
        )
        op.alter_column(
            "gate_decision_records",
            "target_ref",
            existing_type=sa.String(length=2048),
            nullable=False,
        )
    for index_name, column in _HOT_INDEXES:
        op.create_index(index_name, "gate_decision_records", [column])


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql" and context.is_offline_mode():
        raise RuntimeError(
            "0040a_gate_permit_contract requires an online PostgreSQL "
            "connection so bounded legacy pages and concurrent indexes commit "
            "and resume safely. Generate reviewed offline DDL only through "
            "0040_gate_execution_permits, then run 0040a online."
        )
    if dialect == "postgresql":
        _upgrade_postgresql()
    else:
        _upgrade_non_postgresql(dialect)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _install_migration_append_boundary()
        op.alter_column(
            "gate_decision_records",
            "target_ref",
            existing_type=sa.String(length=2048),
            nullable=True,
        )
        op.execute(
            """ALTER TABLE public.gate_decision_records
            ADD CONSTRAINT ck_0040_gate_target_ref_present
            CHECK (target_ref IS NOT NULL) NOT VALID"""
        )
        with op.get_context().autocommit_block():
            for index_name, _column in _HOT_INDEXES:
                op.execute(
                    f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}"
                )
        return
    for index_name, _column in reversed(_HOT_INDEXES):
        op.drop_index(index_name, table_name="gate_decision_records")
    if dialect == "sqlite":
        with op.batch_alter_table("gate_decision_records") as batch:
            batch.alter_column(
                "target_ref",
                existing_type=sa.String(length=2048),
                nullable=True,
            )
    else:
        op.alter_column(
            "gate_decision_records",
            "target_ref",
            existing_type=sa.String(length=2048),
            nullable=True,
        )
