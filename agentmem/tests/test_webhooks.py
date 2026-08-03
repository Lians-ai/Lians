"""
Webhook registration, delivery dispatch, and signature verification tests.

All outbound HTTP calls are intercepted via monkeypatch so no real network
traffic is generated.
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from lians.config import get_settings
from lians.db import get_db
from lians.main import app
from lians.models import ApiKey, WebhookDelivery, WebhookEndpoint
from lians.webhook_service import (
    MEMORY_CONFLICT,
    MEMORY_ERASED,
    MEMORY_SUPERSEDED,
    WebhookConflictError,
    WebhookCapacityError,
    _http_post,
    _sign,
    _validate_webhook_url,
    delete_webhook,
    dispatch_event,
    list_webhooks,
    register_webhook,
    update_webhook,
)

TEST_NS = "webhook-test-ns"
TEST_KEY = "wh-test-api-key-12345"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _api_h():
    return {"X-API-Key": TEST_KEY}


@pytest.fixture(autouse=True)
def enable_legacy_webhooks_for_compatibility_tests(monkeypatch):
    monkeypatch.setenv("LEGACY_WEBHOOKS_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def client(db):
    hashed = hashlib.sha256(TEST_KEY.encode()).hexdigest()
    db.add(ApiKey(hashed_key=hashed, namespace=TEST_NS, scopes=["read", "write", "admin"]))
    await db.commit()

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


# â”€â”€ Signing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_sign_produces_sha256_prefix():
    sig = _sign("mysecret", b"hello")
    assert sig.startswith("sha256=")
    assert len(sig) == len("sha256=") + 64


def test_sign_is_deterministic():
    assert _sign("s", b"body") == _sign("s", b"body")


def test_sign_differs_for_different_bodies():
    assert _sign("s", b"a") != _sign("s", b"b")


def test_sign_differs_for_different_secrets():
    assert _sign("s1", b"body") != _sign("s2", b"body")


def test_sign_verifiable():
    secret = "test-secret-abc"
    body = b'{"event":"memory.superseded"}'
    sig = _sign(secret, body)
    hex_part = sig[len("sha256="):]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(hex_part, expected)


# â”€â”€ Registration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.asyncio
async def test_register_webhook(db):
    ep = await register_webhook(
        db, TEST_NS,
        url="https://receiver.example.com/hook",
        secret="my-secret",
        events=[MEMORY_SUPERSEDED],
    )
    assert ep.id is not None
    assert ep.namespace == TEST_NS
    assert ep.enabled is True
    assert MEMORY_SUPERSEDED in ep.events
    assert ep.secret != "my-secret"
    assert ep.secret.startswith("lians-sealed:v2:")


@pytest.mark.parametrize("url", [
    "http://example.com/hook",
    "https://127.0.0.1/hook",
    "https://[::1]/hook",
    "https://169.254.169.254/latest/meta-data",
    "https://localhost/hook",
    "https://user:password@example.com/hook",
])
def test_webhook_url_rejects_ssrf_destinations(url):
    with pytest.raises(ValueError):
        _validate_webhook_url(url)


@pytest.mark.asyncio
async def test_http_post_blocks_private_destination_before_network():
    status, error = await _http_post(
        "https://169.254.169.254/latest/meta-data",
        b"{}",
        "sha256=test",
    )
    assert status == 0
    assert error == "blocked_destination"


@pytest.mark.asyncio
async def test_http_post_does_not_persist_remote_response_body(monkeypatch):
    async def fake_resolve(_url):
        return "receiver.example", 443, ("203.0.113.10",)

    class FakeResponse:
        status_code = 403
        is_success = False
        text = "secret tenant response body"

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, _url, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        "lians.webhook_service._resolve_webhook_destination",
        fake_resolve,
    )
    monkeypatch.setattr(
        "lians.webhook_service._pinned_webhook_url",
        lambda *_args: ("https://203.0.113.10/hook", "receiver.example"),
    )
    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)

    status, error = await _http_post(
        "https://receiver.example/hook",
        b"{}",
        "sha256=test",
    )

    assert status == 403
    assert error == "http_status_403"
    assert "secret" not in error


@pytest.mark.asyncio
async def test_http_post_emits_canonical_and_legacy_signature_headers(monkeypatch):
    captured: dict = {}

    async def fake_resolve(_url):
        return "receiver.example", 443, ("203.0.113.10",)

    class FakeResponse:
        status_code = 204
        is_success = True
        text = ""

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["request"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(
        "lians.webhook_service._resolve_webhook_destination",
        fake_resolve,
    )
    monkeypatch.setattr(
        "lians.webhook_service._pinned_webhook_url",
        lambda *_args: ("https://203.0.113.10/hook", "receiver.example"),
    )
    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)

    signature = "sha256=canonical-test"
    status, error = await _http_post(
        "https://receiver.example/hook",
        b"{}",
        signature,
    )

    assert status == 204
    assert error == ""
    assert captured["request"]["headers"]["X-Lians-Signature"] == signature
    assert captured["request"]["headers"]["X-AgentMem-Signature"] == signature


@pytest.mark.asyncio
async def test_register_webhook_rejects_unknown_event(db):
    with pytest.raises(ValueError, match="Unknown event types"):
        await register_webhook(
            db, TEST_NS,
            url="https://example.com/hook",
            secret="s",
            events=["not.a.real.event"],
        )


@pytest.mark.asyncio
async def test_list_webhooks_namespace_isolation(db):
    await register_webhook(db, TEST_NS, url="https://a.example.com", secret="s", events=[MEMORY_CONFLICT])
    await register_webhook(db, "other-ns", url="https://b.example.com", secret="s", events=[MEMORY_CONFLICT])
    endpoints = await list_webhooks(db, TEST_NS)
    assert len(endpoints) == 1
    assert endpoints[0].url == "https://a.example.com"


@pytest.mark.asyncio
async def test_delete_webhook(db):
    ep = await register_webhook(db, TEST_NS, url="https://example.com", secret="s", events=[MEMORY_ERASED])
    deleted = await delete_webhook(db, TEST_NS, ep.id, ep.updated_at)
    assert deleted is True
    remaining = await list_webhooks(db, TEST_NS)
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_delete_webhook_wrong_namespace_returns_false(db):
    ep = await register_webhook(db, TEST_NS, url="https://example.com", secret="s", events=[MEMORY_ERASED])
    deleted = await delete_webhook(db, "wrong-ns", ep.id, ep.updated_at)
    assert deleted is False


@pytest.mark.asyncio
async def test_update_webhook_disable(db):
    ep = await register_webhook(db, TEST_NS, url="https://example.com", secret="s", events=[MEMORY_SUPERSEDED])
    updated = await update_webhook(
        db, TEST_NS, ep.id, expected_updated_at=ep.updated_at, enabled=False
    )
    assert updated is not None
    assert updated.enabled is False


@pytest.mark.asyncio
async def test_update_webhook_events(db):
    ep = await register_webhook(db, TEST_NS, url="https://example.com", secret="s", events=[MEMORY_SUPERSEDED])
    updated = await update_webhook(
        db,
        TEST_NS,
        ep.id,
        expected_updated_at=ep.updated_at,
        events=[MEMORY_CONFLICT, MEMORY_ERASED],
    )
    assert set(updated.events) == {MEMORY_CONFLICT, MEMORY_ERASED}


@pytest.mark.asyncio
async def test_update_webhook_rejects_a_stale_timestamp(db):
    ep = await register_webhook(
        db,
        TEST_NS,
        url="https://example.com",
        secret="s",
        events=[MEMORY_SUPERSEDED],
    )

    with pytest.raises(WebhookConflictError, match="Resource version conflict"):
        await update_webhook(
            db,
            TEST_NS,
            ep.id,
            expected_updated_at=ep.updated_at - timedelta(microseconds=1),
            enabled=False,
        )


@pytest.mark.asyncio
async def test_register_webhook_enforces_namespace_capacity(db, monkeypatch):
    monkeypatch.setenv("LEGACY_WEBHOOK_MAX_ENDPOINTS_PER_NAMESPACE", "1")
    get_settings.cache_clear()
    try:
        await register_webhook(
            db,
            TEST_NS,
            url="https://first.example.com/hook",
            secret="first-secret",
            events=[MEMORY_SUPERSEDED],
        )
        with pytest.raises(WebhookCapacityError):
            await register_webhook(
                db,
                TEST_NS,
                url="https://second.example.com/hook",
                secret="second-secret",
                events=[MEMORY_SUPERSEDED],
            )
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_delete_webhook_rejects_a_stale_timestamp(db):
    ep = await register_webhook(
        db,
        TEST_NS,
        url="https://example.com",
        secret="s",
        events=[MEMORY_SUPERSEDED],
    )

    with pytest.raises(WebhookConflictError, match="Resource version conflict"):
        await delete_webhook(
            db,
            TEST_NS,
            ep.id,
            ep.updated_at - timedelta(microseconds=1),
        )


# â”€â”€ Dispatch â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.asyncio
async def test_dispatch_calls_http_for_matching_endpoint(db):
    await register_webhook(db, TEST_NS, url="https://recv.example.com/hook", secret="secret123",
                           events=[MEMORY_SUPERSEDED])
    await db.commit()

    captured: list[tuple] = []

    async def fake_http(url, body, signature):
        captured.append((url, body, signature))
        return 200, ""

    with patch("lians.webhook_service._http_post", side_effect=fake_http):
        # Flush so the endpoint is visible to the task
        await dispatch_event(db, TEST_NS, MEMORY_SUPERSEDED, {
            "superseded_memory_id": str(uuid.uuid4()),
            "superseded_by_memory_id": str(uuid.uuid4()),
            "agent_id": "agent-1",
            "confidence": 1.0,
        })
        await db.commit()
        # Allow background tasks to run
        import asyncio
        await asyncio.sleep(0.1)

    assert len(captured) == 1
    url, body, sig = captured[0]
    assert url == "https://recv.example.com/hook"
    payload = json.loads(body)
    assert payload["event"] == MEMORY_SUPERSEDED
    assert payload["namespace"] == TEST_NS
    assert sig.startswith("sha256=")

    # Verify HMAC
    expected_sig = _sign("secret123", body)
    assert hmac.compare_digest(sig, expected_sig)


@pytest.mark.asyncio
async def test_dispatch_skips_disabled_endpoint(db):
    ep = await register_webhook(db, TEST_NS, url="https://recv.example.com", secret="s",
                                events=[MEMORY_SUPERSEDED])
    await update_webhook(
        db, TEST_NS, ep.id, expected_updated_at=ep.updated_at, enabled=False
    )
    await db.commit()

    captured = []

    async def fake_http(url, body, signature):
        captured.append(url)
        return 200, ""

    with patch("lians.webhook_service._http_post", side_effect=fake_http):
        await dispatch_event(db, TEST_NS, MEMORY_SUPERSEDED, {"dummy": True})
        import asyncio
        await asyncio.sleep(0.1)

    assert len(captured) == 0, "Disabled endpoint must not receive deliveries"


@pytest.mark.asyncio
async def test_dispatch_skips_non_matching_event(db):
    await register_webhook(db, TEST_NS, url="https://recv.example.com", secret="s",
                           events=[MEMORY_ERASED])  # only erasure events
    await db.commit()

    captured = []

    async def fake_http(url, body, signature):
        captured.append(url)
        return 200, ""

    with patch("lians.webhook_service._http_post", side_effect=fake_http):
        await dispatch_event(db, TEST_NS, MEMORY_SUPERSEDED, {"dummy": True})
        import asyncio
        await asyncio.sleep(0.1)

    assert len(captured) == 0, "Endpoint not subscribed to MEMORY_SUPERSEDED must not receive it"


@pytest.mark.asyncio
async def test_dispatch_namespace_isolation(db):
    await register_webhook(db, "other-ns", url="https://other.example.com", secret="s",
                           events=[MEMORY_CONFLICT])
    await db.commit()

    captured = []

    async def fake_http(url, body, sig):
        captured.append(url)
        return 200, ""

    with patch("lians.webhook_service._http_post", side_effect=fake_http):
        await dispatch_event(db, TEST_NS, MEMORY_CONFLICT, {"dummy": True})
        import asyncio
        await asyncio.sleep(0.1)

    assert len(captured) == 0


# â”€â”€ API routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.asyncio
async def test_api_register_webhook(client):
    resp = await client.post(
        "/v1/webhooks",
        json={
            "url": "https://api-recv.example.com/hook",
            "events": [MEMORY_SUPERSEDED, MEMORY_CONFLICT],
        },
        headers=_api_h(),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "endpoint" in body
    assert "secret" in body  # secret returned once at registration
    assert len(body["secret"]) >= 32
    assert set(body["endpoint"]["events"]) == {MEMORY_SUPERSEDED, MEMORY_CONFLICT}


@pytest.mark.asyncio
async def test_api_register_webhook_invalid_event(client):
    resp = await client.post(
        "/v1/webhooks",
        json={"url": "https://x.example.com", "events": ["bad.event"]},
        headers=_api_h(),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_api_list_webhooks(client):
    await client.post("/v1/webhooks",
                      json={"url": "https://a.example.com", "events": [MEMORY_ERASED]},
                      headers=_api_h())
    await client.post("/v1/webhooks",
                      json={"url": "https://b.example.com", "events": [MEMORY_CONFLICT]},
                      headers=_api_h())
    resp = await client.get("/v1/webhooks", headers=_api_h())
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_api_delete_webhook(client):
    r = await client.post("/v1/webhooks",
                          json={"url": "https://del.example.com", "events": [MEMORY_ERASED]},
                          headers=_api_h())
    ep_id = r.json()["endpoint"]["id"]
    expected_updated_at = r.json()["endpoint"]["updated_at"]
    del_resp = await client.delete(
        f"/v1/webhooks/{ep_id}",
        params={"expected_updated_at": expected_updated_at},
        headers=_api_h(),
    )
    assert del_resp.status_code == 204
    list_resp = await client.get("/v1/webhooks", headers=_api_h())
    assert len(list_resp.json()) == 0


@pytest.mark.asyncio
async def test_api_patch_webhook(client):
    r = await client.post("/v1/webhooks",
                          json={"url": "https://patch.example.com", "events": [MEMORY_SUPERSEDED]},
                          headers=_api_h())
    ep_id = r.json()["endpoint"]["id"]
    expected_updated_at = r.json()["endpoint"]["updated_at"]
    patch_resp = await client.patch(
        f"/v1/webhooks/{ep_id}",
        json={
            "expected_updated_at": expected_updated_at,
            "enabled": False,
            "description": "Disabled for maintenance",
        },
        headers=_api_h(),
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["enabled"] is False
    assert patch_resp.json()["description"] == "Disabled for maintenance"


@pytest.mark.asyncio
async def test_api_webhook_deliveries(client, db):
    r = await client.post("/v1/webhooks",
                          json={"url": "https://dl.example.com", "events": [MEMORY_SUPERSEDED]},
                          headers=_api_h())
    ep_id = r.json()["endpoint"]["id"]

    # Manually insert a delivery record
    from sqlalchemy import select as sa_select
    ep = (await db.execute(sa_select(WebhookEndpoint).where(
        WebhookEndpoint.id == uuid.UUID(ep_id)))).scalar_one()
    db.add(WebhookDelivery(
        endpoint_id=ep.id,
        event_type=MEMORY_SUPERSEDED,
        payload={"event": MEMORY_SUPERSEDED},
        status_code=200,
    ))
    await db.commit()

    resp = await client.get(f"/v1/webhooks/{ep_id}/deliveries", headers=_api_h())
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["returned"] == 1
    assert data["complete"] is True
    assert data["deliveries"][0]["event_type"] == MEMORY_SUPERSEDED
    assert data["deliveries"][0]["status_code"] == 200


# â”€â”€ End-to-end: supersession triggers webhook â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.asyncio
async def test_supersession_dispatches_webhook(client, db):
    """Adding a newer memory triggers MEMORY_SUPERSEDED to registered endpoint."""
    r = await client.post("/v1/webhooks",
                          json={"url": "https://sup.example.com/hook",
                                "events": [MEMORY_SUPERSEDED],
                                "secret": "test-secret-strong-enough"},
                          headers=_api_h())
    assert r.status_code == 201

    captured = []

    async def fake_http(url, body, sig):
        captured.append(json.loads(body))
        return 200, ""

    with patch("lians.webhook_service._http_post", side_effect=fake_http):
        # Old memory
        await client.post("/v1/memories", json={
            "agent_id": "agent-wh",
            "content": "AAPL EPS $1.40",
            "event_time": T0.isoformat(),
            "metadata": {"ticker": "AAPL", "metric": "eps"},
        }, headers=_api_h())

        # New memory â€” supersedes old
        await client.post("/v1/memories", json={
            "agent_id": "agent-wh",
            "content": "AAPL EPS revised to $1.45",
            "event_time": T1.isoformat(),
            "metadata": {"ticker": "AAPL", "metric": "eps"},
        }, headers=_api_h())

        import asyncio
        await asyncio.sleep(0.2)

    assert any(p["event"] == MEMORY_SUPERSEDED for p in captured), \
        "Supersession must dispatch memory.superseded webhook"
