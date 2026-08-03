"""Expand OTLP barrier provenance and close uncovered namespace RLS gaps.

Revision ID: 0054_otel_barrier
Revises: 0053a_validmind_backfill

``barrier_scope_trusted`` separates historical NULLs from a new writer's
explicit shared NULL. A PostgreSQL compatibility trigger copies the already
authenticated GUC boundary for rolling old writers. Until 0054a drains legacy
rows, the OTLP
barrier policy hides untrusted rows from every scoped caller while preserving
unbarriered compliance access.  The existing ValidMind triggers synchronously
recategorize each row when the companion migration assigns the conservative
legacy sentinel.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import op

revision = "0054_otel_barrier"
down_revision = "0053a_validmind_backfill"
branch_labels = None
depends_on = None

_LEGACY_BARRIER = "__legacy_restricted__"
_MARKER = "validmind_inventory_counted"
_NAMESPACE_RLS_TABLES = (
    "agents",
    "audit_chain_heads",
    "conflict_flags",
    "merkle_anchors",
    "otel_spans",
    "validmind_model_links",
)
_SQLITE_TRIGGER_SUFFIXES = (
    "promote_insert",
    "counted_insert",
    "promoted",
    "marker_guard",
    "projection_update",
    "delete",
)


def _validmind_expand_module() -> ModuleType:
    path = Path(__file__).with_name("0053_validmind_inventory.py")
    spec = importlib.util.spec_from_file_location("lians_migration_0053_expand", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load ValidMind expand implementation from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _drop_sqlite_validmind_source_triggers() -> None:
    for table in ("decision_records", "otel_spans"):
        for suffix in _SQLITE_TRIGGER_SUFFIXES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_validmind_{table}_{suffix}")


def _install_sqlite_validmind_source_triggers(*, otel_barrier: bool) -> None:
    """Reinstall both sources because removal recomputes cross-source time bounds."""

    expand = _validmind_expand_module()
    span_barrier_column = "barrier_group" if otel_barrier else None
    sources = (
        ("decision_records", "decision", "recorded_at", "barrier_group"),
        (
            "otel_spans",
            "span",
            "received_at",
            "barrier_group" if otel_barrier else None,
        ),
    )
    for table, source, timestamp, barrier_column in sources:
        add_body = expand._sqlite_add_body(
            table,
            source,
            timestamp,
            barrier_column,
        )
        remove_body = expand._sqlite_remove_body(
            table,
            source,
            timestamp,
            barrier_column,
            span_barrier_column=span_barrier_column,
        )
        projection_columns = ["namespace", "model_id", "model_version", timestamp]
        if barrier_column is not None:
            projection_columns.append(barrier_column)
        projection_sql = ", ".join(projection_columns)
        op.execute(
            f"""CREATE TRIGGER trg_validmind_{table}_promote_insert
            AFTER INSERT ON {table}
            WHEN NEW.{_MARKER} IS NOT 1
            BEGIN
                UPDATE {table} SET {_MARKER} = 1 WHERE id = NEW.id;
            END"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_validmind_{table}_counted_insert
            AFTER INSERT ON {table}
            WHEN NEW.{_MARKER} IS 1
            BEGIN
                {add_body}
            END"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_validmind_{table}_promoted
            AFTER UPDATE OF {_MARKER} ON {table}
            WHEN OLD.{_MARKER} IS NOT 1 AND NEW.{_MARKER} IS 1
            BEGIN
                {add_body}
            END"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_validmind_{table}_marker_guard
            BEFORE UPDATE OF {_MARKER} ON {table}
            WHEN OLD.{_MARKER} IS 1 AND NEW.{_MARKER} IS NOT 1
            BEGIN
                SELECT RAISE(ABORT, 'ValidMind inventory marker cannot be cleared');
            END"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_validmind_{table}_projection_update
            AFTER UPDATE OF {projection_sql} ON {table}
            WHEN OLD.{_MARKER} IS 1 AND NEW.{_MARKER} IS 1
            BEGIN
                {remove_body}
                {add_body}
            END"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_validmind_{table}_delete
            AFTER DELETE ON {table}
            WHEN OLD.{_MARKER} IS 1
            BEGIN
                {remove_body}
            END"""
        )


def _install_postgresql_rls() -> None:
    for table in _NAMESPACE_RLS_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON public.{table}")
        op.execute(
            f"""CREATE POLICY rls_{table}_namespace ON public.{table}
            USING (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )
            WITH CHECK (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )"""
        )

    # A missing barrier GUC is never treated as unbarriered. Legacy/untrusted
    # rows and the reserved sentinel require the explicit empty admin/compliance
    # context, even if a bad credential was historically assigned that name.
    op.execute("DROP POLICY IF EXISTS rls_otel_spans_barrier ON public.otel_spans")
    op.execute(
        f"""CREATE POLICY rls_otel_spans_barrier ON public.otel_spans
        AS RESTRICTIVE
        USING (
            current_setting('app.current_namespace', true) = '__admin__'
            OR current_setting('agentmem.barrier_group', true) = ''
            OR (
                barrier_scope_trusted IS TRUE
                AND barrier_group IS DISTINCT FROM '{_LEGACY_BARRIER}'
                AND (
                    barrier_group IS NULL
                    OR barrier_group = current_setting(
                        'agentmem.barrier_group', true
                    )
                )
            )
        )
        WITH CHECK (
            current_setting('app.current_namespace', true) = '__admin__'
            OR current_setting('agentmem.barrier_group', true) = ''
            OR (
                barrier_scope_trusted IS TRUE
                AND barrier_group IS DISTINCT FROM '{_LEGACY_BARRIER}'
                AND (
                    barrier_group IS NULL
                    OR barrier_group = current_setting(
                        'agentmem.barrier_group', true
                    )
                )
            )
        )"""
    )
    op.execute(
        "DROP POLICY IF EXISTS rls_scim_group_entitlements_barrier "
        "ON public.scim_group_entitlements"
    )
    op.execute(
        """CREATE POLICY rls_scim_group_entitlements_barrier
        ON public.scim_group_entitlements AS RESTRICTIVE
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
    # Model links are namespace-level governance metadata. ValidMind routes
    # require an explicit unbarriered compliance context; a scoped or missing
    # barrier GUC must not enumerate model identifiers indirectly.
    op.execute(
        "DROP POLICY IF EXISTS rls_validmind_model_links_unbarriered "
        "ON public.validmind_model_links"
    )
    op.execute(
        """CREATE POLICY rls_validmind_model_links_unbarriered
        ON public.validmind_model_links AS RESTRICTIVE
        USING (
            current_setting('app.current_namespace', true) = '__admin__'
            OR current_setting('agentmem.barrier_group', true) = ''
        )
        WITH CHECK (
            current_setting('app.current_namespace', true) = '__admin__'
            OR current_setting('agentmem.barrier_group', true) = ''
        )"""
    )


def _install_postgresql_rolling_writer_boundary() -> None:
    # Old application code omits both new columns, but its authenticated DB
    # transaction already carries the server-derived namespace/barrier GUCs.
    # Capture that trustworthy boundary before the ValidMind BEFORE trigger and
    # RLS WITH CHECK run. Historical rows never pass through this trigger.
    op.execute(
        f"""CREATE FUNCTION public.lians_otel_fill_barrier_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_namespace text;
            v_barrier text;
        BEGIN
            IF TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME <> 'otel_spans' THEN
                RAISE EXCEPTION 'OTLP provenance trigger attached incorrectly';
            END IF;
            v_namespace := current_setting('app.current_namespace', true);
            v_barrier := current_setting('agentmem.barrier_group', true);
            IF NEW.barrier_scope_trusted IS TRUE THEN
                IF NEW.barrier_group = '{_LEGACY_BARRIER}'
                   AND v_namespace IS DISTINCT FROM '__admin__' THEN
                    RAISE EXCEPTION 'reserved OTLP barrier provenance';
                END IF;
                RETURN NEW;
            END IF;
            IF v_namespace IS NULL
               OR v_namespace = ''
               OR v_namespace = '__admin__'
               OR v_namespace IS DISTINCT FROM NEW.namespace
               OR v_barrier IS NULL THEN
                RAISE EXCEPTION 'OTLP barrier provenance is unavailable';
            END IF;
            NEW.barrier_group := NULLIF(v_barrier, '');
            NEW.barrier_scope_trusted := TRUE;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_otel_fill_barrier_provenance() "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute(
        """CREATE TRIGGER trg_00_otel_barrier_provenance
        BEFORE INSERT ON public.otel_spans
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_otel_fill_barrier_provenance()"""
    )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.add_column(
        "otel_spans",
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "otel_spans",
        sa.Column("barrier_scope_trusted", sa.Boolean(), nullable=True),
    )

    if dialect == "postgresql":
        _validmind_expand_module()._postgres_adjust_function(
            otel_barrier_column=True
        )
        _install_postgresql_rolling_writer_boundary()
        _install_postgresql_rls()
    elif dialect == "sqlite":
        _drop_sqlite_validmind_source_triggers()
        _install_sqlite_validmind_source_triggers(otel_barrier=True)
    else:
        raise RuntimeError(f"OTLP barrier provenance is unsupported on {dialect}")


def _assert_downgrade_safe() -> None:
    qualified = (
        "public.otel_spans"
        if op.get_bind().dialect.name == "postgresql"
        else "otel_spans"
    )
    scoped = op.get_bind().execute(
        sa.text(
            f"""SELECT EXISTS (
                    SELECT 1 FROM {qualified}
                    WHERE barrier_group IS NOT NULL
                )"""
        )
    ).scalar_one()
    if scoped:
        raise RuntimeError(
            "0054_otel_barrier downgrade refused: scoped or legacy-restricted "
            "OTLP rows cannot be made shared safely; restore a pre-0054 backup "
            "or explicitly purge/export them before schema rollback"
        )


def downgrade() -> None:
    _assert_downgrade_safe()
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_00_otel_barrier_provenance "
            "ON public.otel_spans"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_otel_fill_barrier_provenance()"
        )
        op.execute(
            "DROP POLICY IF EXISTS rls_otel_spans_barrier ON public.otel_spans"
        )
        op.execute(
            "DROP POLICY IF EXISTS rls_scim_group_entitlements_barrier "
            "ON public.scim_group_entitlements"
        )
        op.execute(
            "DROP POLICY IF EXISTS rls_validmind_model_links_unbarriered "
            "ON public.validmind_model_links"
        )
        for table in reversed(_NAMESPACE_RLS_TABLES):
            op.execute(
                f"DROP POLICY IF EXISTS rls_{table}_namespace ON public.{table}"
            )
            op.execute(f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
        # Regenerate the persisted cross-source inventory function before the
        # OTel barrier column it references is removed.
        _validmind_expand_module()._postgres_adjust_function(
            otel_barrier_column=False
        )
    elif dialect == "sqlite":
        _drop_sqlite_validmind_source_triggers()
        _install_sqlite_validmind_source_triggers(otel_barrier=False)
    op.drop_column("otel_spans", "barrier_scope_trusted")
    op.drop_column("otel_spans", "barrier_group")
