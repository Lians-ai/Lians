"""Focused Universal Recorder authenticity and append-only boundary tests.

The PostgreSQL cases are opt-in through ``TEST_DATABASE_URL``. They exercise
the migrated database boundary and remain skipped in the SQLite unit suite.
"""

from __future__ import annotations

import inspect
import os
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from lians.audit_chain import chain_log
from lians.recorder_models import RecorderEvent, RecorderRun
from lians.recorder_service import (
    RecorderIntegrityError,
    _event_out,
    assert_recorder_event_hash,
    assert_recorder_event_integrity,
    compute_recorder_event_hash,
    index_recorder_evidence_for_decision,
    index_recorder_rows_batch,
    ingest_recorder_event,
    list_run_events,
)

LEGACY_PRINCIPAL_REF = "lians:principal:v1:legacy-unverified"
LEGACY_AUTH_METHOD = "legacy_unverified"
AUTHENTICATED_PRINCIPAL_REF = (
    "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000042"
)


def _run(*, run_id: UUID | None = None) -> RecorderRun:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    stable_id = run_id or uuid4()
    return RecorderRun(
        id=stable_id,
        namespace="recorder-integrity-test",
        barrier_group=None,
        barrier_scope="unbarriered",
        correlation_type="run",
        correlation_value=f"run:{stable_id}",
        correlation_hash="c" * 64,
        boundary_kind="run",
        agent_id="caller-claimed-agent",
        subject_id=None,
        session_id="session-42",
        trace_id="trace-42",
        task_id=None,
        decision_id=None,
        status="open",
        first_occurred_at=now,
        last_occurred_at=now,
        first_recorded_at=now,
        last_recorded_at=now,
        ready_at=None,
        event_count=1,
        protocols=["lians"],
        capture_state={},
        readiness_score=20,
        receipt_ready=False,
        completeness_gaps=["output_capture"],
        diagnostics=[],
        extension_attributes={},
        ingested_by_principal_refs=[AUTHENTICATED_PRINCIPAL_REF],
        ingested_by_auth_methods=["api_key"],
        created_at=now,
        updated_at=now,
    )


def _event(
    *,
    run_id: UUID | None = None,
    event_hash_version: int = 2,
) -> RecorderEvent:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    row = RecorderEvent(
        id=uuid4(),
        namespace="recorder-integrity-test",
        run_id=run_id or uuid4(),
        barrier_group=None,
        barrier_scope="unbarriered",
        schema_version="0.1",
        protocol="lians",
        event_kind="decision.completed",
        event_name="decision.completed",
        phase="response",
        status="completed",
        source_event_id="source-event-42",
        dedup_key="d" * 64,
        idempotency_key_hash="e" * 64,
        source_payload_hash="f" * 64,
        event_hash="",
        event_hash_version=event_hash_version,
        occurred_at=now,
        recorded_at=now + timedelta(milliseconds=10),
        ingested_by_principal_ref=(
            AUTHENTICATED_PRINCIPAL_REF
            if event_hash_version == 2
            else LEGACY_PRINCIPAL_REF
        ),
        ingested_by_auth_method=(
            "api_key" if event_hash_version == 2 else LEGACY_AUTH_METHOD
        ),
        ingested_by_credential_id=(
            "00000000-0000-0000-0000-000000000042"
            if event_hash_version == 2
            else None
        ),
        actor_attribution="claimed_unverified",
        agent_id="caller-claimed-agent",
        subject_id="lians:subject:v1:sha256:" + "1" * 64,
        session_id="session-42",
        trace_id="trace-42",
        span_id="span-42",
        parent_span_id="parent-span-42",
        task_id="task-42",
        context_id="context-42",
        message_id="message-42",
        tool_call_id="tool-call-42",
        decision_id=uuid4(),
        model_id="model-42",
        model_version="2026-08",
        policy_version="policy-42",
        input_hash="a" * 64,
        output_hash="b" * 64,
        capture_mode="hash_only",
        normalized_payload={
            "actor": {
                "attribution": "claimed_unverified",
                "claimed_principal_id": "caller-principal-label",
            },
            "source": {"output": {"sha256": "b" * 64}},
        },
        extension_attributes={"com.example.control": "passed"},
        capture_gaps=[],
        diagnostics=[{"code": "authenticated_ingestion_principal"}],
    )
    row.event_hash = compute_recorder_event_hash(row)
    return row


def _binding_payload(row: RecorderEvent) -> dict[str, object]:
    return {
        "recorder_event_id": str(row.id),
        "recorder_run_id": str(row.run_id),
        "protocol": row.protocol,
        "event_kind": row.event_kind,
        "event_hash_version": row.event_hash_version,
    }


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        (
            "ingested_by_principal_ref",
            "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000099",
        ),
        ("agent_id", "impersonated-agent-label"),
        ("subject_id", "lians:subject:v1:sha256:" + "9" * 64),
        ("recorded_at", datetime(2026, 8, 3, tzinfo=UTC)),
        ("normalized_payload", {"source": {"output": "tampered"}}),
    ],
)
def test_v2_hash_binds_provenance_claims_and_all_event_content(
    field: str,
    tampered_value: object,
) -> None:
    row = _event()
    original_hash = row.event_hash
    assert_recorder_event_hash(row)

    setattr(row, field, tampered_value)
    with pytest.raises(RecorderIntegrityError, match="stored event_hash"):
        assert_recorder_event_hash(row)
    assert row.event_hash == original_hash


def test_legacy_hash_remains_v1_and_explicitly_unverified() -> None:
    row = _event(event_hash_version=1)
    original_hash = row.event_hash
    assert_recorder_event_hash(row)
    assert row.ingested_by_principal_ref == LEGACY_PRINCIPAL_REF
    assert row.ingested_by_auth_method == LEGACY_AUTH_METHOD
    assert row.ingested_by_credential_id is None

    # v1 did not bind authenticated provenance. The explicit legacy sentinel,
    # rather than a fabricated v2 rehash, preserves that historical limitation.
    row.ingested_by_principal_ref = AUTHENTICATED_PRINCIPAL_REF
    assert compute_recorder_event_hash(row) == original_hash


def test_output_keeps_claimed_actor_distinct_from_authenticated_producer() -> None:
    output = _event_out(_event())
    assert output.agent_id == "caller-claimed-agent"
    assert output.actor_attribution == "claimed_unverified"
    assert output.ingested_by_principal_ref == AUTHENTICATED_PRINCIPAL_REF
    assert output.ingested_by_auth_method == "api_key"
    assert output.event_hash_version == 2


@pytest.mark.asyncio
async def test_event_integrity_requires_exact_core_audit_binding(db) -> None:
    run = _run()
    row = _event(run_id=run.id)
    db.add_all([run, row])
    await db.flush()

    with pytest.raises(RecorderIntegrityError, match="exactly one original audit"):
        await assert_recorder_event_integrity(db, row)

    await chain_log(
        db,
        row.namespace,
        "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000099",
        "recorder_ingest",
        content_hash=row.event_hash,
        payload=_binding_payload(row),
    )
    with pytest.raises(RecorderIntegrityError, match="exactly one original audit"):
        await assert_recorder_event_integrity(db, row)

    await chain_log(
        db,
        row.namespace,
        row.ingested_by_principal_ref,
        "recorder_ingest",
        content_hash=row.event_hash,
        payload=_binding_payload(row),
    )
    await assert_recorder_event_integrity(db, row)

    await chain_log(
        db,
        row.namespace,
        row.ingested_by_principal_ref,
        "recorder_ingest",
        content_hash=row.event_hash,
        payload=_binding_payload(row),
    )
    with pytest.raises(RecorderIntegrityError, match="exactly one original audit"):
        await assert_recorder_event_integrity(db, row)


@pytest.mark.asyncio
async def test_legacy_binding_remains_readable_but_not_authenticated(db) -> None:
    run = _run()
    run.ingested_by_principal_refs = [LEGACY_PRINCIPAL_REF]
    run.ingested_by_auth_methods = [LEGACY_AUTH_METHOD]
    row = _event(run_id=run.id, event_hash_version=1)
    db.add_all([run, row])
    await db.flush()

    # Historical audit rows predate authenticated Recorder provenance and may
    # name a claimed agent. Their event/run commitment remains verifiable, but
    # the v1/sentinel fields ensure it is never promoted to authenticated v2.
    await chain_log(
        db,
        row.namespace,
        "historic-caller-claimed-agent",
        "recorder_ingest",
        content_hash=row.event_hash,
        payload={
            "recorder_event_id": str(row.id),
            "recorder_run_id": str(row.run_id),
        },
    )
    await assert_recorder_event_integrity(db, row)
    row.ingested_by_principal_ref = AUTHENTICATED_PRINCIPAL_REF
    with pytest.raises(RecorderIntegrityError, match="invalid legacy provenance"):
        await assert_recorder_event_integrity(db, row)


@pytest.mark.asyncio
async def test_authoritative_event_list_refuses_unbound_rows(db) -> None:
    run = _run()
    row = _event(run_id=run.id)
    db.add_all([run, row])
    await db.flush()

    with pytest.raises(RecorderIntegrityError, match="invalid audit binding"):
        await list_run_events(
            db,
            namespace=row.namespace,
            barrier_group=None,
            run_id=run.id,
            limit=10,
        )

    await chain_log(
        db,
        row.namespace,
        row.ingested_by_principal_ref,
        "recorder_ingest",
        content_hash=row.event_hash,
        payload=_binding_payload(row),
    )
    page = await list_run_events(
        db,
        namespace=row.namespace,
        barrier_group=None,
        run_id=run.id,
        limit=10,
    )
    assert [event.id for event in page.events] == [row.id]
    assert page.total == 1
    assert page.has_more is False


def test_every_authoritative_service_path_invokes_full_integrity_check() -> None:
    ingest_source = inspect.getsource(ingest_recorder_event)
    assert ingest_source.count(
        "await assert_recorder_event_integrity(db, existing)"
    ) == 2
    assert "await assert_recorder_event_integrity(db, row)" in ingest_source
    assert "await assert_recorder_events_integrity(" in inspect.getsource(list_run_events)
    decision_index_source = inspect.getsource(index_recorder_evidence_for_decision)
    assert "index_recorder_rows_batch" in decision_index_source
    batch_source = inspect.getsource(index_recorder_rows_batch)
    assert "await assert_recorder_events_integrity(db, rows)" in batch_source


def test_migration_declares_exact_revision_and_acl_boundary() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0042_recorder_integrity.py"
    )
    spec = spec_from_file_location("migration_0042_recorder_integrity", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "0042_recorder_integrity"
    assert migration.down_revision == "0041a_decision_integrity_idx"
    source = inspect.getsource(migration._install_postgresql_immutability_boundary)
    for contract in (
        "BEFORE UPDATE OR DELETE ON public.recorder_events",
        "BEFORE TRUNCATE ON public.recorder_events",
        "FROM lians_runtime",
        "GRANT SELECT, INSERT ON TABLE public.recorder_events TO lians_runtime",
    ):
        assert contract in source


TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
PG_AVAILABLE = bool(TEST_DB_URL and "postgresql" in TEST_DB_URL)
requires_postgres = pytest.mark.skipif(
    not PG_AVAILABLE,
    reason="TEST_DATABASE_URL is not a PostgreSQL URL",
)


@pytest_asyncio.fixture
async def recorder_pg_db():
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


async def _expect_database_rejection(
    session: AsyncSession,
    statement: str,
    parameters: dict[str, object] | None = None,
) -> None:
    with pytest.raises(DBAPIError, match="recorder_events is append-only"):
        async with session.begin_nested():
            await session.execute(text(statement), parameters or {})


@requires_postgres
@pytest.mark.asyncio
async def test_postgresql_recorder_event_acl_rls_and_triggers(
    recorder_pg_db: AsyncSession,
) -> None:
    privileges = (
        await recorder_pg_db.execute(
            text(
                """SELECT
                    has_table_privilege(
                        'lians_runtime', 'public.recorder_events', 'SELECT'
                    ) AS can_select,
                    has_table_privilege(
                        'lians_runtime', 'public.recorder_events', 'INSERT'
                    ) AS can_insert,
                    has_table_privilege(
                        'lians_runtime', 'public.recorder_events', 'UPDATE'
                    ) AS can_update,
                    has_table_privilege(
                        'lians_runtime', 'public.recorder_events', 'DELETE'
                    ) AS can_delete,
                    has_table_privilege(
                        'lians_runtime', 'public.recorder_events', 'TRUNCATE'
                    ) AS can_truncate"""
            )
        )
    ).mappings().one()
    assert dict(privileges) == {
        "can_select": True,
        "can_insert": True,
        "can_update": False,
        "can_delete": False,
        "can_truncate": False,
    }

    rls = (
        await recorder_pg_db.execute(
            text(
                """SELECT relrowsecurity, relforcerowsecurity
                   FROM pg_class
                   WHERE oid = 'public.recorder_events'::regclass"""
            )
        )
    ).one()
    assert tuple(rls) == (True, True)
    barrier_policy = (
        await recorder_pg_db.execute(
            text(
                """SELECT NOT polpermissive
                   FROM pg_policy
                   WHERE polrelid = 'public.recorder_events'::regclass
                     AND polname = 'barrier_isolation'"""
            )
        )
    ).scalar_one()
    assert barrier_policy is True

    namespace = f"recorder-integrity-{uuid4().hex}"
    run_id = uuid4()
    event_id = uuid4()
    await recorder_pg_db.execute(
        text(
            """INSERT INTO recorder_runs (
                   id, namespace, barrier_scope, correlation_type,
                   correlation_value, correlation_hash, boundary_kind,
                   first_occurred_at, last_occurred_at,
                   first_recorded_at, last_recorded_at, created_at, updated_at
               ) VALUES (
                   :run_id, :namespace, 'unbarriered', 'run', :correlation,
                   :correlation_hash, 'run', now(), now(), now(), now(), now(), now()
               )"""
        ),
        {
            "run_id": run_id,
            "namespace": namespace,
            "correlation": f"run:{run_id}",
            "correlation_hash": "c" * 64,
        },
    )
    await recorder_pg_db.execute(
        text(
            """INSERT INTO recorder_events (
                   id, namespace, run_id, barrier_scope, schema_version,
                   protocol, event_kind, phase, dedup_key, source_payload_hash,
                   event_hash, event_hash_version, occurred_at, recorded_at,
                   ingested_by_principal_ref, ingested_by_auth_method,
                   ingested_by_credential_id, actor_attribution, capture_mode
               ) VALUES (
                   :event_id, :namespace, :run_id, 'unbarriered', '0.1',
                   'lians', 'test.event', 'event', :dedup_key,
                   :source_payload_hash, :event_hash, 2, now(), now(),
                   :principal_ref, 'api_key', :credential_id,
                   'not_supplied', 'hash_only'
               )"""
        ),
        {
            "event_id": event_id,
            "namespace": namespace,
            "run_id": run_id,
            "dedup_key": "d" * 64,
            "source_payload_hash": "e" * 64,
            "event_hash": "f" * 64,
            "principal_ref": AUTHENTICATED_PRINCIPAL_REF,
            "credential_id": "00000000-0000-0000-0000-000000000042",
        },
    )

    await _expect_database_rejection(
        recorder_pg_db,
        "UPDATE recorder_events SET status = 'tampered' WHERE id = :event_id",
        {"event_id": event_id},
    )
    await _expect_database_rejection(
        recorder_pg_db,
        "DELETE FROM recorder_events WHERE id = :event_id",
        {"event_id": event_id},
    )
    await _expect_database_rejection(
        recorder_pg_db,
        "TRUNCATE TABLE recorder_events",
    )
