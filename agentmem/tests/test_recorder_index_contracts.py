"""Static production contracts for durable Recorder evidence back-linking."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIANS = ROOT / "src" / "lians"
MIGRATIONS = ROOT / "alembic" / "versions"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _revision(path: Path) -> tuple[str, str]:
    tree = ast.parse(_read(path))
    values: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"revision", "down_revision"}
            and isinstance(node.value, ast.Constant)
        ):
            values[node.targets[0].id] = str(node.value.value)
    return values["revision"], values["down_revision"]


def test_fixed_snapshot_job_replaces_the_synchronous_rejection_cliff() -> None:
    service = _read(LIANS / "recorder_service.py")
    routes = _read(LIANS / "api" / "routes_decisions.py")
    assert "if total > _DECISION_RECORDER_INDEX_LIMIT:" in service
    assert "await _enqueue_recorder_evidence_index_job(" in service
    assert "snapshot_max_recorded_at=boundary[0]" in service
    assert "snapshot_max_event_id=boundary[1]" in service
    assert "recorder_evidence_synchronous_limit_exceeded" not in routes
    assert "RecorderEvidenceIndexLimitExceeded" not in service


def test_job_pages_are_leased_bounded_integrity_checked_and_atomic() -> None:
    worker = _read(LIANS / "recorder_index_service.py")
    recorder = _read(LIANS / "recorder_service.py")
    for contract in (
        ".with_for_update(skip_locked=",
        ".limit(page_size)",
        "await index_recorder_rows_batch(",
        "job.events_indexed += len(rows)",
        "job.cursor_event_id = rows[-1].id",
        "await db.commit()",
        "job.events_indexed == job.snapshot_event_count",
        "job.cursor_event_id != job.snapshot_max_event_id",
    ):
        assert contract in worker
    assert "await assert_recorder_events_integrity(db, rows)" in recorder
    assert "ensure_artifacts_bulk" in recorder
    assert "ensure_links_bulk" in recorder
    assert "_RECORDER_EVIDENCE_BULK_PAGE_SIZE = 500" in recorder


def test_coverage_stays_partial_until_exact_completion_or_retry() -> None:
    recorder = _read(LIANS / "recorder_service.py")
    worker = _read(LIANS / "recorder_index_service.py")
    assert 'gaps.add("recorder_index_pending")' in recorder
    assert 'gaps.add("recorder_index_failed")' in recorder
    assert 'row.status = "partial" if gaps else "complete"' in recorder
    assert 'state="completed"' in worker
    assert 'state="failed"' in worker
    assert "retry_recorder_index_job" in worker


def test_database_fence_rls_guards_and_online_indexes_are_release_ordered() -> None:
    expand = MIGRATIONS / "0058_recorder_index_jobs.py"
    indexes = MIGRATIONS / "0058a_live_supersession_indexes.py"
    assert _revision(expand) == (
        "0058_recorder_index_jobs",
        "0057_decision_auth_snapshot",
    )
    assert _revision(indexes) == (
        "0058a_live_supersession_indexes",
        "0058_recorder_index_jobs",
    )
    expand_source = _read(expand)
    for contract in (
        "lians_recorder_decision_fence",
        "BEFORE INSERT ON public.decision_records",
        "BEFORE INSERT ON public.recorder_events",
        "FORCE ROW LEVEL SECURITY",
        "AS RESTRICTIVE",
        "SKIP",
        "trg_recorder_index_job_reject_delete",
        "trg_recorder_index_job_reject_truncate",
    ):
        if contract != "SKIP":
            assert contract in expand_source
    index_source = _read(indexes)
    for contract in (
        "CREATE INDEX CONCURRENTLY ix_recorder_event_decision_snapshot",
        "CREATE INDEX CONCURRENTLY ix_recorder_event_run_page",
        "CREATE INDEX CONCURRENTLY ix_memories_subject_erasure_page",
        "CREATE INDEX CONCURRENTLY ix_live_facts_subject_erasure_page",
        "CREATE INDEX CONCURRENTLY ix_relationships_subject_erasure_page",
        "CREATE INDEX CONCURRENTLY ix_pending_admissions_subject_erasure_page",
        "CREATE INDEX CONCURRENTLY ix_event_log_recorder_binding",
        "CREATE INDEX CONCURRENTLY ix_memories_supersession_live",
        "CREATE INDEX CONCURRENTLY ix_relationships_exclusive_live",
        "CREATE INDEX CONCURRENTLY ix_ledger_event_scope_page",
        "CREATE INDEX CONCURRENTLY ix_decision_record_scope_page",
        "CREATE INDEX CONCURRENTLY ix_evidence_artifact_scope_page",
        "DROP INDEX CONCURRENTLY IF EXISTS",
        "context.is_offline_mode()",
        "indisvalid",
    ):
        assert contract in index_source
    assert "(namespace, subject_id, erased_at, id)" not in index_source


def test_worker_is_required_and_supervised_in_production() -> None:
    config = _read(LIANS / "config.py")
    main = _read(LIANS / "main.py")
    assert "recorder_evidence_index_worker_enabled: bool = True" in config
    assert "production and not settings.recorder_evidence_index_worker_enabled" in _read(
        LIANS / "recorder_index_service.py"
    )
    assert '"recorder-evidence-index-worker"' in main
    assert "recorder_index_worker_status" in main


def test_both_python_sdks_and_typescript_expose_job_and_run_page_contracts() -> None:
    repository = ROOT.parent
    canonical_client = _read(repository / "sdk" / "python" / "src" / "lians" / "client.py")
    compatibility_client = _read(ROOT / "sdk" / "python" / "lians" / "client.py")
    compatibility_sync = _read(ROOT / "sdk" / "python" / "lians" / "sync_client.py")
    typescript = _read(ROOT / "sdk" / "typescript" / "src" / "client.ts")
    for source in (canonical_client, compatibility_client):
        assert "recorder_run_events_page" in source
        assert "recorder_evidence_index_job_for_decision" in source
        assert "retry_recorder_evidence_index_job" in source
    assert "recorder_run_events_page" in compatibility_sync
    assert "recorder_evidence_index_job_for_decision" in compatibility_sync
    assert "recorderRunEventsPage" in typescript
    assert "recorderEvidenceIndexJobForDecision" in typescript
