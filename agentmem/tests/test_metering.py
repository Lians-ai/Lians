"""Behavioral contracts for the transactional Stripe metering ledger.

These tests deliberately inject a provider double. They must never contact
Stripe and do not depend on process-local queues, caches, or worker globals for
delivery correctness.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from lians import metering as metering_mod
from lians.config import get_settings
from lians.metering_models import MeteringAttemptRecord, MeteringEvent
from lians.models import Base as AppBase
from lians.models import NamespacePolicy
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture(autouse=True)
def deterministic_metering_settings(monkeypatch: pytest.MonkeyPatch):
    """Keep every test independent of developer-machine environment values."""

    values = {
        "AIRGAP_MODE": "false",
        "DEPLOYMENT_ENVIRONMENT": "development",
        "STRIPE_API_KEY": "",
        "STRIPE_METER_DECISION_EVENT": "lians_authoritative_decision",
        "STRIPE_METER_PROTECTED_ACTION_EVENT": "lians_protected_action",
        "STRIPE_METER_WRITE_EVENT": "agentmem_memory_write",
        "STRIPE_METER_RECALL_EVENT": "agentmem_memory_recall",
        "STRIPE_METER_ASYNC_ERROR_DESTINATION_CONFIGURED": "false",
        "STRIPE_METER_WORKER_ENABLED": "true",
        "STRIPE_METER_WORKER_POLL_SECONDS": "0.01",
        "STRIPE_METER_WORKER_BATCH_SIZE": "8",
        "STRIPE_METER_DELIVERY_CONCURRENCY": "2",
        "STRIPE_METER_LEASE_SECONDS": "60",
        "STRIPE_METER_PROVIDER_TIMEOUT_SECONDS": "10",
        "STRIPE_METER_RETRY_BASE_SECONDS": "1",
        "STRIPE_METER_RETRY_MAX_SECONDS": "10",
        "STRIPE_METER_MAX_ATTEMPTS": "3",
        "STRIPE_METER_IDEMPOTENCY_WINDOW_SECONDS": "3600",
        "STRIPE_METER_MAX_EVENT_AGE_SECONDS": "86400",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    monkeypatch.setattr(metering_mod, "_worker_last_poll_at", None)
    monkeypatch.setattr(metering_mod, "_worker_last_heartbeat_at", None)
    monkeypatch.setattr(metering_mod, "_worker_last_delivery_at", None)
    monkeypatch.setattr(metering_mod, "_worker_last_error_at", None)
    monkeypatch.setattr(metering_mod, "_worker_last_error_digest", None)
    monkeypatch.setattr(metering_mod, "_worker_terminal_error", None)
    monkeypatch.setattr(metering_mod, "_worker_last_iteration_healthy", False)
    monkeypatch.setattr(metering_mod, "_worker_backlog", {})
    monkeypatch.setattr(metering_mod, "_worker_oldest_due_at", None)
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # PostgreSQL-only indexes elsewhere in the shared application metadata are
    # intentionally omitted by SQLite contract tests.
    pg_indexes = [
        index
        for table in AppBase.metadata.tables.values()
        for index in table.indexes
        if index.dialect_kwargs.get("postgresql_using") not in (None, False)
    ]
    for index in pg_indexes:
        index.table.indexes.discard(index)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(AppBase.metadata.create_all)
    finally:
        for index in pg_indexes:
            index.table.indexes.add(index)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


async def _configure_customer(
    factory: async_sessionmaker[AsyncSession],
    namespace: str,
    customer_id: str = "cus_contract123",
) -> None:
    async with factory() as db:
        db.add(NamespacePolicy(namespace=namespace, stripe_customer_id=customer_id))
        await db.commit()


async def _stage_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    namespace: str,
    source_identifier: str | None = None,
    occurred_at: datetime | None = None,
) -> UUID:
    await _configure_customer(factory, namespace)
    async with factory() as db:
        row = await metering_mod.enqueue_usage_event(
            db,
            namespace=namespace,
            event_name="agentmem_memory_write",
            quantity=1,
            source_identifier=source_identifier or f"w:{uuid4()}",
            occurred_at=occurred_at or datetime.now(UTC),
        )
        assert row is not None
        await db.commit()
        return row.id


async def _claim_and_prepare(
    factory: async_sessionmaker[AsyncSession],
    event_id: UUID,
    worker_id: str = "worker-a",
) -> metering_mod._ProviderEvent:
    async with factory() as db:
        claimed = await metering_mod.claim_due_metering_events(
            db,
            worker_id=worker_id,
            batch_size=1,
            lease_seconds=60,
        )
    assert claimed == [event_id]
    provider_event = await metering_mod._prepare_provider_attempt(
        factory,
        event_id=event_id,
        worker_id=worker_id,
    )
    assert provider_event is not None
    return provider_event


class TestTransactionalEnqueue:
    @pytest.mark.asyncio
    async def test_product_native_units_have_stable_distinct_source_identities(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await _configure_customer(session_factory, "protected-units")
        decision_id = uuid4()
        permit_id = uuid4()
        occurred_at = datetime.now(UTC).replace(microsecond=123456)

        async with session_factory() as db:
            decision = await metering_mod.enqueue_authoritative_decision_usage_event(
                db,
                namespace="protected-units",
                decision_id=decision_id,
                occurred_at=occurred_at,
            )
            decision_replay = (
                await metering_mod.enqueue_authoritative_decision_usage_event(
                    db,
                    namespace="protected-units",
                    decision_id=decision_id,
                    occurred_at=occurred_at,
                )
            )
            action = await metering_mod.enqueue_protected_action_usage_event(
                db,
                namespace="protected-units",
                permit_id=permit_id,
                occurred_at=occurred_at,
            )
            action_replay = await metering_mod.enqueue_protected_action_usage_event(
                db,
                namespace="protected-units",
                permit_id=permit_id,
                occurred_at=occurred_at,
            )
            assert decision is not None and decision_replay is not None
            assert action is not None and action_replay is not None
            assert decision.id == decision_replay.id
            assert action.id == action_replay.id
            assert decision.provider_identifier != action.provider_identifier
            await db.commit()

        async with session_factory() as db:
            rows = list(
                (
                    await db.execute(
                        select(MeteringEvent).where(
                            MeteringEvent.namespace == "protected-units"
                        )
                    )
                ).scalars()
            )
            assert {row.event_name for row in rows} == {
                "lians_authoritative_decision",
                "lians_protected_action",
            }
            assert all(row.quantity == 1 for row in rows)

    @pytest.mark.asyncio
    async def test_caller_controls_commit_and_empty_key_does_not_drop_usage(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await _configure_customer(session_factory, "atomic")
        occurred_at = datetime.now(UTC)

        async with session_factory() as db:
            row = await metering_mod.enqueue_usage_event(
                db,
                namespace="atomic",
                event_name="agentmem_memory_write",
                quantity=1,
                source_identifier="w:rolled-back",
                occurred_at=occurred_at,
            )
            assert row is not None
            await db.rollback()

        async with session_factory() as db:
            count = await db.scalar(select(func.count()).select_from(MeteringEvent))
            assert count == 0
            row = await metering_mod.enqueue_usage_event(
                db,
                namespace="atomic",
                event_name="agentmem_memory_write",
                quantity=1,
                source_identifier="w:committed",
                occurred_at=occurred_at - timedelta(minutes=5),
            )
            assert row is not None
            assert row.created_at > row.occurred_at
            await db.commit()

        async with session_factory() as db:
            count = await db.scalar(select(func.count()).select_from(MeteringEvent))
            assert count == 1

    @pytest.mark.asyncio
    async def test_missing_customer_and_airgap_stage_nothing(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async with session_factory() as db:
            assert (
                await metering_mod.enqueue_usage_event(
                    db,
                    namespace="unbilled",
                    event_name="agentmem_memory_write",
                    quantity=1,
                    source_identifier="w:none",
                )
                is None
            )

        await _configure_customer(session_factory, "airgap")
        monkeypatch.setenv("AIRGAP_MODE", "true")
        get_settings.cache_clear()
        async with session_factory() as db:
            assert (
                await metering_mod.enqueue_usage_event(
                    db,
                    namespace="airgap",
                    event_name="agentmem_memory_write",
                    quantity=1,
                    source_identifier="w:airgap",
                )
                is None
            )
        async with session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(MeteringEvent)) == 0

    @pytest.mark.asyncio
    async def test_invalid_semantic_meter_fails_before_unbilled_short_circuit(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as db:
            with pytest.raises(metering_mod.MeteringConfigurationError):
                await metering_mod.enqueue_usage_event(
                    db,
                    namespace="unbilled-invalid-config",
                    event_name="invalid protected unit",
                    quantity=1,
                    source_identifier="decision:invalid-config",
                )
            await db.rollback()

        async with session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(MeteringEvent)) == 0

    @pytest.mark.asyncio
    async def test_customer_is_snapshotted_without_a_process_cache(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await _configure_customer(session_factory, "snapshot", "cus_original")
        async with session_factory() as db:
            row = await metering_mod.enqueue_usage_event(
                db,
                namespace="snapshot",
                event_name="agentmem_memory_write",
                quantity=1,
                source_identifier="w:snapshot",
            )
            assert row is not None
            await db.commit()
            policy = await db.get(NamespacePolicy, "snapshot")
            assert policy is not None
            policy.stripe_customer_id = "cus_rotated"
            await db.commit()
            assert await metering_mod.get_customer_id(db, "snapshot") == "cus_rotated"

        async with session_factory() as db:
            event = (await db.execute(select(MeteringEvent))).scalar_one()
            assert event.customer_id == "cus_original"

    @pytest.mark.asyncio
    async def test_source_identity_is_idempotent_but_billing_facts_are_immutable(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await _configure_customer(session_factory, "idempotent")
        occurred_at = datetime.now(UTC).replace(microsecond=123456)
        async with session_factory() as db:
            first = await metering_mod.enqueue_usage_event(
                db,
                namespace="idempotent",
                event_name="agentmem_memory_write",
                quantity=1,
                source_identifier="w:stable",
                occurred_at=occurred_at,
            )
            second = await metering_mod.enqueue_usage_event(
                db,
                namespace="idempotent",
                event_name="agentmem_memory_write",
                quantity=1,
                source_identifier="w:stable",
                occurred_at=occurred_at,
            )
            assert first is second
            with pytest.raises(metering_mod.MeteringConflictError):
                await metering_mod.enqueue_usage_event(
                    db,
                    namespace="idempotent",
                    event_name="agentmem_memory_write",
                    quantity=2,
                    source_identifier="w:stable",
                    occurred_at=occurred_at,
                )
            with pytest.raises(metering_mod.MeteringConflictError):
                await metering_mod.enqueue_usage_event(
                    db,
                    namespace="idempotent",
                    event_name="agentmem_memory_write",
                    quantity=1,
                    source_identifier="w:stable",
                    occurred_at=occurred_at + timedelta(seconds=1),
                )

    @pytest.mark.asyncio
    async def test_invalid_quantity_name_source_and_future_time_fail_closed(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await _configure_customer(session_factory, "validation")
        async with session_factory() as db:
            for kwargs in (
                {"quantity": 0},
                {"quantity": True},
                {"event_name": "not allowed!"},
                {"source_identifier": ""},
                {"occurred_at": datetime.now(UTC) + timedelta(minutes=6)},
            ):
                values = {
                    "namespace": "validation",
                    "event_name": "agentmem_memory_write",
                    "quantity": 1,
                    "source_identifier": f"w:{uuid4()}",
                }
                values.update(kwargs)
                with pytest.raises(metering_mod.MeteringConfigurationError):
                    await metering_mod.enqueue_usage_event(db, **values)

    def test_retired_process_queue_fails_loudly_except_in_airgap(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with pytest.raises(metering_mod.MeteringConfigurationError):
            metering_mod.queue_usage_event("write", "cus_123", 1, "w:old")
        monkeypatch.setenv("AIRGAP_MODE", "true")
        get_settings.cache_clear()
        metering_mod.queue_usage_event("write", "cus_123", 1, "w:airgap")


class TestLeaseAndDelivery:
    @pytest.mark.asyncio
    async def test_claims_do_not_overlap_and_expired_lease_is_recovered(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        first_id = await _stage_event(session_factory, namespace="claim-a")
        second_id = await _stage_event(session_factory, namespace="claim-b")
        async with session_factory() as db:
            first_claim = await metering_mod.claim_due_metering_events(
                db,
                worker_id="worker-a",
                batch_size=1,
                lease_seconds=60,
            )
            second_claim = await metering_mod.claim_due_metering_events(
                db,
                worker_id="worker-b",
                batch_size=1,
                lease_seconds=60,
            )
            assert len(first_claim) == len(second_claim) == 1
            assert set(first_claim).isdisjoint(second_claim)

            expired_id = first_claim[0]
            expired = await db.get(MeteringEvent, expired_id)
            assert expired is not None
            expired.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await db.commit()
            recovered = await metering_mod.claim_due_metering_events(
                db,
                worker_id="worker-c",
                batch_size=1,
                lease_seconds=60,
            )
            assert recovered == [expired_id]
            refreshed = await db.get(MeteringEvent, expired_id)
            assert refreshed is not None and refreshed.lease_owner == "worker-c"
        assert {first_id, second_id} == set(first_claim + second_claim)

    @pytest.mark.asyncio
    async def test_prepare_renews_lease_and_appends_started_record(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        event_id = await _stage_event(session_factory, namespace="renew")
        async with session_factory() as db:
            assert await metering_mod.claim_due_metering_events(
                db,
                worker_id="worker-a",
                batch_size=1,
                lease_seconds=60,
            ) == [event_id]
            row = await db.get(MeteringEvent, event_id)
            assert row is not None
            row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

        provider_event = await metering_mod._prepare_provider_attempt(
            session_factory,
            event_id=event_id,
            worker_id="worker-a",
        )
        assert provider_event is not None and provider_event.attempt_number == 1
        async with session_factory() as db:
            row = await db.get(MeteringEvent, event_id)
            assert row is not None and row.lease_expires_at is not None
            assert metering_mod._utc(row.lease_expires_at) > datetime.now(UTC)
            records = (
                await db.execute(
                    select(MeteringAttemptRecord).where(
                        MeteringAttemptRecord.event_id == event_id
                    )
                )
            ).scalars().all()
            assert [(record.record_type, record.outcome) for record in records] == [
                ("started", "started")
            ]

    @pytest.mark.asyncio
    async def test_provider_receives_stable_identifier_timestamp_and_idempotency_key(
        self,
    ) -> None:
        response = SimpleNamespace(
            id="mtr_test",
            last_response=SimpleNamespace(code=200, request_id="req_test"),
        )
        create = AsyncMock(return_value=response)
        stripe_module = SimpleNamespace(
            billing=SimpleNamespace(MeterEvent=SimpleNamespace(create_async=create))
        )
        occurred_at = datetime.now(UTC) - timedelta(minutes=1)
        event = metering_mod._ProviderEvent(
            id=uuid4(),
            event_name="agentmem_memory_write",
            customer_id="cus_provider",
            quantity=7,
            provider_identifier="lians_" + "a" * 64,
            occurred_at=occurred_at,
            attempt_number=1,
        )
        result = await metering_mod._send_to_stripe(
            stripe_module,
            api_key="sk_test_injected",
            event=event,
        )
        assert result.delivered is True
        kwargs = create.await_args.kwargs
        assert kwargs["identifier"] == event.provider_identifier
        assert kwargs["idempotency_key"] == event.provider_identifier
        assert kwargs["timestamp"] == int(occurred_at.timestamp())
        assert kwargs["payload"] == {"stripe_customer_id": "cus_provider", "value": "7"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status_code", "expected_retryable"),
        ((429, True), (503, True), (400, False)),
    )
    async def test_provider_failures_are_safely_classified(
        self,
        status_code: int,
        expected_retryable: bool,
    ) -> None:
        class ProviderError(Exception):
            code = "provider_contract"
            headers: ClassVar[dict[str, str]] = {"Retry-After": "2"}

            def __init__(self, code: int) -> None:
                super().__init__("intentionally not persisted")
                self.http_status = code

        create = AsyncMock(side_effect=ProviderError(status_code))
        stripe_module = SimpleNamespace(
            billing=SimpleNamespace(MeterEvent=SimpleNamespace(create_async=create))
        )
        event = metering_mod._ProviderEvent(
            id=uuid4(),
            event_name="agentmem_memory_write",
            customer_id="cus_provider",
            quantity=1,
            provider_identifier="lians_" + "f" * 64,
            occurred_at=datetime.now(UTC),
            attempt_number=1,
        )
        result = await metering_mod._send_to_stripe(
            stripe_module,
            api_key="sk_test_injected",
            event=event,
        )
        assert result.delivered is False
        assert result.retryable is expected_retryable
        assert result.status_code == status_code
        assert result.error_code == f"stripe_{status_code}_provider_contract"
        assert result.error_digest is not None and len(result.error_digest) == 64
        assert result.retry_after_seconds == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("delivered", "retryable", "expected_status", "expected_outcome"),
        (
            (True, False, "delivered", "delivered"),
            (False, True, "retry", "retry"),
            (False, False, "dead_letter", "dead_letter"),
        ),
    )
    async def test_provider_results_are_projected_and_appended(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        delivered: bool,
        retryable: bool,
        expected_status: str,
        expected_outcome: str,
    ) -> None:
        namespace = f"result-{expected_status}"
        event_id = await _stage_event(session_factory, namespace=namespace)
        event = await _claim_and_prepare(session_factory, event_id)
        result = metering_mod._StripeResult(
            delivered=delivered,
            retryable=retryable,
            status_code=200 if delivered else (503 if retryable else 400),
            error_code=None if delivered else "stripe_contract_error",
            error_digest=None if delivered else "a" * 64,
            response_digest="b" * 64 if delivered else None,
            retry_after_seconds=1 if retryable else None,
            duration_ms=12,
        )
        await metering_mod._record_provider_result(
            session_factory,
            event=event,
            worker_id="worker-a",
            result=result,
        )
        async with session_factory() as db:
            row = await db.get(MeteringEvent, event_id)
            assert row is not None and row.status == expected_status
            records = (
                await db.execute(
                    select(MeteringAttemptRecord)
                    .where(MeteringAttemptRecord.event_id == event_id)
                    .order_by(MeteringAttemptRecord.record_type)
                )
            ).scalars().all()
            assert {record.outcome for record in records} == {"started", expected_outcome}

    @pytest.mark.asyncio
    async def test_lost_lease_records_result_without_overwriting_new_owner(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        event_id = await _stage_event(session_factory, namespace="lease-lost")
        event = await _claim_and_prepare(session_factory, event_id)
        async with session_factory() as db:
            row = await db.get(MeteringEvent, event_id)
            assert row is not None
            row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
            row = await db.get(MeteringEvent, event_id)
            assert row is not None
            row.lease_owner = "worker-b"
            row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
            await db.commit()
        result = metering_mod._StripeResult(
            delivered=True,
            retryable=False,
            status_code=200,
            error_code=None,
            error_digest=None,
            response_digest="c" * 64,
            retry_after_seconds=None,
            duration_ms=8,
        )
        await metering_mod._record_provider_result(
            session_factory,
            event=event,
            worker_id="worker-a",
            result=result,
        )
        async with session_factory() as db:
            row = await db.get(MeteringEvent, event_id)
            assert row is not None
            assert row.status == "leased" and row.lease_owner == "worker-b"
            finish = (
                await db.execute(
                    select(MeteringAttemptRecord).where(
                        MeteringAttemptRecord.event_id == event_id,
                        MeteringAttemptRecord.record_type == "finished",
                    )
                )
            ).scalar_one()
            assert finish.outcome == "lease_lost"
            assert finish.status_code == 200
            assert finish.response_digest == "c" * 64

    @pytest.mark.asyncio
    async def test_provider_age_budget_dead_letters_before_any_attempt(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        event_id = await _stage_event(
            session_factory,
            namespace="too-old",
            occurred_at=datetime.now(UTC) - timedelta(days=2),
        )
        async with session_factory() as db:
            assert await metering_mod.claim_due_metering_events(
                db,
                worker_id="worker-a",
                batch_size=1,
                lease_seconds=60,
            ) == [event_id]
        assert (
            await metering_mod._prepare_provider_attempt(
                session_factory,
                event_id=event_id,
                worker_id="worker-a",
            )
            is None
        )
        async with session_factory() as db:
            row = await db.get(MeteringEvent, event_id)
            assert row is not None
            assert row.status == "dead_letter"
            assert row.last_error_code == "provider_event_age_exceeded"
            assert row.attempt_count == 0

    @pytest.mark.asyncio
    async def test_idempotency_window_stops_automatic_retry(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        event_id = await _stage_event(session_factory, namespace="window")
        event = await _claim_and_prepare(session_factory, event_id)
        await metering_mod._record_provider_result(
            session_factory,
            event=event,
            worker_id="worker-a",
            result=metering_mod._StripeResult(
                delivered=False,
                retryable=True,
                status_code=503,
                error_code="stripe_503_provider_error",
                error_digest="1" * 64,
                response_digest=None,
                retry_after_seconds=1,
                duration_ms=10,
            ),
        )
        async with session_factory() as db:
            row = await db.get(MeteringEvent, event_id)
            assert row is not None and row.first_attempt_at is not None
            after_window = metering_mod._utc(row.first_attempt_at) + timedelta(seconds=3601)
            row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

        async def advanced_database_clock(_db: AsyncSession) -> datetime:
            return after_window

        monkeypatch.setattr(metering_mod, "_database_now", advanced_database_clock)
        async with session_factory() as db:
            assert await metering_mod.claim_due_metering_events(
                db,
                worker_id="worker-b",
                batch_size=1,
                lease_seconds=60,
            ) == [event_id]
        assert (
            await metering_mod._prepare_provider_attempt(
                session_factory,
                event_id=event_id,
                worker_id="worker-b",
            )
            is None
        )
        async with session_factory() as db:
            row = await db.get(MeteringEvent, event_id)
            assert row is not None
            assert row.status == "dead_letter"
            assert row.last_error_code == "idempotency_window_expired"
            assert row.attempt_count == 1


class TestDeadLetterReconciliation:
    @pytest.mark.asyncio
    async def test_replay_requires_assertion_resets_safety_epoch_and_preserves_ledger(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        event_id = await _stage_event(session_factory, namespace="replay")
        event = await _claim_and_prepare(session_factory, event_id)
        result = metering_mod._StripeResult(
            delivered=False,
            retryable=False,
            status_code=400,
            error_code="stripe_invalid",
            error_digest="d" * 64,
            response_digest=None,
            retry_after_seconds=None,
            duration_ms=5,
        )
        await metering_mod._record_provider_result(
            session_factory,
            event=event,
            worker_id="worker-a",
            result=result,
        )

        async with session_factory() as db:
            row = await db.get(MeteringEvent, event_id)
            assert row is not None
            original_identifier = row.provider_identifier
            original_limit = row.attempt_limit
            with pytest.raises(metering_mod.MeteringConflictError):
                await metering_mod.replay_dead_letter_event(
                    db,
                    event_id,
                    reconciliation="ambiguous",  # type: ignore[arg-type]
                )
            replayed = await metering_mod.replay_dead_letter_event(
                db,
                event_id,
                reconciliation="provider_confirmed_not_accepted",
            )
            assert replayed.status == "retry"
            assert replayed.provider_identifier == original_identifier
            assert replayed.attempt_limit == original_limit + 3
            assert replayed.replay_count == 1
            assert replayed.first_attempt_at is None
            assert replayed.last_attempt_at is None
            assert replayed.dead_lettered_at is None
            await db.commit()

        async with session_factory() as db:
            records = await db.scalar(
                select(func.count())
                .select_from(MeteringAttemptRecord)
                .where(MeteringAttemptRecord.event_id == event_id)
            )
            assert records == 2

    def test_production_configuration_requires_live_key_and_async_error_destination(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("STRIPE_API_KEY", "sk_test_not_production")
        monkeypatch.setenv("STRIPE_METER_ASYNC_ERROR_DESTINATION_CONFIGURED", "false")
        get_settings.cache_clear()
        errors = metering_mod.validate_metering_configuration(
            get_settings(),
            production=True,
        )
        assert any("live secret or restricted key" in error for error in errors)
        assert any("ASYNC_ERROR_DESTINATION" in error for error in errors)

        monkeypatch.setenv("STRIPE_API_KEY", "rk_live_contract")
        monkeypatch.setenv("STRIPE_METER_ASYNC_ERROR_DESTINATION_CONFIGURED", "true")
        get_settings.cache_clear()
        monkeypatch.setattr(metering_mod.importlib.util, "find_spec", lambda _name: object())
        assert (
            metering_mod.validate_metering_configuration(
                get_settings(),
                production=True,
            )
            == []
        )

    @pytest.mark.parametrize(
        ("name", "value", "error_fragment"),
        (
            (
                "STRIPE_METER_DECISION_EVENT",
                "invalid protected unit",
                "STRIPE_METER_DECISION_EVENT",
            ),
            (
                "STRIPE_METER_PROTECTED_ACTION_EVENT",
                "",
                "STRIPE_METER_PROTECTED_ACTION_EVENT",
            ),
            (
                "STRIPE_METER_PROTECTED_ACTION_EVENT",
                "lians_authoritative_decision",
                "must be distinct",
            ),
        ),
    )
    def test_protected_unit_meter_names_fail_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        value: str,
        error_fragment: str,
    ) -> None:
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        errors = metering_mod.validate_metering_configuration(
            get_settings(),
            production=False,
        )
        assert any(error_fragment in error for error in errors)


class TestMeteringObservability:
    def test_scrape_refreshes_bounded_worker_and_backlog_metrics(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from lians import metrics

        if not metrics._PROM_AVAILABLE:
            pytest.skip("prometheus-client is not installed")
        monkeypatch.setenv("STRIPE_API_KEY", "rk_live_metrics")
        get_settings.cache_clear()
        now = datetime.now(UTC)
        monkeypatch.setattr(metering_mod, "_worker_last_poll_at", now)
        monkeypatch.setattr(metering_mod, "_worker_last_heartbeat_at", now)
        monkeypatch.setattr(metering_mod, "_worker_backlog", {"pending": 2})
        monkeypatch.setattr(
            metering_mod,
            "_worker_oldest_due_at",
            now - timedelta(seconds=10),
        )
        body, _content_type = metrics.generate_metrics()
        rendered = body.decode("utf-8")
        assert "lians_metering_delivery_enabled 1.0" in rendered
        assert "lians_metering_worker_healthy 1.0" in rendered
        assert 'lians_metering_events{status="pending"} 2.0' in rendered
        assert "lians_metering_oldest_due_age_seconds" in rendered


@pytest_asyncio.fixture
async def admin_client(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ADMIN_SECRET", "metering-admin-secret")
    get_settings.cache_clear()
    from httpx import ASGITransport, AsyncClient
    from lians.db import get_db
    from lians.main import app

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.clear()


class TestMeteringAdminSurface:
    headers: ClassVar[dict[str, str]] = {"X-Admin-Secret": "metering-admin-secret"}

    @pytest.mark.asyncio
    async def test_status_and_event_projection_are_secret_free_and_operable(
        self,
        admin_client,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        event_id = await _stage_event(session_factory, namespace="admin-list")
        status_response = await admin_client.get(
            "/v1/admin/billing-metering/status",
            headers=self.headers,
        )
        assert status_response.status_code == 200
        status_body = status_response.json()
        assert status_body["pending_events"] == 1
        assert status_body["provider_configured"] is False

        events_response = await admin_client.get(
            "/v1/admin/billing-metering/events",
            headers=self.headers,
        )
        assert events_response.status_code == 200
        event_body = events_response.json()[0]
        assert event_body["id"] == str(event_id)
        assert event_body["provider_identifier"].startswith("lians_")
        assert "customer_id" not in event_body
        assert "request_hash" not in event_body

    @pytest.mark.asyncio
    async def test_replay_endpoint_requires_reconciliation_body_and_audits_reference_hash(
        self,
        admin_client,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        from lians.models import EventLog

        event_id = await _stage_event(session_factory, namespace="admin-replay")
        event = await _claim_and_prepare(session_factory, event_id)
        await metering_mod._record_provider_result(
            session_factory,
            event=event,
            worker_id="worker-a",
            result=metering_mod._StripeResult(
                delivered=False,
                retryable=False,
                status_code=400,
                error_code="operator_test",
                error_digest="e" * 64,
                response_digest=None,
                retry_after_seconds=None,
                duration_ms=4,
            ),
        )

        missing = await admin_client.post(
            f"/v1/admin/billing-metering/events/{event_id}/replay",
            headers=self.headers,
        )
        assert missing.status_code == 422
        ambiguous = await admin_client.post(
            f"/v1/admin/billing-metering/events/{event_id}/replay",
            headers=self.headers,
            json={
                "reconciliation": "ambiguous",
                "reconciliation_reference": "INC-1",
            },
        )
        assert ambiguous.status_code == 422
        replayed = await admin_client.post(
            f"/v1/admin/billing-metering/events/{event_id}/replay",
            headers=self.headers,
            json={
                "reconciliation": "provider_confirmed_not_accepted",
                "reconciliation_reference": "INC-12345",
            },
        )
        assert replayed.status_code == 200
        assert replayed.json()["status"] == "retry"

        async with session_factory() as db:
            audit = (
                await db.execute(
                    select(EventLog).where(
                        EventLog.namespace == "admin-replay",
                        EventLog.op == "admin.billing_meter_replay",
                    )
                )
            ).scalar_one()
            assert audit.payload["reconciliation"] == "provider_confirmed_not_accepted"
            assert audit.payload["reconciliation_reference_hash"] == metering_mod._sha256(
                "INC-12345"
            )
            assert "INC-12345" not in str(audit.payload)
