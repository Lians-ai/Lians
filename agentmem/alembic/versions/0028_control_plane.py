"""Add trust, Gate, investigation, and remediation control-plane foundations.

Revision ID: 0028_control_plane
Revises: 0027_universal_recorder
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028_control_plane"
down_revision = "0027_universal_recorder"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "receipt_issuers",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("issuer_uri", sa.String(2048), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_by", sa.String(255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("namespace", "name", name="uq_receipt_issuer_ns_name"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_receipt_issuer_status"),
    )
    op.create_index("ix_receipt_issuers_namespace", "receipt_issuers", ["namespace"])
    op.create_index("ix_receipt_issuers_barrier_group", "receipt_issuers", ["barrier_group"])
    op.create_index("ix_receipt_issuer_ns_status", "receipt_issuers", ["namespace", "status"])

    op.create_table(
        "trusted_receipt_keys",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column(
            "issuer_id",
            _uuid(),
            sa.ForeignKey("receipt_issuers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("key_id", sa.String(255), nullable=False),
        sa.Column("algorithm", sa.String(32), nullable=False, server_default="ed25519"),
        # Public verification material only. There is intentionally no private-key column.
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("public_key_format", sa.String(32), nullable=False, server_default="raw-base64"),
        sa.Column("fingerprint_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_by", sa.String(255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_from_key_id", sa.String(255), nullable=True),
        sa.Column("replaced_by_key_id", sa.String(255), nullable=True),
        sa.Column("rotation_reason", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("namespace", "key_id", name="uq_trusted_receipt_key_ns_key_id"),
        sa.CheckConstraint("algorithm = 'ed25519'", name="ck_trusted_receipt_key_algorithm"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_trusted_receipt_key_status"),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_trusted_receipt_key_window",
        ),
    )
    for name, columns in (
        ("ix_trusted_receipt_keys_namespace", ["namespace"]),
        ("ix_trusted_receipt_keys_barrier_group", ["barrier_group"]),
        ("ix_trusted_receipt_keys_issuer_id", ["issuer_id"]),
        ("ix_trusted_receipt_keys_fingerprint_sha256", ["fingerprint_sha256"]),
        ("ix_trusted_key_ns_status", ["namespace", "status"]),
        ("ix_trusted_key_issuer_status", ["issuer_id", "status"]),
    ):
        op.create_index(name, "trusted_receipt_keys", columns)

    op.create_table(
        "gate_policy_sets",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("default_disposition", sa.String(16), nullable=False, server_default="allow"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("activated_by", sa.String(255), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("namespace", "name", "version", name="uq_gate_policy_ns_name_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')", name="ck_gate_policy_status"
        ),
        sa.CheckConstraint(
            "default_disposition IN ('allow', 'deny', 'review')",
            name="ck_gate_policy_default_disposition",
        ),
    )
    for name, columns in (
        ("ix_gate_policy_sets_namespace", ["namespace"]),
        ("ix_gate_policy_sets_barrier_group", ["barrier_group"]),
        ("ix_gate_policy_sets_policy_hash", ["policy_hash"]),
        ("ix_gate_policy_ns_name_status", ["namespace", "name", "status"]),
    ):
        op.create_index(name, "gate_policy_sets", columns)

    op.create_table(
        "gate_policy_rules",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column(
            "policy_set_id",
            _uuid(),
            sa.ForeignKey("gate_policy_sets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("action_on_failure", sa.String(16), nullable=False, server_default="deny"),
        sa.Column("applies_to_decision_types", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("applies_to_risk_levels", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("required_receipt_grade", sa.String(1), nullable=True),
        sa.Column(
            "require_trusted_issuer", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "require_sources_current", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "require_policy_attached", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("required_principal_scopes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("minimum_approval_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("required_approval_roles", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "require_information_barrier_match",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "block_untrusted_content", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("max_untrusted_content_score", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("policy_set_id", "name", name="uq_gate_rule_policy_name"),
        sa.CheckConstraint(
            "action_on_failure IN ('deny', 'review')", name="ck_gate_rule_failure_action"
        ),
        sa.CheckConstraint("minimum_approval_count >= 0", name="ck_gate_rule_approval_count"),
        sa.CheckConstraint(
            "max_untrusted_content_score IS NULL OR "
            "(max_untrusted_content_score >= 0 AND max_untrusted_content_score <= 100)",
            name="ck_gate_rule_untrusted_score",
        ),
    )
    for name, columns in (
        ("ix_gate_policy_rules_namespace", ["namespace"]),
        ("ix_gate_policy_rules_barrier_group", ["barrier_group"]),
        ("ix_gate_policy_rules_policy_set_id", ["policy_set_id"]),
        ("ix_gate_rule_policy_priority", ["policy_set_id", "priority"]),
    ):
        op.create_index(name, "gate_policy_rules", columns)

    op.create_table(
        "gate_decision_records",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("policy_set_id", _uuid(), nullable=False),
        sa.Column("policy_name", sa.String(255), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("principal_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("decision_id", _uuid(), nullable=True),
        sa.Column("change_event_id", _uuid(), nullable=True),
        sa.Column("receipt_hash", sa.String(64), nullable=True),
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("applied_rules", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("input_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("evaluation_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "evaluated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "disposition IN ('allow', 'deny', 'review')", name="ck_gate_decision_disposition"
        ),
    )
    for name, columns in (
        ("ix_gate_decision_records_namespace", ["namespace"]),
        ("ix_gate_decision_records_barrier_group", ["barrier_group"]),
        ("ix_gate_decision_records_policy_set_id", ["policy_set_id"]),
        ("ix_gate_decision_records_principal_id", ["principal_id"]),
        ("ix_gate_decision_records_action", ["action"]),
        ("ix_gate_decision_records_decision_id", ["decision_id"]),
        ("ix_gate_decision_records_change_event_id", ["change_event_id"]),
        ("ix_gate_decision_records_receipt_hash", ["receipt_hash"]),
        ("ix_gate_decision_records_disposition", ["disposition"]),
        ("ix_gate_decision_records_request_hash", ["request_hash"]),
        ("ix_gate_decision_records_evaluation_hash", ["evaluation_hash"]),
        ("ix_gate_decision_records_evaluated_at", ["evaluated_at"]),
        ("ix_gate_decision_ns_time", ["namespace", "evaluated_at"]),
        ("ix_gate_decision_ns_disposition", ["namespace", "disposition"]),
    ):
        op.create_index(name, "gate_decision_records", columns)

    op.create_table(
        "investigation_cases",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("owner_principal", sa.String(255), nullable=True),
        sa.Column("decision_id", _uuid(), nullable=True),
        sa.Column("change_event_id", _uuid(), nullable=True),
        sa.Column("gate_decision_id", _uuid(), nullable=True),
        sa.Column("opened_by", sa.String(255), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_summary", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_investigation_case_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_review', 'remediating', 'resolved', 'closed')",
            name="ck_investigation_case_status",
        ),
    )
    for name, columns in (
        ("ix_investigation_cases_namespace", ["namespace"]),
        ("ix_investigation_cases_barrier_group", ["barrier_group"]),
        ("ix_investigation_cases_owner_principal", ["owner_principal"]),
        ("ix_investigation_cases_decision_id", ["decision_id"]),
        ("ix_investigation_cases_change_event_id", ["change_event_id"]),
        ("ix_investigation_cases_gate_decision_id", ["gate_decision_id"]),
        ("ix_investigation_cases_opened_at", ["opened_at"]),
        ("ix_investigation_case_ns_status", ["namespace", "status"]),
        ("ix_investigation_case_ns_owner", ["namespace", "owner_principal"]),
    ):
        op.create_index(name, "investigation_cases", columns)

    op.create_table(
        "remediation_tasks",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column(
            "case_id",
            _uuid(),
            sa.ForeignKey("investigation_cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("owner_principal", sa.String(255), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_id", _uuid(), nullable=True),
        sa.Column("change_event_id", _uuid(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'blocked', 'cancelled', 'closed')",
            name="ck_remediation_task_status",
        ),
    )
    for name, columns in (
        ("ix_remediation_tasks_namespace", ["namespace"]),
        ("ix_remediation_tasks_barrier_group", ["barrier_group"]),
        ("ix_remediation_tasks_case_id", ["case_id"]),
        ("ix_remediation_tasks_owner_principal", ["owner_principal"]),
        ("ix_remediation_tasks_due_at", ["due_at"]),
        ("ix_remediation_tasks_decision_id", ["decision_id"]),
        ("ix_remediation_tasks_change_event_id", ["change_event_id"]),
        ("ix_remediation_task_ns_status", ["namespace", "status"]),
        ("ix_remediation_task_case_status", ["case_id", "status"]),
    ):
        op.create_index(name, "remediation_tasks", columns)

    op.create_table(
        "control_closure_attestations",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("resource_type", sa.String(16), nullable=False),
        sa.Column("resource_id", _uuid(), nullable=False),
        sa.Column("attested_by", sa.String(255), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("decision_id", _uuid(), nullable=True),
        sa.Column("change_event_id", _uuid(), nullable=True),
        sa.Column("attestation_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "attested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "namespace", "resource_type", "resource_id", name="uq_control_closure_resource"
        ),
        sa.CheckConstraint(
            "resource_type IN ('case', 'task')", name="ck_control_attestation_resource_type"
        ),
    )
    for name, columns in (
        ("ix_control_closure_attestations_namespace", ["namespace"]),
        ("ix_control_closure_attestations_barrier_group", ["barrier_group"]),
        ("ix_control_closure_attestations_resource_id", ["resource_id"]),
        ("ix_control_closure_attestations_decision_id", ["decision_id"]),
        ("ix_control_closure_attestations_change_event_id", ["change_event_id"]),
        ("ix_control_closure_attestations_attestation_hash", ["attestation_hash"]),
        ("ix_control_closure_attestations_attested_at", ["attested_at"]),
        ("ix_control_attestation_ns_time", ["namespace", "attested_at"]),
    ):
        op.create_index(name, "control_closure_attestations", columns)

    if op.get_bind().dialect.name != "postgresql":
        return

    control_tables = (
        "receipt_issuers",
        "trusted_receipt_keys",
        "gate_policy_sets",
        "gate_policy_rules",
        "gate_decision_records",
        "investigation_cases",
        "remediation_tasks",
        "control_closure_attestations",
    )
    for table in control_tables:
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
            )"""
        )

    # Gate decisions and closure attestations are evidence, never mutable state.
    op.execute(
        """CREATE FUNCTION lians_control_reject_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql"""
    )
    for table in ("gate_decision_records", "control_closure_attestations", "gate_policy_rules"):
        op.execute(
            f"""CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION lians_control_reject_mutation()"""
        )
    for table in (
        "receipt_issuers",
        "trusted_receipt_keys",
        "gate_policy_sets",
        "investigation_cases",
        "remediation_tasks",
    ):
        op.execute(
            f"""CREATE TRIGGER trg_{table}_no_delete
            BEFORE DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION lians_control_reject_mutation()"""
        )

    # A policy name/barrier has at most one active version, including NULL barriers.
    op.execute(
        """CREATE UNIQUE INDEX uq_gate_policy_one_active
        ON gate_policy_sets (namespace, name, COALESCE(barrier_group, ''))
        WHERE status = 'active'"""
    )

    # A policy version may change lifecycle only; its hash-covered definition is immutable.
    op.execute(
        """CREATE FUNCTION lians_control_guard_policy_definition()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.namespace IS DISTINCT FROM NEW.namespace
               OR OLD.barrier_group IS DISTINCT FROM NEW.barrier_group
               OR OLD.name IS DISTINCT FROM NEW.name
               OR OLD.version IS DISTINCT FROM NEW.version
               OR OLD.description IS DISTINCT FROM NEW.description
               OR OLD.default_disposition IS DISTINCT FROM NEW.default_disposition
               OR OLD.policy_hash IS DISTINCT FROM NEW.policy_hash
               OR OLD.metadata IS DISTINCT FROM NEW.metadata
               OR OLD.created_by IS DISTINCT FROM NEW.created_by
               OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
                RAISE EXCEPTION 'Gate policy definitions are immutable; create a new version';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER trg_gate_policy_definition_immutable
        BEFORE UPDATE ON gate_policy_sets
        FOR EACH ROW EXECUTE FUNCTION lians_control_guard_policy_definition()"""
    )

    # Public verification material cannot be replaced in place; rotation creates a new row.
    op.execute(
        """CREATE FUNCTION lians_control_guard_public_key()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.namespace IS DISTINCT FROM NEW.namespace
               OR OLD.barrier_group IS DISTINCT FROM NEW.barrier_group
               OR OLD.issuer_id IS DISTINCT FROM NEW.issuer_id
               OR OLD.key_id IS DISTINCT FROM NEW.key_id
               OR OLD.algorithm IS DISTINCT FROM NEW.algorithm
               OR OLD.public_key IS DISTINCT FROM NEW.public_key
               OR OLD.public_key_format IS DISTINCT FROM NEW.public_key_format
               OR OLD.fingerprint_sha256 IS DISTINCT FROM NEW.fingerprint_sha256
               OR OLD.valid_from IS DISTINCT FROM NEW.valid_from
               OR OLD.valid_until IS DISTINCT FROM NEW.valid_until
               OR OLD.created_by IS DISTINCT FROM NEW.created_by
               OR OLD.created_at IS DISTINCT FROM NEW.created_at
               OR OLD.rotated_from_key_id IS DISTINCT FROM NEW.rotated_from_key_id
               OR OLD.metadata IS DISTINCT FROM NEW.metadata THEN
                RAISE EXCEPTION 'Trusted public-key material is immutable; rotate the key';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER trg_trusted_public_key_immutable
        BEFORE UPDATE ON trusted_receipt_keys
        FOR EACH ROW EXECUTE FUNCTION lians_control_guard_public_key()"""
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_trusted_public_key_immutable ON trusted_receipt_keys"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_gate_policy_definition_immutable ON gate_policy_sets"
        )
        for table in ("gate_decision_records", "control_closure_attestations", "gate_policy_rules"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
        for table in (
            "receipt_issuers",
            "trusted_receipt_keys",
            "gate_policy_sets",
            "investigation_cases",
            "remediation_tasks",
        ):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_delete ON {table}")
        op.execute("DROP INDEX IF EXISTS uq_gate_policy_one_active")
        op.execute("DROP FUNCTION IF EXISTS lians_control_guard_public_key()")
        op.execute("DROP FUNCTION IF EXISTS lians_control_guard_policy_definition()")
        op.execute("DROP FUNCTION IF EXISTS lians_control_reject_mutation()")

        control_tables = (
            "receipt_issuers",
            "trusted_receipt_keys",
            "gate_policy_sets",
            "gate_policy_rules",
            "gate_decision_records",
            "investigation_cases",
            "remediation_tasks",
            "control_closure_attestations",
        )
        for table in control_tables:
            op.execute(f"DROP POLICY IF EXISTS barrier_isolation ON {table}")
            op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    op.drop_table("control_closure_attestations")
    op.drop_table("remediation_tasks")
    op.drop_table("investigation_cases")
    op.drop_table("gate_decision_records")
    op.drop_table("gate_policy_rules")
    op.drop_table("gate_policy_sets")
    op.drop_table("trusted_receipt_keys")
    op.drop_table("receipt_issuers")
