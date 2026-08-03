"""Add mediated, single-use execution permits to the runtime Gate.

Revision ID: 0040_gate_execution_permits
Revises: 0039a_audit_append_contract
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040_gate_execution_permits"
down_revision = "0039a_audit_append_contract"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True)


_PERMIT_TABLES = (
    "gate_execution_permits",
    "gate_execution_permit_consumptions",
)


def _install_policy_definition_guard(*, include_permit_boundary: bool) -> None:
    permit_guards = ""
    if include_permit_boundary:
        permit_guards = """
               OR OLD.enforcement_principal_ids IS DISTINCT FROM NEW.enforcement_principal_ids
               OR OLD.maximum_permit_ttl_seconds IS DISTINCT FROM NEW.maximum_permit_ttl_seconds"""
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
               OR OLD.protected_actions IS DISTINCT FROM NEW.protected_actions
               OR OLD.target_ref_prefixes IS DISTINCT FROM NEW.target_ref_prefixes
               {permit_guards}
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


def _install_active_selector_guard(*, include_permit_boundary: bool) -> None:
    permit_guard = ""
    if include_permit_boundary:
        permit_guard = """
                IF jsonb_typeof(NEW.enforcement_principal_ids::jsonb) = 'array'
                   AND jsonb_array_length(NEW.enforcement_principal_ids::jsonb) = 0 THEN
                    -- A rolling 0.4 writer cannot populate the new execution
                    -- boundary. Preserve its write as inert historical policy
                    -- evidence instead of either breaking availability or
                    -- permitting an unmediated allow.
                    NEW.status := 'retired';
                    NEW.retired_at := COALESCE(NEW.retired_at, clock_timestamp());
                    RETURN NEW;
                END IF;
                IF jsonb_typeof(NEW.enforcement_principal_ids::jsonb) <> 'array'
                   OR NEW.maximum_permit_ttl_seconds NOT BETWEEN 1 AND 300 THEN
                    RAISE EXCEPTION
                        'Active Gate policies require enforcement principals and a bounded permit TTL';
                END IF;"""
    op.execute(
        f"""CREATE OR REPLACE FUNCTION lians_gate_guard_active_selector()
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
                {permit_guard}
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
                          WHERE op.value = np.value
                             OR (
                                  right(np.value, 1) IN ('/', ':', '#', '?')
                                  AND left(op.value, length(np.value)) = np.value
                             )
                             OR (
                                  right(op.value, 1) IN ('/', ':', '#', '?')
                                  AND left(np.value, length(op.value)) = op.value
                             )
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


def _install_postgresql_guards() -> None:
    _install_policy_definition_guard(include_permit_boundary=True)
    _install_active_selector_guard(include_permit_boundary=True)

    # ``gate_decision_records`` is already append-only. The next semantic
    # revision must fill one newly expanded column, so retain the boundary and
    # expose only an owner-role + migration-GUC UPDATE exception. 0040a removes
    # this exception before it stamps successfully.
    op.execute(
        """CREATE OR REPLACE FUNCTION lians_control_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            table_owner name;
        BEGIN
            SELECT pg_get_userbyid(relation.relowner)
            INTO table_owner
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = TG_TABLE_SCHEMA
              AND relation.relname = TG_TABLE_NAME;
            IF TG_TABLE_SCHEMA = 'public'
               AND TG_TABLE_NAME = 'gate_decision_records'
               AND TG_OP = 'UPDATE'
               AND current_setting(
                    'lians.migration_gate_decision_backfill', true
               ) = '0040a_gate_permit_contract'
               AND pg_has_role(current_user, table_owner, 'USAGE') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION lians_control_reject_mutation() FROM PUBLIC"
    )

    # Existing definitions are preserved as historical evidence, but only
    # definitions inserted after this migration may become executable.  This
    # trigger gives direct SQL writers the same nonempty/canonical contract as
    # the API without rewriting old policy hashes.
    op.execute(
        r"""CREATE FUNCTION lians_gate_validate_policy_enforcement()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF jsonb_typeof(NEW.enforcement_principal_ids::jsonb) <> 'array'
               OR NEW.maximum_permit_ttl_seconds NOT BETWEEN 1 AND 300 THEN
                RAISE EXCEPTION
                    'Gate policy requires an enforcement-principal array and TTL 1-300s';
            END IF;
            IF jsonb_array_length(NEW.enforcement_principal_ids::jsonb) = 0 THEN
                IF NEW.status = 'active' THEN
                    NEW.status := 'retired';
                    NEW.retired_at := COALESCE(NEW.retired_at, clock_timestamp());
                END IF;
                RETURN NEW;
            END IF;
            IF EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(NEW.enforcement_principal_ids::jsonb) p(value)
                    WHERE p.value !~ '^lians:principal:v1:(api-key:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|oidc:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$'
               )
               OR (
                    SELECT count(*) <> count(DISTINCT p.value)
                    FROM jsonb_array_elements_text(NEW.enforcement_principal_ids::jsonb) p(value)
               )
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(NEW.target_ref_prefixes::jsonb) target(value)
                    WHERE length(target.value) > 2048
                       OR octet_length(target.value) <> length(target.value)
                       OR target.value !~ '^[a-z][a-z0-9+.-]{0,31}:[][A-Za-z0-9._~:/?#@!$&''()*+,;=%-]+$'
                       OR regexp_replace(
                            target.value, '%[0-9A-F]{2}', '', 'g'
                       ) LIKE '%!%%' ESCAPE '!'
               ) THEN
                RAISE EXCEPTION
                    'Gate policy requires canonical target selectors, unique canonical enforcement principal references, and TTL 1-300s';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION lians_gate_validate_policy_enforcement() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER trg_gate_policy_validate_enforcement
        BEFORE INSERT OR UPDATE OF enforcement_principal_ids, maximum_permit_ttl_seconds
        ON gate_policy_sets
        FOR EACH ROW EXECUTE FUNCTION lians_gate_validate_policy_enforcement()"""
    )

    op.execute(
        """CREATE FUNCTION lians_gate_fill_legacy_execution_boundary()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NEW.target_ref IS NULL THEN
                NEW.target_ref := COALESCE(
                    NULLIF(NEW.input_snapshot ->> 'target_ref', ''),
                    'lians:legacy-unbound'
                );
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "lians_gate_fill_legacy_execution_boundary() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER trg_gate_decision_fill_legacy_execution_boundary
        BEFORE INSERT ON gate_decision_records
        FOR EACH ROW EXECUTE FUNCTION
            lians_gate_fill_legacy_execution_boundary()"""
    )

    for table in _PERMIT_TABLES:
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
        """CREATE FUNCTION lians_gate_permit_reject_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only and cannot be truncated', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql"""
    )
    for table in _PERMIT_TABLES:
        op.execute(
            f"""CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION lians_gate_permit_reject_mutation()"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_{table}_no_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION lians_gate_permit_reject_mutation()"""
        )
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM PUBLIC")

    op.execute(
        """CREATE FUNCTION lians_gate_validate_permit_grant()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            evaluation gate_decision_records%ROWTYPE;
            policy gate_policy_sets%ROWTYPE;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended('lians:gate-permit-evaluation:' || NEW.evaluation_id, 0)
            );
            SELECT * INTO evaluation
            FROM gate_decision_records
            WHERE id = NEW.evaluation_id
            FOR SHARE;
            SELECT * INTO policy
            FROM gate_policy_sets
            WHERE id = NEW.policy_set_id
            FOR SHARE;
            IF evaluation.id IS NULL
               OR policy.id IS NULL
               OR evaluation.disposition <> 'allow'
               OR evaluation.namespace IS DISTINCT FROM NEW.namespace
               OR evaluation.barrier_group IS DISTINCT FROM NEW.barrier_group
               OR evaluation.policy_set_id IS DISTINCT FROM NEW.policy_set_id
               OR evaluation.policy_name IS DISTINCT FROM policy.name
               OR evaluation.policy_version IS DISTINCT FROM policy.version
               OR evaluation.policy_hash IS DISTINCT FROM policy.policy_hash
               OR evaluation.decision_id IS DISTINCT FROM NEW.decision_id
               OR evaluation.action IS DISTINCT FROM NEW.action
               OR evaluation.target_ref IS DISTINCT FROM NEW.target_ref
               OR evaluation.enforcement_principal_id IS DISTINCT FROM NEW.enforcement_principal_id
               OR evaluation.principal_id IS NOT DISTINCT FROM NEW.enforcement_principal_id
               OR evaluation.execution_request_hash IS DISTINCT FROM NEW.execution_request_hash
               OR evaluation.evaluated_at IS DISTINCT FROM NEW.issued_at
               OR NEW.target_ref = 'lians:legacy-unbound'
               OR policy.namespace IS DISTINCT FROM NEW.namespace
               OR policy.status <> 'active'
               OR NOT (
                    policy.barrier_group IS NULL
                    OR policy.barrier_group IS NOT DISTINCT FROM NEW.barrier_group
               )
               OR NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(policy.protected_actions::jsonb) a(value)
                    WHERE a.value = NEW.action
               )
               OR NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(policy.target_ref_prefixes::jsonb) t(value)
                    WHERE t.value = NEW.target_ref
                       OR (
                            right(t.value, 1) IN ('/', ':', '#', '?')
                            AND left(NEW.target_ref, length(t.value)) = t.value
                       )
               )
               OR NEW.expires_at > NEW.issued_at
                    + policy.maximum_permit_ttl_seconds * interval '1 second'
               OR octet_length(NEW.target_ref) <> length(NEW.target_ref)
               OR NEW.target_ref !~ '^[a-z][a-z0-9+.-]{0,31}:[][A-Za-z0-9._~:/?#@!$&''()*+,;=%-]+$'
               OR regexp_replace(
                    NEW.target_ref, '%[0-9A-F]{2}', '', 'g'
               ) LIKE '%!%%' ESCAPE '!'
               OR NEW.execution_request_hash !~ '^[0-9a-f]{64}$'
               OR NEW.token_digest !~ '^[0-9a-f]{64}$'
               OR NEW.grant_hash !~ '^[0-9a-f]{64}$'
               OR NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(policy.enforcement_principal_ids::jsonb) p(value)
                    WHERE p.value = NEW.enforcement_principal_id
               ) THEN
                RAISE EXCEPTION
                    'execution permit must exactly match an allow evaluation and immutable policy';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION lians_gate_validate_permit_grant() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER trg_gate_execution_permit_validate_insert
        BEFORE INSERT ON gate_execution_permits
        FOR EACH ROW EXECUTE FUNCTION lians_gate_validate_permit_grant()"""
    )

    # This deferred constraint makes it impossible to commit a PostgreSQL allow
    # verdict without the exactly-one grant. The application inserts the verdict
    # first to satisfy the grant FK, then inserts the grant in the same transaction.
    op.execute(
        """CREATE FUNCTION lians_gate_require_allow_permit()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NEW.disposition = 'allow' AND NOT EXISTS (
                SELECT 1
                FROM gate_execution_permits permit
                WHERE permit.evaluation_id = NEW.id
                  AND permit.namespace = NEW.namespace
                  AND permit.barrier_group IS NOT DISTINCT FROM NEW.barrier_group
                  AND permit.policy_set_id = NEW.policy_set_id
                  AND permit.decision_id = NEW.decision_id
                  AND permit.action = NEW.action
                  AND permit.target_ref = NEW.target_ref
                  AND permit.enforcement_principal_id = NEW.enforcement_principal_id
                  AND permit.execution_request_hash = NEW.execution_request_hash
            ) THEN
                RAISE EXCEPTION 'allow Gate evaluation requires exactly one execution permit';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION lians_gate_require_allow_permit() FROM PUBLIC"
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_gate_allow_requires_execution_permit
        AFTER INSERT ON gate_decision_records
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION lians_gate_require_allow_permit()"""
    )

    op.execute(
        """CREATE FUNCTION lians_gate_validate_permit_consumption()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            permit gate_execution_permits%ROWTYPE;
            evaluation gate_decision_records%ROWTYPE;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended('lians:gate-permit:' || NEW.permit_id, 0)
            );
            SELECT * INTO permit
            FROM gate_execution_permits
            WHERE id = NEW.permit_id
            FOR SHARE;
            SELECT * INTO evaluation
            FROM gate_decision_records
            WHERE id = NEW.evaluation_id
            FOR SHARE;
            IF permit.id IS NULL
               OR evaluation.id IS NULL
               OR EXISTS (
                    SELECT 1 FROM gate_execution_permit_consumptions
                    WHERE permit_id = NEW.permit_id
               )
               OR clock_timestamp() >= permit.expires_at
               OR NEW.consumed_at < permit.issued_at
               OR NEW.consumed_at >= permit.expires_at
               OR NEW.consumed_at > clock_timestamp() + interval '5 seconds'
               OR permit.namespace IS DISTINCT FROM NEW.namespace
               OR permit.barrier_group IS DISTINCT FROM NEW.barrier_group
               OR permit.evaluation_id IS DISTINCT FROM NEW.evaluation_id
               OR permit.policy_set_id IS DISTINCT FROM NEW.policy_set_id
               OR permit.decision_id IS DISTINCT FROM NEW.decision_id
               OR permit.enforcement_principal_id IS DISTINCT FROM NEW.consuming_principal_id
               OR permit.action IS DISTINCT FROM NEW.action
               OR permit.target_ref IS DISTINCT FROM NEW.target_ref
               OR permit.execution_request_hash IS DISTINCT FROM NEW.execution_request_hash
               OR permit.grant_hash IS DISTINCT FROM NEW.grant_hash
               OR permit.token_digest IS DISTINCT FROM NEW.token_digest
               OR evaluation.disposition <> 'allow'
               OR evaluation.namespace IS DISTINCT FROM NEW.namespace
               OR evaluation.barrier_group IS DISTINCT FROM NEW.barrier_group
               OR evaluation.policy_set_id IS DISTINCT FROM NEW.policy_set_id
               OR evaluation.decision_id IS DISTINCT FROM NEW.decision_id
               OR evaluation.action IS DISTINCT FROM NEW.action
               OR evaluation.target_ref IS DISTINCT FROM NEW.target_ref
               OR evaluation.enforcement_principal_id IS DISTINCT FROM NEW.consuming_principal_id
               OR evaluation.execution_request_hash IS DISTINCT FROM NEW.execution_request_hash
               OR NEW.execution_request_hash !~ '^[0-9a-f]{64}$'
               OR NEW.token_digest !~ '^[0-9a-f]{64}$'
               OR NEW.grant_hash !~ '^[0-9a-f]{64}$'
               OR NEW.consumption_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'execution permit is invalid or unusable';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION lians_gate_validate_permit_consumption() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER trg_gate_execution_permit_consumption_validate_insert
        BEFORE INSERT ON gate_execution_permit_consumptions
        FOR EACH ROW EXECUTE FUNCTION lians_gate_validate_permit_consumption()"""
    )


def _install_sqlite_guards() -> None:
    op.execute(
        """CREATE TRIGGER trg_gate_policy_validate_enforcement
        BEFORE INSERT ON gate_policy_sets
        WHEN NEW.maximum_permit_ttl_seconds NOT BETWEEN 1 AND 300
          OR EXISTS (
              SELECT 1 FROM json_each(NEW.enforcement_principal_ids)
              WHERE value <> lower(value)
                 OR NOT (
                      (length(value) = 63 AND value LIKE 'lians:principal:v1:api-key:%')
                      OR (length(value) = 97 AND value LIKE 'lians:principal:v1:oidc:%')
                 )
          )
          OR (
              SELECT count(*) <> count(DISTINCT value)
              FROM json_each(NEW.enforcement_principal_ids)
          )
          OR EXISTS (
              SELECT 1 FROM json_each(NEW.target_ref_prefixes)
              WHERE length(value) > 2048
                 OR length(CAST(value AS BLOB)) <> length(value)
                 OR instr(value, ':') < 2
                 OR substr(value, 1, instr(value, ':') - 1)
                    <> lower(substr(value, 1, instr(value, ':') - 1))
                 OR value LIKE '% %'
          )
        BEGIN
            SELECT RAISE(ABORT, 'invalid Gate enforcement boundary');
        END"""
    )
    for operation in ("INSERT", "UPDATE OF status"):
        suffix = "insert" if operation == "INSERT" else "activate"
        op.execute(
            f"""CREATE TRIGGER trg_gate_policy_retire_legacy_{suffix}
            AFTER {operation} ON gate_policy_sets
            WHEN NEW.status = 'active'
              AND json_array_length(NEW.enforcement_principal_ids) = 0
            BEGIN
                UPDATE gate_policy_sets
                SET status = 'retired',
                    retired_at = COALESCE(retired_at, CURRENT_TIMESTAMP)
                WHERE id = NEW.id;
            END"""
        )
    op.execute(
        """CREATE TRIGGER trg_gate_policy_enforcement_immutable
        BEFORE UPDATE OF enforcement_principal_ids, maximum_permit_ttl_seconds
        ON gate_policy_sets
        WHEN OLD.enforcement_principal_ids IS NOT NEW.enforcement_principal_ids
          OR OLD.maximum_permit_ttl_seconds IS NOT NEW.maximum_permit_ttl_seconds
        BEGIN
            SELECT RAISE(
                ABORT,
                'Gate policy enforcement definitions are immutable; create a new version'
            );
        END"""
    )
    for table in _PERMIT_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"""CREATE TRIGGER trg_{table}_reject_{operation.lower()}
                BEFORE {operation} ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{table} is append-only');
                END"""
            )

    op.execute(
        """CREATE TRIGGER trg_gate_execution_permit_validate_insert
        BEFORE INSERT ON gate_execution_permits
        WHEN NOT EXISTS (
            SELECT 1
            FROM gate_decision_records evaluation
            JOIN gate_policy_sets policy ON policy.id = NEW.policy_set_id
            WHERE evaluation.id = NEW.evaluation_id
              AND evaluation.disposition = 'allow'
              AND evaluation.namespace = NEW.namespace
              AND evaluation.barrier_group IS NEW.barrier_group
              AND evaluation.policy_set_id = NEW.policy_set_id
              AND evaluation.policy_name = policy.name
              AND evaluation.policy_version = policy.version
              AND evaluation.policy_hash = policy.policy_hash
              AND evaluation.decision_id = NEW.decision_id
              AND evaluation.action = NEW.action
              AND evaluation.target_ref = NEW.target_ref
              AND evaluation.enforcement_principal_id = NEW.enforcement_principal_id
              AND evaluation.principal_id <> NEW.enforcement_principal_id
              AND evaluation.execution_request_hash = NEW.execution_request_hash
              AND evaluation.evaluated_at = NEW.issued_at
              AND policy.namespace = NEW.namespace
              AND policy.status = 'active'
              AND (policy.barrier_group IS NULL OR policy.barrier_group IS NEW.barrier_group)
              AND EXISTS (
                    SELECT 1 FROM json_each(policy.protected_actions)
                    WHERE value = NEW.action
              )
              AND EXISTS (
                    SELECT 1 FROM json_each(policy.target_ref_prefixes)
                    WHERE value = NEW.target_ref
                       OR (
                            substr(value, -1, 1) IN ('/', ':', '#', '?')
                            AND substr(NEW.target_ref, 1, length(value)) = value
                       )
              )
              AND datetime(NEW.expires_at) <= datetime(
                    NEW.issued_at,
                    '+' || policy.maximum_permit_ttl_seconds || ' seconds'
              )
              AND EXISTS (
                    SELECT 1 FROM json_each(policy.enforcement_principal_ids)
                    WHERE value = NEW.enforcement_principal_id
              )
              AND length(NEW.target_ref) <= 2048
              AND length(CAST(NEW.target_ref AS BLOB)) = length(NEW.target_ref)
              AND instr(NEW.target_ref, ':') >= 2
              AND substr(NEW.target_ref, 1, instr(NEW.target_ref, ':') - 1)
                  = lower(substr(NEW.target_ref, 1, instr(NEW.target_ref, ':') - 1))
              AND NEW.target_ref NOT LIKE '% %'
              AND length(NEW.execution_request_hash) = 64
              AND NEW.execution_request_hash NOT GLOB '*[^0-9a-f]*'
              AND length(NEW.token_digest) = 64
              AND NEW.token_digest NOT GLOB '*[^0-9a-f]*'
              AND length(NEW.grant_hash) = 64
              AND NEW.grant_hash NOT GLOB '*[^0-9a-f]*'
        )
        BEGIN
            SELECT RAISE(ABORT, 'permit does not match an allow evaluation');
        END"""
    )
    op.execute(
        """CREATE TRIGGER trg_gate_execution_permit_consumption_validate_insert
        BEFORE INSERT ON gate_execution_permit_consumptions
        WHEN NOT EXISTS (
            SELECT 1
            FROM gate_execution_permits permit
            JOIN gate_decision_records evaluation
              ON evaluation.id = permit.evaluation_id
            WHERE permit.id = NEW.permit_id
              AND permit.namespace = NEW.namespace
              AND permit.barrier_group IS NEW.barrier_group
              AND permit.evaluation_id = NEW.evaluation_id
              AND permit.policy_set_id = NEW.policy_set_id
              AND permit.decision_id = NEW.decision_id
              AND permit.enforcement_principal_id = NEW.consuming_principal_id
              AND permit.action = NEW.action
              AND permit.target_ref = NEW.target_ref
              AND permit.execution_request_hash = NEW.execution_request_hash
              AND permit.grant_hash = NEW.grant_hash
              AND permit.token_digest = NEW.token_digest
              AND datetime('now') < datetime(permit.expires_at)
              AND datetime(NEW.consumed_at) >= datetime(permit.issued_at)
              AND datetime(NEW.consumed_at) < datetime(permit.expires_at)
              AND evaluation.disposition = 'allow'
              AND evaluation.namespace = NEW.namespace
              AND evaluation.barrier_group IS NEW.barrier_group
              AND evaluation.policy_set_id = NEW.policy_set_id
              AND evaluation.decision_id = NEW.decision_id
              AND evaluation.action = NEW.action
              AND evaluation.target_ref = NEW.target_ref
              AND evaluation.enforcement_principal_id = NEW.consuming_principal_id
              AND evaluation.execution_request_hash = NEW.execution_request_hash
              AND length(NEW.execution_request_hash) = 64
              AND NEW.execution_request_hash NOT GLOB '*[^0-9a-f]*'
              AND length(NEW.token_digest) = 64
              AND NEW.token_digest NOT GLOB '*[^0-9a-f]*'
              AND length(NEW.grant_hash) = 64
              AND NEW.grant_hash NOT GLOB '*[^0-9a-f]*'
              AND length(NEW.consumption_hash) = 64
              AND NEW.consumption_hash NOT GLOB '*[^0-9a-f]*'
        ) OR EXISTS (
            SELECT 1 FROM gate_execution_permit_consumptions
            WHERE permit_id = NEW.permit_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'execution permit is invalid or unusable');
        END"""
    )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.add_column(
        "gate_policy_sets",
        sa.Column(
            "enforcement_principal_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "gate_policy_sets",
        sa.Column(
            "maximum_permit_ttl_seconds",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
    )
    if dialect == "sqlite":
        with op.batch_alter_table("gate_policy_sets") as batch:
            batch.create_check_constraint(
                "ck_gate_policy_permit_ttl",
                "maximum_permit_ttl_seconds BETWEEN 1 AND 300",
            )
    elif dialect == "postgresql":
        op.execute(
            """ALTER TABLE public.gate_policy_sets
            ADD CONSTRAINT ck_gate_policy_permit_ttl
            CHECK (maximum_permit_ttl_seconds BETWEEN 1 AND 300)
            NOT VALID"""
        )
    else:
        op.create_check_constraint(
            "ck_gate_policy_permit_ttl",
            "gate_policy_sets",
            "maximum_permit_ttl_seconds BETWEEN 1 AND 300",
        )
    op.add_column(
        "gate_decision_records",
        sa.Column("target_ref", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "gate_decision_records",
        sa.Column("enforcement_principal_id", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "gate_decision_records",
        sa.Column("execution_request_hash", sa.String(length=64), nullable=True),
    )
    if dialect == "postgresql":
        # The contract revision validates this after its committed, bounded
        # legacy backfill. NOT VALID keeps this expand step metadata-only.
        op.execute(
            """ALTER TABLE public.gate_decision_records
            ADD CONSTRAINT ck_0040_gate_target_ref_present
            CHECK (target_ref IS NOT NULL) NOT VALID"""
        )

    op.create_table(
        "gate_execution_permits",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column(
            "evaluation_id",
            _uuid(),
            sa.ForeignKey("gate_decision_records.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "policy_set_id",
            _uuid(),
            sa.ForeignKey("gate_policy_sets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "decision_id",
            _uuid(),
            sa.ForeignKey("decision_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("enforcement_principal_id", sa.String(length=512), nullable=False),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("target_ref", sa.String(length=2048), nullable=False),
        sa.Column("execution_request_hash", sa.String(length=64), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False, unique=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("grant_hash", sa.String(length=64), nullable=False, unique=True),
        sa.CheckConstraint("expires_at > issued_at", name="ck_gate_permit_expiry"),
        sa.CheckConstraint(
            "length(token_digest) = 64", name="ck_gate_permit_token_digest"
        ),
        sa.CheckConstraint(
            "length(execution_request_hash) = 64",
            name="ck_gate_permit_request_hash",
        ),
        sa.CheckConstraint(
            "length(grant_hash) = 64", name="ck_gate_permit_grant_hash"
        ),
        sa.CheckConstraint(
            "enforcement_principal_id LIKE 'lians:principal:v1:%'",
            name="ck_gate_permit_principal_ref",
        ),
    )
    for name, columns in (
        ("ix_gate_execution_permits_namespace", ["namespace"]),
        ("ix_gate_execution_permits_barrier_group", ["barrier_group"]),
        ("ix_gate_execution_permits_evaluation_id", ["evaluation_id"]),
        ("ix_gate_execution_permits_policy_set_id", ["policy_set_id"]),
        ("ix_gate_execution_permits_decision_id", ["decision_id"]),
        ("ix_gate_execution_permits_enforcement_principal_id", ["enforcement_principal_id"]),
        ("ix_gate_execution_permits_action", ["action"]),
        ("ix_gate_execution_permits_issued_at", ["issued_at"]),
        ("ix_gate_execution_permits_expires_at", ["expires_at"]),
        ("ix_gate_execution_permits_grant_hash", ["grant_hash"]),
        ("ix_gate_permit_ns_expiry", ["namespace", "expires_at"]),
    ):
        op.create_index(name, "gate_execution_permits", columns)

    op.create_table(
        "gate_execution_permit_consumptions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column(
            "permit_id",
            _uuid(),
            sa.ForeignKey("gate_execution_permits.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "evaluation_id",
            _uuid(),
            sa.ForeignKey("gate_decision_records.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "policy_set_id",
            _uuid(),
            sa.ForeignKey("gate_policy_sets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "decision_id",
            _uuid(),
            sa.ForeignKey("decision_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("consuming_principal_id", sa.String(length=512), nullable=False),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("target_ref", sa.String(length=2048), nullable=False),
        sa.Column("execution_request_hash", sa.String(length=64), nullable=False),
        sa.Column("grant_hash", sa.String(length=64), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumption_hash", sa.String(length=64), nullable=False, unique=True),
        sa.CheckConstraint(
            "length(grant_hash) = 64",
            name="ck_gate_permit_consumption_grant_hash",
        ),
        sa.CheckConstraint(
            "length(token_digest) = 64",
            name="ck_gate_permit_consumption_token_digest",
        ),
        sa.CheckConstraint(
            "length(execution_request_hash) = 64",
            name="ck_gate_permit_consumption_request_hash",
        ),
        sa.CheckConstraint(
            "length(consumption_hash) = 64",
            name="ck_gate_permit_consumption_hash",
        ),
        sa.CheckConstraint(
            "consuming_principal_id LIKE 'lians:principal:v1:%'",
            name="ck_gate_permit_consumption_principal_ref",
        ),
    )
    for name, columns in (
        ("ix_gate_execution_permit_consumptions_namespace", ["namespace"]),
        ("ix_gate_execution_permit_consumptions_barrier_group", ["barrier_group"]),
        ("ix_gate_execution_permit_consumptions_permit_id", ["permit_id"]),
        ("ix_gate_execution_permit_consumptions_evaluation_id", ["evaluation_id"]),
        ("ix_gate_execution_permit_consumptions_policy_set_id", ["policy_set_id"]),
        ("ix_gate_execution_permit_consumptions_decision_id", ["decision_id"]),
        (
            "ix_gate_execution_permit_consumptions_consuming_principal_id",
            ["consuming_principal_id"],
        ),
        ("ix_gate_execution_permit_consumptions_consumed_at", ["consumed_at"]),
        ("ix_gate_execution_permit_consumptions_consumption_hash", ["consumption_hash"]),
        ("ix_gate_permit_consumption_ns_time", ["namespace", "consumed_at"]),
    ):
        op.create_index(name, "gate_execution_permit_consumptions", columns)

    if dialect == "postgresql":
        _install_postgresql_guards()
    elif dialect == "sqlite":
        _install_sqlite_guards()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "trg_gate_decision_fill_legacy_execution_boundary "
            "ON gate_decision_records"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_gate_allow_requires_execution_permit "
            "ON gate_decision_records"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_gate_execution_permit_consumption_validate_insert "
            "ON gate_execution_permit_consumptions"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_gate_execution_permit_validate_insert "
            "ON gate_execution_permits"
        )
        for table in _PERMIT_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_truncate ON {table}")
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
            op.execute(f"DROP POLICY IF EXISTS barrier_isolation ON {table}")
            op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(
            "DROP TRIGGER IF EXISTS trg_gate_policy_validate_enforcement "
            "ON gate_policy_sets"
        )
        op.execute("DROP FUNCTION IF EXISTS lians_gate_validate_permit_consumption()")
        op.execute("DROP FUNCTION IF EXISTS lians_gate_require_allow_permit()")
        op.execute("DROP FUNCTION IF EXISTS lians_gate_validate_permit_grant()")
        op.execute("DROP FUNCTION IF EXISTS lians_gate_permit_reject_mutation()")
        op.execute("DROP FUNCTION IF EXISTS lians_gate_validate_policy_enforcement()")
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "lians_gate_fill_legacy_execution_boundary()"
        )
        op.execute(
            """CREATE OR REPLACE FUNCTION lians_control_reject_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql"""
        )
        _install_policy_definition_guard(include_permit_boundary=False)
        _install_active_selector_guard(include_permit_boundary=False)
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_gate_policy_validate_enforcement")
        op.execute("DROP TRIGGER IF EXISTS trg_gate_policy_enforcement_immutable")
        op.execute("DROP TRIGGER IF EXISTS trg_gate_policy_retire_legacy_insert")
        op.execute("DROP TRIGGER IF EXISTS trg_gate_policy_retire_legacy_activate")
        op.execute("DROP TRIGGER IF EXISTS trg_gate_execution_permit_validate_insert")
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "trg_gate_execution_permit_consumption_validate_insert"
        )
        for table in _PERMIT_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_update")
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_delete")

    op.drop_table("gate_execution_permit_consumptions")
    op.drop_table("gate_execution_permits")
    if dialect == "postgresql":
        op.drop_constraint(
            "ck_0040_gate_target_ref_present",
            "gate_decision_records",
            type_="check",
        )
    op.drop_column("gate_decision_records", "execution_request_hash")
    op.drop_column("gate_decision_records", "enforcement_principal_id")
    op.drop_column("gate_decision_records", "target_ref")
    if dialect == "sqlite":
        with op.batch_alter_table("gate_policy_sets") as batch:
            batch.drop_constraint("ck_gate_policy_permit_ttl", type_="check")
    else:
        op.drop_constraint(
            "ck_gate_policy_permit_ttl", "gate_policy_sets", type_="check"
        )
    op.drop_column("gate_policy_sets", "maximum_permit_ttl_seconds")
    op.drop_column("gate_policy_sets", "enforcement_principal_ids")
