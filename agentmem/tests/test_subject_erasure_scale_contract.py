"""Static contracts for the durable, bounded subject-erasure boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "lians"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_erasure_request_is_durable_irreversible_and_idempotent() -> None:
    service = _source(SRC / "subject_erasure_service.py")
    routes = _source(SRC / "api" / "routes_privacy.py")

    assert "await lock_subject_key_for_update" in service
    assert "await acquire_namespace_cache_lock" in service
    assert "await invalidate_namespace" in service
    assert "await destroy_subject_key" in service
    assert "snapshot_memory_count" in service
    assert "UniqueConstraint(\"namespace\", \"subject_ref\"" in _source(
        SRC / "subject_erasure_models.py"
    )
    assert "status_code=status.HTTP_202_ACCEPTED" in routes
    assert 'alias="Idempotency-Key"' in routes
    assert "reject_non_replayable_idempotency_key" not in routes


def test_worker_and_certificate_are_bounded_and_exact() -> None:
    service = _source(SRC / "subject_erasure_service.py")
    legacy = _source(SRC / "memory_service.py")
    schemas = _source(SRC / "schemas.py")

    assert ".limit(page_size)" in service
    assert "max_pages" in service
    assert "SubjectErasureMemoryEvidence" in service
    assert "select(func.count(SubjectErasureMemoryEvidence.memory_id))" in service
    assert ".limit(bounded_limit + 1)" in service
    assert "bounded_limit = max(1, min(500, limit))" in service
    assert "verify_chain" not in service
    certificate_block = legacy.split("async def get_erasure_certificate", 1)[1]
    assert "select(EventLog)" not in certificate_block
    assert ".scalars().all()" not in certificate_block
    assert "content_hashes: list[str] = Field(max_length=500)" in schemas


def test_worker_is_readiness_and_metrics_visible() -> None:
    main = _source(SRC / "main.py")
    metrics = _source(SRC / "metrics.py")
    observability = _source(SRC / "observability_service.py")

    assert "run_subject_erasure_worker" in main
    assert 'checks["subject_erasure_worker"]' in main
    assert "validate_subject_erasure_worker_configuration" in main
    assert "lians_subject_erasure_jobs" in metrics
    assert "set_subject_erasure_inventory" in observability


def test_migration_and_cache_fence_contracts_are_present() -> None:
    migration = _source(
        ROOT / "alembic" / "versions" / "0059_subject_erasure_jobs.py"
    )
    cache = _source(SRC / "cache.py")
    memory = _source(SRC / "memory_service.py")

    assert 'revision = "0059_subject_erasure_jobs"' in migration
    assert 'down_revision = "0058a_live_supersession_indexes"' in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "subject_erasure_memory_evidence" in migration
    assert "lians_subject_erasure_evidence_immutable" in migration
    assert "_namespace_generation_key" in cache
    assert "async def invalidate_namespace" in cache
    assert "acquire_namespace_cache_lock(db, namespace, shared=True)" in memory
