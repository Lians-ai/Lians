"""PostgreSQL-only integrity boundary contracts for durable metering."""

from __future__ import annotations

import inspect
import os
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from lians.metering import claim_due_metering_events
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


def test_migration_declares_rls_acl_and_immutability_contracts() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0044_durable_metering.py"
    )
    spec = spec_from_file_location("migration_0044_durable_metering", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "0044_durable_metering"
    assert migration.down_revision == "0043_evidence_impact_jobs"
    source = inspect.getsource(migration._install_postgres_guards)
    for contract in (
        "FORCE ROW LEVEL SECURITY",
        "metering event identity fields are immutable",
        "delivered metering events are immutable",
        "metering_attempt_records is append-only",
        "durable metering tables cannot be truncated",
        "GRANT SELECT, INSERT, UPDATE ON metering_events TO lians_runtime",
        "GRANT SELECT, INSERT ON metering_attempt_records TO lians_runtime",
        "REVOKE DELETE, TRUNCATE",
    ):
        assert contract in source
    claim_source = inspect.getsource(claim_due_metering_events)
    assert "with_for_update" in claim_source
    assert "skip_locked" in claim_source


TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
PG_AVAILABLE = bool(TEST_DB_URL and "postgresql" in TEST_DB_URL)
requires_postgres = pytest.mark.skipif(
    not PG_AVAILABLE,
    reason="TEST_DATABASE_URL is not a PostgreSQL URL",
)


@pytest_asyncio.fixture
async def metering_pg_db():
    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        await session.execute(
            text("SELECT set_config('app.current_namespace', '__admin__', true)")
        )
        await session.execute(
            text("SELECT set_config('agentmem.barrier_group', '', true)")
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def _expect_rejection(
    session: AsyncSession,
    statement: str,
    *,
    match: str,
    parameters: dict[str, object] | None = None,
) -> None:
    with pytest.raises(DBAPIError, match=match):
        async with session.begin_nested():
            await session.execute(text(statement), parameters or {})


@requires_postgres
@pytest.mark.asyncio
async def test_postgresql_metering_acl_rls_and_state_guards(
    metering_pg_db: AsyncSession,
) -> None:
    event_privileges = (
        await metering_pg_db.execute(
            text(
                """SELECT
                    has_table_privilege('lians_runtime', 'metering_events', 'SELECT')
                        AS can_select,
                    has_table_privilege('lians_runtime', 'metering_events', 'INSERT')
                        AS can_insert,
                    has_table_privilege('lians_runtime', 'metering_events', 'UPDATE')
                        AS can_update,
                    has_table_privilege('lians_runtime', 'metering_events', 'DELETE')
                        AS can_delete,
                    has_table_privilege('lians_runtime', 'metering_events', 'TRUNCATE')
                        AS can_truncate"""
            )
        )
    ).mappings().one()
    assert dict(event_privileges) == {
        "can_select": True,
        "can_insert": True,
        "can_update": True,
        "can_delete": False,
        "can_truncate": False,
    }
    attempt_privileges = (
        await metering_pg_db.execute(
            text(
                """SELECT
                    has_table_privilege(
                        'lians_runtime', 'metering_attempt_records', 'SELECT'
                    ) AS can_select,
                    has_table_privilege(
                        'lians_runtime', 'metering_attempt_records', 'INSERT'
                    ) AS can_insert,
                    has_table_privilege(
                        'lians_runtime', 'metering_attempt_records', 'UPDATE'
                    ) AS can_update,
                    has_table_privilege(
                        'lians_runtime', 'metering_attempt_records', 'DELETE'
                    ) AS can_delete,
                    has_table_privilege(
                        'lians_runtime', 'metering_attempt_records', 'TRUNCATE'
                    ) AS can_truncate"""
            )
        )
    ).mappings().one()
    assert dict(attempt_privileges) == {
        "can_select": True,
        "can_insert": True,
        "can_update": False,
        "can_delete": False,
        "can_truncate": False,
    }
    rls_rows = (
        await metering_pg_db.execute(
            text(
                """SELECT relname, relrowsecurity, relforcerowsecurity
                   FROM pg_class
                   WHERE oid IN (
                       'metering_events'::regclass,
                       'metering_attempt_records'::regclass
                   )
                   ORDER BY relname"""
            )
        )
    ).all()
    assert rls_rows == [
        ("metering_attempt_records", True, True),
        ("metering_events", True, True),
    ]

    event_id = uuid4()
    started_id = uuid4()
    finished_id = uuid4()
    now = datetime.now(UTC)
    values = {
        "event_id": event_id,
        "namespace": "metering-integrity",
        "source_hash": "a" * 64,
        "request_hash": "b" * 64,
        "provider_identifier": f"lians_{uuid4().hex}",
        "now": now,
    }
    await metering_pg_db.execute(
        text(
            """INSERT INTO metering_events (
                   id, namespace, event_name, customer_id, quantity,
                   source_identifier_hash, request_hash, provider_identifier,
                   status, attempt_count, attempt_limit, replay_count,
                   next_attempt_at, occurred_at, created_at, updated_at
               ) VALUES (
                   :event_id, :namespace, 'agentmem_memory_write', 'cus_integrity', 1,
                   :source_hash, :request_hash, :provider_identifier,
                   'pending', 0, 3, 0, :now, :now, :now, :now
               )"""
        ),
        values,
    )
    await _expect_rejection(
        metering_pg_db,
        "UPDATE metering_events SET customer_id = 'cus_tampered' WHERE id = :event_id",
        match="identity fields are immutable",
        parameters={"event_id": event_id},
    )
    await _expect_rejection(
        metering_pg_db,
        """UPDATE metering_events
           SET status = 'delivered', delivered_at = :now
           WHERE id = :event_id""",
        match="invalid metering event status transition",
        parameters={"event_id": event_id, "now": now},
    )

    await metering_pg_db.execute(
        text(
            """UPDATE metering_events
               SET status = 'leased', lease_owner = 'worker-a',
                   lease_expires_at = :lease_expires
               WHERE id = :event_id"""
        ),
        {"event_id": event_id, "lease_expires": now + timedelta(minutes=1)},
    )
    await metering_pg_db.execute(
        text(
            """UPDATE metering_events
               SET attempt_count = 1, first_attempt_at = :now, last_attempt_at = :now
               WHERE id = :event_id"""
        ),
        {"event_id": event_id, "now": now},
    )
    await metering_pg_db.execute(
        text(
            """INSERT INTO metering_attempt_records (
                   id, namespace, event_id, attempt_number, record_type, outcome,
                   worker_id, recorded_at
               ) VALUES (
                   :id, :namespace, :event_id, 1, 'started', 'started',
                   'worker-a', :now
               )"""
        ),
        {**values, "id": started_id},
    )
    await metering_pg_db.execute(
        text(
            """UPDATE metering_events
               SET status = 'delivered', delivered_at = :now,
                   lease_owner = NULL, lease_expires_at = NULL
               WHERE id = :event_id"""
        ),
        {"event_id": event_id, "now": now},
    )
    await metering_pg_db.execute(
        text(
            """INSERT INTO metering_attempt_records (
                   id, namespace, event_id, attempt_number, record_type, outcome,
                   worker_id, status_code, response_digest, duration_ms, recorded_at
               ) VALUES (
                   :id, :namespace, :event_id, 1, 'finished', 'delivered',
                   'worker-a', 200, :response_digest, 5, :now
               )"""
        ),
        {**values, "id": finished_id, "response_digest": "c" * 64},
    )
    await _expect_rejection(
        metering_pg_db,
        "UPDATE metering_events SET updated_at = :later WHERE id = :event_id",
        match="delivered metering events are immutable",
        parameters={"event_id": event_id, "later": now + timedelta(seconds=1)},
    )
    await _expect_rejection(
        metering_pg_db,
        "UPDATE metering_attempt_records SET duration_ms = 6 WHERE id = :id",
        match="metering_attempt_records is append-only",
        parameters={"id": finished_id},
    )

    replay_event_id = uuid4()
    await metering_pg_db.execute(
        text(
            """INSERT INTO metering_events (
                   id, namespace, event_name, customer_id, quantity,
                   source_identifier_hash, request_hash, provider_identifier,
                   status, attempt_count, attempt_limit, replay_count,
                   next_attempt_at, first_attempt_at, last_attempt_at,
                   dead_lettered_at, last_status_code, last_error_code,
                   last_error_digest, occurred_at, created_at, updated_at
               ) VALUES (
                   :event_id, :namespace, 'agentmem_memory_write', 'cus_integrity', 1,
                   :source_hash, :request_hash, :provider_identifier,
                   'dead_letter', 1, 3, 0,
                   :now, :now, :now, :now, 400, 'stripe_invalid',
                   :error_digest, :now, :now, :now
               )"""
        ),
        {
            "event_id": replay_event_id,
            "namespace": "metering-replay-integrity",
            "source_hash": "d" * 64,
            "request_hash": "e" * 64,
            "provider_identifier": f"lians_{uuid4().hex}",
            "error_digest": "f" * 64,
            "now": now,
        },
    )
    await _expect_rejection(
        metering_pg_db,
        """UPDATE metering_events
           SET status = 'retry', dead_lettered_at = NULL
           WHERE id = :event_id""",
        match="invalid metering dead-letter replay mutation",
        parameters={"event_id": replay_event_id},
    )
    await metering_pg_db.execute(
        text(
            """UPDATE metering_events
               SET status = 'retry', attempt_limit = 6, replay_count = 1,
                   next_attempt_at = :now, first_attempt_at = NULL,
                   last_attempt_at = NULL, dead_lettered_at = NULL,
                   last_status_code = NULL, last_error_code = NULL,
                   last_error_digest = NULL, last_response_digest = NULL
               WHERE id = :event_id"""
        ),
        {"event_id": replay_event_id, "now": now},
    )
    replay_projection = (
        await metering_pg_db.execute(
            text(
                """SELECT status, attempt_count, attempt_limit, replay_count,
                          first_attempt_at, last_error_code
                   FROM metering_events
                   WHERE id = :event_id"""
            ),
            {"event_id": replay_event_id},
        )
    ).one()
    assert tuple(replay_projection) == ("retry", 1, 6, 1, None, None)

    await _expect_rejection(
        metering_pg_db,
        "DELETE FROM metering_events WHERE id = :event_id",
        match="metering_events cannot be deleted",
        parameters={"event_id": event_id},
    )
    await _expect_rejection(
        metering_pg_db,
        "TRUNCATE TABLE metering_attempt_records, metering_events",
        match="durable metering tables cannot be truncated",
    )
