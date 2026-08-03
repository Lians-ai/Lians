"""Persistent database write fence for master-key rotation.

Revision ID: 0037_master_key_write_fence
Revises: 0036_master_key_rotation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_master_key_write_fence"
down_revision = "0036_master_key_rotation"
branch_labels = None
depends_on = None


FENCE_TRIGGERS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "subject_keys",
        "trg_subject_keys_master_key_fence",
        "lians_master_key_fence_subject",
        (),
    ),
    (
        "pending_admissions",
        "trg_pending_admissions_master_key_fence",
        "lians_master_key_fence_sealed",
        ("content", "required"),
    ),
    (
        "webhook_endpoints",
        "trg_webhook_endpoints_master_key_fence",
        "lians_master_key_fence_sealed",
        ("secret", "required"),
    ),
    (
        "gate_approval_attestations",
        "trg_gate_approval_attestations_master_key_fence",
        "lians_master_key_fence_sealed",
        ("statement_encrypted", "nullable"),
    ),
    (
        "decision_review_events",
        "trg_decision_review_events_master_key_fence",
        "lians_master_key_fence_sealed",
        ("note_encrypted", "nullable"),
    ),
    (
        "integration_destinations",
        "trg_integration_destinations_master_key_fence",
        "lians_master_key_fence_sealed",
        ("secret_config_encrypted", "required"),
    ),
    (
        "integration_outbox_events",
        "trg_integration_outbox_events_master_key_fence",
        "lians_master_key_fence_sealed",
        ("payload_encrypted", "required"),
    ),
    (
        "control_closure_attestations",
        "trg_control_closure_attestations_master_key_fence",
        "lians_master_key_fence_closure",
        (),
    ),
)


def _qualified_schema(bind: sa.engine.Connection) -> tuple[str, str]:
    schema = str(bind.execute(sa.text("SELECT current_schema()")).scalar_one())
    return schema, bind.dialect.identifier_preparer.quote(schema)


def _create_postgresql_functions_and_triggers(bind: sa.engine.Connection) -> None:
    _, schema = _qualified_schema(bind)
    state_table = f"{schema}.master_key_write_fence_state"

    bind.exec_driver_sql(
        f"""
        CREATE FUNCTION {schema}.lians_master_key_fence_allows(p_key_id text)
        RETURNS boolean
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, {schema}
        AS $lians$
        DECLARE
            v_current_key_id text;
            v_previous_key_id text;
        BEGIN
            -- VOLATILE plus FOR SHARE ensures a statement released by the
            -- operator's table lock follows the latest committed fence row.
            SELECT current_key_id, previous_key_id
            INTO v_current_key_id, v_previous_key_id
            FROM {state_table}
            WHERE singleton_id = 1
            FOR SHARE;
            IF NOT FOUND THEN
                RETURN TRUE;
            END IF;
            RETURN p_key_id = v_current_key_id OR (
                v_previous_key_id IS NOT NULL AND p_key_id = v_previous_key_id
            );
        END;
        $lians$
        """
    )
    bind.exec_driver_sql(
        f"""
        CREATE FUNCTION {schema}.lians_master_key_fence_check_sealed(
            p_value text,
            p_nullable boolean
        )
        RETURNS boolean
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, {schema}
        AS $lians$
        DECLARE
            v_key_id text;
            v_payload text;
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM {state_table} WHERE singleton_id = 1
            ) THEN
                RETURN TRUE;
            END IF;
            IF p_value IS NULL THEN
                RETURN p_nullable;
            END IF;
            IF p_value !~ '^lians-sealed:v2:[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}:[A-Za-z0-9_-]+={{0,2}}$' THEN
                RETURN FALSE;
            END IF;
            v_key_id := split_part(p_value, ':', 3);
            v_payload := split_part(p_value, ':', 4);
            IF length(v_payload) < 40 OR mod(length(v_payload), 4) <> 0 THEN
                RETURN FALSE;
            END IF;
            RETURN {schema}.lians_master_key_fence_allows(v_key_id);
        END;
        $lians$
        """
    )
    bind.exec_driver_sql(
        f"""
        CREATE FUNCTION {schema}.lians_master_key_fence_sealed()
        RETURNS trigger
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, {schema}
        AS $lians$
        DECLARE
            v_value text;
        BEGIN
            IF TG_NARGS <> 2 OR TG_ARGV[1] NOT IN ('required', 'nullable') THEN
                RAISE EXCEPTION 'master-key sealed fence trigger is misconfigured'
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            v_value := to_jsonb(NEW) ->> TG_ARGV[0];
            IF NOT {schema}.lians_master_key_fence_check_sealed(
                v_value,
                TG_ARGV[1] = 'nullable'
            ) THEN
                RAISE EXCEPTION 'master-key write fence rejected sealed envelope'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $lians$
        """
    )
    bind.exec_driver_sql(
        f"""
        CREATE FUNCTION {schema}.lians_master_key_fence_subject()
        RETURNS trigger
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, {schema}
        AS $lians$
        DECLARE
            v_key_id_length integer;
            v_key_id text;
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM {state_table} WHERE singleton_id = 1
            ) THEN
                RETURN NEW;
            END IF;

            -- Crypto-shredded keys remain either NULL or a zeroed byte string.
            IF NEW.destroyed_at IS NOT NULL THEN
                IF NEW.enc_key IS NULL OR encode(NEW.enc_key, 'hex') ~ '^(00)*$' THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'master-key write fence rejected nonzero destroyed key'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.enc_key IS NULL
               OR octet_length(NEW.enc_key) < 75
               OR substring(NEW.enc_key FROM 1 FOR 13) <> decode('6c69616e732d64656b3a763200', 'hex') THEN
                RAISE EXCEPTION 'master-key write fence rejected subject-key envelope'
                    USING ERRCODE = 'check_violation';
            END IF;
            v_key_id_length := get_byte(NEW.enc_key, 13);
            IF v_key_id_length NOT BETWEEN 1 AND 64
               OR octet_length(NEW.enc_key) <> 74 + v_key_id_length THEN
                RAISE EXCEPTION 'master-key write fence rejected subject-key envelope'
                    USING ERRCODE = 'check_violation';
            END IF;
            BEGIN
                v_key_id := convert_from(
                    substring(NEW.enc_key FROM 15 FOR v_key_id_length),
                    'UTF8'
                );
            EXCEPTION
                WHEN character_not_in_repertoire OR untranslatable_character THEN
                    RAISE EXCEPTION 'master-key write fence rejected subject-key key identifier'
                        USING ERRCODE = 'check_violation';
            END;
            IF v_key_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}$'
               OR NOT {schema}.lians_master_key_fence_allows(v_key_id) THEN
                RAISE EXCEPTION 'master-key write fence rejected subject-key key identifier'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $lians$
        """
    )
    bind.exec_driver_sql(
        f"""
        CREATE FUNCTION {schema}.lians_master_key_fence_closure()
        RETURNS trigger
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, {schema}
        AS $lians$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM {state_table} WHERE singleton_id = 1
            ) THEN
                RETURN NEW;
            END IF;
            IF NEW.statement IS NOT NULL
               OR NEW.statement_encrypted IS NULL
               OR NOT {schema}.lians_master_key_fence_check_sealed(
                   NEW.statement_encrypted,
                   FALSE
               ) THEN
                RAISE EXCEPTION 'master-key write fence rejected closure statement storage'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $lians$
        """
    )

    function_signatures = (
        "lians_master_key_fence_allows(text)",
        "lians_master_key_fence_check_sealed(text, boolean)",
        "lians_master_key_fence_sealed()",
        "lians_master_key_fence_subject()",
        "lians_master_key_fence_closure()",
    )
    for signature in function_signatures:
        bind.exec_driver_sql(f"REVOKE ALL ON FUNCTION {schema}.{signature} FROM PUBLIC")

    for table, trigger, function, args in FENCE_TRIGGERS:
        arguments = ", ".join("'" + value.replace("'", "''") + "'" for value in args)
        bind.exec_driver_sql(
            f"CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE ON {schema}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {schema}.{function}({arguments})"
        )


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"
    current_id_check = (
        "current_key_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'"
        if postgres
        else "length(current_key_id) BETWEEN 1 AND 64"
    )
    previous_id_check = (
        "previous_key_id IS NULL OR ("
        + (
            "previous_key_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'"
            if postgres
            else "length(previous_key_id) BETWEEN 1 AND 64"
        )
        + " AND previous_key_id <> current_key_id)"
    )
    op.create_table(
        "master_key_write_fence_state",
        sa.Column(
            "singleton_id",
            sa.SmallInteger(),
            primary_key=True,
            nullable=False,
            server_default="1",
        ),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("current_key_id", sa.String(length=64), nullable=False),
        sa.Column("previous_key_id", sa.String(length=64), nullable=True),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column(
            "prepared_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("narrowed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "singleton_id = 1",
            name="ck_master_key_write_fence_singleton",
        ),
        sa.CheckConstraint(
            "phase IN ('prepared', 'narrowed')",
            name="ck_master_key_write_fence_phase",
        ),
        sa.CheckConstraint(
            current_id_check,
            name="ck_master_key_write_fence_current_id",
        ),
        sa.CheckConstraint(
            previous_id_check,
            name="ck_master_key_write_fence_previous_id",
        ),
        sa.CheckConstraint(
            "generation >= 1",
            name="ck_master_key_write_fence_generation",
        ),
        sa.CheckConstraint(
            "((phase = 'prepared' AND previous_key_id IS NOT NULL AND narrowed_at IS NULL) "
            "OR (phase = 'narrowed' AND previous_key_id IS NULL AND narrowed_at IS NOT NULL))",
            name="ck_master_key_write_fence_phase_storage",
        ),
    )
    if postgres:
        _create_postgresql_functions_and_triggers(bind)


def downgrade() -> None:
    bind = op.get_bind()
    active = int(
        bind.execute(
            sa.text("SELECT count(*) FROM master_key_write_fence_state")
        ).scalar_one()
    )
    if active:
        raise RuntimeError(
            "0037 downgrade refused: remove the active write fence only through an approved recovery procedure"
        )
    if bind.dialect.name == "postgresql":
        _, schema = _qualified_schema(bind)
        for table, trigger, _, _ in reversed(FENCE_TRIGGERS):
            bind.exec_driver_sql(f"DROP TRIGGER {trigger} ON {schema}.{table}")
        for signature in (
            "lians_master_key_fence_closure()",
            "lians_master_key_fence_subject()",
            "lians_master_key_fence_sealed()",
            "lians_master_key_fence_check_sealed(text, boolean)",
            "lians_master_key_fence_allows(text)",
        ):
            bind.exec_driver_sql(f"DROP FUNCTION {schema}.{signature}")
    op.drop_table("master_key_write_fence_state")
