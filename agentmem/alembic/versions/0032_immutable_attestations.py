"""Immutable Gate approvals and hash-chained decision review history.

Revision ID: 0032_immutable_attestations
Revises: 0031_enterprise_provisioning
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_immutable_attestations"
down_revision = "0031_enterprise_provisioning"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True)


_TABLES = ("gate_approval_attestations", "decision_review_events")


def upgrade() -> None:
    # API-key identities now participate directly in role-bound approvals.
    # Production databases enforce the same role/barrier invariants as the API
    # contracts; SQLite development databases retain application validation.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM api_keys
                    WHERE role IS NOT NULL
                      AND role NOT IN ('owner', 'analyst', 'compliance', 'readonly')
                ) THEN
                    RAISE EXCEPTION '0032: api_keys contains unsupported named roles'
                        USING HINT = 'Repair roles before retrying the migration.';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM api_keys
                    WHERE barrier_group IS NOT NULL
                      AND barrier_group !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$'
                ) THEN
                    RAISE EXCEPTION '0032: api_keys contains invalid barrier groups'
                        USING HINT = 'Normalize barrier groups before retrying the migration.';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM trusted_receipt_keys
                    WHERE key_id !~ '^[A-Za-z0-9_~-]([A-Za-z0-9._~-]*[A-Za-z0-9_~-])?$'
                ) THEN
                    RAISE EXCEPTION '0032: trusted receipt key IDs are not safe URL segments'
                        USING HINT = 'Rotate unsafe key IDs before retrying the migration.';
                END IF;
            END
            $$"""
        )
        op.alter_column(
            "gate_decision_records",
            "principal_id",
            existing_type=sa.String(length=255),
            type_=sa.String(length=512),
            existing_nullable=False,
        )
        op.create_check_constraint(
            "ck_api_key_role",
            "api_keys",
            "role IS NULL OR role IN ('owner', 'analyst', 'compliance', 'readonly')",
        )
        op.create_check_constraint(
            "ck_api_key_barrier_group",
            "api_keys",
            "barrier_group IS NULL OR "
            "(char_length(barrier_group) BETWEEN 1 AND 255 "
            "AND barrier_group = btrim(barrier_group) "
            "AND barrier_group ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$')",
        )
        op.create_check_constraint(
            "ck_trusted_receipt_key_safe_id",
            "trusted_receipt_keys",
            "key_id ~ '^[A-Za-z0-9_~-]([A-Za-z0-9._~-]*[A-Za-z0-9_~-])?$'",
        )

    op.create_table(
        "gate_approval_attestations",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("series_key", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("approval_principal_id", sa.String(length=512), nullable=False),
        sa.Column("attested_by", sa.String(length=512), nullable=False),
        sa.Column("principal_type", sa.String(length=32), nullable=True),
        sa.Column("attester_role", sa.String(length=100), nullable=False),
        sa.Column("auth_method", sa.String(length=32), nullable=False),
        sa.Column("credential_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column(
            "decision_id",
            _uuid(),
            sa.ForeignKey("decision_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "change_event_id",
            _uuid(),
            sa.ForeignKey("ledger_events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "policy_set_id",
            _uuid(),
            sa.ForeignKey("gate_policy_sets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("target_ref", sa.String(length=2048), nullable=True),
        sa.Column("target_barrier_group", sa.String(length=255), nullable=True),
        sa.Column("receipt_hash", sa.String(length=64), nullable=True),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("statement_encrypted", sa.Text(), nullable=True),
        sa.Column("statement_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "evidence_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "supersedes_id",
            _uuid(),
            sa.ForeignKey("gate_approval_attestations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("prior_attestation_hash", sa.String(length=64), nullable=True),
        sa.Column("attestation_hash", sa.String(length=64), nullable=False),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "namespace",
            "series_key",
            "sequence",
            name="uq_gate_approval_series_sequence",
        ),
        sa.UniqueConstraint("supersedes_id", name="uq_gate_approval_supersedes"),
        sa.UniqueConstraint("attestation_hash", name="uq_gate_approval_attestation_hash"),
        sa.CheckConstraint("sequence > 0", name="ck_gate_approval_sequence"),
        sa.CheckConstraint(
            "status IN ('approved', 'rejected', 'revoked')",
            name="ck_gate_approval_status",
        ),
        sa.CheckConstraint(
            "attester_role IN ('owner', 'analyst', 'compliance', 'readonly')",
            name="ck_gate_approval_role",
        ),
        sa.CheckConstraint(
            "auth_method IN ('api_key', 'oidc_bearer')",
            name="ck_gate_approval_auth_method",
        ),
        sa.CheckConstraint(
            "target_barrier_group IS NULL OR target_barrier_group = barrier_group",
            name="ck_gate_approval_target_barrier",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > attested_at",
            name="ck_gate_approval_expiry",
        ),
        sa.CheckConstraint(
            "status != 'revoked' OR expires_at IS NULL",
            name="ck_gate_approval_revoked_expiry",
        ),
        sa.CheckConstraint(
            "statement_encrypted IS NULL OR "
            "statement_encrypted LIKE 'lians-sealed:v1:%'",
            name="ck_gate_approval_statement_sealed",
        ),
        sa.CheckConstraint(
            "(sequence = 1 AND supersedes_id IS NULL AND prior_attestation_hash IS NULL) OR "
            "(sequence > 1 AND supersedes_id IS NOT NULL AND prior_attestation_hash IS NOT NULL)",
            name="ck_gate_approval_chain_shape",
        ),
    )
    for name, columns in (
        ("ix_gate_approval_attestations_namespace", ["namespace"]),
        ("ix_gate_approval_attestations_barrier_group", ["barrier_group"]),
        ("ix_gate_approval_attestations_series_key", ["series_key"]),
        (
            "ix_gate_approval_attestations_approval_principal_id",
            ["approval_principal_id"],
        ),
        ("ix_gate_approval_attestations_attester_role", ["attester_role"]),
        ("ix_gate_approval_attestations_status", ["status"]),
        ("ix_gate_approval_attestations_action", ["action"]),
        ("ix_gate_approval_attestations_decision_id", ["decision_id"]),
        ("ix_gate_approval_attestations_change_event_id", ["change_event_id"]),
        ("ix_gate_approval_attestations_policy_set_id", ["policy_set_id"]),
        ("ix_gate_approval_attestations_receipt_hash", ["receipt_hash"]),
        ("ix_gate_approval_attestations_context_hash", ["context_hash"]),
        ("ix_gate_approval_attestations_expires_at", ["expires_at"]),
        ("ix_gate_approval_attestations_attestation_hash", ["attestation_hash"]),
        ("ix_gate_approval_attestations_attested_at", ["attested_at"]),
        (
            "ix_gate_approval_ns_context_time",
            ["namespace", "context_hash", "attested_at"],
        ),
    ):
        op.create_index(name, "gate_approval_attestations", columns)

    op.create_table(
        "decision_review_events",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column(
            "decision_id",
            _uuid(),
            sa.ForeignKey("decision_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reviewer_principal_id", sa.String(length=512), nullable=False),
        sa.Column("reviewer_principal_type", sa.String(length=32), nullable=True),
        sa.Column("reviewer_role", sa.String(length=100), nullable=True),
        sa.Column("auth_method", sa.String(length=32), nullable=False),
        sa.Column("credential_id", sa.String(length=255), nullable=True),
        sa.Column("note_encrypted", sa.Text(), nullable=True),
        sa.Column("note_hash", sa.String(length=64), nullable=True),
        sa.Column("prior_event_hash", sa.String(length=64), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "namespace", "decision_id", "sequence", name="uq_decision_review_sequence"
        ),
        sa.UniqueConstraint(
            "namespace",
            "decision_id",
            "prior_event_hash",
            name="uq_decision_review_prior_hash",
        ),
        sa.UniqueConstraint("event_hash", name="uq_decision_review_event_hash"),
        sa.CheckConstraint("sequence > 0", name="ck_decision_review_sequence"),
        sa.CheckConstraint(
            "status IN ('requested', 'affirmed', 'overturned', 'withdrawn')",
            name="ck_decision_review_status",
        ),
        sa.CheckConstraint(
            "reviewer_role IS NULL OR "
            "reviewer_role IN ('owner', 'analyst', 'compliance', 'readonly')",
            name="ck_decision_review_role",
        ),
        sa.CheckConstraint(
            "auth_method IN ('api_key', 'oidc_bearer')",
            name="ck_decision_review_auth_method",
        ),
        sa.CheckConstraint(
            "note_encrypted IS NULL OR note_encrypted LIKE 'lians-sealed:v1:%'",
            name="ck_decision_review_note_sealed",
        ),
        sa.CheckConstraint(
            "(sequence = 1 AND prior_event_hash IS NULL) OR "
            "(sequence > 1 AND prior_event_hash IS NOT NULL)",
            name="ck_decision_review_chain_shape",
        ),
    )
    for name, columns in (
        ("ix_decision_review_events_namespace", ["namespace"]),
        ("ix_decision_review_events_barrier_group", ["barrier_group"]),
        ("ix_decision_review_events_decision_id", ["decision_id"]),
        ("ix_decision_review_events_status", ["status"]),
        ("ix_decision_review_events_event_hash", ["event_hash"]),
        ("ix_decision_review_events_reviewed_at", ["reviewed_at"]),
        (
            "ix_decision_review_ns_decision_time",
            ["namespace", "decision_id", "reviewed_at"],
        ),
    ):
        op.create_index(name, "decision_review_events", columns)

    if op.get_bind().dialect.name == "postgresql":
        _install_postgres_guards()
    elif op.get_bind().dialect.name == "sqlite":
        _install_sqlite_guards()


def _install_postgres_guards() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY rls_{table}_namespace ON {table}
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
            f"""CREATE POLICY barrier_isolation ON {table} AS RESTRICTIVE
            USING (
                barrier_group IS NULL
                OR current_setting('agentmem.barrier_group', true) IS NULL
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )
            WITH CHECK (
                barrier_group IS NULL
                OR current_setting('agentmem.barrier_group', true) IS NULL
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )"""
        )

    op.execute(
        """CREATE FUNCTION lians_attestation_reject_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql"""
    )
    for table in _TABLES:
        op.execute(
            f"""CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION lians_attestation_reject_mutation()"""
        )

    op.execute(
        """CREATE FUNCTION lians_gate_approval_validate_insert()
        RETURNS trigger AS $$
        DECLARE prior gate_approval_attestations%ROWTYPE;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    'lians:gate-approval:' || NEW.namespace || ':' || NEW.series_key,
                    0
                )
            );
            IF NOT EXISTS (
                SELECT 1 FROM gate_policy_sets policy
                WHERE policy.id = NEW.policy_set_id
                  AND policy.namespace = NEW.namespace
                  AND (policy.barrier_group IS NULL OR policy.barrier_group = NEW.barrier_group)
            ) THEN
                RAISE EXCEPTION 'approval policy is outside the attestation boundary';
            END IF;
            IF NEW.decision_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM decision_records decision
                WHERE decision.id = NEW.decision_id
                  AND decision.namespace = NEW.namespace
                  AND (decision.barrier_group IS NULL OR decision.barrier_group = NEW.barrier_group)
            ) THEN
                RAISE EXCEPTION 'approval decision is outside the attestation boundary';
            END IF;
            IF NEW.change_event_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM ledger_events event
                WHERE event.id = NEW.change_event_id
                  AND event.namespace = NEW.namespace
                  AND (event.barrier_group IS NULL OR event.barrier_group = NEW.barrier_group)
            ) THEN
                RAISE EXCEPTION 'approval change event is outside the attestation boundary';
            END IF;
            IF NEW.sequence = 1 THEN
                IF EXISTS (
                    SELECT 1 FROM gate_approval_attestations
                    WHERE namespace = NEW.namespace AND series_key = NEW.series_key
                ) THEN
                    RAISE EXCEPTION 'approval series already has a root event';
                END IF;
            ELSE
                SELECT * INTO prior FROM gate_approval_attestations
                WHERE id = NEW.supersedes_id FOR SHARE;
                IF NOT FOUND
                   OR prior.namespace IS DISTINCT FROM NEW.namespace
                   OR prior.barrier_group IS DISTINCT FROM NEW.barrier_group
                   OR prior.series_key IS DISTINCT FROM NEW.series_key
                   OR prior.sequence + 1 IS DISTINCT FROM NEW.sequence
                   OR prior.attestation_hash IS DISTINCT FROM NEW.prior_attestation_hash
                   OR prior.approval_principal_id IS DISTINCT FROM NEW.approval_principal_id
                   OR prior.action IS DISTINCT FROM NEW.action
                   OR prior.decision_id IS DISTINCT FROM NEW.decision_id
                   OR prior.change_event_id IS DISTINCT FROM NEW.change_event_id
                   OR prior.policy_set_id IS DISTINCT FROM NEW.policy_set_id
                   OR prior.policy_hash IS DISTINCT FROM NEW.policy_hash
                   OR prior.target_ref IS DISTINCT FROM NEW.target_ref
                   OR prior.target_barrier_group IS DISTINCT FROM NEW.target_barrier_group
                   OR prior.receipt_hash IS DISTINCT FROM NEW.receipt_hash
                   OR prior.context_hash IS DISTINCT FROM NEW.context_hash THEN
                    RAISE EXCEPTION 'invalid approval-attestation predecessor or boundary';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER trg_gate_approval_validate_insert
        BEFORE INSERT ON gate_approval_attestations
        FOR EACH ROW EXECUTE FUNCTION lians_gate_approval_validate_insert()"""
    )

    op.execute(
        """CREATE FUNCTION lians_decision_review_validate_insert()
        RETURNS trigger AS $$
        DECLARE prior decision_review_events%ROWTYPE;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    'lians:decision-review:' || NEW.namespace || ':' || NEW.decision_id,
                    0
                )
            );
            IF NOT EXISTS (
                SELECT 1 FROM decision_records decision
                WHERE decision.id = NEW.decision_id
                  AND decision.namespace = NEW.namespace
                  AND decision.barrier_group IS NOT DISTINCT FROM NEW.barrier_group
            ) THEN
                RAISE EXCEPTION 'decision review is outside the decision barrier';
            END IF;
            IF NEW.sequence = 1 THEN
                IF EXISTS (
                    SELECT 1 FROM decision_review_events
                    WHERE namespace = NEW.namespace AND decision_id = NEW.decision_id
                ) THEN
                    RAISE EXCEPTION 'decision review chain already has a root event';
                END IF;
            ELSE
                SELECT * INTO prior FROM decision_review_events
                WHERE namespace = NEW.namespace
                  AND decision_id = NEW.decision_id
                  AND event_hash = NEW.prior_event_hash
                FOR SHARE;
                IF NOT FOUND
                   OR prior.barrier_group IS DISTINCT FROM NEW.barrier_group
                   OR prior.sequence + 1 IS DISTINCT FROM NEW.sequence THEN
                    RAISE EXCEPTION 'invalid decision-review predecessor';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER trg_decision_review_validate_insert
        BEFORE INSERT ON decision_review_events
        FOR EACH ROW EXECUTE FUNCTION lians_decision_review_validate_insert()"""
    )

    op.execute(
        """CREATE FUNCTION lians_decision_review_projection_guard()
        RETURNS trigger AS $$
        DECLARE latest decision_review_events%ROWTYPE;
        BEGIN
            SELECT * INTO latest FROM decision_review_events
            WHERE namespace = NEW.namespace AND decision_id = NEW.id
            ORDER BY sequence DESC LIMIT 1;
            IF NOT FOUND
               OR NEW.human_review_status IS DISTINCT FROM latest.status
               OR NEW.human_reviewer IS DISTINCT FROM latest.reviewer_principal_id
               OR NEW.human_reviewed_at IS DISTINCT FROM latest.reviewed_at THEN
                RAISE EXCEPTION 'human review fields are a projection of immutable review events';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER trg_decision_review_projection_guard
        BEFORE UPDATE OF human_review_status, human_reviewer, human_reviewed_at
        ON decision_records
        FOR EACH ROW EXECUTE FUNCTION lians_decision_review_projection_guard()"""
    )


def _install_sqlite_guards() -> None:
    for table in _TABLES:
        for operation in ("UPDATE", "DELETE"):
            suffix = operation.lower()
            op.execute(
                f"""CREATE TRIGGER trg_{table}_reject_{suffix}
                BEFORE {operation} ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{table} is append-only');
                END"""
            )

    op.execute(
        """CREATE TRIGGER trg_gate_approval_validate_insert
        BEFORE INSERT ON gate_approval_attestations
        WHEN (
            NOT EXISTS (
                SELECT 1 FROM gate_policy_sets policy
                WHERE policy.id = NEW.policy_set_id
                  AND policy.namespace = NEW.namespace
                  AND (policy.barrier_group IS NULL OR policy.barrier_group = NEW.barrier_group)
            )
        ) OR (
            NEW.decision_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM decision_records decision
                WHERE decision.id = NEW.decision_id
                  AND decision.namespace = NEW.namespace
                  AND (decision.barrier_group IS NULL OR decision.barrier_group = NEW.barrier_group)
            )
        ) OR (
            NEW.change_event_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM ledger_events event
                WHERE event.id = NEW.change_event_id
                  AND event.namespace = NEW.namespace
                  AND (event.barrier_group IS NULL OR event.barrier_group = NEW.barrier_group)
            )
        ) OR (
            NEW.sequence = 1 AND EXISTS (
                SELECT 1 FROM gate_approval_attestations
                WHERE namespace = NEW.namespace AND series_key = NEW.series_key
            )
        ) OR (
            NEW.sequence > 1 AND NOT EXISTS (
                SELECT 1 FROM gate_approval_attestations prior
                WHERE prior.id = NEW.supersedes_id
                  AND prior.namespace = NEW.namespace
                  AND prior.barrier_group IS NEW.barrier_group
                  AND prior.series_key = NEW.series_key
                  AND prior.sequence + 1 = NEW.sequence
                  AND prior.attestation_hash = NEW.prior_attestation_hash
                  AND prior.approval_principal_id = NEW.approval_principal_id
                  AND prior.context_hash = NEW.context_hash
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid approval-attestation predecessor');
        END"""
    )
    op.execute(
        """CREATE TRIGGER trg_decision_review_validate_insert
        BEFORE INSERT ON decision_review_events
        WHEN (
            NOT EXISTS (
                SELECT 1 FROM decision_records decision
                WHERE decision.id = NEW.decision_id
                  AND decision.namespace = NEW.namespace
                  AND decision.barrier_group IS NEW.barrier_group
            )
        ) OR (
            NEW.sequence = 1 AND EXISTS (
                SELECT 1 FROM decision_review_events
                WHERE namespace = NEW.namespace AND decision_id = NEW.decision_id
            )
        ) OR (
            NEW.sequence > 1 AND NOT EXISTS (
                SELECT 1 FROM decision_review_events prior
                WHERE prior.namespace = NEW.namespace
                  AND prior.decision_id = NEW.decision_id
                  AND prior.barrier_group IS NEW.barrier_group
                  AND prior.sequence + 1 = NEW.sequence
                  AND prior.event_hash = NEW.prior_event_hash
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid decision-review predecessor');
        END"""
    )
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


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_decision_review_projection_guard ON decision_records"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_decision_review_validate_insert ON decision_review_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_gate_approval_validate_insert "
            "ON gate_approval_attestations"
        )
        for table in _TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
            op.execute(f"DROP POLICY IF EXISTS barrier_isolation ON {table}")
            op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute("DROP FUNCTION IF EXISTS lians_decision_review_projection_guard()")
        op.execute("DROP FUNCTION IF EXISTS lians_decision_review_validate_insert()")
        op.execute("DROP FUNCTION IF EXISTS lians_gate_approval_validate_insert()")
        op.execute("DROP FUNCTION IF EXISTS lians_attestation_reject_mutation()")
        op.drop_constraint("ck_api_key_barrier_group", "api_keys", type_="check")
        op.drop_constraint("ck_api_key_role", "api_keys", type_="check")
        op.drop_constraint(
            "ck_trusted_receipt_key_safe_id", "trusted_receipt_keys", type_="check"
        )
        op.alter_column(
            "gate_decision_records",
            "principal_id",
            existing_type=sa.String(length=512),
            type_=sa.String(length=255),
            existing_nullable=False,
        )
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_decision_review_projection_guard")
        op.execute("DROP TRIGGER IF EXISTS trg_decision_review_validate_insert")
        op.execute("DROP TRIGGER IF EXISTS trg_gate_approval_validate_insert")
        for table in _TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_update")
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_delete")

    op.drop_table("decision_review_events")
    op.drop_table("gate_approval_attestations")
