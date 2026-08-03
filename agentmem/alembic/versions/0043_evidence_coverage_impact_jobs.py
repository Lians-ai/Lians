"""Add explicit evidence coverage and resumable exhaustive impact jobs.

Revision ID: 0043_evidence_impact_jobs
Revises: 0042a_recorder_backfill
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0043_evidence_impact_jobs"
down_revision = "0042a_recorder_backfill"
branch_labels = None
depends_on = None

_KINDS = (
    "source",
    "policy",
    "model",
    "tool",
    "permission",
    "instruction",
    "input",
    "output",
)
_REGISTRATION_FENCE_HASH_SEED = 1279873363
_APPEND_ONLY_EVIDENCE_TABLES = (
    "evidence_artifacts",
    "decision_evidence_links",
    "decision_evidence_link_registrations",
    "decision_evidence_coverage_sets",
)
_DURABLE_MUTABLE_EVIDENCE_TABLES = (
    "decision_evidence_kind_coverage",
    "decision_impact_assessment_jobs",
    "decision_impact_assessment_matches",
)


def _sequence_type() -> sa.types.TypeEngine:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _set_postgresql_migration_context() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("SELECT set_config('app.current_namespace', '__admin__', true)")
    )
    connection.execute(
        sa.text("SELECT set_config('agentmem.barrier_group', '', true)")
    )


def _backfill_unknown_legacy_coverage() -> None:
    dialect = op.get_bind().dialect.name
    op.execute(
        """INSERT INTO decision_evidence_coverage_sets (
               namespace, barrier_group, decision_id, registered_at
           )
           SELECT namespace, barrier_group, id, recorded_at
           FROM decision_records"""
    )
    values = ", ".join(f"('{kind}')" for kind in _KINDS)
    if dialect == "postgresql":
        op.execute(
            f"""INSERT INTO decision_evidence_kind_coverage (
                   id, coverage_set_sequence, namespace, barrier_group,
                   decision_id, kind, status, indexer_version,
                   normalization_scope, source_watermark, gap_codes,
                   indexed_artifact_count, assessed_at, created_at, updated_at
               )
               SELECT gen_random_uuid(), coverage.sequence, coverage.namespace,
                      coverage.barrier_group, coverage.decision_id, kinds.kind,
                      'unknown', 'legacy-unassessed', 'legacy_pre_watermark',
                      NULL, '["legacy_backfill_unknown"]'::json, 0, NULL,
                      coverage.registered_at, coverage.registered_at
               FROM decision_evidence_coverage_sets AS coverage
               CROSS JOIN (VALUES {values}) AS kinds(kind)"""
        )
    elif dialect == "sqlite":
        op.execute(
            f"""WITH kinds(kind) AS (VALUES {values})
               INSERT INTO decision_evidence_kind_coverage (
                   id, coverage_set_sequence, namespace, barrier_group,
                   decision_id, kind, status, indexer_version,
                   normalization_scope, source_watermark, gap_codes,
                   indexed_artifact_count, assessed_at, created_at, updated_at
               )
               SELECT lower(hex(randomblob(16))), coverage.sequence,
                      coverage.namespace, coverage.barrier_group,
                      coverage.decision_id, kinds.kind,
                      'unknown', 'legacy-unassessed', 'legacy_pre_watermark',
                      NULL, '["legacy_backfill_unknown"]', 0, NULL,
                      coverage.registered_at, coverage.registered_at
               FROM decision_evidence_coverage_sets AS coverage
               CROSS JOIN kinds"""
        )
    else:
        connection = op.get_bind()
        coverage_rows = connection.execute(
            sa.text(
                """SELECT sequence, namespace, barrier_group, decision_id,
                          registered_at
                   FROM decision_evidence_coverage_sets
                   ORDER BY sequence"""
            )
        ).mappings()
        rows = []
        for coverage in coverage_rows:
            for kind in _KINDS:
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "coverage_set_sequence": coverage["sequence"],
                        "namespace": coverage["namespace"],
                        "barrier_group": coverage["barrier_group"],
                        "decision_id": coverage["decision_id"],
                        "kind": kind,
                        "status": "unknown",
                        "indexer_version": "legacy-unassessed",
                        "normalization_scope": "legacy_pre_watermark",
                        "source_watermark": None,
                        "gap_codes": ["legacy_backfill_unknown"],
                        "indexed_artifact_count": 0,
                        "assessed_at": None,
                        "created_at": coverage["registered_at"],
                        "updated_at": coverage["registered_at"],
                    }
                )
                if len(rows) >= 1000:
                    connection.execute(
                        sa.table(
                            "decision_evidence_kind_coverage",
                            sa.column("id", sa.Uuid()),
                            sa.column("coverage_set_sequence", sa.BigInteger()),
                            sa.column("namespace", sa.String()),
                            sa.column("barrier_group", sa.String()),
                            sa.column("decision_id", sa.Uuid()),
                            sa.column("kind", sa.String()),
                            sa.column("status", sa.String()),
                            sa.column("indexer_version", sa.String()),
                            sa.column("normalization_scope", sa.String()),
                            sa.column("source_watermark", sa.String()),
                            sa.column("gap_codes", sa.JSON()),
                            sa.column("indexed_artifact_count", sa.Integer()),
                            sa.column("assessed_at", sa.DateTime(timezone=True)),
                            sa.column("created_at", sa.DateTime(timezone=True)),
                            sa.column("updated_at", sa.DateTime(timezone=True)),
                        ).insert(),
                        rows,
                    )
                    rows = []
        if rows:
            table = sa.table(
                "decision_evidence_kind_coverage",
                *[
                    sa.column(name)
                    for name in (
                        "id",
                        "coverage_set_sequence",
                        "namespace",
                        "barrier_group",
                        "decision_id",
                        "kind",
                        "status",
                        "indexer_version",
                        "normalization_scope",
                        "source_watermark",
                        "gap_codes",
                        "indexed_artifact_count",
                        "assessed_at",
                        "created_at",
                        "updated_at",
                    )
                ],
            )
            connection.execute(table.insert(), rows)


def _install_postgresql_rls() -> None:
    for table in (
        "decision_evidence_link_registrations",
        "decision_evidence_coverage_sets",
        "decision_evidence_kind_coverage",
    ):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
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
        op.execute(
            f"""CREATE POLICY barrier_isolation ON public.{table} AS RESTRICTIVE
            USING (
                barrier_group IS NULL
                OR current_setting('agentmem.barrier_group', true) IS NULL
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )"""
        )

    for table, barrier_column in (
        ("decision_impact_assessment_jobs", "barrier_group"),
        ("decision_impact_assessment_matches", "job_barrier_group"),
    ):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
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
        if table == "decision_impact_assessment_matches":
            barrier_expression = """
                current_setting('app.current_namespace', true) = '__admin__'
                OR EXISTS (
                    SELECT 1
                    FROM public.decision_impact_assessment_jobs AS owned_job
                    WHERE owned_job.id =
                        decision_impact_assessment_matches.job_id
                      AND owned_job.namespace =
                        decision_impact_assessment_matches.namespace
                      AND (
                          (
                              current_setting(
                                  'agentmem.barrier_group', true
                              ) = ''
                              AND owned_job.barrier_group IS NULL
                          )
                          OR owned_job.barrier_group = current_setting(
                              'agentmem.barrier_group', true
                          )
                      )
                )
            """
        else:
            barrier_expression = f"""
                current_setting('app.current_namespace', true) = '__admin__'
                OR (
                    current_setting('agentmem.barrier_group', true) = ''
                    AND {barrier_column} IS NULL
                )
                OR {barrier_column} =
                    current_setting('agentmem.barrier_group', true)
            """
        op.execute(
            f"""CREATE POLICY barrier_exact ON public.{table} AS RESTRICTIVE
            USING ({barrier_expression})"""
        )

    for table in (
        *_APPEND_ONLY_EVIDENCE_TABLES[2:],
        *_DURABLE_MUTABLE_EVIDENCE_TABLES,
    ):
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
    for table in _APPEND_ONLY_EVIDENCE_TABLES[2:]:
        op.execute(
            f"GRANT SELECT ON TABLE public.{table} TO lians_runtime"
        )
        op.execute(
            f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.{table} "
            "FROM lians_runtime"
        )
    for table in _DURABLE_MUTABLE_EVIDENCE_TABLES:
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE ON TABLE public.{table} TO lians_runtime"
        )
        op.execute(
            f"REVOKE DELETE, TRUNCATE ON TABLE public.{table} FROM lians_runtime"
        )

    op.execute(
        """CREATE FUNCTION public.lians_evidence_coverage_guard_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF ROW(
                NEW.id,
                NEW.coverage_set_sequence,
                NEW.namespace,
                NEW.barrier_group,
                NEW.decision_id,
                NEW.kind,
                NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id,
                OLD.coverage_set_sequence,
                OLD.namespace,
                OLD.barrier_group,
                OLD.decision_id,
                OLD.kind,
                OLD.created_at
            ) THEN
                RAISE EXCEPTION
                    'decision evidence coverage identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE TRIGGER trg_decision_evidence_kind_coverage_guard_update
        BEFORE UPDATE ON public.decision_evidence_kind_coverage
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_evidence_coverage_guard_update()"""
    )

    op.execute(
        """CREATE FUNCTION public.lians_impact_job_guard_update()
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
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE TRIGGER trg_decision_impact_assessment_jobs_guard_update
        BEFORE UPDATE ON public.decision_impact_assessment_jobs
        FOR EACH ROW EXECUTE FUNCTION public.lians_impact_job_guard_update()"""
    )

    op.execute(
        """CREATE FUNCTION public.lians_impact_match_guard_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF ROW(
                NEW.sequence,
                NEW.namespace,
                NEW.job_id,
                NEW.job_barrier_group,
                NEW.decision_id,
                NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.sequence,
                OLD.namespace,
                OLD.job_id,
                OLD.job_barrier_group,
                OLD.decision_id,
                OLD.created_at
            ) THEN
                RAISE EXCEPTION 'impact assessment match identity is immutable';
            END IF;
            IF OLD.impact_status = 'direct_reference'
               AND NEW.impact_status <> 'direct_reference' THEN
                RAISE EXCEPTION 'impact assessment match cannot be downgraded';
            END IF;
            IF NEW.risk_score < OLD.risk_score
               OR NEW.updated_at < OLD.updated_at THEN
                RAISE EXCEPTION 'impact assessment match cannot move backward';
            END IF;
            IF NOT (
                COALESCE(NEW.match_basis, '[]'::json)::jsonb
                @> COALESCE(OLD.match_basis, '[]'::json)::jsonb
            ) OR NOT (
                COALESCE(NEW.match_sources, '[]'::json)::jsonb
                @> COALESCE(OLD.match_sources, '[]'::json)::jsonb
            ) THEN
                RAISE EXCEPTION 'impact assessment match evidence cannot be removed';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE TRIGGER trg_decision_impact_assessment_matches_guard_update
        BEFORE UPDATE ON public.decision_impact_assessment_matches
        FOR EACH ROW EXECUTE FUNCTION public.lians_impact_match_guard_update()"""
    )
    for function in (
        "lians_evidence_coverage_guard_update",
        "lians_impact_job_guard_update",
        "lians_impact_match_guard_update",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC")
    for sequence in (
        "decision_evidence_coverage_sets_sequence_seq",
        "decision_evidence_link_registrations_sequence_seq",
    ):
        op.execute(
            f"REVOKE USAGE, SELECT ON SEQUENCE public.{sequence} FROM lians_runtime"
        )
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE "
        "public.decision_impact_assessment_matches_sequence_seq TO lians_runtime"
    )
    values = ", ".join(f"('{kind}')" for kind in _KINDS)
    op.execute(
        f"""CREATE FUNCTION public.lians_register_decision_evidence_coverage()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_sequence bigint;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    NEW.namespace,
                    {_REGISTRATION_FENCE_HASH_SEED}
                )
            );
            INSERT INTO public.decision_evidence_coverage_sets (
                namespace, barrier_group, decision_id, registered_at
            ) VALUES (
                NEW.namespace, NEW.barrier_group, NEW.id, clock_timestamp()
            ) ON CONFLICT (namespace, decision_id) DO NOTHING;

            SELECT coverage.sequence INTO v_sequence
            FROM public.decision_evidence_coverage_sets AS coverage
            WHERE coverage.namespace = NEW.namespace
              AND coverage.decision_id = NEW.id;

            INSERT INTO public.decision_evidence_kind_coverage (
                id, coverage_set_sequence, namespace, barrier_group,
                decision_id, kind, status, indexer_version,
                normalization_scope, source_watermark, gap_codes,
                indexed_artifact_count, assessed_at, created_at, updated_at
            )
            SELECT gen_random_uuid(), v_sequence, NEW.namespace,
                   NEW.barrier_group, NEW.id, kinds.kind, 'unknown',
                   'registration-pending', 'decision_insert_registration',
                   NULL, '["normalization_pending"]'::json, 0, NULL,
                   clock_timestamp(), clock_timestamp()
            FROM (VALUES {values}) AS kinds(kind)
            ON CONFLICT (namespace, decision_id, kind) DO NOTHING;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.lians_register_decision_evidence_coverage() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER trg_decision_register_evidence_coverage
        AFTER INSERT ON public.decision_records
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_register_decision_evidence_coverage()"""
    )
    op.execute(
        f"""CREATE FUNCTION public.lians_register_decision_evidence_link()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    NEW.namespace,
                    {_REGISTRATION_FENCE_HASH_SEED}
                )
            );
            INSERT INTO public.decision_evidence_link_registrations (
                namespace, barrier_group, link_id, registered_at
            ) VALUES (
                NEW.namespace, NEW.barrier_group, NEW.id, clock_timestamp()
            ) ON CONFLICT (link_id) DO NOTHING;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.lians_register_decision_evidence_link() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER trg_decision_register_evidence_link
        AFTER INSERT ON public.decision_evidence_links
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_register_decision_evidence_link()"""
    )


def _install_postgresql_integrity_boundaries() -> None:
    op.execute(
        """CREATE FUNCTION public.lians_evidence_append_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only; % is forbidden',
                TG_TABLE_NAME, TG_OP;
        END;
        $$"""
    )
    op.execute(
        """CREATE FUNCTION public.lians_evidence_state_reject_removal()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION '% is durable state; % is forbidden',
                TG_TABLE_NAME, TG_OP;
        END;
        $$"""
    )
    for function in (
        "lians_evidence_append_reject_mutation",
        "lians_evidence_state_reject_removal",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC")

    for table in _APPEND_ONLY_EVIDENCE_TABLES:
        op.execute(
            f"""CREATE TRIGGER trg_{table}_reject_mutation
            BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION
                public.lians_evidence_append_reject_mutation()"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_{table}_reject_truncate
            BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION
                public.lians_evidence_append_reject_mutation()"""
        )
        op.execute(
            f"REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.{table} FROM PUBLIC"
        )
        op.execute(
            f"REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.{table} "
            "FROM lians_runtime"
        )
        if table in _APPEND_ONLY_EVIDENCE_TABLES[:2]:
            op.execute(
                f"GRANT SELECT, INSERT ON TABLE public.{table} TO lians_runtime"
            )
        else:
            op.execute(
                f"GRANT SELECT ON TABLE public.{table} TO lians_runtime"
            )
            op.execute(
                f"REVOKE INSERT ON TABLE public.{table} FROM lians_runtime"
            )

    for table in _DURABLE_MUTABLE_EVIDENCE_TABLES:
        op.execute(
            f"""CREATE TRIGGER trg_{table}_reject_delete
            BEFORE DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION
                public.lians_evidence_state_reject_removal()"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_{table}_reject_truncate
            BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION
                public.lians_evidence_state_reject_removal()"""
        )
        op.execute(
            f"REVOKE DELETE, TRUNCATE ON TABLE public.{table} FROM PUBLIC"
        )
        op.execute(
            f"REVOKE DELETE, TRUNCATE ON TABLE public.{table} FROM lians_runtime"
        )


def _install_sqlite_registration_trigger() -> None:
    statements = []
    for kind in _KINDS:
        statements.append(
            f"""INSERT OR IGNORE INTO decision_evidence_kind_coverage (
                    id, coverage_set_sequence, namespace, barrier_group,
                    decision_id, kind, status, indexer_version,
                    normalization_scope, source_watermark, gap_codes,
                    indexed_artifact_count, assessed_at, created_at, updated_at
                )
                SELECT lower(hex(randomblob(16))), coverage.sequence,
                       NEW.namespace, NEW.barrier_group, NEW.id, '{kind}',
                       'unknown', 'registration-pending',
                       'decision_insert_registration', NULL,
                       '["normalization_pending"]', 0, NULL,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM decision_evidence_coverage_sets AS coverage
                WHERE coverage.namespace = NEW.namespace
                  AND coverage.decision_id = NEW.id;"""
        )
    body = "\n".join(statements)
    op.execute(
        f"""CREATE TRIGGER trg_decision_register_evidence_coverage
        AFTER INSERT ON decision_records
        BEGIN
            INSERT OR IGNORE INTO decision_evidence_coverage_sets (
                namespace, barrier_group, decision_id, registered_at
            ) VALUES (
                NEW.namespace, NEW.barrier_group, NEW.id, CURRENT_TIMESTAMP
            );
            {body}
        END"""
    )
    op.execute(
        """CREATE TRIGGER trg_decision_register_evidence_link
        AFTER INSERT ON decision_evidence_links
        BEGIN
            INSERT OR IGNORE INTO decision_evidence_link_registrations (
                namespace, barrier_group, link_id, registered_at
            ) VALUES (
                NEW.namespace, NEW.barrier_group, NEW.id, CURRENT_TIMESTAMP
            );
        END"""
    )


def _install_sqlite_integrity_boundaries() -> None:
    for table in _APPEND_ONLY_EVIDENCE_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"""CREATE TRIGGER trg_{table}_reject_{operation.casefold()}
                BEFORE {operation} ON {table}
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        '{table} is append-only; {operation} is forbidden'
                    );
                END"""
            )
    for table in _DURABLE_MUTABLE_EVIDENCE_TABLES:
        op.execute(
            f"""CREATE TRIGGER trg_{table}_reject_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table} is durable; DELETE is forbidden');
            END"""
        )
    op.execute(
        """CREATE TRIGGER trg_decision_evidence_kind_coverage_guard_update
        BEFORE UPDATE ON decision_evidence_kind_coverage
        BEGIN
            SELECT RAISE(
                ABORT,
                'decision evidence coverage identity is immutable'
            ) WHERE
                NEW.id IS NOT OLD.id
                OR NEW.coverage_set_sequence IS NOT OLD.coverage_set_sequence
                OR NEW.namespace IS NOT OLD.namespace
                OR NEW.barrier_group IS NOT OLD.barrier_group
                OR NEW.decision_id IS NOT OLD.decision_id
                OR NEW.kind IS NOT OLD.kind
                OR NEW.created_at IS NOT OLD.created_at;
        END"""
    )
    op.execute(
        """CREATE TRIGGER trg_decision_impact_assessment_jobs_guard_update
        BEFORE UPDATE ON decision_impact_assessment_jobs
        BEGIN
            SELECT RAISE(
                ABORT,
                'impact assessment snapshot identity is immutable'
            ) WHERE
                NEW.id IS NOT OLD.id
                OR NEW.namespace IS NOT OLD.namespace
                OR NEW.barrier_group IS NOT OLD.barrier_group
                OR NEW.barrier_scope IS NOT OLD.barrier_scope
                OR NEW.idempotency_key_hash IS NOT OLD.idempotency_key_hash
                OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
                OR NEW.dependency_kind IS NOT OLD.dependency_kind
                OR NEW.dependency_value IS NOT OLD.dependency_value
                OR NEW.dependency_lookup_hash IS NOT OLD.dependency_lookup_hash
                OR NEW.change_type IS NOT OLD.change_type
                OR NEW.change_occurred_at IS NOT OLD.change_occurred_at
                OR NEW.note IS NOT OLD.note
                OR NEW.requested_by_principal_ref IS NOT
                    OLD.requested_by_principal_ref
                OR NEW.requested_by_auth_method IS NOT
                    OLD.requested_by_auth_method
                OR NEW.snapshot_max_coverage_sequence IS NOT
                    OLD.snapshot_max_coverage_sequence
                OR NEW.snapshot_max_link_sequence IS NOT
                    OLD.snapshot_max_link_sequence
                OR NEW.record_event IS NOT OLD.record_event
                OR NEW.created_at IS NOT OLD.created_at;
            SELECT RAISE(
                ABORT,
                'terminal impact assessment is immutable'
            ) WHERE OLD.status IN ('completed', 'failed');
            SELECT RAISE(
                ABORT,
                'impact assessment progress cannot move backward'
            ) WHERE
                (OLD.status = 'running' AND NEW.status = 'pending')
                OR NEW.cursor_coverage_sequence < OLD.cursor_coverage_sequence
                OR NEW.decisions_scanned < OLD.decisions_scanned
                OR NEW.fallback_candidates_scanned <
                    OLD.fallback_candidates_scanned
                OR NEW.indexed_decisions_matched < OLD.indexed_decisions_matched
                OR NEW.legacy_decisions_matched < OLD.legacy_decisions_matched
                OR NEW.matches_found < OLD.matches_found
                OR NEW.direct_count < OLD.direct_count
                OR NEW.reachable_count < OLD.reachable_count
                OR NEW.pages_completed < OLD.pages_completed
                OR NEW.updated_at < OLD.updated_at;
            SELECT RAISE(
                ABORT,
                'impact assessment timestamps are immutable once set'
            ) WHERE
                (OLD.started_at IS NOT NULL
                    AND NEW.started_at IS NOT OLD.started_at)
                OR (OLD.completion_event_id IS NOT NULL
                    AND NEW.completion_event_id IS NOT
                        OLD.completion_event_id);
            SELECT RAISE(
                ABORT,
                'impact assessment completion is inconsistent'
            ) WHERE
                NEW.status = 'completed'
                AND (
                    NEW.cursor_coverage_sequence IS NOT
                        NEW.snapshot_max_coverage_sequence
                    OR NEW.completed_at IS NULL
                    OR NEW.matches_found IS NOT
                        NEW.direct_count + NEW.reachable_count
                    OR (NEW.record_event = 1
                        AND NEW.completion_event_id IS NULL)
                    OR (NEW.record_event = 0
                        AND NEW.completion_event_id IS NOT NULL)
                );
            SELECT RAISE(
                ABORT,
                'unfinished impact assessment cannot be completed'
            ) WHERE
                NEW.status <> 'completed'
                AND (
                    NEW.completed_at IS NOT NULL
                    OR NEW.completion_event_id IS NOT NULL
                );
            SELECT RAISE(
                ABORT,
                'started impact assessment requires start time'
            ) WHERE
                NEW.status IN ('running', 'completed', 'failed')
                AND NEW.started_at IS NULL;
            SELECT RAISE(
                ABORT,
                'failed impact assessment requires failure code'
            ) WHERE NEW.status = 'failed' AND NEW.failure_code IS NULL;
        END"""
    )
    op.execute(
        """CREATE TRIGGER trg_decision_impact_assessment_matches_guard_update
        BEFORE UPDATE ON decision_impact_assessment_matches
        BEGIN
            SELECT RAISE(
                ABORT,
                'impact assessment match identity is immutable'
            ) WHERE
                NEW.sequence IS NOT OLD.sequence
                OR NEW.namespace IS NOT OLD.namespace
                OR NEW.job_id IS NOT OLD.job_id
                OR NEW.job_barrier_group IS NOT OLD.job_barrier_group
                OR NEW.decision_id IS NOT OLD.decision_id
                OR NEW.created_at IS NOT OLD.created_at;
            SELECT RAISE(
                ABORT,
                'impact assessment match cannot move backward'
            ) WHERE
                (OLD.impact_status = 'direct_reference'
                    AND NEW.impact_status <> 'direct_reference')
                OR NEW.risk_score < OLD.risk_score
                OR NEW.updated_at < OLD.updated_at;
        END"""
    )


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _set_postgresql_migration_context()

    op.create_table(
        "decision_evidence_coverage_sets",
        sa.Column("sequence", _sequence_type(), primary_key=True, autoincrement=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "namespace",
            "decision_id",
            name="uq_decision_evidence_coverage_set",
        ),
    )
    for name, columns in (
        ("ix_decision_evidence_coverage_sets_namespace", ["namespace"]),
        ("ix_decision_evidence_coverage_sets_barrier_group", ["barrier_group"]),
        (
            "ix_decision_evidence_coverage_scan",
            ["namespace", "sequence", "decision_id"],
        ),
    ):
        op.create_index(name, "decision_evidence_coverage_sets", columns)

    op.create_table(
        "decision_evidence_link_registrations",
        sa.Column("sequence", _sequence_type(), primary_key=True, autoincrement=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column(
            "link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decision_evidence_links.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_decision_evidence_link_registrations_namespace", ["namespace"]),
        (
            "ix_decision_evidence_link_registrations_barrier_group",
            ["barrier_group"],
        ),
        (
            "ix_decision_evidence_link_registration_scan",
            ["namespace", "sequence", "link_id"],
        ),
    ):
        op.create_index(name, "decision_evidence_link_registrations", columns)
    op.execute(
        """INSERT INTO decision_evidence_link_registrations (
               namespace, barrier_group, link_id, registered_at
           )
           SELECT namespace, barrier_group, id, recorded_at
           FROM decision_evidence_links"""
    )

    op.create_table(
        "decision_evidence_kind_coverage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "coverage_set_sequence",
            _sequence_type(),
            sa.ForeignKey(
                "decision_evidence_coverage_sets.sequence", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("indexer_version", sa.String(64), nullable=False),
        sa.Column("normalization_scope", sa.String(64), nullable=False),
        sa.Column("source_watermark", sa.String(64), nullable=True),
        sa.Column("gap_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "indexed_artifact_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('source','policy','model','tool','permission',"
            "'instruction','input','output')",
            name="ck_decision_evidence_coverage_kind",
        ),
        sa.CheckConstraint(
            "status IN ('unknown','partial','complete')",
            name="ck_decision_evidence_coverage_status",
        ),
        sa.CheckConstraint(
            "source_watermark IS NULL OR length(source_watermark) = 64",
            name="ck_decision_evidence_coverage_watermark",
        ),
        sa.CheckConstraint(
            "(status = 'unknown' AND source_watermark IS NULL "
            "AND assessed_at IS NULL) OR "
            "(status IN ('partial','complete') "
            "AND source_watermark IS NOT NULL AND assessed_at IS NOT NULL)",
            name="ck_decision_evidence_coverage_assessment_state",
        ),
        sa.CheckConstraint(
            "indexed_artifact_count >= 0",
            name="ck_decision_evidence_coverage_artifact_count",
        ),
        sa.CheckConstraint(
            "json_array_length(gap_codes) <= 32",
            name="ck_decision_evidence_coverage_gap_bound",
        ),
        sa.UniqueConstraint(
            "namespace",
            "decision_id",
            "kind",
            name="uq_decision_evidence_kind_coverage",
        ),
    )
    for name, columns in (
        ("ix_decision_evidence_kind_coverage_namespace", ["namespace"]),
        ("ix_decision_evidence_kind_coverage_barrier_group", ["barrier_group"]),
        (
            "ix_decision_evidence_coverage_kind_status",
            ["namespace", "kind", "status", "decision_id"],
        ),
        (
            "ix_decision_evidence_coverage_set_kind",
            ["coverage_set_sequence", "kind"],
        ),
    ):
        op.create_index(name, "decision_evidence_kind_coverage", columns)

    _backfill_unknown_legacy_coverage()

    op.create_table(
        "decision_impact_assessment_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("barrier_scope", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("dependency_kind", sa.String(32), nullable=False),
        sa.Column("dependency_value", sa.String(1537), nullable=False),
        sa.Column("dependency_lookup_hash", sa.String(64), nullable=False),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("change_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(2000), nullable=True),
        sa.Column("requested_by_principal_ref", sa.String(512), nullable=False),
        sa.Column("requested_by_auth_method", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("snapshot_max_coverage_sequence", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_max_link_sequence", sa.BigInteger(), nullable=False),
        sa.Column(
            "cursor_coverage_sequence", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("decisions_scanned", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "fallback_candidates_scanned",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "indexed_decisions_matched",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "legacy_decisions_matched",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("matches_found", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("direct_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reachable_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("pages_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("record_event", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "completion_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ledger_events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "dependency_kind IN ('source','policy','model','tool','permission',"
            "'instruction','input','output')",
            name="ck_impact_assessment_dependency_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_impact_assessment_status",
        ),
        sa.CheckConstraint(
            "snapshot_max_coverage_sequence >= 0 "
            "AND snapshot_max_link_sequence >= 0 "
            "AND cursor_coverage_sequence >= 0 "
            "AND cursor_coverage_sequence <= snapshot_max_coverage_sequence",
            name="ck_impact_assessment_cursors",
        ),
        sa.CheckConstraint(
            "decisions_scanned >= 0 AND fallback_candidates_scanned >= 0 "
            "AND indexed_decisions_matched >= 0 "
            "AND legacy_decisions_matched >= 0 AND matches_found >= 0 "
            "AND direct_count >= 0 AND reachable_count >= 0 "
            "AND pages_completed >= 0",
            name="ck_impact_assessment_counts",
        ),
        sa.CheckConstraint(
            "fallback_candidates_scanned <= decisions_scanned",
            name="ck_impact_assessment_fallback_count",
        ),
        sa.UniqueConstraint(
            "namespace",
            "barrier_scope",
            "idempotency_key_hash",
            name="uq_impact_assessment_idempotency",
        ),
    )
    for name, columns in (
        ("ix_decision_impact_assessment_jobs_namespace", ["namespace"]),
        ("ix_decision_impact_assessment_jobs_barrier_group", ["barrier_group"]),
        (
            "ix_impact_assessment_queue",
            ["namespace", "barrier_scope", "status", "created_at"],
        ),
    ):
        op.create_index(name, "decision_impact_assessment_jobs", columns)

    op.create_table(
        "decision_impact_assessment_matches",
        sa.Column("sequence", _sequence_type(), primary_key=True, autoincrement=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decision_impact_assessment_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_barrier_group", sa.String(), nullable=True),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decision_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("impact_status", sa.String(32), nullable=False),
        sa.Column("match_basis", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("match_sources", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "impact_status IN ('direct_reference','reachable')",
            name="ck_impact_assessment_match_status",
        ),
        sa.CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100",
            name="ck_impact_assessment_match_risk_score",
        ),
        sa.CheckConstraint(
            "risk_level IN ('critical','high','medium','low')",
            name="ck_impact_assessment_match_risk_level",
        ),
        sa.CheckConstraint(
            "(risk_score >= 85 AND risk_level = 'critical') OR "
            "(risk_score >= 70 AND risk_score < 85 AND risk_level = 'high') OR "
            "(risk_score >= 45 AND risk_score < 70 AND risk_level = 'medium') OR "
            "(risk_score < 45 AND risk_level = 'low')",
            name="ck_impact_assessment_match_risk_consistency",
        ),
        sa.CheckConstraint(
            "json_array_length(match_basis) <= 100",
            name="ck_impact_assessment_match_basis_bound",
        ),
        sa.CheckConstraint(
            "json_array_length(match_sources) <= 2",
            name="ck_impact_assessment_match_sources_bound",
        ),
        sa.UniqueConstraint(
            "namespace",
            "job_id",
            "decision_id",
            name="uq_impact_assessment_match",
        ),
    )
    for name, columns in (
        ("ix_decision_impact_assessment_matches_namespace", ["namespace"]),
        (
            "ix_decision_impact_assessment_matches_job_barrier_group",
            ["job_barrier_group"],
        ),
        (
            "ix_impact_assessment_match_page",
            ["namespace", "job_id", "sequence"],
        ),
    ):
        op.create_index(name, "decision_impact_assessment_matches", columns)

    if op.get_bind().dialect.name == "postgresql":
        _install_postgresql_rls()
        _install_postgresql_integrity_boundaries()
    elif op.get_bind().dialect.name == "sqlite":
        _install_sqlite_registration_trigger()
        _install_sqlite_integrity_boundaries()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table, trigger in (
            (
                "decision_evidence_kind_coverage",
                "trg_decision_evidence_kind_coverage_guard_update",
            ),
            (
                "decision_impact_assessment_jobs",
                "trg_decision_impact_assessment_jobs_guard_update",
            ),
            (
                "decision_impact_assessment_matches",
                "trg_decision_impact_assessment_matches_guard_update",
            ),
        ):
            op.execute(
                f"DROP TRIGGER IF EXISTS {trigger} ON public.{table}"
            )
        for function in (
            "lians_impact_match_guard_update",
            "lians_impact_job_guard_update",
            "lians_evidence_coverage_guard_update",
        ):
            op.execute(f"DROP FUNCTION IF EXISTS public.{function}()")
        for table in _DURABLE_MUTABLE_EVIDENCE_TABLES:
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table}_reject_truncate "
                f"ON public.{table}"
            )
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table}_reject_delete "
                f"ON public.{table}"
            )
        for table in _APPEND_ONLY_EVIDENCE_TABLES:
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table}_reject_truncate "
                f"ON public.{table}"
            )
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table}_reject_mutation "
                f"ON public.{table}"
            )
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "public.lians_evidence_state_reject_removal()"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "public.lians_evidence_append_reject_mutation()"
        )
        # Restore the ordinary grants established before this migration for
        # the two pre-existing graph tables. TRUNCATE remains revoked.
        for table in _APPEND_ONLY_EVIDENCE_TABLES[:2]:
            op.execute(
                f"GRANT UPDATE, DELETE ON TABLE public.{table} TO lians_runtime"
            )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_decision_register_evidence_link "
            "ON public.decision_evidence_links"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "public.lians_register_decision_evidence_link()"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_decision_register_evidence_coverage "
            "ON public.decision_records"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "public.lians_register_decision_evidence_coverage()"
        )
        for table, policy in (
            ("decision_impact_assessment_matches", "barrier_exact"),
            ("decision_impact_assessment_jobs", "barrier_exact"),
            ("decision_evidence_kind_coverage", "barrier_isolation"),
            ("decision_evidence_coverage_sets", "barrier_isolation"),
            ("decision_evidence_link_registrations", "barrier_isolation"),
        ):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON public.{table}")
        for table in (
            "decision_impact_assessment_matches",
            "decision_impact_assessment_jobs",
            "decision_evidence_kind_coverage",
            "decision_evidence_coverage_sets",
            "decision_evidence_link_registrations",
        ):
            op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON public.{table}")
            op.execute(f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
    elif op.get_bind().dialect.name == "sqlite":
        for trigger in (
            "trg_decision_evidence_kind_coverage_guard_update",
            "trg_decision_impact_assessment_jobs_guard_update",
            "trg_decision_impact_assessment_matches_guard_update",
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        for table in _DURABLE_MUTABLE_EVIDENCE_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_delete")
        for table in _APPEND_ONLY_EVIDENCE_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_update")
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_decision_register_evidence_link")
        op.execute("DROP TRIGGER IF EXISTS trg_decision_register_evidence_coverage")

    op.drop_table("decision_impact_assessment_matches")
    op.drop_table("decision_impact_assessment_jobs")
    op.drop_table("decision_evidence_kind_coverage")
    op.drop_table("decision_evidence_link_registrations")
    op.drop_table("decision_evidence_coverage_sets")
