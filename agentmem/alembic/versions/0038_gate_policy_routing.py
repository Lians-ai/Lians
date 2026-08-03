"""Make Gate routing authoritative and add approval-assurance policy fields.

Revision ID: 0038_gate_policy_routing
Revises: 0037_master_key_write_fence
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_gate_policy_routing"
down_revision = "0037_master_key_write_fence"
branch_labels = None
depends_on = None


def _install_policy_definition_guard(*, include_selectors: bool) -> None:
    selector_guards = ""
    if include_selectors:
        selector_guards = """
               OR OLD.protected_actions IS DISTINCT FROM NEW.protected_actions
               OR OLD.target_ref_prefixes IS DISTINCT FROM NEW.target_ref_prefixes"""
    op.execute(
        f"""CREATE OR REPLACE FUNCTION lians_control_guard_policy_definition()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.namespace IS DISTINCT FROM NEW.namespace
               OR OLD.barrier_group IS DISTINCT FROM NEW.barrier_group
               OR OLD.name IS DISTINCT FROM NEW.name
               OR OLD.version IS DISTINCT FROM NEW.version
               OR OLD.description IS DISTINCT FROM NEW.description
               OR OLD.default_disposition IS DISTINCT FROM NEW.default_disposition
               {selector_guards}
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


def _install_active_selector_guard() -> None:
    # This trigger is deliberately SECURITY DEFINER: selector ambiguity must be
    # checked across every row in the namespace/barrier even when RLS limits the
    # activating session. The fixed public qualification and search path prevent
    # object-shadowing attacks.
    op.execute(
        """CREATE OR REPLACE FUNCTION lians_gate_guard_active_selector()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NEW.status = 'active' AND OLD.status IS DISTINCT FROM 'active' THEN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(
                        'lians:gate-policy:' || NEW.namespace || ':' ||
                        COALESCE(NEW.barrier_group, '<shared>'),
                        0
                    )
                );
                IF jsonb_typeof(NEW.protected_actions::jsonb) <> 'array'
                   OR jsonb_array_length(NEW.protected_actions::jsonb) = 0
                   OR jsonb_typeof(NEW.target_ref_prefixes::jsonb) <> 'array'
                   OR jsonb_array_length(NEW.target_ref_prefixes::jsonb) = 0 THEN
                    RAISE EXCEPTION
                        'Active Gate policies require protected actions and target prefixes';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM public.gate_policy_sets AS other
                    WHERE other.id <> NEW.id
                      AND other.namespace = NEW.namespace
                      AND other.barrier_group IS NOT DISTINCT FROM NEW.barrier_group
                      AND other.status = 'active'
                      AND EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements_text(other.protected_actions::jsonb) oa(value)
                          JOIN jsonb_array_elements_text(NEW.protected_actions::jsonb) na(value)
                            ON oa.value = na.value
                      )
                      AND EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements_text(other.target_ref_prefixes::jsonb) op(value)
                          CROSS JOIN jsonb_array_elements_text(NEW.target_ref_prefixes::jsonb) np(value)
                          WHERE left(op.value, length(np.value)) = np.value
                             OR left(np.value, length(op.value)) = op.value
                      )
                ) THEN
                    RAISE EXCEPTION
                        'Active Gate action/target selectors overlap another policy';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION lians_gate_guard_active_selector() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER trg_gate_policy_active_selector
        BEFORE INSERT OR UPDATE OF status ON gate_policy_sets
        FOR EACH ROW EXECUTE FUNCTION lians_gate_guard_active_selector()"""
    )


def upgrade() -> None:
    op.add_column(
        "gate_policy_sets",
        sa.Column("protected_actions", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "gate_policy_sets",
        sa.Column("target_ref_prefixes", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column(
        "gate_policy_sets",
        "default_disposition",
        existing_type=sa.String(16),
        existing_nullable=False,
        server_default="deny",
    )
    op.create_index(
        "ix_gate_policy_ns_status_barrier",
        "gate_policy_sets",
        ["namespace", "status", "barrier_group"],
    )

    op.add_column(
        "gate_policy_rules",
        sa.Column(
            "allowed_approval_principal_types",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "gate_policy_rules",
        sa.Column("maximum_approval_age_seconds", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_gate_rule_approval_age",
        "gate_policy_rules",
        "maximum_approval_age_seconds IS NULL OR "
        "maximum_approval_age_seconds BETWEEN 60 AND 31536000",
    )

    if op.get_bind().dialect.name == "postgresql":
        _install_policy_definition_guard(include_selectors=True)
        _install_active_selector_guard()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_gate_policy_active_selector ON gate_policy_sets"
        )
        op.execute("DROP FUNCTION IF EXISTS lians_gate_guard_active_selector()")
        _install_policy_definition_guard(include_selectors=False)

    op.drop_constraint(
        "ck_gate_rule_approval_age", "gate_policy_rules", type_="check"
    )
    op.drop_column("gate_policy_rules", "maximum_approval_age_seconds")
    op.drop_column("gate_policy_rules", "allowed_approval_principal_types")
    op.drop_index("ix_gate_policy_ns_status_barrier", table_name="gate_policy_sets")
    op.alter_column(
        "gate_policy_sets",
        "default_disposition",
        existing_type=sa.String(16),
        existing_nullable=False,
        server_default="allow",
    )
    op.drop_column("gate_policy_sets", "target_ref_prefixes")
    op.drop_column("gate_policy_sets", "protected_actions")
