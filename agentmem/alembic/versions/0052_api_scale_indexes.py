"""Add bounded-inventory and compliance query indexes.

Revision ID: 0052_api_scale_indexes
Revises: 0051_impact_snapshot_row_count

The ValidMind integration exposes opaque SHA-256-derived identifiers. PostgreSQL
therefore needs expression indexes that match the lookup predicate exactly;
ordinary model-ID indexes cannot accelerate a hash predicate. Composite indexes
cover the grouped inventories, compliance windows, and deterministic trust lists.
PostgreSQL builds and drops these indexes concurrently to avoid long writer stalls
on established installations. Online retries inspect ``pg_index`` and repair an
invalid artifact left by an interrupted concurrent build before Alembic stamps
the revision.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052_api_scale_indexes"
down_revision = "0051_impact_snapshot_row_count"
branch_labels = None
depends_on = None

_REGULAR_INDEXES = (
    (
        "ix_decision_validmind_model_inventory",
        "decision_records",
        ("namespace", "model_id", "recorded_at"),
    ),
    (
        "ix_otel_validmind_model_inventory",
        "otel_spans",
        ("namespace", "model_id", "received_at"),
    ),
    (
        "ix_event_log_compliance_op_time",
        "event_log",
        ("namespace", "op", "created_at"),
    ),
    (
        "ix_conflict_validmind_ticket_list",
        "conflict_flags",
        ("namespace", "detected_at", "id"),
    ),
    (
        "ix_receipt_issuer_list",
        "receipt_issuers",
        ("namespace", "status", "created_at", "id"),
    ),
    (
        "ix_receipt_issuer_all_list",
        "receipt_issuers",
        ("namespace", "created_at", "id"),
    ),
    (
        "ix_trusted_key_issuer_list",
        "trusted_receipt_keys",
        ("issuer_id", "status", "created_at", "id"),
    ),
    (
        "ix_trusted_key_issuer_all_list",
        "trusted_receipt_keys",
        ("issuer_id", "created_at", "id"),
    ),
    (
        "ix_remediation_task_case_status_list",
        "remediation_tasks",
        ("case_id", "status", "created_at", "id"),
    ),
    (
        "ix_remediation_task_case_list",
        "remediation_tasks",
        ("case_id", "created_at", "id"),
    ),
)

_POSTGRES_EXPRESSION_INDEXES = (
    (
        "ix_decision_validmind_external_id",
        "decision_records",
        "model",
        "model_id",
    ),
    (
        "ix_otel_validmind_external_id",
        "otel_spans",
        "model",
        "model_id",
    ),
    (
        "ix_agent_validmind_external_id",
        "agents",
        "agent",
        "agent_id",
    ),
)

_POSTGRES_JSONB_GIN_INDEXES = (
    (
        "ix_gate_policy_protected_actions_gin",
        "gate_policy_sets",
        "protected_actions",
    ),
    (
        "ix_gate_policy_target_selectors_gin",
        "gate_policy_sets",
        "target_ref_prefixes",
    ),
)


def _quoted_columns(columns: tuple[str, ...]) -> str:
    return ", ".join(f'"{column}"' for column in columns)


def _external_id_expression(kind: str, source_column: str) -> str:
    return (
        f"('lians-{kind}-'::text || substr("
        "public.lians_sha256_text("
        f"'{kind}:'::text || \"{source_column}\"), "
        "1, 20))"
    )


def _online_index_state(name: str) -> tuple[bool, str] | None:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                """SELECT index.indisvalid, indexed_table.relname AS table_name
                   FROM pg_class AS index_relation
                   JOIN pg_namespace AS namespace
                     ON namespace.oid = index_relation.relnamespace
                   JOIN pg_index AS index
                     ON index.indexrelid = index_relation.oid
                   JOIN pg_class AS indexed_table
                     ON indexed_table.oid = index.indrelid
                   WHERE namespace.nspname = current_schema()
                     AND index_relation.relname = :name"""
            ),
            {"name": name},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return bool(row["indisvalid"]), str(row["table_name"])


def _ensure_online_concurrent_index(name: str, table: str, create_sql: str) -> None:
    """Resume safely after a failed CREATE INDEX CONCURRENTLY attempt."""

    state = _online_index_state(name)
    if state is not None:
        valid, indexed_table = state
        if indexed_table != table:
            raise RuntimeError(
                f"0052 index name collision: {name} belongs to {indexed_table}, "
                f"expected {table}"
            )
        if valid:
            return
        op.execute(f'DROP INDEX CONCURRENTLY "{name}"')
    op.execute(create_sql)


def _install_validmind_agent_lookup() -> None:
    """Install an RLS-safe boundary that can use the full opaque-ID index."""
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_validmind_lookup_agent(
            p_namespace text,
            p_external_id text
        ) RETURNS TABLE(agent_id text)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT agent.agent_id::text
            FROM public.agents AS agent
           WHERE p_namespace IS NOT NULL
             AND length(p_external_id) = 32
             AND (
                 current_setting('app.current_namespace', true) = '__admin__'
                 OR p_namespace = current_setting(
                     'app.current_namespace', true
                 )
             )
             AND agent.namespace = p_namespace
             AND (
                 'lians-agent-'::text || substr(
                     public.lians_sha256_text(
                         'agent:'::text || agent.agent_id
                     ),
                     1,
                     20
                 )
             ) = p_external_id
           ORDER BY agent.agent_id
           LIMIT 2
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.lians_validmind_lookup_agent(text,text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "public.lians_validmind_lookup_agent(text,text) TO lians_runtime"
    )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        # The canonical wrapper fixes the byte encoding to UTF-8 and is marked
        # IMMUTABLE, unlike PostgreSQL's STABLE ``convert_to`` primitive.  The
        # runtime needs permission to use the same expression in indexed
        # opaque-ID lookups.
        op.execute(
            "GRANT EXECUTE ON FUNCTION public.lians_sha256_text(text) "
            "TO lians_runtime"
        )
        with op.get_context().autocommit_block():
            for name, table, columns in _REGULAR_INDEXES:
                create_sql = (
                    f'CREATE INDEX CONCURRENTLY "{name}" '
                    f'ON "{table}" ({_quoted_columns(columns)})'
                )
                if op.get_context().as_sql:
                    op.execute(
                        create_sql.replace(
                            "CONCURRENTLY ", "CONCURRENTLY IF NOT EXISTS ", 1
                        )
                    )
                else:
                    _ensure_online_concurrent_index(name, table, create_sql)
            for name, table, kind, source_column in _POSTGRES_EXPRESSION_INDEXES:
                predicate = (
                    f' WHERE "{source_column}" IS NOT NULL'
                    if source_column == "model_id"
                    else ""
                )
                create_sql = (
                    f'CREATE INDEX CONCURRENTLY "{name}" '
                    f'ON "{table}" ("namespace", '
                    f'{_external_id_expression(kind, source_column)}){predicate}'
                )
                if op.get_context().as_sql:
                    op.execute(
                        create_sql.replace(
                            "CONCURRENTLY ", "CONCURRENTLY IF NOT EXISTS ", 1
                        )
                    )
                else:
                    _ensure_online_concurrent_index(name, table, create_sql)
            for name, table, source_column in _POSTGRES_JSONB_GIN_INDEXES:
                create_sql = (
                    f'CREATE INDEX CONCURRENTLY "{name}" ON "{table}" '
                    f'USING gin (("{source_column}"::jsonb))'
                )
                if op.get_context().as_sql:
                    op.execute(
                        create_sql.replace(
                            "CONCURRENTLY ", "CONCURRENTLY IF NOT EXISTS ", 1
                        )
                    )
                else:
                    _ensure_online_concurrent_index(name, table, create_sql)
        _install_validmind_agent_lookup()
        return

    for name, table, columns in _REGULAR_INDEXES:
        op.create_index(name, table, list(columns), unique=False)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "public.lians_validmind_lookup_agent(text,text)"
        )
        with op.get_context().autocommit_block():
            for name, _table, _source_column in reversed(
                _POSTGRES_JSONB_GIN_INDEXES
            ):
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
            for name, _table, _kind, _source_column in reversed(
                _POSTGRES_EXPRESSION_INDEXES
            ):
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
            for name, _table, _columns in reversed(_REGULAR_INDEXES):
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
        op.execute(
            "REVOKE ALL ON FUNCTION public.lians_sha256_text(text) "
            "FROM lians_runtime"
        )
        return

    for name, table, _columns in reversed(_REGULAR_INDEXES):
        op.drop_index(name, table_name=table)
