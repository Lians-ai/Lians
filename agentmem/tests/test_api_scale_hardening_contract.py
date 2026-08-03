"""Deferred source contracts for bounded public inventory paths."""

from __future__ import annotations

import inspect
from pathlib import Path

from lians.api.routes_compliance import compliance_report
from lians.api.routes_control import (
    _policy_match_expressions,
    close_investigation_case,
    list_gate_policies,
    list_issuers,
    list_remediation_tasks,
    list_trusted_keys,
    revoke_issuer,
    update_remediation_task,
)
from lians.api.routes_validmind import (
    ValidMindModelMetadata,
    ValidMindModelOut,
    _model_record,
    _model_records,
    get_validmind_model,
    update_validmind_model,
)


def test_inventory_routes_expose_bounded_pagination() -> None:
    for route in (
        list_issuers,
        list_trusted_keys,
        list_remediation_tasks,
        list_gate_policies,
    ):
        parameters = inspect.signature(route).parameters
        assert {"offset", "limit"}.issubset(parameters)


def test_validmind_filters_and_pages_in_sql() -> None:
    list_source = inspect.getsource(_model_records)
    lookup_source = inspect.getsource(_model_record)
    assert ".offset(offset)" in list_source
    assert ".limit(limit)" in list_source
    assert "validmind_catalog_keys" in list_source
    assert "offset + limit" in list_source
    assert "ValidMindModelInventory" in list_source
    assert "ValidMindModelInventory" in lookup_source
    assert "ValidMindLegacyModelAlias" in lookup_source
    assert "DecisionRecord" not in list_source
    assert "OTelSpan" not in list_source
    assert "DecisionRecord" not in lookup_source
    assert "OTelSpan" not in lookup_source
    assert "_model_records" not in inspect.getsource(get_validmind_model)
    assert "_model_records" not in inspect.getsource(update_validmind_model)


def test_validmind_model_schema_discloses_version_completeness() -> None:
    assert {
        "id",
        "name",
        "status",
        "resource_type",
        "metadata",
        "created_at",
        "updated_at",
    } == set(ValidMindModelOut.model_fields)
    assert {
        "versions",
        "versions_total",
        "versions_complete",
        "versions_limit",
        "decision_count",
        "genai_span_count",
        "lians_scope_id",
    }.issubset(ValidMindModelMetadata.model_fields)


def test_compliance_counts_events_in_sql() -> None:
    source = inspect.getsource(compliance_report)
    assert "select(EventLog)" not in source
    assert "func.count(func.distinct(subject_ref))" in source
    assert 'EventLog.payload["confidence"].as_float()' in source


def test_issuer_revoke_is_a_bounded_set_update() -> None:
    source = inspect.getsource(revoke_issuer)
    assert "_MAX_ISSUER_KEYS_PER_REVOCATION" in source
    assert "update(TrustedReceiptKey)" in source
    assert "active_key_count" in source
    assert ".scalars().all()" not in source


def test_case_close_uses_parent_lock_and_sql_count() -> None:
    close_source = inspect.getsource(close_investigation_case)
    task_source = inspect.getsource(update_remediation_task)
    assert "select(func.count())" in close_source
    assert "outstanding_task_count" in close_source
    assert "select(RemediationTask)" not in close_source
    assert "_lock_task_and_parent_case" in task_source


def test_scale_indexes_include_opaque_id_and_inventory_paths() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0052_api_scale_indexes.py"
    ).read_text(encoding="utf-8")
    for index_name in (
        "ix_decision_validmind_model_inventory",
        "ix_otel_validmind_model_inventory",
        "ix_event_log_compliance_op_time",
        "ix_conflict_validmind_ticket_list",
        "ix_receipt_issuer_list",
        "ix_receipt_issuer_all_list",
        "ix_trusted_key_issuer_list",
        "ix_trusted_key_issuer_all_list",
        "ix_remediation_task_case_status_list",
        "ix_remediation_task_case_list",
        "ix_decision_validmind_external_id",
        "ix_otel_validmind_external_id",
        "ix_agent_validmind_external_id",
        "ix_gate_policy_protected_actions_gin",
        "ix_gate_policy_target_selectors_gin",
    ):
        assert index_name in migration
    assert "CREATE INDEX CONCURRENTLY" in migration
    assert "pg_index" in migration
    assert "indisvalid" in migration
    assert "DROP INDEX CONCURRENTLY" in migration
    assert "public.lians_sha256_text" in migration
    assert "convert_to(" not in migration
    assert "lians_validmind_lookup_agent" in migration
    assert "SECURITY DEFINER" in migration
    assert "LIMIT 2" in migration


def test_validmind_inventory_migrations_are_scoped_exact_and_resumable() -> None:
    versions = Path(__file__).parents[1] / "alembic" / "versions"
    expand = (versions / "0053_validmind_inventory.py").read_text(encoding="utf-8")
    backfill = (versions / "0053a_validmind_backfill.py").read_text(
        encoding="utf-8"
    )
    for contract in (
        "validmind_barrier_scopes",
        "validmind_model_inventory",
        "validmind_model_versions",
        "validmind_legacy_model_aliases",
        "validmind_inventory_counted",
        "lians_validmind_inventory_adjust",
        "lians_validmind_source_change",
        "FORCE ROW LEVEL SECURITY",
        "legacy_external_id",
        "uq_validmind_inventory_external_id",
    ):
        assert contract in expand
    assert "to_jsonb(NEW)->>'barrier_group'" in expand
    assert "p_delta NOT IN (-1, 1)" in expand
    assert "version_count = version_count - 1" in expand
    assert "min(bounds.first_at)" in expand
    assert "max(bounds.last_at)" in expand
    assert "ORDER BY decision.recorded_at DESC" in expand
    assert "0053a_validmind_backfill requires an online connection" in backfill
    assert "autocommit_block" in backfill
    assert "FOR UPDATE" in backfill
    assert "VALIDATE CONSTRAINT" in backfill
    assert "lians_validmind_canonicalize_link" in backfill
    assert "ix_decision_validmind_scope_bounds" in backfill
    assert "CREATE INDEX CONCURRENTLY" in backfill
    assert "BEFORE INSERT OR UPDATE OR DELETE" in backfill
    assert "pg_trigger_depth() > 1" in backfill
    assert "pg_try_advisory_xact_lock" in backfill
    assert "ERRCODE = '40001'" in backfill
    assert "INSERT INTO public.validmind_model_links" in backfill
    assert "DELETE FROM public.validmind_model_links AS counterpart" in backfill
    assert "trg_validmind_link_sync_delete" in backfill
    assert "lians_validmind_sync_unique_alias_link" in backfill
    assert "lians_validmind_cleanup_scoped_link" in backfill
    assert "trg_validmind_alias_link_sync_update" in backfill
    assert "AFTER INSERT OR DELETE OR UPDATE OF" in backfill
    assert "trg_validmind_alias_identity_guard" in backfill
    assert "trg_validmind_alias_link_sync_delete" in backfill
    assert "trg_validmind_inventory_link_repair" in backfill
    assert "v_legacy_exists AND v_canonical_exists" in backfill
    assert "ELSIF v_canonical_exists" in backfill
    assert "TG_OP = 'DELETE' OR v_target_count > 1" in backfill
    assert "ValidMind legacy identifier collides with a scoped identifier" in backfill
    assert "_remove_postgres_ambiguous_link_page" in backfill
    assert "_assert_postgres_no_link_identifier_collisions" in backfill
    assert "_mirror_postgres_canonical_link_page" in backfill
    assert "_assert_postgres_link_transition_invariants" in backfill
    assert "alias_inventory_mismatch" in backfill
    assert "inventory_alias_missing" in backfill
    assert "_remove_sqlite_ambiguous_links" in backfill
    assert "_assert_sqlite_no_link_identifier_collisions" in backfill
    assert "_mirror_sqlite_canonical_links" in backfill
    assert "_assert_sqlite_link_transition_invariants" in backfill


def test_gate_hot_path_uses_database_selector_matching() -> None:
    source = inspect.getsource(_policy_match_expressions)
    assert 'op("?")' in source
    assert 'op("?|")' in source
    assert "jsonb_array_elements_text" in source
