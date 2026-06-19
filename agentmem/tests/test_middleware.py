"""
Tests for production middleware: deep health check, request IDs,
structured JSON logging, and rate limiting.
"""
from __future__ import annotations

import hashlib
import json
import logging
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient, ASGITransport

from src.agentmem.main import app
from src.agentmem.db import get_db
from src.agentmem.models import ApiKey

TEST_KEY = "middleware-test-key"
TEST_NS = "mw-test-ns"


@pytest_asyncio.fixture
async def client(db):
    hashed = hashlib.sha256(TEST_KEY.encode()).hexdigest()
    db.add(ApiKey(hashed_key=hashed, namespace=TEST_NS, scopes=["read", "write"]))
    await db.commit()

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


# ── Deep health check ────────────────────────────────────────────────────────

class TestHealthEndpoint:

    async def test_health_returns_200_when_db_ok(self, client):
        # DB is SQLite in-memory (always reachable); mock Redis ping to succeed
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["db"] == "ok"
        assert body["checks"]["redis"] == "ok"

    async def test_health_returns_503_when_redis_down(self, client):
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(side_effect=ConnectionError("Redis down"))
            resp = await client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["checks"]["redis"].startswith("error:")
        assert body["checks"]["db"] == "ok"

    async def test_health_returns_503_when_db_down(self, db):
        """Simulate DB failure by overriding get_db with a session that raises on execute."""
        from src.agentmem.db import get_db

        bad_session = AsyncMock()
        bad_session.execute = AsyncMock(side_effect=Exception("DB unreachable"))

        async def _bad_db():
            yield bad_session

        app.dependency_overrides[get_db] = _bad_db
        try:
            with patch("src.agentmem.cache._get_redis") as mock_redis:
                mock_redis.return_value.ping = AsyncMock(return_value=True)
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as c:
                    resp = await c.get("/health")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["checks"]["db"].startswith("error:")

    async def test_health_no_auth_required(self, client):
        """Health endpoint must be reachable without an API key."""
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            resp = await client.get("/health")
        # 200 or 503 — either is fine, but NOT 401
        assert resp.status_code in (200, 503)

    async def test_health_includes_both_checks(self, client):
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            resp = await client.get("/health")
        body = resp.json()
        assert "db" in body["checks"]
        assert "redis" in body["checks"]


# ── Request ID middleware ─────────────────────────────────────────────────────

class TestRequestIDMiddleware:

    async def test_request_id_generated_when_absent(self, client):
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            resp = await client.get("/health")
        assert "x-request-id" in resp.headers
        req_id = resp.headers["x-request-id"]
        assert len(req_id) == 36  # UUID4 format

    async def test_request_id_propagated_from_caller(self, client):
        caller_id = "my-trace-abc-123"
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            resp = await client.get("/health", headers={"X-Request-ID": caller_id})
        assert resp.headers["x-request-id"] == caller_id

    async def test_each_request_gets_unique_id(self, client):
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            r1 = await client.get("/health")
            r2 = await client.get("/health")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


# ── Structured JSON logging ───────────────────────────────────────────────────

class TestJSONFormatter:

    def test_formats_as_valid_json(self):
        from src.agentmem.middleware import _JSONFormatter
        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="agentmem.test", level=logging.INFO,
            pathname="", lineno=0, msg="hello world",
            args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["msg"] == "hello world"
        assert parsed["logger"] == "agentmem.test"
        assert "ts" in parsed

    def test_includes_extra_fields(self):
        from src.agentmem.middleware import _JSONFormatter
        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="agentmem.access", level=logging.INFO,
            pathname="", lineno=0, msg="",
            args=(), exc_info=None,
        )
        record.method = "POST"
        record.path = "/v1/memories"
        record.status = 200
        record.duration_ms = 42.1
        record.request_id = "abc-123"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["method"] == "POST"
        assert parsed["path"] == "/v1/memories"
        assert parsed["status"] == 200
        assert parsed["duration_ms"] == 42.1
        assert parsed["request_id"] == "abc-123"

    def test_omits_empty_msg(self):
        from src.agentmem.middleware import _JSONFormatter
        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="n", level=logging.INFO,
            pathname="", lineno=0, msg="",
            args=(), exc_info=None,
        )
        parsed = json.loads(formatter.format(record))
        assert "msg" not in parsed

    def test_includes_exception_info(self):
        from src.agentmem.middleware import _JSONFormatter
        formatter = _JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="n", level=logging.ERROR,
            pathname="", lineno=0, msg="oops",
            args=(), exc_info=exc_info,
        )
        parsed = json.loads(formatter.format(record))
        assert "exc" in parsed
        assert "ValueError" in parsed["exc"]


# ── Rate limiting ─────────────────────────────────────────────────────────────

class TestRateLimitMiddleware:

    async def test_under_limit_passes(self, client):
        """Requests within the limit return the normal response."""
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            r = AsyncMock()
            r.incr = AsyncMock(return_value=1)
            r.expire = AsyncMock()
            r.ping = AsyncMock(return_value=True)
            mock_redis.return_value = r
            resp = await client.get("/health")
        # Health is exempt from rate limiting — always passes
        assert resp.status_code in (200, 503)

    async def test_over_limit_returns_429(self, client):
        """When Redis returns a count above the limit, respond with 429."""
        from src.agentmem.middleware import RateLimitMiddleware

        with patch("src.agentmem.cache._get_redis") as mock_redis:
            r = AsyncMock()
            # Simulate count already exceeding the 300 req/min default
            r.incr = AsyncMock(return_value=301)
            r.expire = AsyncMock()
            mock_redis.return_value = r

            resp = await client.post(
                "/v1/recall",
                json={"agent_id": "a", "query": "test"},
                headers={"X-API-Key": TEST_KEY},
            )

        assert resp.status_code == 429
        body = resp.json()
        assert "Rate limit exceeded" in body["detail"]
        assert "Retry-After" in resp.headers
        assert resp.headers["Retry-After"] == "60"

    async def test_429_includes_ratelimit_headers(self, client):
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            r = AsyncMock()
            r.incr = AsyncMock(return_value=999)
            r.expire = AsyncMock()
            mock_redis.return_value = r

            resp = await client.post(
                "/v1/recall",
                json={"agent_id": "a", "query": "test"},
                headers={"X-API-Key": TEST_KEY},
            )

        assert resp.status_code == 429
        assert resp.headers.get("X-RateLimit-Limit") == "300"
        assert resp.headers.get("X-RateLimit-Remaining") == "0"

    async def test_redis_down_fails_open(self, client):
        """If Redis is unreachable, rate limiting must not block requests."""
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            mock_redis.return_value.incr = AsyncMock(side_effect=ConnectionError("Redis down"))
            resp = await client.post(
                "/v1/recall",
                json={"agent_id": "a", "query": "test"},
                headers={"X-API-Key": TEST_KEY},
            )
        # Should get a normal response (401/200/422) — NOT 429
        assert resp.status_code != 429

    async def test_health_exempt_from_rate_limit(self, client):
        """Health checks must never be rate-limited regardless of Redis state."""
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            r = AsyncMock()
            r.incr = AsyncMock(return_value=9999)  # way over limit
            r.expire = AsyncMock()
            r.ping = AsyncMock(return_value=True)
            mock_redis.return_value = r
            resp = await client.get("/health")
        assert resp.status_code != 429

    async def test_no_api_key_skips_rate_limit(self, client):
        """Unauthenticated requests are handled by auth, not rate limiting."""
        with patch("src.agentmem.cache._get_redis") as mock_redis:
            r = AsyncMock()
            r.incr = AsyncMock(return_value=9999)
            r.expire = AsyncMock()
            mock_redis.return_value = r
            resp = await client.post(
                "/v1/recall",
                json={"agent_id": "a", "query": "test"},
            )
        # Auth middleware should 401, not rate limiter 429
        assert resp.status_code == 401
