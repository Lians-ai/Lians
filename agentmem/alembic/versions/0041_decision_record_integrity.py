"""Bind DecisionRecord rows to authenticated immutable recorder provenance.

Revision ID: 0041_decision_record_integrity
Revises: 0040a_gate_permit_contract
"""

import sqlalchemy as sa
from alembic import context, op

revision = "0041_decision_record_integrity"
down_revision = "0040a_gate_permit_contract"
branch_labels = None
depends_on = None

_LEGACY_PRINCIPAL = "lians:principal:v1:legacy-unverified"
_LEGACY_AUTH_METHOD = "legacy_unverified"

_CHECK_CONSTRAINTS = (
    (
        "ck_decision_record_hash_version",
        "record_hash_version IN (1, 2)",
    ),
    (
        "ck_decision_record_integrity_status",
        "record_integrity_status IN ('verified', 'legacy_unverified')",
    ),
    (
        "ck_decision_record_hash_length",
        "length(record_hash) = 64 AND record_hash = lower(record_hash)",
    ),
    (
        "ck_decision_record_provenance_state",
        """(
            record_hash_version = 1
            AND record_integrity_status = 'legacy_unverified'
            AND recorded_by_principal_ref =
                'lians:principal:v1:legacy-unverified'
            AND recorded_by_auth_method = 'legacy_unverified'
            AND recorded_by_credential_ref IS NULL
        ) OR (
            record_hash_version = 2
            AND record_integrity_status = 'verified'
            AND recorded_by_principal_ref LIKE 'lians:principal:v1:%'
            AND recorded_by_principal_ref <>
                'lians:principal:v1:legacy-unverified'
            AND length(recorded_by_principal_ref) > 20
            AND recorded_by_auth_method <> 'legacy_unverified'
            AND length(recorded_by_auth_method) > 0
            AND recorded_by_credential_ref LIKE
                'lians:credential:v1:sha256:%'
            AND length(recorded_by_credential_ref) = 91
        )""",
    ),
)

_HASH_COVERED_COLUMNS = (
    "id",
    "namespace",
    "agent_id",
    "recorded_by_principal_ref",
    "recorded_by_auth_method",
    "recorded_by_credential_ref",
    "barrier_group",
    "decision_type",
    "outcome",
    "reason_codes",
    "regime",
    "subject_id",
    "session_id",
    "model_id",
    "model_version",
    "policy_version",
    "decided_at",
    "recorded_at",
    "knowledge_as_of",
    "knowledge_recorded_as_of",
    "evidence_memory_ids",
    "input_hash",
    "output_hash",
    "supersedes_id",
    "metadata",
    "record_hash_version",
    "record_integrity_status",
    "record_hash",
)


def _install_postgresql_guards() -> None:
    op.execute(
        """CREATE FUNCTION public.lians_decision_record_immutable_guard()
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
    op.execute(
        """CREATE TRIGGER trg_decision_record_immutable
        BEFORE UPDATE OR DELETE ON public.decision_records
        FOR EACH ROW EXECUTE FUNCTION public.lians_decision_record_immutable_guard()"""
    )
    op.execute(
        """CREATE TRIGGER trg_decision_record_reject_truncate
        BEFORE TRUNCATE ON public.decision_records
        FOR EACH STATEMENT EXECUTE FUNCTION public.lians_decision_record_immutable_guard()"""
    )
    # Migration 0040 predates authenticated DecisionRecord provenance. Close
    # its direct-SQL gap without weakening the existing permit-grant validator.
    op.execute(
        """CREATE FUNCTION public.lians_gate_require_verified_decision_record()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM public.decision_records AS decision
                JOIN public.event_log AS binding
                  ON binding.namespace = decision.namespace
                 AND binding.op = 'decision_recorded'
                 AND binding.agent_id = decision.recorded_by_principal_ref
                 AND binding.content_hash = decision.record_hash
                 AND binding.payload = jsonb_build_object(
                        'schema', 'lians.decision-record-binding.v1',
                        'decision_id', decision.id::text,
                        'record_hash', decision.record_hash
                     )
                WHERE decision.id = NEW.decision_id
                  AND decision.namespace = NEW.namespace
                  AND decision.barrier_group IS NOT DISTINCT FROM NEW.barrier_group
                  AND decision.record_hash_version = 2
                  AND decision.record_integrity_status = 'verified'
                  AND decision.recorded_by_principal_ref LIKE 'lians:principal:v1:%'
                  AND decision.recorded_by_principal_ref <>
                      'lians:principal:v1:legacy-unverified'
                  AND length(decision.recorded_by_principal_ref) > 20
                  AND decision.recorded_by_auth_method <> 'legacy_unverified'
                  AND length(decision.recorded_by_auth_method) > 0
                  AND decision.recorded_by_credential_ref LIKE
                      'lians:credential:v1:sha256:%'
                  AND length(decision.recorded_by_credential_ref) = 91
            ) THEN
                RAISE EXCEPTION
                    'execution permits require an authenticated verified decision record';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.lians_gate_require_verified_decision_record() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER trg_gate_execution_permit_require_verified_decision
        BEFORE INSERT ON public.gate_execution_permits
        FOR EACH ROW EXECUTE FUNCTION
            public.lians_gate_require_verified_decision_record()"""
    )


def _install_sqlite_projection_guard() -> None:
    # Reinstalled because SQLite table reconstruction drops table-owned triggers.
    op.execute(
        """CREATE TRIGGER trg_decision_review_projection_guard
        BEFORE UPDATE OF human_review_status, human_reviewer, human_reviewed_at
        ON decision_records
        WHEN NOT EXISTS (
            SELECT 1 FROM decision_review_events latest
            WHERE latest.namespace = NEW.namespace
              AND latest.decision_id = NEW.id
              AND latest.status = NEW.human_review_status
              AND latest.reviewer_principal_id = NEW.human_reviewer
              AND latest.reviewed_at = NEW.human_reviewed_at
              AND latest.sequence = (
                  SELECT MAX(sequence) FROM decision_review_events
                  WHERE namespace = NEW.namespace AND decision_id = NEW.id
              )
        )
        BEGIN
            SELECT RAISE(ABORT, 'human review fields require an immutable review event');
        END"""
    )


def _install_sqlite_guards() -> None:
    columns = ", ".join(_HASH_COVERED_COLUMNS)
    op.execute(
        f"""CREATE TRIGGER trg_decision_record_immutable
        BEFORE UPDATE OF {columns} ON decision_records
        BEGIN
            SELECT RAISE(ABORT, 'hash-covered decision record fields are immutable');
        END"""
    )
    op.execute(
        """CREATE TRIGGER trg_decision_record_reject_delete
        BEFORE DELETE ON decision_records
        BEGIN
            SELECT RAISE(ABORT, 'decision records are immutable');
        END"""
    )
    op.execute(
        """CREATE TRIGGER trg_gate_execution_permit_require_verified_decision
        BEFORE INSERT ON gate_execution_permits
        WHEN NOT EXISTS (
            SELECT 1
            FROM decision_records decision
            JOIN event_log binding
              ON binding.namespace = decision.namespace
             AND binding.op = 'decision_recorded'
             AND binding.agent_id = decision.recorded_by_principal_ref
             AND binding.content_hash = decision.record_hash
             AND json_extract(binding.payload, '$.schema') =
                 'lians.decision-record-binding.v1'
             AND replace(json_extract(binding.payload, '$.decision_id'), '-', '') =
                 replace(CAST(decision.id AS TEXT), '-', '')
             AND json_extract(binding.payload, '$.record_hash') = decision.record_hash
            WHERE decision.id = NEW.decision_id
              AND decision.namespace = NEW.namespace
              AND decision.barrier_group IS NEW.barrier_group
              AND decision.record_hash_version = 2
              AND decision.record_integrity_status = 'verified'
              AND decision.recorded_by_principal_ref LIKE 'lians:principal:v1:%'
              AND decision.recorded_by_principal_ref <>
                  'lians:principal:v1:legacy-unverified'
              AND length(decision.recorded_by_principal_ref) > 20
              AND decision.recorded_by_auth_method <> 'legacy_unverified'
              AND length(decision.recorded_by_auth_method) > 0
              AND decision.recorded_by_credential_ref LIKE
                  'lians:credential:v1:sha256:%'
              AND length(decision.recorded_by_credential_ref) = 91
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'execution permits require an authenticated verified decision record'
            );
        END"""
    )
    _install_sqlite_projection_guard()


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        # decision_records has FORCE RLS. The dedicated migrator must make its
        # intentionally cross-tenant maintenance context explicit.
        op.execute("SELECT set_config('app.current_namespace', '__admin__', true)")
        op.execute("SELECT set_config('agentmem.barrier_group', '', true)")
    op.add_column(
        "decision_records",
        sa.Column(
            "recorded_by_principal_ref",
            sa.String(),
            nullable=False,
            server_default=_LEGACY_PRINCIPAL,
        ),
    )
    op.add_column(
        "decision_records",
        sa.Column(
            "recorded_by_auth_method",
            sa.String(length=64),
            nullable=False,
            server_default=_LEGACY_AUTH_METHOD,
        ),
    )
    op.add_column(
        "decision_records",
        sa.Column("recorded_by_credential_ref", sa.String(), nullable=True),
    )
    op.add_column(
        "decision_records",
        sa.Column(
            "record_hash_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "decision_records",
        sa.Column(
            "record_integrity_status",
            sa.String(length=32),
            nullable=False,
            server_default="legacy_unverified",
        ),
    )

    if context.is_offline_mode():
        if dialect == "postgresql":
            op.execute(
                """DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM public.decision_records
                         WHERE record_hash IS NULL
                            OR length(record_hash) <> 64
                            OR record_hash <> lower(record_hash)
                    ) THEN
                        RAISE EXCEPTION
                            'DecisionRecord integrity migration refused malformed hashes';
                    END IF;
                END;
                $$"""
            )
    else:
        malformed = bind.execute(
            sa.text(
                """SELECT COUNT(*) FROM decision_records
                   WHERE record_hash IS NULL
                      OR length(record_hash) <> 64
                      OR record_hash <> lower(record_hash)"""
            )
        ).scalar_one()
        if malformed:
            raise RuntimeError(
                "DecisionRecord integrity migration refused malformed historical hashes"
            )

    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_decision_review_projection_guard")
    if dialect == "postgresql":
        # Constant legacy defaults are PostgreSQL fast defaults: historical rows
        # and rolling 0.4.2 inserts are classified without a table rewrite. New
        # 0.5 writers explicitly supply the authenticated v2 tuple.
        for name, expression in _CHECK_CONSTRAINTS:
            op.execute(
                f"ALTER TABLE public.decision_records ADD CONSTRAINT {name} "
                f"CHECK ({expression}) NOT VALID"
            )
        op.execute(
            "ALTER TABLE public.decision_records "
            + ", ".join(
                f"VALIDATE CONSTRAINT {name}"
                for name, _expression in _CHECK_CONSTRAINTS
            )
        )
        _install_postgresql_guards()
    else:
        with op.batch_alter_table("decision_records") as batch:
            for name, expression in _CHECK_CONSTRAINTS:
                batch.create_check_constraint(name, expression)
    if dialect == "sqlite":
        _install_sqlite_guards()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "trg_gate_execution_permit_require_verified_decision "
            "ON public.gate_execution_permits"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "public.lians_gate_require_verified_decision_record()"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_decision_record_reject_truncate "
            "ON public.decision_records"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_decision_record_immutable "
            "ON public.decision_records"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS public.lians_decision_record_immutable_guard()"
        )
    elif dialect == "sqlite":
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "trg_gate_execution_permit_require_verified_decision"
        )
        op.execute("DROP TRIGGER IF EXISTS trg_decision_record_immutable")
        op.execute("DROP TRIGGER IF EXISTS trg_decision_record_reject_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_decision_review_projection_guard")

    with op.batch_alter_table("decision_records") as batch:
        batch.drop_constraint(
            "ck_decision_record_provenance_state",
            type_="check",
        )
        batch.drop_constraint(
            "ck_decision_record_hash_length",
            type_="check",
        )
        batch.drop_constraint(
            "ck_decision_record_integrity_status",
            type_="check",
        )
        batch.drop_constraint(
            "ck_decision_record_hash_version",
            type_="check",
        )
        batch.drop_column("record_integrity_status")
        batch.drop_column("record_hash_version")
        batch.drop_column("recorded_by_credential_ref")
        batch.drop_column("recorded_by_auth_method")
        batch.drop_column("recorded_by_principal_ref")

    if dialect == "sqlite":
        _install_sqlite_projection_guard()
