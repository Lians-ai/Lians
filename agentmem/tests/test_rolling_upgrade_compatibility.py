"""Deferred 0.4.2 -> 0.5.0 mixed-writer compatibility contracts.

The PostgreSQL cases run only when TEST_DATABASE_URL names a migrated test
database. They intentionally use the predecessor release's raw INSERT shapes.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from lians.idempotency import operation_claim, scoped_key_hash
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

VERSIONS = Path(__file__).parents[1] / "alembic" / "versions"


def _migration_source(filename: str) -> str:
    return (VERSIONS / filename).read_text(encoding="utf-8")


def test_expand_migrations_keep_old_writers_inside_guarded_boundaries() -> None:
    audit = _migration_source("0039_audit_append_boundary.py")
    audit_contract = _migration_source("0039a_audit_append_contract.py")
    decisions = _migration_source("0041_decision_record_integrity.py")
    decision_index = _migration_source(
        "0041a_decision_record_integrity_index.py"
    )
    recorder = _migration_source("0042_recorder_integrity.py")
    recorder_backfill = _migration_source("0042a_recorder_integrity_backfill.py")
    idempotency = _migration_source("0046_operation_idempotency.py")
    idempotency_backfill = _migration_source(
        "0046a_operation_idempotency_backfill.py"
    )

    for contract in (
        "SECURITY DEFINER",
        "pg_advisory_xact_lock",
        "NEW.chain_position := v_expected_position",
        "NEW.hash_version := 3",
        "NEW.row_hash := public.lians_event_row_hash_v3",
        "trg_event_log_advance_head",
        "GRANT SELECT, INSERT ON event_log TO lians_runtime",
        "postgresql_concurrently=True",
        "LIMIT :batch_size",
        "after_namespace",
        "result.close()",
    ):
        assert contract in audit
    assert "by_namespace" not in audit
    assert "stream_results=True" not in audit
    assert "requires an online PostgreSQL" in audit_contract
    assert "_contract_upgrade" in audit_contract

    for marker in (
        "lians:principal:v1:legacy-unverified",
        "legacy_unverified",
        'server_default="1"',
        "NOT VALID",
    ):
        assert marker in decisions
    assert "UPDATE decision_records SET" not in decisions
    assert "postgresql_concurrently=True" in decision_index
    assert "indisvalid" in decision_index

    for marker in (
        "lians:principal:v1:legacy-unverified",
        "legacy_unverified",
        "trg_recorder_run_provenance_project",
        "NOT VALID",
    ):
        assert marker in recorder
    assert "UPDATE recorder_events SET" not in recorder
    assert "SKIP LOCKED" in recorder_backfill
    assert "postgresql_concurrently=True" in recorder_backfill
    assert "indisvalid" in recorder_backfill

    for contract in (
        "lians_mirror_legacy_idempotency",
        "pg_advisory_xact_lock",
        "BEFORE INSERT ON public.idempotency_keys",
        "Legacy and current idempotency claims disagree",
        "NOT VALID",
    ):
        assert contract in idempotency
    assert 'op.drop_table("idempotency_keys")' not in idempotency
    for contract in ("LIMIT :batch_size", "last_key", "autocommit_block"):
        assert contract in idempotency_backfill


TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
PG_AVAILABLE = bool(TEST_DB_URL and "postgresql" in TEST_DB_URL)
requires_postgres = pytest.mark.skipif(
    not PG_AVAILABLE,
    reason="TEST_DATABASE_URL is not a PostgreSQL URL",
)


@pytest_asyncio.fixture
async def rolling_pg_db():
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


@requires_postgres
@pytest.mark.asyncio
async def test_old_audit_insert_is_serialized_and_canonicalized(
    rolling_pg_db: AsyncSession,
) -> None:
    event_id = uuid4()
    namespace = f"rolling-audit-{uuid4().hex}"
    caller_time = datetime(2000, 1, 1, tzinfo=UTC)
    row = (
        await rolling_pg_db.execute(
            text(
                """INSERT INTO event_log (
                       id, namespace, agent_id, op, content_hash, payload,
                       created_at, prev_hash, row_hash, hash_version
                   ) VALUES (
                       :id, :namespace, 'old-pod', 'rolling_probe', :content_hash,
                       CAST(:payload AS jsonb), :created_at,
                       :caller_prev, :caller_hash, 2
                   )
                   RETURNING prev_hash, row_hash, hash_version, chain_position,
                             created_at"""
            ),
            {
                "id": event_id,
                "namespace": namespace,
                "content_hash": "a" * 64,
                "payload": '{"legacy":true}',
                "created_at": caller_time,
                "caller_prev": "e" * 64,
                "caller_hash": "f" * 64,
            },
        )
    ).mappings().one()

    assert row["prev_hash"] == "0" * 64
    assert row["row_hash"] != "f" * 64
    assert row["hash_version"] == 3
    assert row["chain_position"] == 1
    assert row["created_at"] != caller_time
    canonical = await rolling_pg_db.scalar(
        text(
            """SELECT row_hash = public.lians_event_row_hash_v3(
                       prev_hash, chain_position, id, namespace, agent_id, op,
                       memory_id, content_hash, created_at, payload::jsonb
                   )
                   FROM event_log WHERE id = :id"""
        ),
        {"id": event_id},
    )
    assert canonical is True


@requires_postgres
@pytest.mark.asyncio
async def test_old_audit_insert_cannot_cross_session_namespace(
    rolling_pg_db: AsyncSession,
) -> None:
    allowed_namespace = f"rolling-allowed-{uuid4().hex}"
    await rolling_pg_db.execute(
        text("SELECT set_config('app.current_namespace', :namespace, true)"),
        {"namespace": allowed_namespace},
    )
    # The migration owner is intentionally allowed to bypass the wrapper while
    # installing/repairing the boundary. Exercise the real NOBYPASSRLS runtime
    # capability identity instead of falsely testing through that owner path.
    await rolling_pg_db.execute(text("SET SESSION AUTHORIZATION lians_runtime"))
    try:
        with pytest.raises(DBAPIError):
            async with rolling_pg_db.begin_nested():
                await rolling_pg_db.execute(
                    text(
                        """INSERT INTO event_log (
                               id, namespace, agent_id, op, payload, created_at,
                               prev_hash, row_hash, hash_version
                           ) VALUES (
                               :id, :namespace, 'old-pod', 'cross_tenant_probe',
                               '{}'::jsonb, now(), :hash, :hash, 2
                           )"""
                    ),
                    {
                        "id": uuid4(),
                        "namespace": f"rolling-denied-{uuid4().hex}",
                        "hash": "0" * 64,
                    },
                )
    finally:
        await rolling_pg_db.execute(text("RESET SESSION AUTHORIZATION"))
    await rolling_pg_db.execute(
        text("SELECT set_config('app.current_namespace', '__admin__', true)")
    )


@requires_postgres
@pytest.mark.asyncio
async def test_old_decision_and_recorder_shapes_are_explicitly_legacy(
    rolling_pg_db: AsyncSession,
) -> None:
    namespace = f"rolling-evidence-{uuid4().hex}"
    now = datetime.now(UTC)
    decision_id = uuid4()
    await rolling_pg_db.execute(
        text(
            """INSERT INTO decision_records (
                   id, namespace, agent_id, decision_type, outcome,
                   decided_at, recorded_at, knowledge_as_of, record_hash
               ) VALUES (
                   :id, :namespace, 'old-claimed-agent', 'rolling_probe',
                   'allow', :now, :now, :now, :record_hash
               )"""
        ),
        {
            "id": decision_id,
            "namespace": namespace,
            "now": now,
            "record_hash": "a" * 64,
        },
    )
    decision = (
        await rolling_pg_db.execute(
            text(
                """SELECT record_hash_version, record_integrity_status,
                          recorded_by_principal_ref, recorded_by_auth_method,
                          recorded_by_credential_ref
                   FROM decision_records WHERE id = :id"""
            ),
            {"id": decision_id},
        )
    ).mappings().one()
    assert dict(decision) == {
        "record_hash_version": 1,
        "record_integrity_status": "legacy_unverified",
        "recorded_by_principal_ref": "lians:principal:v1:legacy-unverified",
        "recorded_by_auth_method": "legacy_unverified",
        "recorded_by_credential_ref": None,
    }

    run_id = uuid4()
    event_id = uuid4()
    await rolling_pg_db.execute(
        text(
            """INSERT INTO recorder_runs (
                   id, namespace, barrier_scope, correlation_type,
                   correlation_value, correlation_hash,
                   first_occurred_at, last_occurred_at,
                   first_recorded_at, last_recorded_at, created_at, updated_at
               ) VALUES (
                   :id, :namespace, '__unbarriered__', 'run', :correlation,
                   :correlation_hash, :now, :now, :now, :now, :now, :now
               )"""
        ),
        {
            "id": run_id,
            "namespace": namespace,
            "correlation": uuid4().hex,
            "correlation_hash": "b" * 64,
            "now": now,
        },
    )
    await rolling_pg_db.execute(
        text(
            """INSERT INTO recorder_events (
                   id, namespace, run_id, barrier_scope, schema_version,
                   protocol, event_kind, phase, dedup_key,
                   source_payload_hash, event_hash, occurred_at, recorded_at,
                   capture_mode
               ) VALUES (
                   :id, :namespace, :run_id, '__unbarriered__', '1',
                   'custom', 'rolling_probe', 'completed', :dedup_key,
                   :source_hash, :event_hash, :now, :now, 'metadata_only'
               )"""
        ),
        {
            "id": event_id,
            "namespace": namespace,
            "run_id": run_id,
            "dedup_key": "c" * 64,
            "source_hash": "d" * 64,
            "event_hash": "e" * 64,
            "now": now,
        },
    )
    event = (
        await rolling_pg_db.execute(
            text(
                """SELECT event_hash_version, ingested_by_principal_ref,
                          ingested_by_auth_method, ingested_by_credential_id,
                          actor_attribution
                   FROM recorder_events WHERE id = :id"""
            ),
            {"id": event_id},
        )
    ).mappings().one()
    assert dict(event) == {
        "event_hash_version": 1,
        "ingested_by_principal_ref": "lians:principal:v1:legacy-unverified",
        "ingested_by_auth_method": "legacy_unverified",
        "ingested_by_credential_id": None,
        "actor_attribution": "claimed_unverified",
    }
    run = (
        await rolling_pg_db.execute(
            text(
                """SELECT ingested_by_principal_refs, ingested_by_auth_methods
                   FROM recorder_runs WHERE id = :id"""
            ),
            {"id": run_id},
        )
    ).mappings().one()
    assert run["ingested_by_principal_refs"] == [
        "lians:principal:v1:legacy-unverified"
    ]
    assert run["ingested_by_auth_methods"] == ["legacy_unverified"]


@requires_postgres
@pytest.mark.asyncio
async def test_old_and_new_memory_claims_share_one_authoritative_mapping(
    rolling_pg_db: AsyncSession,
) -> None:
    namespace = f"rolling-idempotency-{uuid4().hex}"
    old_key = f"old-{uuid4().hex}"
    old_memory_id = uuid4()
    now = datetime.now(UTC)
    await rolling_pg_db.execute(
        text(
            """INSERT INTO idempotency_keys (key, namespace, memory_id, created_at)
               VALUES (:key, :namespace, :memory_id, :created_at)"""
        ),
        {
            "key": old_key,
            "namespace": namespace,
            "memory_id": old_memory_id,
            "created_at": now,
        },
    )
    mirrored = (
        await rolling_pg_db.execute(
            text(
                """SELECT legacy_unverified_request, resource_kind, resource_ids
                   FROM operation_idempotency
                   WHERE namespace = :namespace
                     AND operation = 'memory.create'
                     AND key_hash = :key_hash"""
            ),
            {
                "namespace": namespace,
                "key_hash": scoped_key_hash(namespace, "memory.create", old_key),
            },
        )
    ).mappings().one()
    assert mirrored["legacy_unverified_request"] is True
    assert mirrored["resource_kind"] == "memory"
    assert [UUID(value) for value in mirrored["resource_ids"]] == [old_memory_id]

    new_key = f"new-{uuid4().hex}"
    new_memory_id = uuid4()
    async with operation_claim(
        rolling_pg_db,
        namespace=namespace,
        operation="memory.create",
        key=new_key,
        request={"content_hash": "f" * 64},
    ) as claim:
        await claim._complete(
            resource_kind="memory",
            resource_ids=[new_memory_id],
            response_status=200,
        )
    legacy_id = await rolling_pg_db.scalar(
        text(
            """SELECT memory_id FROM idempotency_keys
               WHERE key = :key AND namespace = :namespace"""
        ),
        {"key": new_key, "namespace": namespace},
    )
    assert legacy_id == new_memory_id


@requires_postgres
@pytest.mark.asyncio
async def test_legacy_claim_disagreement_aborts_instead_of_forking(
    rolling_pg_db: AsyncSession,
) -> None:
    namespace = f"rolling-conflict-{uuid4().hex}"
    key = f"conflict-{uuid4().hex}"
    authoritative_id = uuid4()
    conflicting_id = uuid4()
    key_hash = scoped_key_hash(namespace, "memory.create", key)
    await rolling_pg_db.execute(
        text(
            """INSERT INTO operation_idempotency (
                   namespace, operation, key_hash, request_digest,
                   legacy_unverified_request, resource_kind, resource_ids,
                   response_status, created_at
               ) VALUES (
                   :namespace, 'memory.create', :key_hash, :legacy_digest,
                   TRUE, 'memory',
                   jsonb_build_array(CAST(:memory_id AS text))::json,
                   200, now()
               )"""
        ),
        {
            "namespace": namespace,
            "key_hash": key_hash,
            "legacy_digest": "0" * 64,
            "memory_id": str(authoritative_id),
        },
    )
    with pytest.raises(DBAPIError, match="idempotency claims disagree"):
        async with rolling_pg_db.begin_nested():
            await rolling_pg_db.execute(
                text(
                    """INSERT INTO idempotency_keys (
                           key, namespace, memory_id, created_at
                       ) VALUES (:key, :namespace, :memory_id, now())"""
                ),
                {
                    "key": key,
                    "namespace": namespace,
                    "memory_id": conflicting_id,
                },
            )
    assert (
        await rolling_pg_db.scalar(
            text(
                """SELECT COUNT(*) FROM idempotency_keys
                   WHERE key = :key AND namespace = :namespace"""
            ),
            {"key": key, "namespace": namespace},
        )
        == 0
    )
