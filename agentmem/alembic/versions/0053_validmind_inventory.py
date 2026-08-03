"""Add an exact, barrier-scoped ValidMind model inventory.

Revision ID: 0053_validmind_inventory
Revises: 0052_api_scale_indexes

The expand revision installs source-row markers and synchronous triggers before
the online companion begins historical work.  A nullable marker distinguishes
pre-expand rows from rolling-writer INSERTs without a snapshot race: every new
row is forced to ``true`` by the trigger, while 0053a claims legacy rows in
bounded committed UPDATE pages.  Inventory identities use a private random
barrier-to-scope mapping, so neither public IDs nor API metadata expose raw
information-barrier names.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_validmind_inventory"
down_revision = "0052_api_scale_indexes"
branch_labels = None
depends_on = None

_MARKER = "validmind_inventory_counted"
_VERSION_LIMIT = 100
_NEW_TABLES = (
    "validmind_barrier_scopes",
    "validmind_model_inventory",
    "validmind_model_versions",
    "validmind_legacy_model_aliases",
)


def _create_tables() -> None:
    op.create_table(
        "validmind_barrier_scopes",
        sa.Column("namespace", sa.String(), primary_key=True),
        sa.Column("barrier_key", sa.String(), primary_key=True),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "namespace",
            "scope_id",
            name="uq_validmind_scope_namespace_id",
        ),
    )
    op.create_table(
        "validmind_model_inventory",
        sa.Column("namespace", sa.String(), primary_key=True),
        sa.Column("scope_id", sa.String(length=36), primary_key=True),
        sa.Column("model_id", sa.String(), primary_key=True),
        sa.Column("external_id", sa.String(length=32), nullable=False),
        sa.Column("legacy_external_id", sa.String(length=32), nullable=False),
        sa.Column("decision_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("span_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("version_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("versions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace", "scope_id"],
            ["validmind_barrier_scopes.namespace", "validmind_barrier_scopes.scope_id"],
            name="fk_validmind_inventory_scope",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "namespace",
            "external_id",
            name="uq_validmind_inventory_external_id",
        ),
        sa.CheckConstraint(
            "decision_count >= 0 AND span_count >= 0 "
            "AND decision_count + span_count > 0",
            name="ck_validmind_inventory_activity",
        ),
        sa.CheckConstraint(
            "version_count >= 0",
            name="ck_validmind_inventory_version_count",
        ),
        sa.CheckConstraint(
            "json_array_length(versions) <= 100 "
            "AND json_array_length(versions) <= version_count",
            name="ck_validmind_inventory_versions",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_validmind_inventory_time_order",
        ),
        sa.CheckConstraint(
            "length(external_id) = 32 AND length(legacy_external_id) = 32",
            name="ck_validmind_inventory_external_ids",
        ),
    )
    op.create_index(
        "ix_validmind_inventory_legacy_id",
        "validmind_model_inventory",
        ["namespace", "legacy_external_id"],
    )
    op.create_index(
        "ix_validmind_inventory_llm_list",
        "validmind_model_inventory",
        ["namespace", "model_id", "scope_id"],
        postgresql_where=sa.text("span_count > 0"),
        sqlite_where=sa.text("span_count > 0"),
    )
    op.create_index(
        "ix_validmind_inventory_ml_list",
        "validmind_model_inventory",
        ["namespace", "model_id", "scope_id"],
        postgresql_where=sa.text("span_count = 0 AND decision_count > 0"),
        sqlite_where=sa.text("span_count = 0 AND decision_count > 0"),
    )
    op.create_table(
        "validmind_model_versions",
        sa.Column("namespace", sa.String(), primary_key=True),
        sa.Column("scope_id", sa.String(length=36), primary_key=True),
        sa.Column("model_id", sa.String(), primary_key=True),
        sa.Column("model_version", sa.String(), primary_key=True),
        sa.Column("decision_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("span_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["namespace", "scope_id", "model_id"],
            [
                "validmind_model_inventory.namespace",
                "validmind_model_inventory.scope_id",
                "validmind_model_inventory.model_id",
            ],
            name="fk_validmind_version_inventory",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "decision_count >= 0 AND span_count >= 0 "
            "AND decision_count + span_count > 0",
            name="ck_validmind_version_activity",
        ),
    )
    op.create_table(
        "validmind_legacy_model_aliases",
        sa.Column("namespace", sa.String(), primary_key=True),
        sa.Column("legacy_external_id", sa.String(length=32), primary_key=True),
        sa.Column("target_count", sa.BigInteger(), nullable=False),
        sa.Column("canonical_external_id", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "(target_count = 1 AND canonical_external_id IS NOT NULL) OR "
            "(target_count > 1 AND canonical_external_id IS NULL)",
            name="ck_validmind_legacy_alias_state",
        ),
    )


def _postgres_adjust_function(*, otel_barrier_column: bool = False) -> None:
    span_barrier = (
        "span.barrier_group"
        if otel_barrier_column
        else "(to_jsonb(span)->>'barrier_group')"
    )
    op.execute(
        f"""CREATE OR REPLACE FUNCTION public.lians_validmind_inventory_adjust(
            p_namespace text,
            p_barrier_group text,
            p_model_id text,
            p_model_version text,
            p_occurred_at timestamptz,
            p_source text,
            p_delta integer
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_barrier_key text;
            v_scope_id text;
            v_external_id text;
            v_legacy_external_id text;
            v_created integer;
            v_distinct_removed boolean := false;
            v_decision_count bigint;
            v_span_count bigint;
            v_version_count bigint;
            v_version_decisions bigint;
            v_version_spans bigint;
            v_created_at timestamptz;
            v_updated_at timestamptz;
            v_alias_targets bigint;
            v_remaining_external_id text;
        BEGIN
            IF p_model_id IS NULL THEN
                RETURN;
            END IF;
            IF p_namespace IS NULL OR btrim(p_namespace) = ''
               OR p_occurred_at IS NULL
               OR p_source NOT IN ('decision', 'span')
               OR p_delta NOT IN (-1, 1) THEN
                RAISE EXCEPTION 'invalid ValidMind inventory adjustment';
            END IF;

            v_barrier_key := CASE
                WHEN p_barrier_group IS NULL THEN 'shared:'
                ELSE 'barrier:' || p_barrier_group
            END;
            INSERT INTO public.validmind_barrier_scopes (
                namespace, barrier_key, scope_id
            ) VALUES (
                p_namespace, v_barrier_key, gen_random_uuid()::text
            ) ON CONFLICT (namespace, barrier_key) DO NOTHING;
            SELECT scope.scope_id
              INTO v_scope_id
              FROM public.validmind_barrier_scopes AS scope
             WHERE scope.namespace = p_namespace
               AND scope.barrier_key = v_barrier_key;
            IF v_scope_id IS NULL THEN
                RAISE EXCEPTION 'ValidMind barrier scope resolution failed';
            END IF;

            v_external_id := 'lians-model-' || substr(
                encode(
                    public.digest(
                        convert_to(
                            'model:' || v_scope_id || ':' || p_model_id,
                            'UTF8'
                        ),
                        'sha256'
                    ),
                    'hex'
                ),
                1,
                20
            );
            v_legacy_external_id := 'lians-model-' || substr(
                encode(
                    public.digest(
                        convert_to('model:' || p_model_id, 'UTF8'),
                        'sha256'
                    ),
                    'hex'
                ),
                1,
                20
            );

            IF p_delta = 1 THEN
                INSERT INTO public.validmind_model_inventory (
                    namespace, scope_id, model_id, external_id,
                    legacy_external_id, decision_count, span_count,
                    version_count, versions, created_at, updated_at
                ) VALUES (
                    p_namespace,
                    v_scope_id,
                    p_model_id,
                    v_external_id,
                    v_legacy_external_id,
                    CASE WHEN p_source = 'decision' THEN 1 ELSE 0 END,
                    CASE WHEN p_source = 'span' THEN 1 ELSE 0 END,
                    0,
                    '[]'::json,
                    p_occurred_at,
                    p_occurred_at
                ) ON CONFLICT (namespace, scope_id, model_id) DO NOTHING;
                GET DIAGNOSTICS v_created = ROW_COUNT;
                IF v_created = 1 THEN
                    INSERT INTO public.validmind_legacy_model_aliases (
                        namespace, legacy_external_id, target_count,
                        canonical_external_id
                    ) VALUES (
                        p_namespace, v_legacy_external_id, 1, v_external_id
                    ) ON CONFLICT (namespace, legacy_external_id) DO UPDATE
                      SET target_count =
                              validmind_legacy_model_aliases.target_count + 1,
                          canonical_external_id = NULL;
                ELSE
                    UPDATE public.validmind_model_inventory
                       SET decision_count = decision_count +
                               CASE WHEN p_source = 'decision' THEN 1 ELSE 0 END,
                           span_count = span_count +
                               CASE WHEN p_source = 'span' THEN 1 ELSE 0 END,
                           created_at = LEAST(created_at, p_occurred_at),
                           updated_at = GREATEST(updated_at, p_occurred_at)
                     WHERE namespace = p_namespace
                       AND scope_id = v_scope_id
                       AND model_id = p_model_id;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'ValidMind inventory upsert lost its target';
                    END IF;
                END IF;

                IF p_model_version IS NOT NULL THEN
                    INSERT INTO public.validmind_model_versions (
                        namespace, scope_id, model_id, model_version,
                        decision_count, span_count
                    ) VALUES (
                        p_namespace,
                        v_scope_id,
                        p_model_id,
                        p_model_version,
                        CASE WHEN p_source = 'decision' THEN 1 ELSE 0 END,
                        CASE WHEN p_source = 'span' THEN 1 ELSE 0 END
                    ) ON CONFLICT (
                        namespace, scope_id, model_id, model_version
                    ) DO NOTHING;
                    GET DIAGNOSTICS v_created = ROW_COUNT;
                    IF v_created = 0 THEN
                        UPDATE public.validmind_model_versions
                           SET decision_count = decision_count +
                                   CASE WHEN p_source = 'decision' THEN 1 ELSE 0 END,
                               span_count = span_count +
                                   CASE WHEN p_source = 'span' THEN 1 ELSE 0 END
                         WHERE namespace = p_namespace
                           AND scope_id = v_scope_id
                           AND model_id = p_model_id
                           AND model_version = p_model_version;
                    ELSE
                        UPDATE public.validmind_model_inventory
                           SET version_count = version_count + 1,
                               versions = COALESCE(
                                   (
                                       SELECT json_agg(
                                           sample.model_version
                                           ORDER BY sample.model_version
                                       )
                                         FROM (
                                             SELECT version.model_version
                                               FROM public.validmind_model_versions AS version
                                              WHERE version.namespace = p_namespace
                                                AND version.scope_id = v_scope_id
                                                AND version.model_id = p_model_id
                                              ORDER BY version.model_version
                                              LIMIT {_VERSION_LIMIT}
                                         ) AS sample
                                   ),
                                   '[]'::json
                               )
                         WHERE namespace = p_namespace
                           AND scope_id = v_scope_id
                           AND model_id = p_model_id;
                    END IF;
                END IF;
                RETURN;
            END IF;

            SELECT inventory.decision_count,
                   inventory.span_count,
                   inventory.version_count
              INTO v_decision_count, v_span_count, v_version_count
              FROM public.validmind_model_inventory AS inventory
             WHERE inventory.namespace = p_namespace
               AND inventory.scope_id = v_scope_id
               AND inventory.model_id = p_model_id
             FOR UPDATE;
            IF NOT FOUND
               OR (p_source = 'decision' AND v_decision_count < 1)
               OR (p_source = 'span' AND v_span_count < 1) THEN
                RAISE EXCEPTION 'ValidMind inventory decrement has no contribution';
            END IF;

            IF p_model_version IS NOT NULL THEN
                SELECT version.decision_count, version.span_count
                  INTO v_version_decisions, v_version_spans
                  FROM public.validmind_model_versions AS version
                 WHERE version.namespace = p_namespace
                   AND version.scope_id = v_scope_id
                   AND version.model_id = p_model_id
                   AND version.model_version = p_model_version
                 FOR UPDATE;
                IF NOT FOUND
                   OR (p_source = 'decision' AND v_version_decisions < 1)
                   OR (p_source = 'span' AND v_version_spans < 1) THEN
                    RAISE EXCEPTION 'ValidMind version decrement has no contribution';
                END IF;
                IF v_version_decisions + v_version_spans = 1 THEN
                    DELETE FROM public.validmind_model_versions
                     WHERE namespace = p_namespace
                       AND scope_id = v_scope_id
                       AND model_id = p_model_id
                       AND model_version = p_model_version;
                    v_distinct_removed := true;
                    UPDATE public.validmind_model_inventory
                       SET version_count = version_count - 1,
                           versions = COALESCE(
                               (
                                   SELECT json_agg(
                                       sample.model_version
                                       ORDER BY sample.model_version
                                   )
                                     FROM (
                                         SELECT version.model_version
                                           FROM public.validmind_model_versions AS version
                                          WHERE version.namespace = p_namespace
                                            AND version.scope_id = v_scope_id
                                            AND version.model_id = p_model_id
                                          ORDER BY version.model_version
                                          LIMIT {_VERSION_LIMIT}
                                     ) AS sample
                               ),
                               '[]'::json
                           )
                     WHERE namespace = p_namespace
                       AND scope_id = v_scope_id
                       AND model_id = p_model_id;
                ELSE
                    UPDATE public.validmind_model_versions
                       SET decision_count = decision_count -
                               CASE WHEN p_source = 'decision' THEN 1 ELSE 0 END,
                           span_count = span_count -
                               CASE WHEN p_source = 'span' THEN 1 ELSE 0 END
                     WHERE namespace = p_namespace
                       AND scope_id = v_scope_id
                       AND model_id = p_model_id
                       AND model_version = p_model_version;
                END IF;
            END IF;

            v_decision_count := v_decision_count -
                CASE WHEN p_source = 'decision' THEN 1 ELSE 0 END;
            v_span_count := v_span_count -
                CASE WHEN p_source = 'span' THEN 1 ELSE 0 END;
            IF v_decision_count + v_span_count = 0 THEN
                IF v_version_count - (
                    CASE WHEN v_distinct_removed THEN 1 ELSE 0 END
                ) <> 0 THEN
                    RAISE EXCEPTION 'ValidMind inventory/version terminal counts diverged';
                END IF;
                DELETE FROM public.validmind_model_inventory
                 WHERE namespace = p_namespace
                   AND scope_id = v_scope_id
                   AND model_id = p_model_id;

                SELECT alias.target_count
                  INTO v_alias_targets
                  FROM public.validmind_legacy_model_aliases AS alias
                 WHERE alias.namespace = p_namespace
                   AND alias.legacy_external_id = v_legacy_external_id
                 FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'ValidMind legacy alias is missing';
                ELSIF v_alias_targets = 1 THEN
                    DELETE FROM public.validmind_legacy_model_aliases
                     WHERE namespace = p_namespace
                       AND legacy_external_id = v_legacy_external_id;
                ELSIF v_alias_targets = 2 THEN
                    SELECT inventory.external_id
                      INTO v_remaining_external_id
                      FROM public.validmind_model_inventory AS inventory
                     WHERE inventory.namespace = p_namespace
                       AND inventory.legacy_external_id = v_legacy_external_id
                     ORDER BY inventory.scope_id, inventory.model_id
                     LIMIT 2;
                    IF v_remaining_external_id IS NULL THEN
                        RAISE EXCEPTION 'ValidMind legacy alias cannot resolve survivor';
                    END IF;
                    UPDATE public.validmind_legacy_model_aliases
                       SET target_count = 1,
                           canonical_external_id = v_remaining_external_id
                     WHERE namespace = p_namespace
                       AND legacy_external_id = v_legacy_external_id;
                ELSE
                    UPDATE public.validmind_legacy_model_aliases
                       SET target_count = target_count - 1,
                           canonical_external_id = NULL
                     WHERE namespace = p_namespace
                       AND legacy_external_id = v_legacy_external_id;
                END IF;
                RETURN;
            END IF;

            SELECT min(bounds.first_at), max(bounds.last_at)
              INTO v_created_at, v_updated_at
              FROM (
                  SELECT (
                             SELECT decision.recorded_at
                               FROM public.decision_records AS decision
                              WHERE decision.namespace = p_namespace
                                AND decision.model_id = p_model_id
                                AND decision.{_MARKER} IS TRUE
                                AND decision.barrier_group IS NOT DISTINCT FROM
                                    p_barrier_group
                              ORDER BY decision.recorded_at, decision.id
                              LIMIT 1
                         ) AS first_at,
                         (
                             SELECT decision.recorded_at
                               FROM public.decision_records AS decision
                              WHERE decision.namespace = p_namespace
                                AND decision.model_id = p_model_id
                                AND decision.{_MARKER} IS TRUE
                                AND decision.barrier_group IS NOT DISTINCT FROM
                                    p_barrier_group
                              ORDER BY decision.recorded_at DESC, decision.id DESC
                              LIMIT 1
                         ) AS last_at
                  UNION ALL
                  SELECT (
                             SELECT span.received_at
                               FROM public.otel_spans AS span
                              WHERE span.namespace = p_namespace
                                AND span.model_id = p_model_id
                                AND span.{_MARKER} IS TRUE
                                AND {span_barrier} IS NOT DISTINCT FROM
                                    p_barrier_group
                              ORDER BY span.received_at, span.id
                              LIMIT 1
                         ) AS first_at,
                         (
                             SELECT span.received_at
                               FROM public.otel_spans AS span
                              WHERE span.namespace = p_namespace
                                AND span.model_id = p_model_id
                                AND span.{_MARKER} IS TRUE
                                AND {span_barrier} IS NOT DISTINCT FROM
                                    p_barrier_group
                              ORDER BY span.received_at DESC, span.id DESC
                              LIMIT 1
                         ) AS last_at
              ) AS bounds;
            IF v_created_at IS NULL OR v_updated_at IS NULL THEN
                RAISE EXCEPTION 'ValidMind inventory boundary reconciliation found no rows';
            END IF;
            UPDATE public.validmind_model_inventory
               SET decision_count = v_decision_count,
                   span_count = v_span_count,
                   created_at = v_created_at,
                   updated_at = v_updated_at
             WHERE namespace = p_namespace
               AND scope_id = v_scope_id
               AND model_id = p_model_id;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_validmind_inventory_adjust("
        "text, text, text, text, timestamptz, text, integer) "
        "FROM PUBLIC, lians_runtime"
    )


def _postgres_source_triggers() -> None:
    op.execute(
        f"""CREATE FUNCTION public.lians_validmind_force_counted()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            NEW.{_MARKER} := TRUE;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        f"""CREATE FUNCTION public.lians_validmind_source_change()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_old_namespace text;
            v_old_barrier text;
            v_source text;
        BEGIN
            IF TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME NOT IN ('decision_records', 'otel_spans') THEN
                RAISE EXCEPTION 'ValidMind source trigger attached to an invalid table';
            END IF;
            v_source := CASE
                WHEN TG_TABLE_NAME = 'decision_records' THEN 'decision'
                ELSE 'span'
            END;
            v_old_namespace := current_setting('app.current_namespace', true);
            v_old_barrier := current_setting('agentmem.barrier_group', true);
            PERFORM set_config('app.current_namespace', '__admin__', true);
            PERFORM set_config('agentmem.barrier_group', '', true);

            IF TG_OP = 'UPDATE'
               AND OLD.{_MARKER} IS TRUE
               AND NEW.{_MARKER} IS TRUE
               AND OLD.namespace IS NOT DISTINCT FROM NEW.namespace
               AND (to_jsonb(OLD)->>'barrier_group') IS NOT DISTINCT FROM
                   (to_jsonb(NEW)->>'barrier_group')
               AND OLD.model_id IS NOT DISTINCT FROM NEW.model_id
               AND OLD.model_version IS NOT DISTINCT FROM NEW.model_version
               AND COALESCE(
                   to_jsonb(OLD)->>'recorded_at',
                   to_jsonb(OLD)->>'received_at'
               ) IS NOT DISTINCT FROM COALESCE(
                   to_jsonb(NEW)->>'recorded_at',
                   to_jsonb(NEW)->>'received_at'
               ) THEN
                PERFORM set_config(
                    'app.current_namespace', COALESCE(v_old_namespace, ''), true
                );
                PERFORM set_config(
                    'agentmem.barrier_group', COALESCE(v_old_barrier, ''), true
                );
                RETURN NEW;
            END IF;

            IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.{_MARKER} IS TRUE THEN
                PERFORM public.lians_validmind_inventory_adjust(
                    OLD.namespace,
                    to_jsonb(OLD)->>'barrier_group',
                    OLD.model_id,
                    OLD.model_version,
                    COALESCE(
                        to_jsonb(OLD)->>'recorded_at',
                        to_jsonb(OLD)->>'received_at'
                    )::timestamptz,
                    v_source,
                    -1
                );
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.{_MARKER} IS TRUE THEN
                PERFORM public.lians_validmind_inventory_adjust(
                    NEW.namespace,
                    to_jsonb(NEW)->>'barrier_group',
                    NEW.model_id,
                    NEW.model_version,
                    COALESCE(
                        to_jsonb(NEW)->>'recorded_at',
                        to_jsonb(NEW)->>'received_at'
                    )::timestamptz,
                    v_source,
                    1
                );
            END IF;

            PERFORM set_config(
                'app.current_namespace', COALESCE(v_old_namespace, ''), true
            );
            PERFORM set_config(
                'agentmem.barrier_group', COALESCE(v_old_barrier, ''), true
            );
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        EXCEPTION WHEN OTHERS THEN
            PERFORM set_config(
                'app.current_namespace', COALESCE(v_old_namespace, ''), true
            );
            PERFORM set_config(
                'agentmem.barrier_group', COALESCE(v_old_barrier, ''), true
            );
            RAISE;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_validmind_force_counted() "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_validmind_source_change() "
        "FROM PUBLIC, lians_runtime"
    )
    for table in ("decision_records", "otel_spans"):
        op.execute(
            f"""CREATE TRIGGER trg_00_validmind_{table}_counted
            BEFORE INSERT OR UPDATE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.lians_validmind_force_counted()"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_validmind_{table}_inventory
            AFTER INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.lians_validmind_source_change()"""
        )


def _postgres_decision_backfill_boundary() -> None:
    # 0041's guard is preserved byte-for-byte semantically, except that the new
    # non-hash projection marker is excluded and an owner + exact-GUC transition
    # is available only until 0053a restores the strict form.
    op.execute(
        f"""CREATE OR REPLACE FUNCTION public.lians_decision_record_immutable_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            table_owner name;
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'decision records are immutable; record a superseding correction';
            END IF;
            SELECT pg_get_userbyid(relation.relowner)
              INTO table_owner
              FROM pg_class AS relation
              JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = TG_TABLE_SCHEMA
               AND relation.relname = TG_TABLE_NAME;
            IF current_setting('lians.migration_validmind_inventory', true) =
                   '0053a_validmind_backfill'
               AND pg_has_role(current_user, table_owner, 'USAGE')
               AND OLD.{_MARKER} IS NOT TRUE
               AND NEW.{_MARKER} IS TRUE
               AND (to_jsonb(NEW) - '{_MARKER}') IS NOT DISTINCT FROM
                   (to_jsonb(OLD) - '{_MARKER}') THEN
                RETURN NEW;
            END IF;
            IF (
                to_jsonb(NEW) - ARRAY[
                    'human_review_status', 'human_reviewer', 'human_reviewed_at',
                    '{_MARKER}'
                ]
            ) IS DISTINCT FROM (
                to_jsonb(OLD) - ARRAY[
                    'human_review_status', 'human_reviewer', 'human_reviewed_at',
                    '{_MARKER}'
                ]
            ) THEN
                RAISE EXCEPTION 'hash-covered decision record fields are immutable';
            END IF;
            IF NEW.human_review_status IS NOT DISTINCT FROM OLD.human_review_status
               AND NEW.human_reviewer IS NOT DISTINCT FROM OLD.human_reviewer
               AND NEW.human_reviewed_at IS NOT DISTINCT FROM OLD.human_reviewed_at THEN
                RAISE EXCEPTION 'decision record updates require a review projection change';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )


def _postgres_rls_and_privileges() -> None:
    for table in _NEW_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")

    for table in ("validmind_barrier_scopes", "validmind_model_versions"):
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
    for table in (
        "validmind_model_inventory",
        "validmind_legacy_model_aliases",
    ):
        op.execute(
            f"""CREATE POLICY rls_{table}_select ON public.{table}
            FOR SELECT USING (
                (
                    namespace = current_setting('app.current_namespace', true)
                    OR current_setting('app.current_namespace', true) = '__admin__'
                )
                AND COALESCE(
                    current_setting('agentmem.barrier_group', true), ''
                ) = ''
            )"""
        )
        op.execute(
            f"""CREATE POLICY rls_{table}_insert ON public.{table}
            FOR INSERT WITH CHECK (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )"""
        )
        op.execute(
            f"""CREATE POLICY rls_{table}_update ON public.{table}
            FOR UPDATE
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
            f"""CREATE POLICY rls_{table}_delete ON public.{table}
            FOR DELETE USING (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )"""
        )

    for table in _NEW_TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC, lians_runtime")
    op.execute(
        "GRANT SELECT ON TABLE public.validmind_model_inventory, "
        "public.validmind_legacy_model_aliases TO lians_runtime"
    )


def _sqlite_scope_key(row: str, barrier_column: str | None) -> str:
    if barrier_column is None:
        return "'shared:'"
    return (
        f"CASE WHEN {row}.{barrier_column} IS NULL THEN 'shared:' "
        f"ELSE 'barrier:' || {row}.{barrier_column} END"
    )


def _sqlite_scope_id() -> str:
    return (
        "lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-' || "
        "'4' || substr(lower(hex(randomblob(2))), 2) || '-' || "
        "substr('89ab', (random() & 3) + 1, 1) || "
        "substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))"
    )


def _sqlite_add_body(
    table: str,
    source: str,
    timestamp: str,
    barrier_column: str | None,
) -> str:
    scope_key = _sqlite_scope_key("NEW", barrier_column)
    decision_value = "1" if source == "decision" else "0"
    span_value = "1" if source == "span" else "0"
    return f"""
        INSERT INTO validmind_barrier_scopes (
            namespace, barrier_key, scope_id
        )
        SELECT NEW.namespace, {scope_key}, {_sqlite_scope_id()}
         WHERE NEW.model_id IS NOT NULL
        ON CONFLICT (namespace, barrier_key) DO NOTHING;

        INSERT INTO validmind_legacy_model_aliases (
            namespace, legacy_external_id, target_count, canonical_external_id
        )
        SELECT NEW.namespace,
               lians_legacy_model_id(NEW.model_id),
               1,
               lians_external_id('model', NEW.model_id, scope.scope_id)
          FROM validmind_barrier_scopes AS scope
         WHERE NEW.model_id IS NOT NULL
           AND scope.namespace = NEW.namespace
           AND scope.barrier_key = {scope_key}
           AND NOT EXISTS (
               SELECT 1 FROM validmind_model_inventory AS inventory
                WHERE inventory.namespace = NEW.namespace
                  AND inventory.scope_id = scope.scope_id
                  AND inventory.model_id = NEW.model_id
           )
        ON CONFLICT (namespace, legacy_external_id) DO UPDATE SET
            target_count = target_count + 1,
            canonical_external_id = NULL;

        INSERT INTO validmind_model_inventory (
            namespace, scope_id, model_id, external_id, legacy_external_id,
            decision_count, span_count, version_count, versions,
            created_at, updated_at
        )
        SELECT NEW.namespace,
               scope.scope_id,
               NEW.model_id,
               lians_external_id('model', NEW.model_id, scope.scope_id),
               lians_legacy_model_id(NEW.model_id),
               {decision_value},
               {span_value},
               0,
               json('[]'),
               NEW.{timestamp},
               NEW.{timestamp}
          FROM validmind_barrier_scopes AS scope
         WHERE NEW.model_id IS NOT NULL
           AND scope.namespace = NEW.namespace
           AND scope.barrier_key = {scope_key}
        ON CONFLICT (namespace, scope_id, model_id) DO UPDATE SET
            decision_count = decision_count + {decision_value},
            span_count = span_count + {span_value},
            created_at = min(created_at, excluded.created_at),
            updated_at = max(updated_at, excluded.updated_at);

        UPDATE validmind_model_inventory
           SET version_count = version_count + 1
         WHERE NEW.model_id IS NOT NULL
           AND NEW.model_version IS NOT NULL
           AND namespace = NEW.namespace
           AND scope_id = (
               SELECT scope.scope_id FROM validmind_barrier_scopes AS scope
                WHERE scope.namespace = NEW.namespace
                  AND scope.barrier_key = {scope_key}
           )
           AND model_id = NEW.model_id
           AND NOT EXISTS (
               SELECT 1 FROM validmind_model_versions AS version
                WHERE version.namespace = NEW.namespace
                  AND version.scope_id = validmind_model_inventory.scope_id
                  AND version.model_id = NEW.model_id
                  AND version.model_version = NEW.model_version
           );

        INSERT INTO validmind_model_versions (
            namespace, scope_id, model_id, model_version,
            decision_count, span_count
        )
        SELECT NEW.namespace,
               scope.scope_id,
               NEW.model_id,
               NEW.model_version,
               {decision_value},
               {span_value}
          FROM validmind_barrier_scopes AS scope
         WHERE NEW.model_id IS NOT NULL
           AND NEW.model_version IS NOT NULL
           AND scope.namespace = NEW.namespace
           AND scope.barrier_key = {scope_key}
        ON CONFLICT (namespace, scope_id, model_id, model_version) DO UPDATE SET
            decision_count = decision_count + {decision_value},
            span_count = span_count + {span_value};

        UPDATE validmind_model_inventory
           SET versions = COALESCE(
               (
                   SELECT json_group_array(sample.model_version)
                     FROM (
                         SELECT version.model_version
                           FROM validmind_model_versions AS version
                          WHERE version.namespace = NEW.namespace
                            AND version.scope_id = validmind_model_inventory.scope_id
                            AND version.model_id = NEW.model_id
                          ORDER BY version.model_version
                          LIMIT {_VERSION_LIMIT}
                     ) AS sample
               ),
               json('[]')
           )
         WHERE NEW.model_id IS NOT NULL
           AND NEW.model_version IS NOT NULL
           AND namespace = NEW.namespace
           AND scope_id = (
               SELECT scope.scope_id FROM validmind_barrier_scopes AS scope
                WHERE scope.namespace = NEW.namespace
                  AND scope.barrier_key = {scope_key}
           )
           AND model_id = NEW.model_id;
    """


def _sqlite_remove_body(
    table: str,
    source: str,
    timestamp: str,
    barrier_column: str | None,
    span_barrier_column: str | None = None,
) -> str:
    del table, timestamp
    scope_key = _sqlite_scope_key("OLD", barrier_column)
    scope_id = (
        "(SELECT scope.scope_id FROM validmind_barrier_scopes AS scope "
        "WHERE scope.namespace = OLD.namespace "
        f"AND scope.barrier_key = {scope_key})"
    )
    decision_value = "1" if source == "decision" else "0"
    span_value = "1" if source == "span" else "0"
    decision_barrier = _sqlite_scope_key("decision", "barrier_group")
    span_barrier = _sqlite_scope_key("span", span_barrier_column)
    return f"""
        UPDATE validmind_model_inventory
           SET version_count = version_count - 1,
               versions = COALESCE(
                   (
                       SELECT json_group_array(sample.model_version)
                         FROM (
                             SELECT version.model_version
                               FROM validmind_model_versions AS version
                              WHERE version.namespace = OLD.namespace
                                AND version.scope_id =
                                    validmind_model_inventory.scope_id
                                AND version.model_id = OLD.model_id
                                AND version.model_version <>
                                    OLD.model_version
                              ORDER BY version.model_version
                              LIMIT {_VERSION_LIMIT}
                         ) AS sample
                   ),
                   json('[]')
               )
         WHERE OLD.model_id IS NOT NULL
           AND OLD.model_version IS NOT NULL
           AND namespace = OLD.namespace
           AND scope_id = {scope_id}
           AND model_id = OLD.model_id
           AND EXISTS (
               SELECT 1 FROM validmind_model_versions AS version
                WHERE version.namespace = OLD.namespace
                  AND version.scope_id = validmind_model_inventory.scope_id
                  AND version.model_id = OLD.model_id
                  AND version.model_version = OLD.model_version
                  AND version.decision_count + version.span_count = 1
           );

        DELETE FROM validmind_model_versions
         WHERE OLD.model_id IS NOT NULL
           AND OLD.model_version IS NOT NULL
           AND namespace = OLD.namespace
           AND scope_id = {scope_id}
           AND model_id = OLD.model_id
           AND model_version = OLD.model_version
           AND decision_count + span_count = 1;
        UPDATE validmind_model_versions
           SET decision_count = decision_count - {decision_value},
               span_count = span_count - {span_value}
         WHERE namespace = OLD.namespace
           AND scope_id = {scope_id}
           AND model_id = OLD.model_id
           AND model_version = OLD.model_version
           AND decision_count + span_count > 1;

        UPDATE validmind_model_inventory
           SET versions = COALESCE(
               (
                   SELECT json_group_array(sample.model_version)
                     FROM (
                         SELECT version.model_version
                           FROM validmind_model_versions AS version
                          WHERE version.namespace = OLD.namespace
                            AND version.scope_id = validmind_model_inventory.scope_id
                            AND version.model_id = OLD.model_id
                          ORDER BY version.model_version
                          LIMIT {_VERSION_LIMIT}
                     ) AS sample
               ),
               json('[]')
           )
         WHERE OLD.model_id IS NOT NULL
           AND OLD.model_version IS NOT NULL
           AND namespace = OLD.namespace
           AND scope_id = {scope_id}
           AND model_id = OLD.model_id;

        DELETE FROM validmind_legacy_model_aliases
         WHERE namespace = OLD.namespace
           AND legacy_external_id = lians_legacy_model_id(OLD.model_id)
           AND target_count = 1
           AND EXISTS (
               SELECT 1 FROM validmind_model_inventory AS inventory
                WHERE inventory.namespace = OLD.namespace
                  AND inventory.scope_id = {scope_id}
                  AND inventory.model_id = OLD.model_id
                  AND inventory.decision_count + inventory.span_count = 1
           );
        UPDATE validmind_legacy_model_aliases
           SET target_count = target_count - 1,
               canonical_external_id = CASE
                   WHEN target_count = 2 THEN (
                       SELECT inventory.external_id
                         FROM validmind_model_inventory AS inventory
                        WHERE inventory.namespace = OLD.namespace
                          AND inventory.legacy_external_id =
                              lians_legacy_model_id(OLD.model_id)
                          AND NOT (
                              inventory.scope_id = {scope_id}
                              AND inventory.model_id = OLD.model_id
                          )
                        ORDER BY inventory.scope_id, inventory.model_id
                        LIMIT 1
                   )
                   ELSE NULL
               END
         WHERE namespace = OLD.namespace
           AND legacy_external_id = lians_legacy_model_id(OLD.model_id)
           AND target_count > 1
           AND EXISTS (
               SELECT 1 FROM validmind_model_inventory AS inventory
                WHERE inventory.namespace = OLD.namespace
                  AND inventory.scope_id = {scope_id}
                  AND inventory.model_id = OLD.model_id
                  AND inventory.decision_count + inventory.span_count = 1
           );

        UPDATE validmind_model_inventory
           SET decision_count = decision_count - {decision_value},
               span_count = span_count - {span_value}
         WHERE OLD.model_id IS NOT NULL
           AND namespace = OLD.namespace
           AND scope_id = {scope_id}
           AND model_id = OLD.model_id
           AND decision_count + span_count > 1;
        DELETE FROM validmind_model_inventory
         WHERE OLD.model_id IS NOT NULL
           AND namespace = OLD.namespace
           AND scope_id = {scope_id}
           AND model_id = OLD.model_id
           AND decision_count + span_count = 1;

        UPDATE validmind_model_inventory
           SET created_at = (
                   SELECT min(activity.occurred_at)
                     FROM (
                         SELECT decision.recorded_at AS occurred_at
                           FROM decision_records AS decision
                          WHERE decision.namespace = OLD.namespace
                            AND decision.model_id = OLD.model_id
                            AND decision.{_MARKER} IS 1
                            AND {decision_barrier} = {scope_key}
                         UNION ALL
                         SELECT span.received_at AS occurred_at
                           FROM otel_spans AS span
                          WHERE span.namespace = OLD.namespace
                            AND span.model_id = OLD.model_id
                            AND span.{_MARKER} IS 1
                            AND {span_barrier} = {scope_key}
                     ) AS activity
               ),
               updated_at = (
                   SELECT max(activity.occurred_at)
                     FROM (
                         SELECT decision.recorded_at AS occurred_at
                           FROM decision_records AS decision
                          WHERE decision.namespace = OLD.namespace
                            AND decision.model_id = OLD.model_id
                            AND decision.{_MARKER} IS 1
                            AND {decision_barrier} = {scope_key}
                         UNION ALL
                         SELECT span.received_at AS occurred_at
                           FROM otel_spans AS span
                          WHERE span.namespace = OLD.namespace
                            AND span.model_id = OLD.model_id
                            AND span.{_MARKER} IS 1
                            AND {span_barrier} = {scope_key}
                     ) AS activity
               )
         WHERE OLD.model_id IS NOT NULL
           AND namespace = OLD.namespace
           AND scope_id = {scope_id}
           AND model_id = OLD.model_id;
    """


def _install_sqlite_source_triggers() -> None:
    for table, source, timestamp, barrier_column in (
        ("decision_records", "decision", "recorded_at", "barrier_group"),
        ("otel_spans", "span", "received_at", None),
    ):
        add_body = _sqlite_add_body(table, source, timestamp, barrier_column)
        remove_body = _sqlite_remove_body(table, source, timestamp, barrier_column)
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


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    _create_tables()
    op.add_column(
        "decision_records",
        sa.Column(_MARKER, sa.Boolean(), nullable=True),
    )
    op.add_column(
        "otel_spans",
        sa.Column(_MARKER, sa.Boolean(), nullable=True),
    )
    if dialect == "postgresql":
        _postgres_adjust_function()
        _postgres_source_triggers()
        _postgres_decision_backfill_boundary()
        for table in ("decision_records", "otel_spans"):
            op.execute(
                f"ALTER TABLE public.{table} ADD CONSTRAINT "
                f"ck_{table}_validmind_counted CHECK ({_MARKER} IS TRUE) NOT VALID"
            )
        _postgres_rls_and_privileges()
    elif dialect == "sqlite":
        _install_sqlite_source_triggers()
    else:
        raise RuntimeError(f"ValidMind inventory is unsupported on {dialect}")


def _restore_original_decision_guard() -> None:
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_decision_record_immutable_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'decision records are immutable; record a superseding correction';
            END IF;
            IF (
                to_jsonb(NEW)
                - ARRAY['human_review_status', 'human_reviewer', 'human_reviewed_at']
            ) IS DISTINCT FROM (
                to_jsonb(OLD)
                - ARRAY['human_review_status', 'human_reviewer', 'human_reviewed_at']
            ) THEN
                RAISE EXCEPTION 'hash-covered decision record fields are immutable';
            END IF;
            IF NEW.human_review_status IS NOT DISTINCT FROM OLD.human_review_status
               AND NEW.human_reviewer IS NOT DISTINCT FROM OLD.human_reviewer
               AND NEW.human_reviewed_at IS NOT DISTINCT FROM OLD.human_reviewed_at THEN
                RAISE EXCEPTION 'decision record updates require a review projection change';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP INDEX IF EXISTS public.ix_decision_validmind_scope_bounds"
        )
        for table in ("decision_records", "otel_spans"):
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_validmind_{table}_inventory "
                f"ON public.{table}"
            )
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_00_validmind_{table}_counted "
                f"ON public.{table}"
            )
            op.execute(
                f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS "
                f"ck_{table}_validmind_counted"
            )
        _restore_original_decision_guard()
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_validmind_source_change()"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_validmind_force_counted()"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_validmind_inventory_adjust("
            "text, text, text, text, timestamptz, text, integer)"
        )
    elif dialect == "sqlite":
        op.execute("DROP INDEX IF EXISTS ix_decision_validmind_scope_bounds")
        for table in ("decision_records", "otel_spans"):
            for suffix in (
                "promote_insert",
                "counted_insert",
                "promoted",
                "marker_guard",
                "projection_update",
                "delete",
            ):
                op.execute(f"DROP TRIGGER IF EXISTS trg_validmind_{table}_{suffix}")
    op.drop_column("otel_spans", _MARKER)
    op.drop_column("decision_records", _MARKER)
    for table in reversed(_NEW_TABLES):
        op.drop_table(table)
