"""Add the append-only pre-robotics agent improvement plane.

Revision ID: 0064_agent_improvement_plane
Revises: 0063_admin_identity_indexes
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0064_agent_improvement_plane"
down_revision = "0063_admin_identity_indexes"
branch_labels = None
depends_on = None

_TABLES = (
    "agent_definitions",
    "component_artifacts",
    "context_bundles",
    "eval_suites",
    "runtime_policy_versions",
    "tool_registry_versions",
    "agent_versions",
    "eval_cases",
    "tool_selection_decisions",
    "agent_version_components",
    "cache_decisions",
    "drift_signals",
    "eval_runs",
    "eval_suite_cases",
    "improvement_outcomes",
    "optimization_studies",
    "routing_decisions",
    "runtime_concurrency_plans",
    "eval_comparisons",
    "eval_trials",
    "improvement_feedback",
    "evaluation_attestations",
    "learning_proposals",
    "metric_results",
    "optimization_candidates",
    "optimization_recommendations",
    "release_candidates",
    "release_attestations",
    "improvement_deployments",
    "improvement_rollbacks",
)


def _install_postgresql_boundaries() -> None:
    for table in _TABLES:
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
            f"""CREATE POLICY rls_{table}_barrier ON public.{table} AS RESTRICTIVE
            USING (
                current_setting('app.current_namespace', true) = '__admin__'
                OR (
                    current_setting('agentmem.barrier_group', true) = ''
                    AND barrier_group IS NULL
                )
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )
            WITH CHECK (
                current_setting('app.current_namespace', true) = '__admin__'
                OR (
                    current_setting('agentmem.barrier_group', true) = ''
                    AND barrier_group IS NULL
                )
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )"""
        )
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
        op.execute(f"GRANT SELECT, INSERT ON TABLE public.{table} TO lians_runtime")
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.{table} FROM lians_runtime")

    op.execute(
        """CREATE FUNCTION public.lians_improvement_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only; % is forbidden', TG_TABLE_NAME, TG_OP;
        END;
        $$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.lians_improvement_reject_mutation() FROM PUBLIC")
    for table in _TABLES:
        op.execute(
            f"""CREATE TRIGGER trg_{table}_reject_mutation
            BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.lians_improvement_reject_mutation()"""
        )
        op.execute(
            f"""CREATE TRIGGER trg_{table}_reject_truncate
            BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.lians_improvement_reject_mutation()"""
        )


def upgrade() -> None:
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(definition_hash) = 64 AND definition_hash = lower(definition_hash)",
            name="ck_agent_definition_hash",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_agent_definition_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "key", name="uq_agent_definition_namespace_key"
        ),
    )
    op.create_index(
        "ix_agent_definition_scope_page",
        "agent_definitions",
        ["namespace", "barrier_group", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "component_artifacts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=True),
        sa.Column("uri", sa.String(length=2048), nullable=True),
        sa.Column(
            "digest_algorithm", sa.String(length=16), server_default="sha256", nullable=False
        ),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("digest_algorithm = 'sha256'", name="ck_component_artifact_algorithm"),
        sa.CheckConstraint(
            "kind IN ('model','prompt','policy','tool','context','code','runtime','permission','other')",
            name="ck_component_artifact_kind",
        ),
        sa.CheckConstraint(
            "length(digest) = 64 AND digest = lower(digest) AND length(artifact_hash) = 64 AND artifact_hash = lower(artifact_hash)",
            name="ck_component_artifact_hashes",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_component_artifact_id_namespace"),
        sa.UniqueConstraint(
            "namespace",
            "barrier_scope",
            "kind",
            "digest",
            name="uq_component_artifact_namespace_kind_digest",
        ),
    )
    op.create_index(
        "ix_component_artifact_scope_page",
        "component_artifacts",
        ["namespace", "barrier_group", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "context_bundles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("tokenizer_engine", sa.String(length=32), nullable=False),
        sa.Column("tokenizer_name", sa.String(length=255), nullable=False),
        sa.Column("tokenizer_hash", sa.String(length=64), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("original_tokens", sa.Integer(), nullable=False),
        sa.Column("compiled_tokens", sa.Integer(), nullable=False),
        sa.Column("compiled_context_encrypted", sa.Text(), nullable=False),
        sa.Column("compiled_context_hash", sa.String(length=64), nullable=False),
        sa.Column("lineage", sa.JSON(), nullable=False),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column("bundle_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "compiled_context_encrypted LIKE 'lians-sealed:v1:%' OR compiled_context_encrypted LIKE 'lians-sealed:v2:%'",
            name="ck_context_bundle_sealed",
        ),
        sa.CheckConstraint(
            "tokenizer_engine IN ('tiktoken','tokenizers-json')",
            name="ck_context_bundle_tokenizer_engine",
        ),
        sa.CheckConstraint(
            "length(tokenizer_hash) = 64 AND length(compiled_context_hash) = 64 AND length(bundle_hash) = 64",
            name="ck_context_bundle_hashes",
        ),
        sa.CheckConstraint(
            "max_tokens > 0 AND original_tokens >= 0 AND compiled_tokens >= 0 AND compiled_tokens <= max_tokens",
            name="ck_context_bundle_token_counts",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_context_bundle_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "bundle_hash", name="uq_context_bundle_scope_hash"
        ),
    )
    op.create_index(
        "ix_context_bundle_scope_page",
        "context_bundles",
        ["namespace", "barrier_group", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "eval_suites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("improvement_contract", sa.JSON(), nullable=False),
        sa.Column("repetitions", sa.Integer(), server_default="2", nullable=False),
        sa.Column("suite_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(suite_hash) = 64", name="ck_eval_suite_hash"),
        sa.CheckConstraint("repetitions BETWEEN 2 AND 100", name="ck_eval_suite_repetitions"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_eval_suite_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "name", "version", name="uq_eval_suite_name_version"
        ),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "suite_hash", name="uq_eval_suite_namespace_hash"
        ),
    )
    op.create_index(
        "ix_eval_suite_scope_page",
        "eval_suites",
        ["namespace", "barrier_group", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "runtime_policy_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=False),
        sa.Column("quality_floor", sa.Float(), nullable=False),
        sa.Column("objective", sa.JSON(), nullable=False),
        sa.Column("request_budget", sa.JSON(), nullable=False),
        sa.Column("timeout_retry_policy", sa.JSON(), nullable=False),
        sa.Column("fallback_policy", sa.JSON(), nullable=False),
        sa.Column("cache_policy", sa.JSON(), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(policy_hash) = 64", name="ck_runtime_policy_hash"),
        sa.CheckConstraint(
            "quality_floor >= 0 AND quality_floor <= 1", name="ck_runtime_policy_quality_floor"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_runtime_policy_id_namespace"),
        sa.UniqueConstraint(
            "namespace",
            "barrier_scope",
            "name",
            "version",
            name="uq_runtime_policy_scope_name_version",
        ),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "policy_hash", name="uq_runtime_policy_scope_hash"
        ),
    )
    op.create_index(
        "ix_runtime_policy_scope_page",
        "runtime_policy_versions",
        ["namespace", "barrier_group", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "tool_registry_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=False),
        sa.Column("tools", sa.JSON(), nullable=False),
        sa.Column("registry_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(registry_hash) = 64", name="ck_tool_registry_hash"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_tool_registry_id_namespace"),
        sa.UniqueConstraint(
            "namespace",
            "barrier_scope",
            "name",
            "version",
            name="uq_tool_registry_scope_name_version",
        ),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "registry_hash", name="uq_tool_registry_scope_hash"
        ),
    )
    op.create_index(
        "ix_tool_registry_scope_page",
        "tool_registry_versions",
        ["namespace", "barrier_group", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("agent_definition_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=False),
        sa.Column("manifest", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(manifest_hash) = 64 AND manifest_hash = lower(manifest_hash)",
            name="ck_agent_version_manifest_hash",
        ),
        sa.ForeignKeyConstraint(
            ["agent_definition_id", "namespace"],
            ["agent_definitions.id", "agent_definitions.namespace"],
            name="fk_agent_version_definition_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_agent_version_id_namespace"),
        sa.UniqueConstraint(
            "namespace",
            "barrier_scope",
            "agent_definition_id",
            "manifest_hash",
            name="uq_agent_version_manifest",
        ),
        sa.UniqueConstraint(
            "namespace",
            "barrier_scope",
            "agent_definition_id",
            "version",
            name="uq_agent_version_label",
        ),
    )
    op.create_index(
        "ix_agent_version_scope_page",
        "agent_versions",
        ["namespace", "barrier_group", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "eval_cases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("decision_record_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_receipt_hash", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("input", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("expected", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("scorer_context", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("tags", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("capture_limitations", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("case_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(decision_record_hash) = 64 AND length(decision_receipt_hash) = 64 AND length(case_hash) = 64",
            name="ck_eval_case_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            name="fk_eval_case_decision_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_eval_case_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "case_hash", name="uq_eval_case_namespace_hash"
        ),
    )
    op.create_index(
        "ix_eval_case_decision", "eval_cases", ["namespace", "decision_id"], unique=False
    )
    op.create_index(
        "ix_eval_case_scope_page",
        "eval_cases",
        ["namespace", "barrier_group", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "tool_selection_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("registry_version_id", sa.UUID(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("tokenizer", sa.JSON(), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("selected_tools", sa.JSON(), nullable=False),
        sa.Column("excluded_tools", sa.JSON(), nullable=False),
        sa.Column("failed_loops", sa.JSON(), nullable=False),
        sa.Column("selected_schema_tokens", sa.Integer(), nullable=False),
        sa.Column("selection_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(query_hash) = 64 AND length(selection_hash) = 64",
            name="ck_tool_selection_hashes",
        ),
        sa.CheckConstraint(
            "token_budget > 0 AND selected_schema_tokens >= 0 AND selected_schema_tokens <= token_budget",
            name="ck_tool_selection_token_budget",
        ),
        sa.ForeignKeyConstraint(
            ["registry_version_id", "namespace"],
            ["tool_registry_versions.id", "tool_registry_versions.namespace"],
            name="fk_tool_selection_registry_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_tool_selection_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "selection_hash", name="uq_tool_selection_scope_hash"
        ),
    )
    op.create_index(
        "ix_tool_selection_scope_page",
        "tool_selection_decisions",
        ["namespace", "barrier_group", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "agent_version_components",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("component_artifact_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("binding_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("length(binding_hash) = 64", name="ck_agent_version_component_hash"),
        sa.CheckConstraint("position >= 0", name="ck_agent_version_component_position"),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_agent_version_component_version_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["component_artifact_id", "namespace"],
            ["component_artifacts.id", "component_artifacts.namespace"],
            name="fk_agent_version_component_artifact_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_version_id",
            "component_artifact_id",
            "role",
            name="uq_agent_version_component_binding",
        ),
        sa.UniqueConstraint(
            "agent_version_id", "role", "position", name="uq_agent_version_component_slot"
        ),
    )
    op.create_table(
        "cache_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("runtime_policy_version_id", sa.UUID(), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("disposition", sa.String(length=16), nullable=False),
        sa.Column("cache_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("permission_scope_hash", sa.String(length=64), nullable=False),
        sa.Column("release_reference_hash", sa.String(length=64), nullable=True),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "disposition IN ('hit','miss','stored','bypass','unavailable')",
            name="ck_cache_decision_disposition",
        ),
        sa.CheckConstraint(
            "mode IN ('exact_response','provider_prompt','tool_result')",
            name="ck_cache_decision_mode",
        ),
        sa.CheckConstraint("operation IN ('lookup','store')", name="ck_cache_decision_operation"),
        sa.CheckConstraint(
            "length(cache_key_hash) = 64 AND length(request_hash) = 64 AND length(permission_scope_hash) = 64 AND length(decision_hash) = 64",
            name="ck_cache_decision_hashes",
        ),
        sa.CheckConstraint(
            "ttl_seconds IS NULL OR ttl_seconds BETWEEN 1 AND 86400", name="ck_cache_decision_ttl"
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_cache_decision_agent_version_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_policy_version_id", "namespace"],
            ["runtime_policy_versions.id", "runtime_policy_versions.namespace"],
            name="fk_cache_decision_policy_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_cache_decision_id_namespace"),
    )
    op.create_index(
        "ix_cache_decision_scope_page",
        "cache_decisions",
        ["namespace", "barrier_group", "decided_at", "id"],
        unique=False,
    )
    op.create_table(
        "drift_signals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("metric_name", sa.String(length=255), nullable=False),
        sa.Column("baseline", sa.JSON(), nullable=False),
        sa.Column("current", sa.JSON(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("magnitude", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("drifted", sa.Boolean(), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("signal_hash", sa.String(length=64), nullable=False),
        sa.Column("detected_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "direction IN ('increase','decrease','absolute')", name="ck_drift_direction"
        ),
        sa.CheckConstraint("method = 'two-window-mean-v1'", name="ck_drift_method"),
        sa.CheckConstraint("length(signal_hash) = 64", name="ck_drift_signal_hash"),
        sa.CheckConstraint("magnitude >= 0 AND threshold >= 0", name="ck_drift_magnitude"),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_drift_signal_agent_version_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_drift_signal_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "signal_hash", name="uq_drift_signal_scope_hash"
        ),
    )
    op.create_index(
        "ix_drift_signal_scope_time",
        "drift_signals",
        ["namespace", "barrier_group", "detected_at", "id"],
        unique=False,
    )
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("suite_id", sa.UUID(), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("environment", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("capture_limitations", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("trial_count", sa.Integer(), nullable=False),
        sa.Column("run_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(run_hash) = 64", name="ck_eval_run_hash"),
        sa.CheckConstraint("trial_count > 0", name="ck_eval_run_trial_count"),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_eval_run_agent_version_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id", "namespace"],
            ["eval_suites.id", "eval_suites.namespace"],
            name="fk_eval_run_suite_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_eval_run_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "run_hash", name="uq_eval_run_namespace_hash"
        ),
    )
    op.create_index(
        "ix_eval_run_suite_page",
        "eval_runs",
        ["namespace", "suite_id", "completed_at", "id"],
        unique=False,
    )
    op.create_table(
        "eval_suite_cases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("suite_id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_eval_suite_case_position"),
        sa.ForeignKeyConstraint(
            ["case_id", "namespace"],
            ["eval_cases.id", "eval_cases.namespace"],
            name="fk_eval_suite_case_case_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id", "namespace"],
            ["eval_suites.id", "eval_suites.namespace"],
            name="fk_eval_suite_case_suite_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("suite_id", "case_id", name="uq_eval_suite_case_member"),
        sa.UniqueConstraint("suite_id", "position", name="uq_eval_suite_case_position"),
    )
    op.create_table(
        "improvement_outcomes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=True),
        sa.Column("deployment_id", sa.UUID(), nullable=True),
        sa.Column("correlation_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("payload_encrypted", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("outcome_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by_principal_ref", sa.String(length=512), nullable=False),
        sa.CheckConstraint(
            "kind IN ('success','failure','correction','dispute','override','incident','business')",
            name="ck_improvement_outcome_kind",
        ),
        sa.CheckConstraint(
            "(payload_encrypted IS NULL AND payload_hash IS NULL) OR (payload_encrypted IS NOT NULL AND length(payload_hash) = 64)",
            name="ck_improvement_outcome_payload_pair",
        ),
        sa.CheckConstraint(
            "length(correlation_hash) = 64 AND length(outcome_hash) = 64",
            name="ck_improvement_outcome_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_outcome_agent_version_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            name="fk_outcome_decision_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_improvement_outcome_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "outcome_hash", name="uq_improvement_outcome_scope_hash"
        ),
    )
    op.create_index(
        "ix_improvement_outcome_scope_time",
        "improvement_outcomes",
        ["namespace", "barrier_group", "occurred_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_improvement_outcome_version_time",
        "improvement_outcomes",
        ["namespace", "agent_version_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "optimization_studies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("suite_id", sa.UUID(), nullable=False),
        sa.Column("baseline_agent_version_id", sa.UUID(), nullable=False),
        sa.Column("objective", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="advisory", nullable=False),
        sa.Column("study_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status = 'advisory'", name="ck_optimization_study_status"),
        sa.CheckConstraint("length(study_hash) = 64", name="ck_optimization_study_hash_length"),
        sa.ForeignKeyConstraint(
            ["baseline_agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_optimization_study_baseline_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id", "namespace"],
            ["eval_suites.id", "eval_suites.namespace"],
            name="fk_optimization_study_suite_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_optimization_study_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "study_hash", name="uq_optimization_study_hash"
        ),
    )
    op.create_table(
        "routing_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("runtime_policy_version_id", sa.UUID(), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("selected", sa.JSON(), nullable=False),
        sa.Column("fallbacks", sa.JSON(), nullable=False),
        sa.Column("rejected", sa.JSON(), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("overhead_ms", sa.Float(), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(request_hash) = 64 AND length(decision_hash) = 64",
            name="ck_routing_decision_hashes",
        ),
        sa.CheckConstraint("overhead_ms >= 0", name="ck_routing_decision_overhead"),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_routing_decision_agent_version_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_policy_version_id", "namespace"],
            ["runtime_policy_versions.id", "runtime_policy_versions.namespace"],
            name="fk_routing_decision_policy_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_routing_decision_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "decision_hash", name="uq_routing_decision_scope_hash"
        ),
    )
    op.create_index(
        "ix_routing_decision_scope_page",
        "routing_decisions",
        ["namespace", "barrier_group", "decided_at", "id"],
        unique=False,
    )
    op.create_table(
        "runtime_concurrency_plans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("calls_hash", sa.String(length=64), nullable=False),
        sa.Column("calls", sa.JSON(), nullable=False),
        sa.Column("batches", sa.JSON(), nullable=False),
        sa.Column("critical_path_depth", sa.Integer(), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("critical_path_depth > 0", name="ck_concurrency_plan_depth"),
        sa.CheckConstraint(
            "length(calls_hash) = 64 AND length(plan_hash) = 64", name="ck_concurrency_plan_hashes"
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_concurrency_plan_agent_version_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_concurrency_plan_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "plan_hash", name="uq_concurrency_plan_scope_hash"
        ),
    )
    op.create_table(
        "eval_comparisons",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("suite_id", sa.UUID(), nullable=False),
        sa.Column("baseline_run_id", sa.UUID(), nullable=False),
        sa.Column("candidate_run_id", sa.UUID(), nullable=False),
        sa.Column("primary_metric", sa.String(length=255), nullable=False),
        sa.Column("primary_improvement", sa.Float(), nullable=False),
        sa.Column("aggregates", sa.JSON(), nullable=False),
        sa.Column("protected_results", sa.JSON(), nullable=False),
        sa.Column("critical_invariants_passed", sa.Boolean(), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("comparison_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "verdict IN ('eligible_for_review','no_verified_improvement','protected_regression')",
            name="ck_eval_comparison_verdict",
        ),
        sa.CheckConstraint("length(comparison_hash) = 64", name="ck_eval_comparison_hash_length"),
        sa.ForeignKeyConstraint(
            ["baseline_run_id", "namespace"],
            ["eval_runs.id", "eval_runs.namespace"],
            name="fk_eval_comparison_baseline_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_run_id", "namespace"],
            ["eval_runs.id", "eval_runs.namespace"],
            name="fk_eval_comparison_candidate_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id", "namespace"],
            ["eval_suites.id", "eval_suites.namespace"],
            name="fk_eval_comparison_suite_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_eval_comparison_id_namespace"),
        sa.UniqueConstraint(
            "namespace",
            "barrier_scope",
            "baseline_run_id",
            "candidate_run_id",
            name="uq_eval_comparison_run_pair",
        ),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "comparison_hash", name="uq_eval_comparison_hash"
        ),
    )
    op.create_table(
        "eval_trials",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("repetition", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=False),
        sa.Column("configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("cost_currency", sa.String(length=3), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trial_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("completed_at >= started_at", name="ck_eval_trial_time_order"),
        sa.CheckConstraint("cost IS NULL OR cost >= 0", name="ck_eval_trial_cost"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_eval_trial_input_tokens"
        ),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_eval_trial_latency"),
        sa.CheckConstraint(
            "length(input_hash) = 64 AND length(output_hash) = 64 AND length(configuration_hash) = 64 AND length(trial_hash) = 64",
            name="ck_eval_trial_hashes",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_eval_trial_output_tokens"
        ),
        sa.CheckConstraint("repetition >= 0", name="ck_eval_trial_repetition"),
        sa.ForeignKeyConstraint(
            ["case_id", "namespace"],
            ["eval_cases.id", "eval_cases.namespace"],
            name="fk_eval_trial_case_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "namespace"],
            ["eval_runs.id", "eval_runs.namespace"],
            name="fk_eval_trial_run_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_eval_trial_id_namespace"),
        sa.UniqueConstraint("run_id", "case_id", "repetition", name="uq_eval_trial_repeat"),
    )
    op.create_table(
        "improvement_feedback",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("outcome_id", sa.UUID(), nullable=True),
        sa.Column("decision_id", sa.UUID(), nullable=True),
        sa.Column("decision_receipt_hash", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("generated_eval_case_id", sa.UUID(), nullable=True),
        sa.Column("feedback_hash", sa.String(length=64), nullable=False),
        sa.Column("authored_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("authored_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('correction','dispute','human_override','incident','rating','comment')",
            name="ck_improvement_feedback_kind",
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64 AND length(feedback_hash) = 64",
            name="ck_improvement_feedback_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_feedback_agent_version_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "namespace"],
            ["decision_records.id", "decision_records.namespace"],
            name="fk_feedback_decision_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generated_eval_case_id", "namespace"],
            ["eval_cases.id", "eval_cases.namespace"],
            name="fk_feedback_eval_case_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["outcome_id", "namespace"],
            ["improvement_outcomes.id", "improvement_outcomes.namespace"],
            name="fk_feedback_outcome_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_improvement_feedback_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "feedback_hash", name="uq_improvement_feedback_scope_hash"
        ),
    )
    op.create_index(
        "ix_improvement_feedback_scope_time",
        "improvement_feedback",
        ["namespace", "barrier_group", "authored_at", "id"],
        unique=False,
    )
    op.create_table(
        "evaluation_attestations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=16), server_default="0.1", nullable=False),
        sa.Column("comparison_id", sa.UUID(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("signature_algorithm", sa.String(length=32), nullable=False),
        sa.Column("signing_key_id", sa.String(length=255), nullable=False),
        sa.Column("signing_public_key", sa.Text(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version = '0.1'", name="ck_evaluation_attestation_version"),
        sa.CheckConstraint("signature_algorithm = 'ed25519'", name="ck_evaluation_attestation_alg"),
        sa.CheckConstraint(
            "length(payload_hash) = 64", name="ck_evaluation_attestation_hash_length"
        ),
        sa.ForeignKeyConstraint(
            ["comparison_id", "namespace"],
            ["eval_comparisons.id", "eval_comparisons.namespace"],
            name="fk_evaluation_attestation_comparison_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_evaluation_attestation_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "payload_hash", name="uq_evaluation_attestation_hash"
        ),
    )
    op.create_table(
        "learning_proposals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("source_feedback_id", sa.UUID(), nullable=True),
        sa.Column("source_drift_signal_id", sa.UUID(), nullable=True),
        sa.Column("eval_case_id", sa.UUID(), nullable=True),
        sa.Column("proposal_type", sa.String(length=32), nullable=False),
        sa.Column("recommendation", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("proposal_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "proposal_type IN ('regression_case','context_change','tool_change','prompt_change','model_change','policy_change','investigate')",
            name="ck_learning_proposal_type",
        ),
        sa.CheckConstraint(
            "status = 'awaiting_customer_approval'", name="ck_learning_proposal_status"
        ),
        sa.CheckConstraint(
            "(source_feedback_id IS NOT NULL AND source_drift_signal_id IS NULL) OR (source_feedback_id IS NULL AND source_drift_signal_id IS NOT NULL)",
            name="ck_learning_proposal_one_source",
        ),
        sa.CheckConstraint("length(proposal_hash) = 64", name="ck_learning_proposal_hash"),
        sa.CheckConstraint("priority >= 0 AND priority <= 1", name="ck_learning_proposal_priority"),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_learning_proposal_agent_version_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["eval_case_id", "namespace"],
            ["eval_cases.id", "eval_cases.namespace"],
            name="fk_learning_proposal_eval_case_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_drift_signal_id", "namespace"],
            ["drift_signals.id", "drift_signals.namespace"],
            name="fk_learning_proposal_drift_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_feedback_id", "namespace"],
            ["improvement_feedback.id", "improvement_feedback.namespace"],
            name="fk_learning_proposal_feedback_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_learning_proposal_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "proposal_hash", name="uq_learning_proposal_scope_hash"
        ),
    )
    op.create_index(
        "ix_learning_proposal_scope_priority",
        "learning_proposals",
        ["namespace", "barrier_group", "status", "priority", "id"],
        unique=False,
    )
    op.create_table(
        "metric_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("trial_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("metric_type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("scorer_id", sa.String(length=255), nullable=False),
        sa.Column("scorer_version", sa.String(length=255), nullable=False),
        sa.Column("scorer_config_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("limitations", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "metric_type IN ('quality','evidence','safety','latency','token','cost','outcome','reliability','autonomy','robustness')",
            name="ck_metric_result_type",
        ),
        sa.CheckConstraint(
            "provenance IN ('provider-reported','workload-reported','client-measured','deterministic','human-authored','model-judged','external','estimated')",
            name="ck_metric_result_provenance",
        ),
        sa.CheckConstraint(
            "length(scorer_config_hash) = 64 AND length(result_hash) = 64",
            name="ck_metric_result_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["trial_id", "namespace"],
            ["eval_trials.id", "eval_trials.namespace"],
            name="fk_metric_result_trial_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trial_id", "name", name="uq_metric_result_trial_name"),
    )
    op.create_table(
        "optimization_candidates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("comparison_id", sa.UUID(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("length(candidate_hash) = 64", name="ck_optimization_candidate_hash"),
        sa.CheckConstraint("rank > 0", name="ck_optimization_candidate_rank"),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_optimization_candidate_version_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["comparison_id", "namespace"],
            ["eval_comparisons.id", "eval_comparisons.namespace"],
            name="fk_optimization_candidate_comparison_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["study_id", "namespace"],
            ["optimization_studies.id", "optimization_studies.namespace"],
            name="fk_optimization_candidate_study_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_optimization_candidate_id_namespace"),
        sa.UniqueConstraint(
            "study_id", "agent_version_id", name="uq_optimization_candidate_version"
        ),
        sa.UniqueConstraint("study_id", "rank", name="uq_optimization_candidate_rank"),
    )
    op.create_table(
        "optimization_recommendations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.JSON(), nullable=False),
        sa.Column("requires_human_approval", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("recommendation_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "disposition IN ('recommend_for_human_review','do_not_recommend')",
            name="ck_optimization_recommendation_disposition",
        ),
        sa.CheckConstraint(
            "length(recommendation_hash) = 64", name="ck_optimization_recommendation_hash"
        ),
        sa.CheckConstraint("requires_human_approval", name="ck_optimization_recommendation_human"),
        sa.ForeignKeyConstraint(
            ["candidate_id", "namespace"],
            ["optimization_candidates.id", "optimization_candidates.namespace"],
            name="fk_optimization_recommendation_candidate_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["study_id", "namespace"],
            ["optimization_studies.id", "optimization_studies.namespace"],
            name="fk_optimization_recommendation_study_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "study_id", "candidate_id", name="uq_optimization_recommendation_candidate"
        ),
    )
    op.create_table(
        "release_candidates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("evaluation_attestation_id", sa.UUID(), nullable=False),
        sa.Column("optimization_study_id", sa.UUID(), nullable=True),
        sa.Column("environment_manifest", sa.JSON(), nullable=False),
        sa.Column("rollout_plan", sa.JSON(), nullable=False),
        sa.Column("release_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(release_hash) = 64", name="ck_release_candidate_hash"),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "namespace"],
            ["agent_versions.id", "agent_versions.namespace"],
            name="fk_release_candidate_agent_version_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_attestation_id", "namespace"],
            ["evaluation_attestations.id", "evaluation_attestations.namespace"],
            name="fk_release_candidate_eval_attestation_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["optimization_study_id", "namespace"],
            ["optimization_studies.id", "optimization_studies.namespace"],
            name="fk_release_candidate_study_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_release_candidate_id_namespace"),
        sa.UniqueConstraint(
            "namespace",
            "barrier_scope",
            "name",
            "version",
            name="uq_release_candidate_scope_name_version",
        ),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "release_hash", name="uq_release_candidate_scope_hash"
        ),
    )
    op.create_index(
        "ix_release_candidate_scope_page",
        "release_candidates",
        ["namespace", "barrier_group", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "release_attestations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=16), server_default="0.1", nullable=False),
        sa.Column("release_candidate_id", sa.UUID(), nullable=False),
        sa.Column("evaluation_attestation_id", sa.UUID(), nullable=False),
        sa.Column("approval_attestation_ids", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("signature_algorithm", sa.String(length=32), nullable=False),
        sa.Column("signing_key_id", sa.String(length=255), nullable=False),
        sa.Column("signing_public_key", sa.Text(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("attested_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version = '0.1'", name="ck_release_attestation_version"),
        sa.CheckConstraint("signature_algorithm = 'ed25519'", name="ck_release_attestation_alg"),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_release_attestation_hash"),
        sa.ForeignKeyConstraint(
            ["evaluation_attestation_id", "namespace"],
            ["evaluation_attestations.id", "evaluation_attestations.namespace"],
            name="fk_release_attestation_eval_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_candidate_id", "namespace"],
            ["release_candidates.id", "release_candidates.namespace"],
            name="fk_release_attestation_candidate_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_release_attestation_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "payload_hash", name="uq_release_attestation_scope_hash"
        ),
        sa.UniqueConstraint("release_candidate_id", name="uq_release_attestation_candidate"),
    )
    op.create_index(
        "ix_release_attestation_scope_page",
        "release_attestations",
        ["namespace", "barrier_group", "attested_at", "id"],
        unique=False,
    )
    op.create_table(
        "improvement_deployments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("release_attestation_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("traffic_percentage", sa.Float(), nullable=False),
        sa.Column("environment", sa.String(length=255), nullable=False),
        sa.Column("external_deployment_ref_hash", sa.String(length=64), nullable=False),
        sa.Column("prior_deployment_id", sa.UUID(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("deployment_hash", sa.String(length=64), nullable=False),
        sa.Column("recorded_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("stage IN ('shadow','canary','production')", name="ck_deployment_stage"),
        sa.CheckConstraint(
            "status IN ('observed','healthy','failed')", name="ck_deployment_status"
        ),
        sa.CheckConstraint(
            "length(external_deployment_ref_hash) = 64 AND length(deployment_hash) = 64",
            name="ck_deployment_hashes",
        ),
        sa.CheckConstraint(
            "traffic_percentage >= 0 AND traffic_percentage <= 100",
            name="ck_deployment_traffic_percentage",
        ),
        sa.ForeignKeyConstraint(
            ["prior_deployment_id", "namespace"],
            ["improvement_deployments.id", "improvement_deployments.namespace"],
            name="fk_deployment_prior_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_attestation_id", "namespace"],
            ["release_attestations.id", "release_attestations.namespace"],
            name="fk_deployment_release_attestation_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "namespace", name="uq_improvement_deployment_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "deployment_hash", name="uq_deployment_scope_hash"
        ),
    )
    op.create_index(
        "ix_deployment_scope_time",
        "improvement_deployments",
        ["namespace", "barrier_group", "deployed_at", "id"],
        unique=False,
    )
    op.create_table(
        "improvement_rollbacks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("barrier_group", sa.String(length=255), nullable=True),
        sa.Column("barrier_scope", sa.String(length=64), nullable=False),
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("target_deployment_id", sa.UUID(), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("external_rollback_ref_hash", sa.String(length=64), nullable=False),
        sa.Column("rollback_hash", sa.String(length=64), nullable=False),
        sa.Column("recorded_by_principal_ref", sa.String(length=512), nullable=False),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "deployment_id <> target_deployment_id", name="ck_rollback_distinct_target"
        ),
        sa.CheckConstraint(
            "length(external_rollback_ref_hash) = 64 AND length(rollback_hash) = 64",
            name="ck_rollback_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id", "namespace"],
            ["improvement_deployments.id", "improvement_deployments.namespace"],
            name="fk_rollback_deployment_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_deployment_id", "namespace"],
            ["improvement_deployments.id", "improvement_deployments.namespace"],
            name="fk_rollback_target_namespace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deployment_id", name="uq_rollback_deployment_once"),
        sa.UniqueConstraint("id", "namespace", name="uq_improvement_rollback_id_namespace"),
        sa.UniqueConstraint(
            "namespace", "barrier_scope", "rollback_hash", name="uq_rollback_scope_hash"
        ),
    )
    op.create_index(
        "ix_rollback_scope_time",
        "improvement_rollbacks",
        ["namespace", "barrier_group", "rolled_back_at", "id"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        _install_postgresql_boundaries()


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS public.lians_improvement_reject_mutation()")
