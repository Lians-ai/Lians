"""Backfill and contract the exact ValidMind inventory in committed pages.

Revision ID: 0053a_validmind_backfill
Revises: 0053_validmind_inventory

Each source row is its own durable progress record.  The expand trigger marks
all concurrent/rolling-writer INSERTs counted before they become visible;
historical NULL markers are claimed with a locked keyset page and the same
synchronous maintenance trigger.  Replaying a committed page is therefore a
no-op, and no snapshot/high-water race can omit a UUID inserted concurrently.
Bounded link pages remove ambiguous 0.4.2 rows and mirror uniquely resolvable
legacy-only or scoped-only mappings. The final trigger set keeps both rows
synchronized on insert/update/delete and follows later exact alias transitions
without exposing or guessing raw barrier names.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "0053a_validmind_backfill"
down_revision = "0053_validmind_inventory"
branch_labels = None
depends_on = None

_BATCH_SIZE = 1_000
_LINK_BATCH_SIZE = 500
_MARKER = "validmind_inventory_counted"
_DECISION_BOUNDARY_INDEX = "ix_decision_validmind_scope_bounds"


def _postgres_index_state(name: str) -> dict[str, object] | None:
    row = op.get_bind().execute(
        sa.text(
            """SELECT index.indisvalid,
                      index.indisunique,
                      indexed_table.relname AS table_name,
                      access_method.amname AS access_method,
                      pg_get_expr(index.indpred, index.indrelid, true)
                          AS predicate,
                      array_agg(
                          pg_get_indexdef(
                              index.indexrelid,
                              key.ordinality,
                              true
                          ) ORDER BY key.ordinality
                      ) AS keys
                 FROM pg_class AS index_relation
                 JOIN pg_namespace AS namespace
                   ON namespace.oid = index_relation.relnamespace
                 JOIN pg_index AS index
                   ON index.indexrelid = index_relation.oid
                 JOIN pg_class AS indexed_table
                   ON indexed_table.oid = index.indrelid
                 JOIN pg_am AS access_method
                   ON access_method.oid = index_relation.relam
           CROSS JOIN LATERAL generate_series(
                     1, index.indnkeyatts
                 ) AS key(ordinality)
                WHERE namespace.nspname = 'public'
                  AND index_relation.relname = :name
                GROUP BY index.indisvalid, index.indisunique,
                         indexed_table.relname, access_method.amname,
                         index.indpred, index.indexrelid, index.indrelid"""
        ),
        {"name": name},
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _ensure_postgres_decision_boundary_index() -> None:
    state = _postgres_index_state(_DECISION_BOUNDARY_INDEX)
    if state is not None:
        keys = tuple(str(value) for value in state.get("keys") or ())
        predicate = "".join(str(state.get("predicate") or "").lower().split())
        if (
            bool(state.get("indisunique"))
            or str(state.get("table_name")) != "decision_records"
            or str(state.get("access_method")) != "btree"
            or keys
            != (
                "namespace",
                "barrier_group",
                "model_id",
                "recorded_at",
                "id",
            )
            or "validmind_inventory_countedistrue" not in predicate
            or "model_idisnotnull" not in predicate
        ):
            raise RuntimeError(
                f"{_DECISION_BOUNDARY_INDEX} has an unexpected definition"
            )
        if bool(state.get("indisvalid")):
            return
        op.execute(
            f"DROP INDEX CONCURRENTLY public.{_DECISION_BOUNDARY_INDEX}"
        )
    op.execute(
        f"""CREATE INDEX CONCURRENTLY {_DECISION_BOUNDARY_INDEX}
        ON public.decision_records (
            namespace, barrier_group, model_id, recorded_at, id
        ) WHERE {_MARKER} IS TRUE AND model_id IS NOT NULL"""
    )


def _claim_postgres_page(table: str) -> int:
    result = op.get_bind().execute(
        sa.text(
            f"""WITH page AS MATERIALIZED (
                    SELECT source.id
                      FROM public.{table} AS source
                     WHERE source.{_MARKER} IS NOT TRUE
                     ORDER BY source.id
                     LIMIT :batch_size
                     FOR UPDATE SKIP LOCKED
                ), claimed AS (
                    UPDATE public.{table} AS source
                       SET {_MARKER} = TRUE
                      FROM page
                     WHERE source.id = page.id
                    RETURNING source.id
                )
                SELECT COUNT(*) FROM claimed"""
        ),
        {"batch_size": _BATCH_SIZE},
    ).scalar_one()
    return int(result)


def _assert_postgres_no_link_identifier_collisions() -> None:
    collision = op.get_bind().execute(
        sa.text(
            """SELECT 1
                 FROM public.validmind_legacy_model_aliases AS alias
                 JOIN public.validmind_model_inventory AS inventory
                   ON inventory.namespace = alias.namespace
                  AND inventory.external_id = alias.legacy_external_id
                WHERE alias.target_count > 1
                   OR alias.canonical_external_id IS DISTINCT FROM
                      alias.legacy_external_id
                LIMIT 1"""
        )
    ).first()
    if collision is not None:
        raise RuntimeError(
            "0053a found a legacy ValidMind identifier that collides with a "
            "different live scoped identifier; no link reconciliation started"
        )


def _mirror_postgres_link_page() -> tuple[int, bool]:
    row = op.get_bind().execute(
        sa.text(
            """WITH page AS MATERIALIZED (
                    SELECT legacy.namespace,
                           legacy.external_id AS legacy_external_id,
                           legacy.vm_cuid,
                           legacy.updated_at,
                           alias.canonical_external_id,
                           canonical.updated_at AS canonical_updated_at
                      FROM public.validmind_model_links AS legacy
                      JOIN public.validmind_legacy_model_aliases AS alias
                        ON alias.namespace = legacy.namespace
                       AND alias.legacy_external_id = legacy.external_id
                       AND alias.target_count = 1
                      JOIN public.validmind_model_inventory AS inventory
                        ON inventory.namespace = alias.namespace
                       AND inventory.legacy_external_id =
                           alias.legacy_external_id
                       AND inventory.external_id =
                           alias.canonical_external_id
                 LEFT JOIN public.validmind_model_links AS canonical
                        ON canonical.namespace = legacy.namespace
                       AND canonical.external_id = alias.canonical_external_id
                     WHERE legacy.external_id <> alias.canonical_external_id
                       AND (
                           canonical.external_id IS NULL
                           OR canonical.vm_cuid IS DISTINCT FROM legacy.vm_cuid
                           OR canonical.updated_at IS DISTINCT FROM legacy.updated_at
                       )
                     ORDER BY legacy.namespace, legacy.external_id
                     LIMIT :batch_size
                     FOR UPDATE OF alias, legacy
                ), conflict AS MATERIALIZED (
                    SELECT 1
                      FROM page
                      JOIN public.validmind_model_links AS canonical
                        ON canonical.namespace = page.namespace
                       AND canonical.external_id = page.canonical_external_id
                     WHERE canonical.vm_cuid IS DISTINCT FROM page.vm_cuid
                     LIMIT 1
                ), normalized AS (
                    UPDATE public.validmind_model_links AS legacy
                       SET updated_at = GREATEST(
                               legacy.updated_at,
                               COALESCE(
                                   page.canonical_updated_at,
                                   legacy.updated_at
                               )
                           )
                      FROM page
                     WHERE NOT EXISTS (SELECT 1 FROM conflict)
                       AND legacy.namespace = page.namespace
                       AND legacy.external_id = page.legacy_external_id
                    RETURNING legacy.namespace,
                              legacy.external_id AS legacy_external_id,
                              legacy.vm_cuid,
                              legacy.updated_at,
                              page.canonical_external_id
                ), mirrored AS (
                    INSERT INTO public.validmind_model_links (
                        namespace, external_id, vm_cuid, updated_at
                    )
                    SELECT normalized.namespace,
                           normalized.canonical_external_id,
                           normalized.vm_cuid,
                           normalized.updated_at
                      FROM normalized
                    ON CONFLICT (namespace, external_id) DO UPDATE
                      SET updated_at = EXCLUDED.updated_at
                      WHERE validmind_model_links.vm_cuid IS NOT DISTINCT FROM
                            EXCLUDED.vm_cuid
                    RETURNING namespace, external_id
                )
                SELECT (SELECT COUNT(*) FROM page) AS page_count,
                       EXISTS (SELECT 1 FROM conflict) AS has_conflict,
                       (SELECT COUNT(*) FROM mirrored) AS changed_count"""
        ),
        {"batch_size": _LINK_BATCH_SIZE},
    ).one()
    page_count = int(row.page_count)
    changed_count = int(row.changed_count)
    has_conflict = bool(row.has_conflict)
    if not has_conflict and page_count != changed_count:
        raise RuntimeError(
            "0053a could not mirror every uniquely scoped legacy ValidMind "
            "link in its locked page"
        )
    return page_count, has_conflict


def _remove_postgres_ambiguous_link_page() -> int:
    row = op.get_bind().execute(
        sa.text(
            """WITH page AS MATERIALIZED (
                    SELECT alias.namespace, alias.legacy_external_id
                      FROM public.validmind_legacy_model_aliases AS alias
                      JOIN public.validmind_model_links AS legacy
                        ON legacy.namespace = alias.namespace
                       AND legacy.external_id = alias.legacy_external_id
                     WHERE alias.target_count > 1
                     ORDER BY alias.namespace, alias.legacy_external_id
                     LIMIT :batch_size
                     FOR UPDATE OF alias, legacy SKIP LOCKED
                ), collision AS MATERIALIZED (
                    SELECT 1
                      FROM page
                      JOIN public.validmind_model_inventory AS inventory
                        ON inventory.namespace = page.namespace
                       AND inventory.external_id = page.legacy_external_id
                     LIMIT 1
                ), removed AS (
                    DELETE FROM public.validmind_model_links AS legacy
                     USING page
                     WHERE NOT EXISTS (SELECT 1 FROM collision)
                       AND legacy.namespace = page.namespace
                       AND legacy.external_id = page.legacy_external_id
                    RETURNING legacy.namespace, legacy.external_id
                )
                SELECT (SELECT COUNT(*) FROM page) AS page_count,
                       EXISTS (SELECT 1 FROM collision) AS has_collision,
                       (SELECT COUNT(*) FROM removed) AS changed_count"""
        ),
        {"batch_size": _LINK_BATCH_SIZE},
    ).one()
    page_count = int(row.page_count)
    changed_count = int(row.changed_count)
    if bool(row.has_collision):
        raise RuntimeError(
            "0053a found a legacy ValidMind identifier that collides with a "
            "live scoped identifier; no ambiguous link in the page was removed"
        )
    if page_count != changed_count:
        raise RuntimeError(
            "0053a could not remove every ambiguous legacy ValidMind link in "
            "its locked page"
        )
    return page_count


def _mirror_postgres_canonical_link_page() -> int:
    row = op.get_bind().execute(
        sa.text(
            """WITH page AS MATERIALIZED (
                    SELECT alias.namespace,
                           alias.legacy_external_id,
                           alias.canonical_external_id,
                           canonical.vm_cuid,
                           canonical.updated_at
                      FROM public.validmind_legacy_model_aliases AS alias
                      JOIN public.validmind_model_inventory AS inventory
                        ON inventory.namespace = alias.namespace
                       AND inventory.legacy_external_id =
                           alias.legacy_external_id
                       AND inventory.external_id =
                           alias.canonical_external_id
                      JOIN public.validmind_model_links AS canonical
                        ON canonical.namespace = alias.namespace
                       AND canonical.external_id = alias.canonical_external_id
                 LEFT JOIN public.validmind_model_links AS legacy
                        ON legacy.namespace = alias.namespace
                       AND legacy.external_id = alias.legacy_external_id
                     WHERE alias.target_count = 1
                       AND alias.canonical_external_id IS NOT NULL
                       AND alias.legacy_external_id <>
                           alias.canonical_external_id
                       AND legacy.external_id IS NULL
                     ORDER BY alias.namespace, alias.legacy_external_id
                     LIMIT :batch_size
                     FOR UPDATE OF alias, canonical SKIP LOCKED
                ), mirrored AS (
                    INSERT INTO public.validmind_model_links (
                        namespace, external_id, vm_cuid, updated_at
                    )
                    SELECT page.namespace,
                           page.legacy_external_id,
                           page.vm_cuid,
                           page.updated_at
                      FROM page
                    ON CONFLICT (namespace, external_id) DO NOTHING
                    RETURNING namespace, external_id
                )
                SELECT (SELECT COUNT(*) FROM page) AS page_count,
                       (SELECT COUNT(*) FROM mirrored) AS changed_count"""
        ),
        {"batch_size": _LINK_BATCH_SIZE},
    ).one()
    page_count = int(row.page_count)
    changed_count = int(row.changed_count)
    if page_count != changed_count:
        raise RuntimeError(
            "0053a could not mirror every uniquely scoped canonical ValidMind "
            "link back to its rolling legacy identifier"
        )
    return page_count


def _assert_postgres_link_transition_invariants() -> None:
    row = op.get_bind().execute(
        sa.text(
            """SELECT EXISTS (
                       SELECT 1
                         FROM public.validmind_legacy_model_aliases AS alias
                         JOIN public.validmind_model_links AS legacy
                           ON legacy.namespace = alias.namespace
                          AND legacy.external_id = alias.legacy_external_id
                        WHERE alias.target_count > 1
                        LIMIT 1
                   ) AS ambiguous_legacy_exists,
                   EXISTS (
                       SELECT 1
                         FROM public.validmind_legacy_model_aliases AS alias
                        WHERE alias.target_count <> (
                                  SELECT COUNT(*)
                                    FROM public.validmind_model_inventory AS inventory
                                   WHERE inventory.namespace = alias.namespace
                                     AND inventory.legacy_external_id =
                                         alias.legacy_external_id
                              )
                           OR (
                               alias.target_count = 1
                               AND alias.canonical_external_id IS DISTINCT FROM (
                                   SELECT min(inventory.external_id)
                                     FROM public.validmind_model_inventory AS inventory
                                    WHERE inventory.namespace = alias.namespace
                                      AND inventory.legacy_external_id =
                                          alias.legacy_external_id
                               )
                           )
                        LIMIT 1
                   ) AS alias_inventory_mismatch,
                   EXISTS (
                       SELECT 1
                         FROM public.validmind_model_inventory AS inventory
                    LEFT JOIN public.validmind_legacy_model_aliases AS alias
                           ON alias.namespace = inventory.namespace
                          AND alias.legacy_external_id =
                              inventory.legacy_external_id
                        WHERE alias.legacy_external_id IS NULL
                        LIMIT 1
                   ) AS inventory_alias_missing,
                   EXISTS (
                       SELECT 1
                         FROM public.validmind_legacy_model_aliases AS alias
                        WHERE alias.target_count = 1
                          AND alias.canonical_external_id IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.validmind_model_inventory AS inventory
                               WHERE inventory.namespace = alias.namespace
                                 AND inventory.legacy_external_id =
                                     alias.legacy_external_id
                                 AND inventory.external_id =
                                     alias.canonical_external_id
                          )
                        LIMIT 1
                   ) AS invalid_unique_alias,
                   EXISTS (
                       SELECT 1
                         FROM public.validmind_legacy_model_aliases AS alias
                         JOIN public.validmind_model_inventory AS inventory
                           ON inventory.namespace = alias.namespace
                          AND inventory.legacy_external_id =
                              alias.legacy_external_id
                          AND inventory.external_id =
                              alias.canonical_external_id
                    LEFT JOIN public.validmind_model_links AS legacy
                           ON legacy.namespace = alias.namespace
                          AND legacy.external_id = alias.legacy_external_id
                    LEFT JOIN public.validmind_model_links AS canonical
                           ON canonical.namespace = alias.namespace
                          AND canonical.external_id =
                              alias.canonical_external_id
                        WHERE alias.target_count = 1
                          AND alias.canonical_external_id IS NOT NULL
                          AND (
                              (legacy.external_id IS NULL) <>
                                  (canonical.external_id IS NULL)
                              OR legacy.vm_cuid IS DISTINCT FROM canonical.vm_cuid
                              OR legacy.updated_at IS DISTINCT FROM
                                  canonical.updated_at
                          )
                        LIMIT 1
                   ) AS unique_pair_mismatch"""
        )
    ).one()
    if (
        bool(row.ambiguous_legacy_exists)
        or bool(row.alias_inventory_mismatch)
        or bool(row.inventory_alias_missing)
        or bool(row.invalid_unique_alias)
        or bool(row.unique_pair_mismatch)
    ):
        raise RuntimeError(
            "0053a link reconciliation did not reach the exact alias transition "
            "invariant"
        )


def _install_strict_decision_guard() -> None:
    op.execute(
        f"""CREATE OR REPLACE FUNCTION public.lians_decision_record_immutable_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'decision records are immutable; record a superseding correction';
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


def _install_postgres_link_compatibility() -> None:
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_validmind_canonicalize_link()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_context_namespace text;
            v_context_barrier text;
            v_namespace text;
            v_external_id text;
            v_canonical_matches bigint;
            v_alias_targets bigint;
            v_canonical_external_id text;
            v_expected_canonical_external_id text;
            v_legacy_external_id text;
            v_input_is_canonical boolean := false;
        BEGIN
            v_context_namespace := current_setting('app.current_namespace', true);
            v_context_barrier := current_setting('agentmem.barrier_group', true);
            IF TG_OP = 'DELETE' THEN
                v_namespace := OLD.namespace;
                v_external_id := OLD.external_id;
            ELSE
                v_namespace := NEW.namespace;
                v_external_id := NEW.external_id;
            END IF;
            IF v_context_namespace IS DISTINCT FROM v_namespace
               AND v_context_namespace IS DISTINCT FROM '__admin__' THEN
                RAISE EXCEPTION 'ValidMind link namespace does not match session context';
            END IF;
            IF TG_OP = 'UPDATE'
               AND (
                   OLD.namespace IS DISTINCT FROM NEW.namespace
                   OR OLD.external_id IS DISTINCT FROM NEW.external_id
               ) THEN
                RAISE EXCEPTION 'ValidMind link identity is immutable';
            END IF;
            IF v_external_id !~ '^lians-model-[0-9a-f]{20}$'
               OR pg_trigger_depth() > 1 THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;
            PERFORM set_config('app.current_namespace', '__admin__', true);
            PERFORM set_config('agentmem.barrier_group', '', true);

            SELECT COUNT(*), min(inventory.legacy_external_id)
              INTO v_canonical_matches, v_legacy_external_id
              FROM (
                  SELECT candidate.legacy_external_id
                    FROM public.validmind_model_inventory AS candidate
                   WHERE candidate.namespace = v_namespace
                     AND candidate.external_id = v_external_id
                   ORDER BY candidate.scope_id, candidate.model_id
                   LIMIT 2
                   FOR KEY SHARE
              ) AS inventory;
            IF v_canonical_matches = 1 THEN
                v_input_is_canonical := true;
                v_canonical_external_id := v_external_id;
                v_expected_canonical_external_id := v_external_id;
            ELSIF v_canonical_matches > 1 THEN
                RAISE EXCEPTION 'ValidMind canonical model identifier collision';
            END IF;

            IF NOT v_input_is_canonical THEN
                v_legacy_external_id := v_external_id;
                SELECT alias.target_count, alias.canonical_external_id
                  INTO v_alias_targets, v_canonical_external_id
                  FROM public.validmind_legacy_model_aliases AS alias
                 WHERE alias.namespace = v_namespace
                   AND alias.legacy_external_id = v_legacy_external_id;
                IF NOT FOUND THEN
                    IF TG_OP = 'DELETE' THEN
                        PERFORM set_config(
                            'app.current_namespace',
                            COALESCE(v_context_namespace, ''),
                            true
                        );
                        PERFORM set_config(
                            'agentmem.barrier_group',
                            COALESCE(v_context_barrier, ''),
                            true
                        );
                        RETURN OLD;
                    END IF;
                    RAISE EXCEPTION 'Unknown ValidMind model identifier';
                ELSIF v_alias_targets > 1
                      OR v_canonical_external_id IS NULL THEN
                    IF TG_OP = 'DELETE' THEN
                        PERFORM set_config(
                            'app.current_namespace',
                            COALESCE(v_context_namespace, ''),
                            true
                        );
                        PERFORM set_config(
                            'agentmem.barrier_group',
                            COALESCE(v_context_barrier, ''),
                            true
                        );
                        RETURN OLD;
                    END IF;
                    RAISE EXCEPTION
                        'Legacy ValidMind model identifier is ambiguous across barrier scopes';
                END IF;
                v_expected_canonical_external_id := v_canonical_external_id;
                SELECT COUNT(*)
                  INTO v_canonical_matches
                  FROM (
                      SELECT 1
                        FROM public.validmind_model_inventory AS inventory
                       WHERE inventory.namespace = v_namespace
                         AND inventory.external_id = v_canonical_external_id
                       ORDER BY inventory.scope_id, inventory.model_id
                       LIMIT 2
                       FOR KEY SHARE
                  ) AS locked_inventory;
                IF v_canonical_matches <> 1 THEN
                    RAISE EXCEPTION
                        'Legacy ValidMind alias lost its canonical inventory row';
                END IF;
            END IF;

            IF NOT pg_try_advisory_xact_lock(
                hashtextextended(
                    'lians:validmind-link:' || v_namespace || ':' ||
                    v_legacy_external_id,
                    0
                )
            ) THEN
                RAISE EXCEPTION 'Concurrent ValidMind link mutation; retry'
                    USING ERRCODE = '40001';
            END IF;
            IF v_input_is_canonical THEN
                SELECT alias.target_count, alias.canonical_external_id
                  INTO v_alias_targets, v_canonical_external_id
                  FROM public.validmind_legacy_model_aliases AS alias
                 WHERE alias.namespace = v_namespace
                   AND alias.legacy_external_id = v_legacy_external_id
                 FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'ValidMind canonical identifier has no alias state';
                ELSIF v_alias_targets = 1
                      AND v_canonical_external_id IS DISTINCT FROM
                          v_expected_canonical_external_id THEN
                    RAISE EXCEPTION
                        'Scoped ValidMind model identifier changed while locking';
                END IF;
            ELSE
                SELECT alias.target_count, alias.canonical_external_id
                  INTO v_alias_targets, v_canonical_external_id
                  FROM public.validmind_legacy_model_aliases AS alias
                 WHERE alias.namespace = v_namespace
                   AND alias.legacy_external_id = v_legacy_external_id
                 FOR KEY SHARE;
                IF NOT FOUND
                   OR v_alias_targets <> 1
                   OR v_canonical_external_id IS DISTINCT FROM
                      v_expected_canonical_external_id THEN
                    RAISE EXCEPTION
                        'Legacy ValidMind model identifier changed while locking';
                END IF;
            END IF;
            IF v_alias_targets = 1
               AND v_legacy_external_id IS DISTINCT FROM v_canonical_external_id THEN
                IF TG_OP = 'DELETE' THEN
                    DELETE FROM public.validmind_model_links AS counterpart
                     WHERE counterpart.namespace = v_namespace
                       AND counterpart.external_id = CASE
                           WHEN v_input_is_canonical THEN v_legacy_external_id
                           ELSE v_canonical_external_id
                       END;
                ELSE
                    INSERT INTO public.validmind_model_links (
                        namespace, external_id, vm_cuid, updated_at
                    ) VALUES (
                        v_namespace,
                        CASE
                            WHEN v_input_is_canonical THEN v_legacy_external_id
                            ELSE v_canonical_external_id
                        END,
                        NEW.vm_cuid,
                        NEW.updated_at
                    ) ON CONFLICT (namespace, external_id) DO UPDATE
                      SET vm_cuid = EXCLUDED.vm_cuid,
                          updated_at = EXCLUDED.updated_at;
                END IF;
            END IF;
            PERFORM set_config(
                'app.current_namespace', COALESCE(v_context_namespace, ''), true
            );
            PERFORM set_config(
                'agentmem.barrier_group', COALESCE(v_context_barrier, ''), true
            );
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        EXCEPTION WHEN OTHERS THEN
            PERFORM set_config(
                'app.current_namespace', COALESCE(v_context_namespace, ''), true
            );
            PERFORM set_config(
                'agentmem.barrier_group', COALESCE(v_context_barrier, ''), true
            );
            RAISE;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_validmind_canonicalize_link() "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_validmind_sync_unique_alias_link()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_context_namespace text;
            v_context_barrier text;
            v_namespace text;
            v_legacy_external_id text;
            v_target_count bigint;
            v_canonical_external_id text;
            v_legacy_vm_cuid text;
            v_legacy_updated_at timestamptz;
            v_legacy_exists boolean := false;
            v_canonical_vm_cuid text;
            v_canonical_updated_at timestamptz;
            v_canonical_exists boolean := false;
            v_updated_at timestamptz;
        BEGIN
            v_context_namespace := current_setting('app.current_namespace', true);
            v_context_barrier := current_setting('agentmem.barrier_group', true);
            IF TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME <> 'validmind_legacy_model_aliases' THEN
                RAISE EXCEPTION 'ValidMind alias-link trigger attached incorrectly';
            END IF;
            IF TG_OP = 'DELETE' THEN
                v_namespace := OLD.namespace;
                v_legacy_external_id := OLD.legacy_external_id;
                v_target_count := 0;
                v_canonical_external_id := OLD.canonical_external_id;
            ELSE
                v_namespace := NEW.namespace;
                v_legacy_external_id := NEW.legacy_external_id;
                v_target_count := NEW.target_count;
                v_canonical_external_id := NEW.canonical_external_id;
            END IF;
            IF TG_OP = 'UPDATE'
               AND (
                   OLD.namespace IS DISTINCT FROM NEW.namespace
                   OR OLD.legacy_external_id IS DISTINCT FROM
                      NEW.legacy_external_id
               ) THEN
                RAISE EXCEPTION 'ValidMind alias identity is immutable';
            END IF;
            IF TG_WHEN = 'BEFORE' THEN
                RETURN NEW;
            END IF;
            IF v_context_namespace IS DISTINCT FROM v_namespace
               AND v_context_namespace IS DISTINCT FROM '__admin__' THEN
                RAISE EXCEPTION
                    'ValidMind alias namespace does not match session context';
            END IF;
            PERFORM set_config('app.current_namespace', '__admin__', true);
            PERFORM set_config('agentmem.barrier_group', '', true);
            IF NOT pg_try_advisory_xact_lock(
                hashtextextended(
                    'lians:validmind-link:' || v_namespace || ':' ||
                    v_legacy_external_id,
                    0
                )
            ) THEN
                RAISE EXCEPTION 'Concurrent ValidMind alias mutation; retry'
                    USING ERRCODE = '40001';
            END IF;

            -- A legacy identifier cannot be removed safely if it collides with
            -- a live scoped identifier. The 80-bit hashes make this exceptional,
            -- but silently deleting scoped state would violate the fail-closed
            -- compatibility contract.
            IF EXISTS (
                   SELECT 1
                     FROM public.validmind_model_inventory AS inventory
                    WHERE inventory.namespace = v_namespace
                      AND inventory.external_id = v_legacy_external_id
                      AND (
                          TG_OP = 'DELETE'
                          OR v_target_count > 1
                          OR v_canonical_external_id IS DISTINCT FROM
                             v_legacy_external_id
                      )
                    LIMIT 1
                    FOR KEY SHARE
               ) THEN
                RAISE EXCEPTION
                    'ValidMind legacy identifier collides with a scoped identifier';
            END IF;

            -- Ambiguous and deleted aliases must never leave a namespace-wide
            -- legacy row readable by a rolling 0.4 caller. Scoped rows remain.
            IF TG_OP = 'DELETE' OR v_target_count > 1 THEN
                DELETE FROM public.validmind_model_links AS legacy
                 WHERE legacy.namespace = v_namespace
                   AND legacy.external_id = v_legacy_external_id;
                PERFORM set_config(
                    'app.current_namespace', COALESCE(v_context_namespace, ''), true
                );
                PERFORM set_config(
                    'agentmem.barrier_group', COALESCE(v_context_barrier, ''), true
                );
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;

            IF v_target_count <> 1 OR v_canonical_external_id IS NULL THEN
                RAISE EXCEPTION 'Invalid ValidMind alias state';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                  FROM public.validmind_model_inventory AS inventory
                 WHERE inventory.namespace = v_namespace
                   AND inventory.legacy_external_id = v_legacy_external_id
                   AND inventory.external_id = v_canonical_external_id
                 LIMIT 1
                 FOR KEY SHARE
            ) THEN
                RAISE EXCEPTION
                    'Unique ValidMind alias has no matching scoped inventory row';
            END IF;

            SELECT link.vm_cuid, link.updated_at
              INTO v_legacy_vm_cuid, v_legacy_updated_at
              FROM public.validmind_model_links AS link
             WHERE link.namespace = v_namespace
               AND link.external_id = v_legacy_external_id
              FOR UPDATE;
            v_legacy_exists := FOUND;
            SELECT link.vm_cuid, link.updated_at
              INTO v_canonical_vm_cuid, v_canonical_updated_at
              FROM public.validmind_model_links AS link
             WHERE link.namespace = v_namespace
               AND link.external_id = v_canonical_external_id
              FOR UPDATE;
            v_canonical_exists := FOUND;
            IF v_legacy_exists
               AND v_canonical_exists
               AND v_canonical_vm_cuid IS DISTINCT FROM v_legacy_vm_cuid THEN
                RAISE EXCEPTION
                    'Conflicting legacy and scoped ValidMind link mappings';
            END IF;
            IF v_legacy_exists AND v_canonical_exists THEN
                v_updated_at := GREATEST(
                    v_legacy_updated_at, v_canonical_updated_at
                );
                UPDATE public.validmind_model_links AS link
                   SET updated_at = v_updated_at
                 WHERE link.namespace = v_namespace
                   AND link.external_id IN (
                       v_legacy_external_id, v_canonical_external_id
                   );
            ELSIF v_legacy_exists THEN
                INSERT INTO public.validmind_model_links (
                    namespace, external_id, vm_cuid, updated_at
                ) VALUES (
                    v_namespace,
                    v_canonical_external_id,
                    v_legacy_vm_cuid,
                    v_legacy_updated_at
                );
            ELSIF v_canonical_exists THEN
                INSERT INTO public.validmind_model_links (
                    namespace, external_id, vm_cuid, updated_at
                ) VALUES (
                    v_namespace,
                    v_legacy_external_id,
                    v_canonical_vm_cuid,
                    v_canonical_updated_at
                );
            END IF;
            PERFORM set_config(
                'app.current_namespace', COALESCE(v_context_namespace, ''), true
            );
            PERFORM set_config(
                'agentmem.barrier_group', COALESCE(v_context_barrier, ''), true
            );
            RETURN NEW;
        EXCEPTION WHEN OTHERS THEN
            PERFORM set_config(
                'app.current_namespace', COALESCE(v_context_namespace, ''), true
            );
            PERFORM set_config(
                'agentmem.barrier_group', COALESCE(v_context_barrier, ''), true
            );
            RAISE;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_validmind_sync_unique_alias_link() "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION public.lians_validmind_cleanup_scoped_link()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_context_namespace text;
            v_context_barrier text;
            v_alias_targets bigint;
            v_alias_canonical_external_id text;
        BEGIN
            v_context_namespace := current_setting('app.current_namespace', true);
            v_context_barrier := current_setting('agentmem.barrier_group', true);
            IF TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME <> 'validmind_model_inventory' THEN
                RAISE EXCEPTION 'ValidMind link-cleanup trigger attached incorrectly';
            END IF;
            IF v_context_namespace IS DISTINCT FROM OLD.namespace
               AND v_context_namespace IS DISTINCT FROM '__admin__' THEN
                RAISE EXCEPTION
                    'ValidMind inventory namespace does not match session context';
            END IF;
            PERFORM set_config('app.current_namespace', '__admin__', true);
            PERFORM set_config('agentmem.barrier_group', '', true);
            IF NOT pg_try_advisory_xact_lock(
                hashtextextended(
                    'lians:validmind-link:' || OLD.namespace || ':' ||
                    OLD.legacy_external_id,
                    0
                )
            ) THEN
                RAISE EXCEPTION 'Concurrent ValidMind inventory mutation; retry'
                    USING ERRCODE = '40001';
            END IF;
            SELECT alias.target_count, alias.canonical_external_id
              INTO v_alias_targets, v_alias_canonical_external_id
              FROM public.validmind_legacy_model_aliases AS alias
             WHERE alias.namespace = OLD.namespace
               AND alias.legacy_external_id = OLD.legacy_external_id
             FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'ValidMind inventory row has no alias state';
            END IF;
            DELETE FROM public.validmind_model_links AS link
             WHERE link.namespace = OLD.namespace
               AND link.external_id = OLD.external_id;
            IF v_alias_targets = 1 THEN
                IF v_alias_canonical_external_id IS DISTINCT FROM OLD.external_id THEN
                    RAISE EXCEPTION
                        'Unique ValidMind alias does not identify deleted inventory row';
                END IF;
                DELETE FROM public.validmind_model_links AS link
                 WHERE link.namespace = OLD.namespace
                   AND link.external_id = OLD.legacy_external_id;
            ELSIF v_alias_targets > 1 THEN
                IF EXISTS (
                    SELECT 1
                      FROM public.validmind_model_inventory AS inventory
                     WHERE inventory.namespace = OLD.namespace
                       AND inventory.external_id = OLD.legacy_external_id
                     LIMIT 1
                     FOR KEY SHARE
                ) THEN
                    RAISE EXCEPTION
                        'ValidMind legacy identifier collides with a scoped identifier';
                END IF;
                DELETE FROM public.validmind_model_links AS link
                 WHERE link.namespace = OLD.namespace
                   AND link.external_id = OLD.legacy_external_id;
            ELSE
                RAISE EXCEPTION 'Invalid ValidMind alias target count';
            END IF;
            PERFORM set_config(
                'app.current_namespace', COALESCE(v_context_namespace, ''), true
            );
            PERFORM set_config(
                'agentmem.barrier_group', COALESCE(v_context_barrier, ''), true
            );
            RETURN OLD;
        EXCEPTION WHEN OTHERS THEN
            PERFORM set_config(
                'app.current_namespace', COALESCE(v_context_namespace, ''), true
            );
            PERFORM set_config(
                'agentmem.barrier_group', COALESCE(v_context_barrier, ''), true
            );
            RAISE;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lians_validmind_cleanup_scoped_link() "
        "FROM PUBLIC, lians_runtime"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validmind_canonicalize_link "
        "ON public.validmind_model_links"
    )
    op.execute(
        """CREATE TRIGGER trg_validmind_canonicalize_link
        BEFORE INSERT OR UPDATE OR DELETE ON public.validmind_model_links
        FOR EACH ROW EXECUTE FUNCTION public.lians_validmind_canonicalize_link()"""
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validmind_sync_unique_alias_link "
        "ON public.validmind_legacy_model_aliases"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validmind_alias_identity_guard "
        "ON public.validmind_legacy_model_aliases"
    )
    op.execute(
        """CREATE TRIGGER trg_validmind_alias_identity_guard
        BEFORE UPDATE OF namespace, legacy_external_id
        ON public.validmind_legacy_model_aliases
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_validmind_sync_unique_alias_link()"""
    )
    op.execute(
        """CREATE TRIGGER trg_validmind_sync_unique_alias_link
        AFTER INSERT OR DELETE OR UPDATE OF target_count, canonical_external_id
        ON public.validmind_legacy_model_aliases
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_validmind_sync_unique_alias_link()"""
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validmind_cleanup_scoped_link "
        "ON public.validmind_model_inventory"
    )
    op.execute(
        """CREATE TRIGGER trg_validmind_cleanup_scoped_link
        AFTER DELETE ON public.validmind_model_inventory
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_validmind_cleanup_scoped_link()"""
    )


def _postgres_upgrade() -> None:
    with op.get_context().autocommit_block():
        # Session scope is deliberate: every page is its own transaction and
        # both source tables may be FORCE-RLS protected in upgraded deployments.
        op.execute(
            sa.text("SELECT set_config('app.current_namespace', '__admin__', false)")
        )
        op.execute(
            sa.text("SELECT set_config('agentmem.barrier_group', '', false)")
        )
        op.execute(
            sa.text(
                "SELECT set_config("
                "'lians.migration_validmind_inventory', "
                "'0053a_validmind_backfill', false)"
            )
        )
        for table in ("decision_records", "otel_spans"):
            while _claim_postgres_page(table):
                pass
        for table in ("decision_records", "otel_spans"):
            remaining = op.get_bind().execute(
                sa.text(
                    f"SELECT COUNT(*) FROM public.{table} "
                    f"WHERE {_MARKER} IS NOT TRUE"
                )
            ).scalar_one()
            if int(remaining):
                raise RuntimeError(
                    f"0053a left {remaining} uncounted rows in {table}"
                )
        _ensure_postgres_decision_boundary_index()
        # Install the idempotent transition boundary before reconciling links.
        # ValidMind PUTs remain quiesced, while concurrent Decision/OTLP source
        # activity can still change alias cardinality and must be covered before
        # the last bounded reconciliation page.
        _install_postgres_link_compatibility()
        _assert_postgres_no_link_identifier_collisions()
        while _remove_postgres_ambiguous_link_page():
            pass
        while True:
            page_count, has_conflict = _mirror_postgres_link_page()
            if has_conflict:
                raise RuntimeError(
                    "0053a found both legacy and scoped ValidMind link rows "
                    "with different vm_cuid values; no link in the page was changed. "
                    "Resolve the conflicting integration mapping, then retry."
                )
            if page_count == 0:
                break
        while _mirror_postgres_canonical_link_page():
            pass
        _assert_postgres_no_link_identifier_collisions()
        _assert_postgres_link_transition_invariants()

    # These final operations are idempotent and share Alembic's stamp
    # transaction. A crash between committed pages re-installs the same trigger
    # boundary and resumes from the remaining uncounted, ambiguous, or one-sided
    # rows; the final assertions admit the contract only at the exact fixed point.
    _install_strict_decision_guard()
    for table in ("decision_records", "otel_spans"):
        op.execute(
            f"ALTER TABLE public.{table} VALIDATE CONSTRAINT "
            f"ck_{table}_validmind_counted"
        )
        op.alter_column(
            table,
            _MARKER,
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        )


def _register_sqlite_functions() -> None:
    from lians.validmind_inventory import (
        validmind_external_id,
        validmind_legacy_model_id,
    )

    connection = op.get_bind().connection
    connection.create_function(
        "lians_external_id",
        3,
        validmind_external_id,
        deterministic=True,
    )
    connection.create_function(
        "lians_legacy_model_id",
        1,
        validmind_legacy_model_id,
        deterministic=True,
    )


def _claim_sqlite_page(table: str) -> int:
    op.get_bind().execute(
        sa.text(
            f"""UPDATE {table}
                   SET {_MARKER} = 1
                 WHERE id IN (
                     SELECT id FROM {table}
                      WHERE {_MARKER} IS NOT 1
                      ORDER BY id
                      LIMIT :batch_size
                 )"""
        ),
        {"batch_size": _BATCH_SIZE},
    )
    return int(op.get_bind().execute(sa.text("SELECT changes()")).scalar_one())


def _mirror_sqlite_links() -> None:
    last_namespace: str | None = None
    last_external_id: str | None = None
    while True:
        rows = op.get_bind().execute(
            sa.text(
                """SELECT legacy.namespace,
                          legacy.external_id AS legacy_external_id,
                          legacy.vm_cuid,
                          legacy.updated_at,
                          alias.canonical_external_id,
                          canonical.external_id AS mirrored_external_id,
                          canonical.vm_cuid AS canonical_vm_cuid
                     FROM validmind_model_links AS legacy
                     JOIN validmind_legacy_model_aliases AS alias
                       ON alias.namespace = legacy.namespace
                      AND alias.legacy_external_id = legacy.external_id
                      AND alias.target_count = 1
                     JOIN validmind_model_inventory AS inventory
                       ON inventory.namespace = alias.namespace
                      AND inventory.legacy_external_id = alias.legacy_external_id
                      AND inventory.external_id = alias.canonical_external_id
                LEFT JOIN validmind_model_links AS canonical
                       ON canonical.namespace = legacy.namespace
                      AND canonical.external_id = alias.canonical_external_id
                    WHERE legacy.external_id <> alias.canonical_external_id
                      AND (
                          canonical.external_id IS NULL
                          OR canonical.vm_cuid IS NOT legacy.vm_cuid
                          OR canonical.updated_at IS NOT legacy.updated_at
                      )
                      AND (
                          :last_namespace IS NULL
                          OR legacy.namespace > :last_namespace
                          OR (
                              legacy.namespace = :last_namespace
                              AND legacy.external_id > :last_external_id
                          )
                      )
                    ORDER BY legacy.namespace, legacy.external_id
                    LIMIT :batch_size"""
            ),
            {
                "batch_size": _LINK_BATCH_SIZE,
                "last_namespace": last_namespace,
                "last_external_id": last_external_id,
            },
        ).mappings().all()
        if not rows:
            return
        checked = []
        for row in rows:
            has_canonical = row["mirrored_external_id"] is not None
            if has_canonical and row["canonical_vm_cuid"] != row["vm_cuid"]:
                raise RuntimeError(
                    "0053a found conflicting legacy and scoped SQLite "
                    "ValidMind link mappings"
                )
            checked.append((row, has_canonical))
        for row, has_canonical in checked:
            if not has_canonical:
                op.get_bind().execute(
                    sa.text(
                        """INSERT INTO validmind_model_links (
                               namespace, external_id, vm_cuid, updated_at
                           ) VALUES (
                               :namespace, :canonical_external_id,
                               :vm_cuid, :updated_at
                           )"""
                    ),
                    dict(row),
                )
            else:
                op.get_bind().execute(
                    sa.text(
                        """UPDATE validmind_model_links
                              SET updated_at = CASE
                                      WHEN updated_at < :updated_at
                                      THEN :updated_at
                                      ELSE updated_at
                                  END
                            WHERE namespace = :namespace
                              AND external_id = :canonical_external_id
                              AND vm_cuid = :vm_cuid"""
                    ),
                    dict(row),
                )
            op.get_bind().execute(
                sa.text(
                    """UPDATE validmind_model_links
                          SET updated_at = (
                              SELECT canonical.updated_at
                                FROM validmind_model_links AS canonical
                               WHERE canonical.namespace = :namespace
                                 AND canonical.external_id =
                                     :canonical_external_id
                          )
                        WHERE namespace = :namespace
                          AND external_id = :legacy_external_id"""
                ),
                dict(row),
            )
        last_namespace = str(rows[-1]["namespace"])
        last_external_id = str(rows[-1]["legacy_external_id"])


def _assert_sqlite_no_link_identifier_collisions() -> None:
    collision = op.get_bind().execute(
        sa.text(
            """SELECT 1
                 FROM validmind_legacy_model_aliases AS alias
                 JOIN validmind_model_inventory AS inventory
                   ON inventory.namespace = alias.namespace
                  AND inventory.external_id = alias.legacy_external_id
                WHERE alias.target_count > 1
                   OR alias.canonical_external_id IS NOT alias.legacy_external_id
                LIMIT 1"""
        )
    ).first()
    if collision is not None:
        raise RuntimeError(
            "0053a found a SQLite legacy ValidMind identifier that collides "
            "with a different live scoped identifier"
        )


def _remove_sqlite_ambiguous_links() -> None:
    collision = op.get_bind().execute(
        sa.text(
            """SELECT 1
                 FROM validmind_legacy_model_aliases AS alias
                 JOIN validmind_model_links AS legacy
                   ON legacy.namespace = alias.namespace
                  AND legacy.external_id = alias.legacy_external_id
                 JOIN validmind_model_inventory AS inventory
                   ON inventory.namespace = alias.namespace
                  AND inventory.external_id = alias.legacy_external_id
                WHERE alias.target_count > 1
                LIMIT 1"""
        )
    ).first()
    if collision is not None:
        raise RuntimeError(
            "0053a found a SQLite legacy ValidMind identifier that collides "
            "with a live scoped identifier"
        )
    while True:
        op.get_bind().execute(
            sa.text(
                """DELETE FROM validmind_model_links
                    WHERE rowid IN (
                        SELECT legacy.rowid
                          FROM validmind_model_links AS legacy
                          JOIN validmind_legacy_model_aliases AS alias
                            ON alias.namespace = legacy.namespace
                           AND alias.legacy_external_id = legacy.external_id
                           AND alias.target_count > 1
                         ORDER BY alias.namespace, alias.legacy_external_id
                         LIMIT :batch_size
                    )"""
            ),
            {"batch_size": _LINK_BATCH_SIZE},
        )
        changed = int(
            op.get_bind().execute(sa.text("SELECT changes()")).scalar_one()
        )
        if changed == 0:
            return


def _mirror_sqlite_canonical_links() -> None:
    last_namespace: str | None = None
    last_external_id: str | None = None
    while True:
        rows = op.get_bind().execute(
            sa.text(
                """SELECT alias.namespace,
                          alias.legacy_external_id,
                          alias.canonical_external_id,
                          canonical.vm_cuid,
                          canonical.updated_at
                     FROM validmind_legacy_model_aliases AS alias
                     JOIN validmind_model_inventory AS inventory
                       ON inventory.namespace = alias.namespace
                      AND inventory.legacy_external_id = alias.legacy_external_id
                      AND inventory.external_id = alias.canonical_external_id
                     JOIN validmind_model_links AS canonical
                       ON canonical.namespace = alias.namespace
                      AND canonical.external_id = alias.canonical_external_id
                LEFT JOIN validmind_model_links AS legacy
                       ON legacy.namespace = alias.namespace
                      AND legacy.external_id = alias.legacy_external_id
                    WHERE alias.target_count = 1
                      AND alias.canonical_external_id IS NOT NULL
                      AND alias.legacy_external_id <> alias.canonical_external_id
                      AND legacy.external_id IS NULL
                      AND (
                          :last_namespace IS NULL
                          OR alias.namespace > :last_namespace
                          OR (
                              alias.namespace = :last_namespace
                              AND alias.legacy_external_id > :last_external_id
                          )
                      )
                    ORDER BY alias.namespace, alias.legacy_external_id
                    LIMIT :batch_size"""
            ),
            {
                "batch_size": _LINK_BATCH_SIZE,
                "last_namespace": last_namespace,
                "last_external_id": last_external_id,
            },
        ).mappings().all()
        if not rows:
            return
        for row in rows:
            op.get_bind().execute(
                sa.text(
                    """INSERT INTO validmind_model_links (
                           namespace, external_id, vm_cuid, updated_at
                       ) VALUES (
                           :namespace, :legacy_external_id, :vm_cuid, :updated_at
                       )"""
                ),
                dict(row),
            )
        last_namespace = str(rows[-1]["namespace"])
        last_external_id = str(rows[-1]["legacy_external_id"])


def _assert_sqlite_link_transition_invariants() -> None:
    row = op.get_bind().execute(
        sa.text(
            """SELECT EXISTS (
                       SELECT 1
                         FROM validmind_legacy_model_aliases AS alias
                         JOIN validmind_model_links AS legacy
                           ON legacy.namespace = alias.namespace
                          AND legacy.external_id = alias.legacy_external_id
                        WHERE alias.target_count > 1
                        LIMIT 1
                   ) AS ambiguous_legacy_exists,
                   EXISTS (
                       SELECT 1
                         FROM validmind_legacy_model_aliases AS alias
                        WHERE alias.target_count <> (
                                  SELECT COUNT(*)
                                    FROM validmind_model_inventory AS inventory
                                   WHERE inventory.namespace = alias.namespace
                                     AND inventory.legacy_external_id =
                                         alias.legacy_external_id
                              )
                           OR (
                               alias.target_count = 1
                               AND alias.canonical_external_id IS NOT (
                                   SELECT min(inventory.external_id)
                                     FROM validmind_model_inventory AS inventory
                                    WHERE inventory.namespace = alias.namespace
                                      AND inventory.legacy_external_id =
                                          alias.legacy_external_id
                               )
                           )
                        LIMIT 1
                   ) AS alias_inventory_mismatch,
                   EXISTS (
                       SELECT 1
                         FROM validmind_model_inventory AS inventory
                    LEFT JOIN validmind_legacy_model_aliases AS alias
                           ON alias.namespace = inventory.namespace
                          AND alias.legacy_external_id =
                              inventory.legacy_external_id
                        WHERE alias.legacy_external_id IS NULL
                        LIMIT 1
                   ) AS inventory_alias_missing,
                   EXISTS (
                       SELECT 1
                         FROM validmind_legacy_model_aliases AS alias
                        WHERE alias.target_count = 1
                          AND alias.canonical_external_id IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1
                                FROM validmind_model_inventory AS inventory
                               WHERE inventory.namespace = alias.namespace
                                 AND inventory.legacy_external_id =
                                     alias.legacy_external_id
                                 AND inventory.external_id =
                                     alias.canonical_external_id
                          )
                        LIMIT 1
                   ) AS invalid_unique_alias,
                   EXISTS (
                       SELECT 1
                         FROM validmind_legacy_model_aliases AS alias
                         JOIN validmind_model_inventory AS inventory
                           ON inventory.namespace = alias.namespace
                          AND inventory.legacy_external_id =
                              alias.legacy_external_id
                          AND inventory.external_id =
                              alias.canonical_external_id
                    LEFT JOIN validmind_model_links AS legacy
                           ON legacy.namespace = alias.namespace
                          AND legacy.external_id = alias.legacy_external_id
                    LEFT JOIN validmind_model_links AS canonical
                           ON canonical.namespace = alias.namespace
                          AND canonical.external_id =
                              alias.canonical_external_id
                        WHERE alias.target_count = 1
                          AND alias.canonical_external_id IS NOT NULL
                          AND (
                              (legacy.external_id IS NULL) IS NOT
                                  (canonical.external_id IS NULL)
                              OR legacy.vm_cuid IS NOT canonical.vm_cuid
                              OR legacy.updated_at IS NOT canonical.updated_at
                          )
                        LIMIT 1
                   ) AS unique_pair_mismatch"""
        )
    ).one()
    if (
        bool(row.ambiguous_legacy_exists)
        or bool(row.alias_inventory_mismatch)
        or bool(row.inventory_alias_missing)
        or bool(row.invalid_unique_alias)
        or bool(row.unique_pair_mismatch)
    ):
        raise RuntimeError(
            "0053a SQLite link reconciliation did not reach the exact alias "
            "transition invariant"
        )


def _install_sqlite_link_compatibility() -> None:
    sync_body = """
        INSERT INTO validmind_model_links (
            namespace, external_id, vm_cuid, updated_at
        )
        SELECT NEW.namespace,
               inventory.legacy_external_id,
               NEW.vm_cuid,
               NEW.updated_at
          FROM validmind_model_inventory AS inventory
          JOIN validmind_legacy_model_aliases AS alias
            ON alias.namespace = inventory.namespace
           AND alias.legacy_external_id = inventory.legacy_external_id
           AND alias.target_count = 1
         WHERE inventory.namespace = NEW.namespace
           AND inventory.external_id = NEW.external_id
           AND inventory.legacy_external_id <> NEW.external_id
        ON CONFLICT (namespace, external_id) DO UPDATE SET
            vm_cuid = excluded.vm_cuid,
            updated_at = excluded.updated_at
        WHERE validmind_model_links.vm_cuid IS NOT excluded.vm_cuid
           OR validmind_model_links.updated_at IS NOT excluded.updated_at;

        INSERT INTO validmind_model_links (
            namespace, external_id, vm_cuid, updated_at
        )
        SELECT NEW.namespace,
               alias.canonical_external_id,
               NEW.vm_cuid,
               NEW.updated_at
          FROM validmind_legacy_model_aliases AS alias
         WHERE alias.namespace = NEW.namespace
           AND alias.legacy_external_id = NEW.external_id
           AND alias.target_count = 1
           AND alias.canonical_external_id <> NEW.external_id
        ON CONFLICT (namespace, external_id) DO UPDATE SET
            vm_cuid = excluded.vm_cuid,
            updated_at = excluded.updated_at
        WHERE validmind_model_links.vm_cuid IS NOT excluded.vm_cuid
           OR validmind_model_links.updated_at IS NOT excluded.updated_at;
    """
    delete_body = """
        DELETE FROM validmind_model_links
         WHERE namespace = OLD.namespace
           AND external_id = (
               SELECT inventory.legacy_external_id
                 FROM validmind_model_inventory AS inventory
                 JOIN validmind_legacy_model_aliases AS alias
                   ON alias.namespace = inventory.namespace
                  AND alias.legacy_external_id = inventory.legacy_external_id
                  AND alias.target_count = 1
                WHERE inventory.namespace = OLD.namespace
                  AND inventory.external_id = OLD.external_id
                LIMIT 1
           );
        DELETE FROM validmind_model_links
         WHERE namespace = OLD.namespace
           AND external_id = (
               SELECT alias.canonical_external_id
                 FROM validmind_legacy_model_aliases AS alias
                WHERE alias.namespace = OLD.namespace
                  AND alias.legacy_external_id = OLD.external_id
                  AND alias.target_count = 1
           );
    """
    alias_unique_sync_body = """
        SELECT RAISE(
            ABORT,
            'ValidMind legacy identifier collides with a scoped identifier'
        )
         WHERE NEW.target_count = 1
           AND NEW.canonical_external_id IS NOT NULL
           AND NEW.canonical_external_id IS NOT NEW.legacy_external_id
           AND EXISTS (
               SELECT 1
                 FROM validmind_model_inventory AS inventory
                WHERE inventory.namespace = NEW.namespace
                  AND inventory.external_id = NEW.legacy_external_id
           );
        SELECT RAISE(
            ABORT,
            'Conflicting legacy and scoped ValidMind link mappings'
        )
         WHERE EXISTS (
             SELECT 1
               FROM validmind_model_links AS legacy
               JOIN validmind_model_links AS canonical
                 ON canonical.namespace = legacy.namespace
                AND canonical.external_id = NEW.canonical_external_id
              WHERE legacy.namespace = NEW.namespace
                AND legacy.external_id = NEW.legacy_external_id
                AND canonical.vm_cuid IS NOT legacy.vm_cuid
                AND NEW.target_count = 1
                AND NEW.canonical_external_id IS NOT NULL
                AND EXISTS (
                    SELECT 1
                      FROM validmind_model_inventory AS inventory
                     WHERE inventory.namespace = NEW.namespace
                       AND inventory.legacy_external_id =
                           NEW.legacy_external_id
                       AND inventory.external_id =
                           NEW.canonical_external_id
                )
         );

        INSERT INTO validmind_model_links (
            namespace, external_id, vm_cuid, updated_at
        )
        SELECT NEW.namespace,
               NEW.canonical_external_id,
               legacy.vm_cuid,
               legacy.updated_at
          FROM validmind_model_links AS legacy
         WHERE legacy.namespace = NEW.namespace
           AND legacy.external_id = NEW.legacy_external_id
           AND NEW.target_count = 1
           AND NEW.canonical_external_id IS NOT NULL
           AND EXISTS (
               SELECT 1
                 FROM validmind_model_inventory AS inventory
                WHERE inventory.namespace = NEW.namespace
                  AND inventory.legacy_external_id = NEW.legacy_external_id
                  AND inventory.external_id = NEW.canonical_external_id
           )
        ON CONFLICT (namespace, external_id) DO UPDATE SET
            updated_at = max(validmind_model_links.updated_at, excluded.updated_at)
        WHERE validmind_model_links.vm_cuid IS excluded.vm_cuid;

        INSERT INTO validmind_model_links (
            namespace, external_id, vm_cuid, updated_at
        )
        SELECT NEW.namespace,
               NEW.legacy_external_id,
               canonical.vm_cuid,
               canonical.updated_at
          FROM validmind_model_links AS canonical
         WHERE canonical.namespace = NEW.namespace
           AND canonical.external_id = NEW.canonical_external_id
           AND NEW.target_count = 1
           AND NEW.canonical_external_id IS NOT NULL
           AND EXISTS (
               SELECT 1
                 FROM validmind_model_inventory AS inventory
                WHERE inventory.namespace = NEW.namespace
                  AND inventory.legacy_external_id = NEW.legacy_external_id
                  AND inventory.external_id = NEW.canonical_external_id
           )
        ON CONFLICT (namespace, external_id) DO UPDATE SET
            updated_at = max(validmind_model_links.updated_at, excluded.updated_at)
        WHERE validmind_model_links.vm_cuid IS excluded.vm_cuid;

        UPDATE validmind_model_links
           SET updated_at = (
               SELECT max(pair.updated_at)
                 FROM validmind_model_links AS pair
                WHERE pair.namespace = NEW.namespace
                  AND pair.external_id IN (
                      NEW.legacy_external_id, NEW.canonical_external_id
                  )
           )
         WHERE namespace = NEW.namespace
           AND external_id IN (
               NEW.legacy_external_id, NEW.canonical_external_id
           )
           AND NEW.target_count = 1
           AND NEW.canonical_external_id IS NOT NULL
           AND EXISTS (
               SELECT 1
                 FROM validmind_model_inventory AS inventory
                WHERE inventory.namespace = NEW.namespace
                  AND inventory.legacy_external_id = NEW.legacy_external_id
                  AND inventory.external_id = NEW.canonical_external_id
           );
    """
    alias_ambiguous_body = """
        SELECT RAISE(
            ABORT,
            'ValidMind legacy identifier collides with a scoped identifier'
        )
         WHERE NEW.target_count > 1
           AND EXISTS (
               SELECT 1
                 FROM validmind_model_inventory AS inventory
                WHERE inventory.namespace = NEW.namespace
                  AND inventory.external_id = NEW.legacy_external_id
           );
        DELETE FROM validmind_model_links
         WHERE namespace = NEW.namespace
           AND external_id = NEW.legacy_external_id
           AND NEW.target_count > 1;
    """
    alias_delete_body = """
        DELETE FROM validmind_model_links
         WHERE namespace = OLD.namespace
           AND external_id = OLD.legacy_external_id;
    """
    inventory_unique_sync_body = """
        SELECT RAISE(
            ABORT,
            'ValidMind legacy identifier collides with a scoped identifier'
        )
         WHERE EXISTS (
             SELECT 1
               FROM validmind_legacy_model_aliases AS alias
              WHERE alias.namespace = NEW.namespace
                AND alias.legacy_external_id = NEW.legacy_external_id
                AND alias.target_count > 1
         )
           AND EXISTS (
               SELECT 1
                 FROM validmind_model_inventory AS inventory
                WHERE inventory.namespace = NEW.namespace
                  AND inventory.external_id = NEW.legacy_external_id
           );
        SELECT RAISE(
            ABORT,
            'ValidMind legacy identifier collides with a scoped identifier'
        )
         WHERE EXISTS (
             SELECT 1
               FROM validmind_legacy_model_aliases AS alias
              WHERE alias.namespace = NEW.namespace
                AND alias.legacy_external_id = NEW.legacy_external_id
                AND alias.target_count = 1
                AND alias.canonical_external_id IS NOT
                    alias.legacy_external_id
         )
           AND EXISTS (
               SELECT 1
                 FROM validmind_model_inventory AS inventory
                WHERE inventory.namespace = NEW.namespace
                  AND inventory.external_id = NEW.legacy_external_id
           );
        SELECT RAISE(
            ABORT,
            'Conflicting legacy and scoped ValidMind link mappings'
        )
         WHERE EXISTS (
             SELECT 1
               FROM validmind_legacy_model_aliases AS alias
               JOIN validmind_model_links AS legacy
                 ON legacy.namespace = alias.namespace
                AND legacy.external_id = alias.legacy_external_id
               JOIN validmind_model_links AS canonical
                 ON canonical.namespace = alias.namespace
                AND canonical.external_id = alias.canonical_external_id
              WHERE alias.namespace = NEW.namespace
                AND alias.legacy_external_id = NEW.legacy_external_id
                AND alias.target_count = 1
                AND alias.canonical_external_id = NEW.external_id
                AND canonical.vm_cuid IS NOT legacy.vm_cuid
         );
        INSERT INTO validmind_model_links (
            namespace, external_id, vm_cuid, updated_at
        )
        SELECT NEW.namespace, NEW.external_id, legacy.vm_cuid, legacy.updated_at
          FROM validmind_legacy_model_aliases AS alias
          JOIN validmind_model_links AS legacy
            ON legacy.namespace = alias.namespace
           AND legacy.external_id = alias.legacy_external_id
         WHERE alias.namespace = NEW.namespace
           AND alias.legacy_external_id = NEW.legacy_external_id
           AND alias.target_count = 1
           AND alias.canonical_external_id = NEW.external_id
        ON CONFLICT (namespace, external_id) DO UPDATE SET
            updated_at = max(validmind_model_links.updated_at, excluded.updated_at)
        WHERE validmind_model_links.vm_cuid IS excluded.vm_cuid;
        INSERT INTO validmind_model_links (
            namespace, external_id, vm_cuid, updated_at
        )
        SELECT NEW.namespace,
               NEW.legacy_external_id,
               canonical.vm_cuid,
               canonical.updated_at
          FROM validmind_legacy_model_aliases AS alias
          JOIN validmind_model_links AS canonical
            ON canonical.namespace = alias.namespace
           AND canonical.external_id = alias.canonical_external_id
         WHERE alias.namespace = NEW.namespace
           AND alias.legacy_external_id = NEW.legacy_external_id
           AND alias.target_count = 1
           AND alias.canonical_external_id = NEW.external_id
        ON CONFLICT (namespace, external_id) DO UPDATE SET
            updated_at = max(validmind_model_links.updated_at, excluded.updated_at)
        WHERE validmind_model_links.vm_cuid IS excluded.vm_cuid;
        UPDATE validmind_model_links
           SET updated_at = (
               SELECT max(pair.updated_at)
                 FROM validmind_model_links AS pair
                WHERE pair.namespace = NEW.namespace
                  AND pair.external_id IN (
                      NEW.legacy_external_id, NEW.external_id
                  )
           )
         WHERE namespace = NEW.namespace
           AND external_id IN (NEW.legacy_external_id, NEW.external_id)
           AND EXISTS (
               SELECT 1
                 FROM validmind_legacy_model_aliases AS alias
                WHERE alias.namespace = NEW.namespace
                  AND alias.legacy_external_id = NEW.legacy_external_id
                  AND alias.target_count = 1
                  AND alias.canonical_external_id = NEW.external_id
           );
        DELETE FROM validmind_model_links
         WHERE namespace = NEW.namespace
           AND external_id = NEW.legacy_external_id
           AND EXISTS (
               SELECT 1
                 FROM validmind_legacy_model_aliases AS alias
                WHERE alias.namespace = NEW.namespace
                  AND alias.legacy_external_id = NEW.legacy_external_id
                  AND alias.target_count > 1
           );
    """
    for name in (
        "trg_validmind_inventory_link_cleanup",
        "trg_validmind_inventory_link_repair",
        "trg_validmind_alias_link_sync_delete",
        "trg_validmind_alias_link_sync_update",
        "trg_validmind_alias_link_sync_insert",
        "trg_validmind_alias_identity_guard",
        "trg_validmind_link_identity_guard",
        "trg_validmind_link_validate_insert",
        "trg_validmind_link_validate_update",
        "trg_validmind_link_sync_insert",
        "trg_validmind_link_sync_update",
        "trg_validmind_link_sync_delete",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.execute(
        """CREATE TRIGGER trg_validmind_alias_identity_guard
        BEFORE UPDATE OF namespace, legacy_external_id
        ON validmind_legacy_model_aliases
        WHEN OLD.namespace IS NOT NEW.namespace
          OR OLD.legacy_external_id IS NOT NEW.legacy_external_id
        BEGIN
            SELECT RAISE(ABORT, 'ValidMind alias identity is immutable');
        END"""
    )
    op.execute(
        """CREATE TRIGGER trg_validmind_link_identity_guard
        BEFORE UPDATE OF namespace, external_id ON validmind_model_links
        WHEN OLD.namespace IS NOT NEW.namespace
          OR OLD.external_id IS NOT NEW.external_id
        BEGIN
            SELECT RAISE(ABORT, 'ValidMind link identity is immutable');
        END"""
    )
    for operation in ("INSERT", "UPDATE"):
        suffix = operation.lower()
        op.execute(
            f"""CREATE TRIGGER trg_validmind_link_validate_{suffix}
            BEFORE {operation} ON validmind_model_links
            WHEN NEW.external_id LIKE 'lians-model-%'
             AND (
                 (
                     SELECT COUNT(*)
                       FROM validmind_model_inventory AS inventory
                      WHERE inventory.namespace = NEW.namespace
                        AND inventory.external_id = NEW.external_id
                 ) <> 1
             )
             AND NOT EXISTS (
                 SELECT 1
                   FROM validmind_legacy_model_aliases AS alias
                  WHERE alias.namespace = NEW.namespace
                    AND alias.legacy_external_id = NEW.external_id
                    AND alias.target_count = 1
                    AND alias.canonical_external_id IS NOT NULL
             )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'Unknown or ambiguous legacy ValidMind model identifier'
                );
            END"""
        )
    op.execute(
        f"""CREATE TRIGGER trg_validmind_link_sync_insert
        AFTER INSERT ON validmind_model_links
        WHEN NEW.external_id LIKE 'lians-model-%'
        BEGIN
            {sync_body}
        END"""
    )
    op.execute(
        f"""CREATE TRIGGER trg_validmind_link_sync_update
        AFTER UPDATE OF vm_cuid, updated_at ON validmind_model_links
        WHEN NEW.external_id LIKE 'lians-model-%'
        BEGIN
            {sync_body}
        END"""
    )
    op.execute(
        f"""CREATE TRIGGER trg_validmind_link_sync_delete
        AFTER DELETE ON validmind_model_links
        WHEN OLD.external_id LIKE 'lians-model-%'
        BEGIN
            {delete_body}
        END"""
    )
    op.execute(
        f"""CREATE TRIGGER trg_validmind_alias_link_sync_insert
        AFTER INSERT ON validmind_legacy_model_aliases
        BEGIN
            SELECT RAISE(
                ABORT,
                'Unique ValidMind alias has no matching scoped inventory row'
            )
             WHERE NEW.target_count = 1
               AND NEW.canonical_external_id IS NOT NULL
               AND EXISTS (
                   SELECT 1
                     FROM validmind_model_inventory AS inventory
                    WHERE inventory.namespace = NEW.namespace
                      AND inventory.legacy_external_id = NEW.legacy_external_id
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM validmind_model_inventory AS inventory
                    WHERE inventory.namespace = NEW.namespace
                      AND inventory.legacy_external_id = NEW.legacy_external_id
                      AND inventory.external_id = NEW.canonical_external_id
               );
            {alias_ambiguous_body}
            {alias_unique_sync_body}
        END"""
    )
    op.execute(
        f"""CREATE TRIGGER trg_validmind_alias_link_sync_update
        AFTER UPDATE OF target_count, canonical_external_id
        ON validmind_legacy_model_aliases
        BEGIN
            SELECT RAISE(
                ABORT,
                'Unique ValidMind alias has no matching scoped inventory row'
            )
             WHERE NEW.target_count = 1
               AND NEW.canonical_external_id IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1
                     FROM validmind_model_inventory AS inventory
                    WHERE inventory.namespace = NEW.namespace
                      AND inventory.legacy_external_id = NEW.legacy_external_id
                      AND inventory.external_id = NEW.canonical_external_id
               );
            {alias_ambiguous_body}
            {alias_unique_sync_body}
        END"""
    )
    op.execute(
        f"""CREATE TRIGGER trg_validmind_alias_link_sync_delete
        AFTER DELETE ON validmind_legacy_model_aliases
        BEGIN
            {alias_delete_body}
        END"""
    )
    op.execute(
        f"""CREATE TRIGGER trg_validmind_inventory_link_repair
        AFTER INSERT ON validmind_model_inventory
        BEGIN
            {inventory_unique_sync_body}
        END"""
    )
    # 0053's SQLite source trigger transitions/deletes the alias before it
    # deletes the terminal inventory row (PostgreSQL does the reverse). A
    # unique alias that now points elsewhere is therefore the 2 -> 1 survivor:
    # remove OLD's scoped link but preserve the newly repaired legacy mirror.
    op.execute(
        """CREATE TRIGGER trg_validmind_inventory_link_cleanup
        AFTER DELETE ON validmind_model_inventory
        BEGIN
            SELECT RAISE(
                ABORT,
                'ValidMind legacy identifier collides with a scoped identifier'
            )
             WHERE (
                 NOT EXISTS (
                     SELECT 1
                       FROM validmind_legacy_model_aliases AS alias
                      WHERE alias.namespace = OLD.namespace
                        AND alias.legacy_external_id = OLD.legacy_external_id
                 )
                 OR EXISTS (
                     SELECT 1
                       FROM validmind_legacy_model_aliases AS alias
                      WHERE alias.namespace = OLD.namespace
                        AND alias.legacy_external_id = OLD.legacy_external_id
                        AND (
                            alias.target_count > 1
                            OR alias.canonical_external_id = OLD.external_id
                        )
                 )
             )
               AND EXISTS (
                 SELECT 1
                   FROM validmind_model_inventory AS inventory
                  WHERE inventory.namespace = OLD.namespace
                    AND inventory.external_id = OLD.legacy_external_id
               );
            DELETE FROM validmind_model_links
             WHERE namespace = OLD.namespace
                AND external_id = OLD.external_id;
            DELETE FROM validmind_model_links
             WHERE namespace = OLD.namespace
               AND external_id = OLD.legacy_external_id
               AND (
                   NOT EXISTS (
                       SELECT 1
                         FROM validmind_legacy_model_aliases AS alias
                        WHERE alias.namespace = OLD.namespace
                          AND alias.legacy_external_id = OLD.legacy_external_id
                   )
                   OR EXISTS (
                       SELECT 1
                         FROM validmind_legacy_model_aliases AS alias
                        WHERE alias.namespace = OLD.namespace
                          AND alias.legacy_external_id = OLD.legacy_external_id
                          AND (
                              alias.target_count > 1
                              OR alias.canonical_external_id = OLD.external_id
                          )
                   )
               );
        END"""
    )


def _sqlite_upgrade() -> None:
    _register_sqlite_functions()
    with op.get_context().autocommit_block():
        for table in ("decision_records", "otel_spans"):
            while _claim_sqlite_page(table):
                pass
        # SQLite serializes writers, but install the same transition boundary
        # first so a connection admitted between committed pages cannot reopen
        # an alias/link gap.
        _install_sqlite_link_compatibility()
        _assert_sqlite_no_link_identifier_collisions()
        _remove_sqlite_ambiguous_links()
        _mirror_sqlite_links()
        _mirror_sqlite_canonical_links()
        _assert_sqlite_no_link_identifier_collisions()
        _assert_sqlite_link_transition_invariants()
    for table in ("decision_records", "otel_spans"):
        remaining = op.get_bind().execute(
            sa.text(
                f"SELECT COUNT(*) FROM {table} WHERE {_MARKER} IS NOT 1"
            )
        ).scalar_one()
        if int(remaining):
            raise RuntimeError(f"0053a left {remaining} uncounted rows in {table}")
    op.execute(
        f"""CREATE INDEX IF NOT EXISTS {_DECISION_BOUNDARY_INDEX}
        ON decision_records (
            namespace, barrier_group, model_id, recorded_at, id
        ) WHERE {_MARKER} IS 1 AND model_id IS NOT NULL"""
    )
    # SQLite cannot add NOT NULL/default metadata without rebuilding these large
    # append tables. The marker guard/promotion triggers enforce the same final
    # invariant without an unbounded table copy.


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0053a_validmind_backfill requires an online connection so "
            "bounded source pages can commit and resume safely. Generate "
            "reviewed offline DDL only through 0053_validmind_inventory, then "
            "run 0053a online."
        )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _postgres_upgrade()
    elif dialect == "sqlite":
        _sqlite_upgrade()
    else:
        raise RuntimeError(f"ValidMind inventory backfill is unsupported on {dialect}")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_validmind_cleanup_scoped_link "
            "ON public.validmind_model_inventory"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_validmind_sync_unique_alias_link "
            "ON public.validmind_legacy_model_aliases"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_validmind_alias_identity_guard "
            "ON public.validmind_legacy_model_aliases"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_validmind_canonicalize_link "
            "ON public.validmind_model_links"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_validmind_cleanup_scoped_link()"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "public.lians_validmind_sync_unique_alias_link()"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_validmind_canonicalize_link()"
        )
        for table in ("decision_records", "otel_spans"):
            op.alter_column(
                table,
                _MARKER,
                existing_type=sa.Boolean(),
                nullable=True,
                server_default=None,
            )
    elif dialect == "sqlite":
        for name in (
            "trg_validmind_inventory_link_cleanup",
            "trg_validmind_inventory_link_repair",
            "trg_validmind_alias_link_sync_delete",
            "trg_validmind_alias_link_sync_update",
            "trg_validmind_alias_link_sync_insert",
            "trg_validmind_alias_identity_guard",
            "trg_validmind_link_sync_delete",
            "trg_validmind_link_sync_update",
            "trg_validmind_link_sync_insert",
            "trg_validmind_link_validate_update",
            "trg_validmind_link_validate_insert",
            "trg_validmind_link_identity_guard",
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
    # Counted data and safely mirrored links remain valid expand-state
    # projections. 0053's downgrade removes them if the operator continues.
