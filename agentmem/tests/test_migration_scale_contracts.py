"""Static contracts for restart-safe, flagship-scale Alembic revisions.

These tests intentionally inspect migration source. Database behavior belongs to
the deferred PostgreSQL migration campaign, where lock, cancellation, resume,
RLS, and concurrent-index failure states can be exercised realistically.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "alembic" / "versions"


def _source(name: str) -> str:
    return (VERSIONS / name).read_text(encoding="utf-8")


def _revision_parent(name: str) -> tuple[str, str | None]:
    source = _source(name)
    revision = re.search(r'^revision = "([^"]+)"', source, re.MULTILINE)
    parent = re.search(r'^down_revision = "([^"]+)"', source, re.MULTILINE)
    assert revision is not None
    return revision.group(1), parent.group(1) if parent is not None else None


def test_alembic_runs_each_revision_in_its_own_transaction() -> None:
    env = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert env.count("transaction_per_migration=True") == 2


def test_revision_ids_fit_the_postgresql_version_table() -> None:
    for path in VERSIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        match = re.search(r'^revision = "([^"]+)"', source, re.MULTILINE)
        if match is not None:
            assert len(match.group(1)) <= 32, (path.name, match.group(1))


def test_system_time_backfill_is_bounded_resumable_and_online_only() -> None:
    source = _source("0025a_system_time_backfill.py")
    assert "LIMIT :batch_size" in source
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "autocommit_block" in source
    assert "CREATE INDEX CONCURRENTLY" in source
    assert "indisvalid" in source
    assert "VALIDATE CONSTRAINT ck_0025a_system_valid_from_present" in source
    assert "requires an online PostgreSQL " in source
    assert "connection so each bounded page" in source
    assert "def _postgresql_offline" not in source


def test_evidence_graph_expand_and_data_revisions_are_separate() -> None:
    expand = _source("0026_evidence_graph.py")
    data = _source("0026a_evidence_graph_backfill.py")
    recorder = _revision_parent("0027_universal_recorder.py")
    assert "_backfill()" not in expand[expand.index("def upgrade()") :]
    assert "snapshot_max_decision_id" in expand
    assert "last_decision_id" in expand
    assert "uuid.uuid5" in expand
    assert "on_conflict_do_nothing" in expand
    assert "resolved_artifact_ids" in expand
    assert "_MAX_CANDIDATES_PER_DECISION" in expand
    assert "pg_column_size" in expand
    assert "octet_length" in expand
    assert ".values(artifact_inserts)" not in expand
    assert ".values(link_inserts)" not in expand
    assert "autocommit_block" in data
    assert "requires an online PostgreSQL " in data
    assert "connection so bounded evidence pages" in data
    assert recorder[1] == "0026a_evidence_graph_backfill"


def test_gate_expand_preserves_legacy_writers_and_contract_is_online() -> None:
    expand = _source("0040_gate_execution_permits.py")
    contract = _source("0040a_gate_execution_permit_contract.py")
    integrity = _revision_parent("0041_decision_record_integrity.py")
    upgrade = expand[expand.index("def upgrade()") : expand.index("def downgrade()")]
    assert "lians_gate_fill_legacy_execution_boundary" in expand
    assert "lians.migration_gate_decision_backfill" in expand
    assert "pg_has_role(current_user, table_owner, 'USAGE')" in expand
    assert "lians:legacy-unbound" in expand
    assert "NEW.status := 'retired'" in expand
    assert "UPDATE gate_policy_sets" not in upgrade
    assert "UPDATE gate_decision_records" not in upgrade
    assert "CREATE INDEX CONCURRENTLY" in contract
    assert "FOR UPDATE SKIP LOCKED" in contract
    assert "_restore_strict_append_boundary()" in contract
    assert "VALIDATE CONSTRAINT ck_0040_gate_target_ref_present" in contract
    assert "requires an online PostgreSQL " in contract
    assert "connection so bounded legacy pages" in contract
    assert integrity[1] == "0040a_gate_permit_contract"


def test_scope_identity_updates_only_mismatches_in_atomic_pages() -> None:
    source = _source("0045_evidence_scope_identity.py")
    assert "lians_0045_canonicalize_page" in source
    assert "LIMIT batch_size" in source
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "lians:0045:temporary:" in source
    assert "CREATE UNIQUE INDEX CONCURRENTLY" in source
    assert "UNIQUE USING INDEX" in source
    assert "NOT VALID" in source
    assert "VALIDATE CONSTRAINT" in source
    assert "NEW.barrier_group IS NOT DISTINCT FROM COALESCE" in source
    assert "decision.barrier_group <> artifact.barrier_group" in source
    assert "requires an online PostgreSQL " in source
    assert "connection so selective canonicalization pages" in source
    assert ".mappings().all()" not in source
    assert "DROP TRIGGER trg_evidence_artifacts_reject_mutation" not in source


def test_auth_lookup_expand_index_and_contract_are_explicitly_fenced() -> None:
    expand = _source("0056_auth_lookup_expand.py")
    index = _source("0056a_admission_index.py")
    contract = _source("0056b_auth_lookup_contract.py")

    assert _revision_parent("0056_auth_lookup_expand.py")[1] == "0055_retention_cursor"
    assert _revision_parent("0056a_admission_index.py")[1] == "0056_auth_lookup_expand"
    assert _revision_parent("0056b_auth_lookup_contract.py")[1] == ("0056a_admission_index")
    assert "SECURITY DEFINER" in expand
    assert "SET search_path = pg_catalog, public" in expand
    assert "SET row_security = off" in expand
    assert "REVOKE ALL ON FUNCTION" in expand
    assert "CREATE INDEX CONCURRENTLY" in index
    assert "indisvalid" in index
    assert "requires an online connection" in index
    assert "namespace, status, barrier_group, created_at, id" in index
    assert "ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in contract
    assert "ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY" in contract
    assert "AS RESTRICTIVE" in contract
    assert "ck_scim_user_group_capacity" in contract
    assert "FOR UPDATE" in contract
    assert "existing SCIM User Group membership exceeds" in contract


def test_decision_v3_authorization_snapshot_is_database_bounded() -> None:
    source = _source("0057_decision_auth_snapshot.py")

    assert _revision_parent("0057_decision_auth_snapshot.py")[1] == ("0056b_auth_lookup_contract")
    assert "record_hash_version IN (1, 2, 3)" in source
    assert "recorded_by_principal_type" in source
    assert "recorded_by_role" in source
    assert "recorded_by_scopes" in source
    assert "lians_decision_authorization_scopes_valid" in source
    assert "_SCOPE_LIMIT = 50" in source
    assert "jsonb_array_length(scopes) > {_SCOPE_LIMIT}" in source
    assert "scope_value = ANY (seen_scopes)" in source
    assert "scope_value = 'write'" in source
    assert "DecisionRecord authorization snapshot is immutable" in source
    assert 'versions = "IN (2, 3)" if allow_v3 else "= 2"' in source
    assert "decision.record_hash_version {versions}" in source
    assert "0057 downgrade refused: DecisionRecord v3 rows exist" in source
    assert "sqlite_master" in source
