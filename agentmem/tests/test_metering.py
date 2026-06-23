"""
Tests for Stripe usage metering.

stripe SDK is not installed in CI â€” all tests inject a lightweight mock via
sys.modules, matching the pattern used in test_kms.py for boto3/hvac.
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

import src.lian.metering as metering_mod


# ---------------------------------------------------------------------------
# Stripe mock
# ---------------------------------------------------------------------------

def _make_stripe_mock(create_async_raises: Exception | None = None):
    """Return a minimal stripe module mock with billing.MeterEvent.create_async."""
    stripe_mock = types.ModuleType("stripe")
    billing_mock = types.ModuleType("stripe.billing")
    meter_event_mock = MagicMock()

    if create_async_raises:
        meter_event_mock.create_async = AsyncMock(side_effect=create_async_raises)
    else:
        meter_event_mock.create_async = AsyncMock(return_value=MagicMock())

    billing_mock.MeterEvent = meter_event_mock
    stripe_mock.billing = billing_mock
    stripe_mock.api_key = None
    return stripe_mock


@pytest.fixture(autouse=True)
def reset_metering(monkeypatch):
    """Reset the metering module queue and cache between tests."""
    monkeypatch.setattr(metering_mod, "_queue", asyncio.Queue(maxsize=10_000))
    monkeypatch.setattr(metering_mod, "_customer_cache", {})
    yield


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    from src.lian.models import Base as AppBase

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    pg_indexes = [
        idx for table in AppBase.metadata.tables.values()
        for idx in table.indexes
        if idx.dialect_kwargs.get("postgresql_using") is not None
    ]
    for idx in pg_indexes:
        idx.table.indexes.discard(idx)

    async with engine.begin() as conn:
        await conn.run_sync(AppBase.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def app_client(monkeypatch):
    """Full FastAPI test client with Stripe key set in env."""
    monkeypatch.setenv("ADMIN_SECRET", "metering-admin-secret")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_metering")

    from src.lian.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("ADMIN_SECRET", "metering-admin-secret")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_metering")
    get_settings.cache_clear()

    from src.lian.models import Base as AppBase
    from src.lian.main import app
    from src.lian.db import get_db

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    pg_indexes = [
        idx for table in AppBase.metadata.tables.values()
        for idx in table.indexes
        if idx.dialect_kwargs.get("postgresql_using") is not None
    ]
    for idx in pg_indexes:
        idx.table.indexes.discard(idx)

    async with engine.begin() as conn:
        await conn.run_sync(AppBase.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override

    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# queue_usage_event
# ---------------------------------------------------------------------------

class TestQueueUsageEvent:

    def test_noop_when_no_api_key(self, monkeypatch):
        monkeypatch.setenv("STRIPE_API_KEY", "")
        from src.lian.config import get_settings
        get_settings.cache_clear()

        q = asyncio.Queue(maxsize=10_000)
        monkeypatch.setattr(metering_mod, "_queue", q)

        metering_mod.queue_usage_event("agentmem_memory_write", "cus_123", 1, "w:abc")
        assert q.qsize() == 0

        get_settings.cache_clear()

    def test_queues_event_when_api_key_set(self, monkeypatch):
        monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
        from src.lian.config import get_settings
        get_settings.cache_clear()

        q = asyncio.Queue(maxsize=10_000)
        monkeypatch.setattr(metering_mod, "_queue", q)

        metering_mod.queue_usage_event("agentmem_memory_write", "cus_123", 1, "w:abc")
        assert q.qsize() == 1
        event = q.get_nowait()
        assert event["event_name"] == "agentmem_memory_write"
        assert event["customer_id"] == "cus_123"
        assert event["quantity"] == 1
        assert event["identifier"] == "w:abc"

        get_settings.cache_clear()

    def test_identifier_truncated_to_100_chars(self, monkeypatch):
        monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
        from src.lian.config import get_settings
        get_settings.cache_clear()

        q = asyncio.Queue(maxsize=10_000)
        monkeypatch.setattr(metering_mod, "_queue", q)

        long_id = "x" * 200
        metering_mod.queue_usage_event("ev", "cus_1", 1, long_id)
        event = q.get_nowait()
        assert len(event["identifier"]) == 100

        get_settings.cache_clear()

    def test_drops_when_queue_full(self, monkeypatch):
        monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
        from src.lian.config import get_settings
        get_settings.cache_clear()

        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        q.put_nowait({"placeholder": True})   # fill the queue
        monkeypatch.setattr(metering_mod, "_queue", q)

        # Should not raise
        metering_mod.queue_usage_event("ev", "cus_1", 1, "id")
        assert q.qsize() == 1  # still 1, event was dropped

        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# get_customer_id (cache behaviour)
# ---------------------------------------------------------------------------

class TestGetCustomerId:

    @pytest.mark.asyncio
    async def test_returns_none_when_no_policy(self, db):
        cid = await metering_mod.get_customer_id(db, "no-policy-ns")
        assert cid is None

    @pytest.mark.asyncio
    async def test_returns_customer_id_from_db(self, db):
        from src.lian.models import NamespacePolicy
        pol = NamespacePolicy(namespace="billing-ns", stripe_customer_id="cus_abc123")
        db.add(pol)
        await db.commit()

        cid = await metering_mod.get_customer_id(db, "billing-ns")
        assert cid == "cus_abc123"

    @pytest.mark.asyncio
    async def test_cache_hit_after_first_lookup(self, db):
        from src.lian.models import NamespacePolicy
        pol = NamespacePolicy(namespace="cache-ns", stripe_customer_id="cus_xyz")
        db.add(pol)
        await db.commit()

        await metering_mod.get_customer_id(db, "cache-ns")   # first call â€” DB hit
        # Mutate DB (shouldn't affect cached value within TTL)
        pol.stripe_customer_id = "cus_changed"
        await db.commit()

        cid2 = await metering_mod.get_customer_id(db, "cache-ns")
        assert cid2 == "cus_xyz"  # still cached

    @pytest.mark.asyncio
    async def test_invalidate_clears_cache(self, db):
        from src.lian.models import NamespacePolicy
        pol = NamespacePolicy(namespace="inv-ns", stripe_customer_id="cus_old")
        db.add(pol)
        await db.commit()

        await metering_mod.get_customer_id(db, "inv-ns")     # populate cache
        metering_mod.invalidate_customer_cache("inv-ns")

        pol.stripe_customer_id = "cus_new"
        await db.commit()
        await db.refresh(pol)

        cid = await metering_mod.get_customer_id(db, "inv-ns")
        assert cid == "cus_new"


# ---------------------------------------------------------------------------
# run_metering_worker
# ---------------------------------------------------------------------------

class TestMeteringWorker:

    @pytest.mark.asyncio
    async def test_worker_exits_without_api_key(self):
        """If api_key is empty the worker returns immediately â€” no task loop."""
        await metering_mod.run_metering_worker("", "w_ev", "r_ev")
        # no hang, no error

    @pytest.mark.asyncio
    async def test_worker_exits_when_stripe_not_installed(self, monkeypatch):
        """If stripe SDK is absent the worker exits with a warning."""
        saved = sys.modules.get("stripe")
        sys.modules.pop("stripe", None)  # hide stripe

        try:
            await metering_mod.run_metering_worker("sk_test_x", "w_ev", "r_ev")
        finally:
            if saved is not None:
                sys.modules["stripe"] = saved

    @pytest.mark.asyncio
    async def test_worker_sends_queued_event(self, monkeypatch):
        """Events placed in the queue before the worker starts are sent."""
        stripe_mock = _make_stripe_mock()
        monkeypatch.setitem(sys.modules, "stripe", stripe_mock)

        q = asyncio.Queue(maxsize=10_000)
        monkeypatch.setattr(metering_mod, "_queue", q)
        q.put_nowait({
            "event_name": "agentmem_memory_write",
            "customer_id": "cus_abc",
            "quantity": 1,
            "identifier": "w:mem-id",
        })

        task = asyncio.create_task(
            metering_mod.run_metering_worker("sk_test_x", "agentmem_memory_write", "agentmem_memory_recall")
        )
        await asyncio.sleep(0.05)   # let worker drain
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        stripe_mock.billing.MeterEvent.create_async.assert_called_once()
        call_kwargs = stripe_mock.billing.MeterEvent.create_async.call_args.kwargs
        assert call_kwargs["event_name"] == "agentmem_memory_write"
        assert call_kwargs["payload"]["stripe_customer_id"] == "cus_abc"
        assert call_kwargs["payload"]["value"] == "1"
        assert call_kwargs["identifier"] == "w:mem-id"

    @pytest.mark.asyncio
    async def test_worker_continues_after_stripe_error(self, monkeypatch):
        """A Stripe API error must not kill the worker â€” next event still sent."""
        stripe_mock = _make_stripe_mock()
        call_count = 0

        async def _failing_then_ok(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Stripe 500")

        stripe_mock.billing.MeterEvent.create_async = _failing_then_ok
        monkeypatch.setitem(sys.modules, "stripe", stripe_mock)

        q = asyncio.Queue(maxsize=10_000)
        monkeypatch.setattr(metering_mod, "_queue", q)
        for i in range(2):
            q.put_nowait({
                "event_name": "ev", "customer_id": "cus_1",
                "quantity": 1, "identifier": f"id{i}",
            })

        task = asyncio.create_task(
            metering_mod.run_metering_worker("sk_test_x", "ev", "ev")
        )
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert call_count == 2  # both events attempted

    @pytest.mark.asyncio
    async def test_worker_cancels_cleanly(self, monkeypatch):
        stripe_mock = _make_stripe_mock()
        monkeypatch.setitem(sys.modules, "stripe", stripe_mock)
        q = asyncio.Queue(maxsize=10_000)
        monkeypatch.setattr(metering_mod, "_queue", q)

        task = asyncio.create_task(
            metering_mod.run_metering_worker("sk_test_x", "w", "r")
        )
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.done()


# ---------------------------------------------------------------------------
# Admin billing endpoints (integration)
# ---------------------------------------------------------------------------

class TestAdminBillingEndpoints:
    ADMIN = "metering-admin-secret"

    @pytest.mark.asyncio
    async def test_get_billing_returns_null_when_unset(self, app_client):
        resp = await app_client.get(
            "/v1/admin/billing/unset-ns",
            headers={"X-Admin-Secret": self.ADMIN},
        )
        assert resp.status_code == 200
        assert resp.json()["stripe_customer_id"] is None

    @pytest.mark.asyncio
    async def test_set_and_get_billing(self, app_client):
        resp = await app_client.put(
            "/v1/admin/billing/acme",
            json={"stripe_customer_id": "cus_acme123"},
            headers={"X-Admin-Secret": self.ADMIN},
        )
        assert resp.status_code == 200
        assert resp.json()["stripe_customer_id"] == "cus_acme123"

        get_resp = await app_client.get(
            "/v1/admin/billing/acme",
            headers={"X-Admin-Secret": self.ADMIN},
        )
        assert get_resp.json()["stripe_customer_id"] == "cus_acme123"

    @pytest.mark.asyncio
    async def test_clear_billing_sets_null(self, app_client):
        await app_client.put(
            "/v1/admin/billing/clear-ns",
            json={"stripe_customer_id": "cus_temp"},
            headers={"X-Admin-Secret": self.ADMIN},
        )
        resp = await app_client.put(
            "/v1/admin/billing/clear-ns",
            json={"stripe_customer_id": None},
            headers={"X-Admin-Secret": self.ADMIN},
        )
        assert resp.status_code == 200
        assert resp.json()["stripe_customer_id"] is None

    @pytest.mark.asyncio
    async def test_billing_set_writes_audit_row(self, app_client):
        await app_client.put(
            "/v1/admin/billing/audit-billing",
            json={"stripe_customer_id": "cus_audit"},
            headers={"X-Admin-Secret": self.ADMIN},
        )
        audit = await app_client.get(
            "/v1/admin/audit/export",
            params={"namespace": "audit-billing"},
            headers={"X-Admin-Secret": self.ADMIN},
        )
        ops = [e["op"] for e in audit.json()["events"]]
        assert "admin.billing_set" in ops

    @pytest.mark.asyncio
    async def test_billing_endpoints_require_admin_secret(self, app_client):
        assert (await app_client.get("/v1/admin/billing/ns")).status_code == 401
        assert (await app_client.put("/v1/admin/billing/ns", json={})).status_code == 401


# ---------------------------------------------------------------------------
# Hot-path integration: add/recall queues events when customer_id is set
# ---------------------------------------------------------------------------

class TestHotPathMetering:

    @pytest.mark.asyncio
    async def test_add_memory_queues_write_event(self, app_client, monkeypatch):
        """POST /v1/memories queues one write event when customer_id is set."""
        queued: list[dict] = []

        def _spy(event_name, customer_id, quantity, identifier):
            queued.append({"event_name": event_name, "customer_id": customer_id,
                           "quantity": quantity, "identifier": identifier})

        monkeypatch.setattr(metering_mod, "queue_usage_event", _spy)

        # Provision API key
        key_resp = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "meter-add", "scopes": ["read", "write"]},
            headers={"X-Admin-Secret": self.ADMIN},
        )
        api_key = key_resp.json()["key"]

        # Set billing
        await app_client.put(
            "/v1/admin/billing/meter-add",
            json={"stripe_customer_id": "cus_meter"},
            headers={"X-Admin-Secret": self.ADMIN},
        )
        metering_mod.invalidate_customer_cache("meter-add")

        from datetime import datetime, timezone
        add_resp = await app_client.post(
            "/v1/memories",
            json={
                "agent_id": "agent-1",
                "content": "AAPL at $190",
                "event_time": datetime.now(timezone.utc).isoformat(),
            },
            headers={"X-API-Key": api_key},
        )
        assert add_resp.status_code == 200

        write_events = [e for e in queued if "write" in e["event_name"]]
        assert len(write_events) == 1
        assert write_events[0]["customer_id"] == "cus_meter"
        assert write_events[0]["quantity"] == 1
        assert write_events[0]["identifier"].startswith("w:")

    ADMIN = "metering-admin-secret"

    @pytest.mark.asyncio
    async def test_recall_queues_recall_event(self, app_client, monkeypatch):
        """POST /v1/recall queues one recall event when customer_id is set."""
        queued: list[dict] = []

        def _spy(event_name, customer_id, quantity, identifier):
            queued.append({"event_name": event_name, "customer_id": customer_id,
                           "quantity": quantity, "identifier": identifier})

        monkeypatch.setattr(metering_mod, "queue_usage_event", _spy)

        key_resp = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "meter-recall", "scopes": ["read", "write"]},
            headers={"X-Admin-Secret": self.ADMIN},
        )
        api_key = key_resp.json()["key"]

        await app_client.put(
            "/v1/admin/billing/meter-recall",
            json={"stripe_customer_id": "cus_rec"},
            headers={"X-Admin-Secret": self.ADMIN},
        )
        metering_mod.invalidate_customer_cache("meter-recall")

        recall_resp = await app_client.post(
            "/v1/recall",
            json={"agent_id": "agent-1", "query": "stock price", "k": 3},
            headers={"X-API-Key": api_key},
        )
        assert recall_resp.status_code == 200

        recall_events = [e for e in queued if "recall" in e["event_name"]]
        assert len(recall_events) == 1
        assert recall_events[0]["customer_id"] == "cus_rec"
        assert recall_events[0]["identifier"].startswith("r:")

    @pytest.mark.asyncio
    async def test_no_event_when_no_customer_id(self, app_client, monkeypatch):
        """When stripe_customer_id is null, no metering event is queued."""
        queued: list[dict] = []

        def _spy(event_name, customer_id, quantity, identifier):
            queued.append({"event_name": event_name})

        monkeypatch.setattr(metering_mod, "queue_usage_event", _spy)

        key_resp = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "no-billing", "scopes": ["read", "write"]},
            headers={"X-Admin-Secret": self.ADMIN},
        )
        api_key = key_resp.json()["key"]
        # No billing set for "no-billing" namespace

        from datetime import datetime, timezone
        await app_client.post(
            "/v1/memories",
            json={
                "agent_id": "agent-1",
                "content": "no billing test",
                "event_time": datetime.now(timezone.utc).isoformat(),
            },
            headers={"X-API-Key": api_key},
        )
        await app_client.post(
            "/v1/recall",
            json={"agent_id": "agent-1", "query": "test", "k": 1},
            headers={"X-API-Key": api_key},
        )
        assert queued == []
