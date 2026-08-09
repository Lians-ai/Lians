"""
Test fixtures: in-memory SQLite-equivalent via async SQLAlchemy.
We use an in-process PG via pytest-postgresql or a real local PG for integration tests.
For unit tests we mock the DB session with an in-memory approach.
"""
import os
import importlib.util
import subprocess
import time
from pathlib import Path

import lians.kms as _kms
import pytest
import pytest_asyncio
from lians.config import Settings, get_settings
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Determinism guard: a developer's local `agentmem/.env` (e.g. a Docker-stack
# env with a real MASTER_ENCRYPTION_KEY, cache/rate-limit/WORM settings) must
# never leak into the test run — it changes crypto, caching, and audit-chain
# behavior, so the suite fails on machines where it passes everywhere else.
# Point pydantic-settings at a nonexistent env file and scrub the same
# variables from the process environment before any Settings() is built.
# Tests that need specific values set them explicitly (monkeypatch/fixtures).
# ---------------------------------------------------------------------------
Settings.model_config["env_file"] = "__lians_tests_ignore_dotenv__"
for _var in (
    "MASTER_ENCRYPTION_KEY", "MASTER_ENCRYPTION_KEY_PREVIOUS", "SUBJECT_REFERENCE_KEY",
    "MASTER_KEY_ID", "MASTER_KEY_PREVIOUS_ID", "KMS_PROVIDER", "EMBEDDING_PROVIDER",
    "KMS_AWS_PREVIOUS_KEY_ID", "KMS_AWS_PREVIOUS_REGION",
    "KMS_AWS_PREVIOUS_ENCRYPTED_KEY", "KMS_AZURE_PREVIOUS_VAULT_URL",
    "KMS_AZURE_PREVIOUS_SECRET_NAME", "KMS_VAULT_PREVIOUS_ADDR",
    "KMS_VAULT_PREVIOUS_PATH", "KMS_VAULT_PREVIOUS_MOUNT_POINT",
    "RATE_LIMIT_PER_MINUTE", "TRUSTED_PROXY_CIDRS", "RECALL_CACHE_ENABLED", "WORM_MODE",
    "ADMISSION_MODE", "SIEM_URL", "STRIPE_API_KEY",
    "STRIPE_METER_DECISION_EVENT", "STRIPE_METER_PROTECTED_ACTION_EVENT",
    "STRIPE_METER_WRITE_EVENT", "STRIPE_METER_RECALL_EVENT", "AIRGAP_MODE",
    "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_SERVICE_NAME", "DATABASE_URL", "REDIS_URL",
    "GRAPH_EXTRACT_LLM", "AUTO_METADATA_LLM",
    "RECALL_RERANKER_MODEL", "RECALL_RERANKER_ONNX_MODEL",
    "RECALL_RERANKER_ONNX_TOKENIZER", "RECALL_RERANKER_PREFETCH",
    "RECALL_RERANKER_BATCH_SIZE", "RECALL_RERANKER_MAX_LENGTH",
    "RECALL_RERANKER_ORT_THREADS", "RECALL_RERANKER_PRIMARY_LEXICAL",
    "RECEIPT_SIGNING_PROVIDER", "RECEIPT_SIGNING_PRIVATE_KEY",
    "RECEIPT_SIGNING_KEY_ID", "RECEIPT_VAULT_ADDR", "RECEIPT_VAULT_TOKEN",
    "RECEIPT_VAULT_TOKEN_FILE", "RECEIPT_VAULT_NAMESPACE",
    "RECEIPT_VAULT_MOUNT_POINT", "RECEIPT_VAULT_KEY_NAME",
    "RECEIPT_VAULT_KEY_VERSION", "RECEIPT_VAULT_PUBLIC_KEY",
    "RECEIPT_VAULT_TIMEOUT_SECONDS", "API_SURFACE",
    "IMPACT_ASSESSMENT_WORKER_ENABLED", "IMPACT_ASSESSMENT_WORKER_POLL_SECONDS",
    "IMPACT_ASSESSMENT_WORKER_BATCH_SIZE", "IMPACT_ASSESSMENT_WORKER_CONCURRENCY",
    "IMPACT_ASSESSMENT_WORKER_LEASE_SECONDS", "IMPACT_ASSESSMENT_WORKER_PAGE_SIZE",
    "IMPACT_ASSESSMENT_WORKER_MAX_PAGES_PER_CLAIM",
    "IMPACT_ASSESSMENT_WORKER_RETRY_BASE_SECONDS",
    "IMPACT_ASSESSMENT_WORKER_RETRY_MAX_SECONDS",
    "IMPACT_ASSESSMENT_WORKER_MAX_ATTEMPTS",
):
    os.environ.pop(_var, None)
os.environ["API_SURFACE"] = "all"
get_settings.cache_clear()

_COMPOSE_DIR = Path(__file__).parent.parent
_DB_URL = "postgresql+asyncpg://agentmem:agentmem@localhost:5432/agentmem"


def _install_sqlite_validmind_source_triggers(connection) -> None:
    """Install the production projection triggers on metadata-created test DBs."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration_path = (
        _COMPOSE_DIR / "alembic" / "versions" / "0054_otel_barrier.py"
    )
    spec = importlib.util.spec_from_file_location(
        "lians_test_migration_0054_otel_barrier",
        migration_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load ValidMind trigger migration: {migration_path}")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = Operations(MigrationContext.configure(connection))
    migration._install_sqlite_validmind_source_triggers(otel_barrier=True)


def pytest_configure(config):
    """
    Auto-provision a pgvector Postgres container when Docker is available so
    test_pgvector.py tests run without any manual setup.  Called before test
    collection, so the module-level pytestmark skip-condition in test_pgvector.py
    sees TEST_DATABASE_URL already set.
    """
    if os.environ.get("TEST_DATABASE_URL"):
        return  # already provided externally

    # Fast-fail: check Docker daemon reachability (3-second timeout)
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=3)
        if r.returncode != 0:
            return
    except Exception:
        return

    compose_file = _COMPOSE_DIR / "docker-compose.yml"
    if not compose_file.exists():
        return

    # Bring up only the postgres service (idempotent if already running).
    # A half-started Docker daemon can hang any of these calls — treat every
    # failure (including TimeoutExpired) as "no Docker" and skip provisioning
    # rather than crashing collection with an INTERNALERROR.
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "up", "-d", "postgres"],
            capture_output=True,
            timeout=60,
            cwd=str(_COMPOSE_DIR),
        )
    except Exception:
        return

    # Wait up to 30 s for Postgres to accept connections
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            r = subprocess.run(
                ["docker", "compose", "-f", str(compose_file),
                 "exec", "-T", "postgres", "pg_isready", "-U", "agentmem"],
                capture_output=True,
                timeout=5,
                cwd=str(_COMPOSE_DIR),
            )
        except Exception:
            return
        if r.returncode == 0:
            break
        time.sleep(1)
    else:
        return  # timed out â€” skip gracefully

    # The database-owned audit append boundary grants its narrowly scoped
    # function only to this fixed NOLOGIN capability role. The disposable
    # Docker test login is a member so PostgreSQL integration tests exercise
    # the same ACL path as a production runtime login.
    role_sql = """
    DO $lians$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lians_runtime') THEN
        CREATE ROLE lians_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS;
      END IF;
      IF EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'lians_runtime'
          AND (rolcanlogin OR rolsuper OR rolbypassrls)
      ) THEN
        RAISE EXCEPTION 'lians_runtime has unsafe role attributes';
      END IF;
    END
    $lians$;
    GRANT lians_runtime TO agentmem;
    """
    subprocess.run(
        [
            "docker", "compose", "-f", str(compose_file),
            "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1",
            "-U", "agentmem", "-d", "agentmem", "-c", role_sql,
        ],
        capture_output=True,
        timeout=30,
        cwd=str(_COMPOSE_DIR),
    )

    # Run migrations (no-op when already at head)
    env = {**os.environ, "DATABASE_URL": _DB_URL}
    subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        timeout=60,
        cwd=str(_COMPOSE_DIR),
        env=env,
    )

    os.environ["TEST_DATABASE_URL"] = _DB_URL


# Override settings for tests
@pytest.fixture(autouse=True)
def test_settings(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", "")
    monkeypatch.setenv("KMS_PROVIDER", "env")
    monkeypatch.setenv("AGENTMEM_ALLOW_UNENCRYPTED", "true")
    monkeypatch.setenv("RLS_BARRIERS_ENABLED", "false")  # SQLite has no RLS
    # Unit tests do not provision Redis. Cache-coherent PostgreSQL writes fail
    # closed when their generation fence cannot advance, so tests that exercise
    # the cache enable it explicitly with a fake Redis backend.
    monkeypatch.setenv("RECALL_CACHE_ENABLED", "false")
    get_settings.cache_clear()
    _kms._reset_cache()
    yield
    get_settings.cache_clear()
    _kms._reset_cache()


@pytest_asyncio.fixture
async def db():
    """SQLite in-memory async session for unit tests (no pgvector)."""

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _register_validmind_functions(dbapi_connection, _connection_record):
        from lians.validmind_inventory import (
            validmind_external_id,
            validmind_legacy_model_id,
        )

        dbapi_connection.create_function(
            "lians_external_id",
            3,
            validmind_external_id,
            deterministic=True,
        )
        dbapi_connection.create_function(
            "lians_legacy_model_id",
            1,
            validmind_legacy_model_id,
            deterministic=True,
        )

    # Drop PG-only indexes before table creation so SQLite doesn't choke
    from lians.models import Base as AppBase
    pg_indexes = [
        idx for table in AppBase.metadata.tables.values()
        for idx in table.indexes
        if idx.dialect_kwargs.get("postgresql_using") not in (None, False)
    ]
    for idx in pg_indexes:
        idx.table.indexes.discard(idx)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(AppBase.metadata.create_all)
            await conn.run_sync(_install_sqlite_validmind_source_triggers)
    finally:
        # SQLAlchemy metadata is process-global. Restore detached indexes before
        # yielding so one SQLite fixture cannot silently weaken later schemas.
        for idx in pg_indexes:
            idx.table.indexes.add(idx)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()
