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
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient, ASGITransport

from src.lians.main import app
from src.lians.db import get_db
from src.lians.models import ApiKey

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


# â”€â”€ Deep health check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestHealthEndpoint:

    async def test_health_returns_200_when_db_ok(self, client):
        # DB is SQLite in-memory (always reachable); mock Redis ping to succeed
        with patch("src.lians.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["db"] == "ok"
        assert body["checks"]["redis"] == "ok"

    async def test_health_returns_503_when_redis_down(self, client):
        with patch("src.lians.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(side_effect=ConnectionError("Redis down"))
            resp = await client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["checks"]["redis"].startswith("error:")
        assert body["checks"]["db"] == "ok"

    async def test_health_returns_503_when_db_down(self, db):
        """Simulate DB failure by overriding get_db with a session that raises on execute."""
        from src.lians.db import get_db

        bad_session = AsyncMock()
        bad_session.execute = AsyncMock(side_effect=Exception("DB unreachable"))

        async def _bad_db():
            yield bad_session

        app.dependency_overrides[get_db] = _bad_db
        try:
            with patch("src.lians.cache._get_redis") as mock_redis:
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
        with patch("src.lians.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            resp = await client.get("/health")
        # 200 or 503 â€” either is fine, but NOT 401
        assert resp.status_code in (200, 503)

    async def test_health_includes_both_checks(self, client):
        with patch("src.lians.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            resp = await client.get("/health")
        body = resp.json()
        assert "db" in body["checks"]
        assert "redis" in body["checks"]


# â”€â”€ Request ID middleware â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestRequestIDMiddleware:

    async def test_request_id_generated_when_absent(self, client):
        with patch("src.lians.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            resp = await client.get("/health")
        assert "x-request-id" in resp.headers
        req_id = resp.headers["x-request-id"]
        assert len(req_id) == 36  # UUID4 format

    async def test_request_id_propagated_from_caller(self, client):
        caller_id = "my-trace-abc-123"
        with patch("src.lians.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            resp = await client.get("/health", headers={"X-Request-ID": caller_id})
        assert resp.headers["x-request-id"] == caller_id

    async def test_each_request_gets_unique_id(self, client):
        with patch("src.lians.cache._get_redis") as mock_redis:
            mock_redis.return_value.ping = AsyncMock(return_value=True)
            r1 = await client.get("/health")
            r2 = await client.get("/health")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


# â”€â”€ Structured JSON logging â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestJSONFormatter:

    def test_formats_as_valid_json(self):
        from src.lians.middleware import _JSONFormatter
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
        from src.lians.middleware import _JSONFormatter
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
        from src.lians.middleware import _JSONFormatter
        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="n", level=logging.INFO,
            pathname="", lineno=0, msg="",
            args=(), exc_info=None,
        )
        parsed = json.loads(formatter.format(record))
        assert "msg" not in parsed

    def test_includes_exception_info(self):
        from src.lians.middleware import _JSONFormatter
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


# â”€â”€ Rate limiting â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestRateLimitMiddleware:

    async def test_redis_window_increment_is_one_atomic_ttl_repair(self):
        from src.lians.cache import _redis_fixed_window_increment

        redis = AsyncMock()
        redis.eval = AsyncMock(return_value=3)

        count = await _redis_fixed_window_increment(
            redis,
            "agentmem:rl:test",
            amount=2,
            window_seconds=60,
        )

        assert count == 3
        redis.eval.assert_awaited_once()
        script, key_count, key, amount, window = redis.eval.await_args.args
        assert key_count == 1
        assert key == "agentmem:rl:test"
        assert amount == 2
        assert window == 60
        assert all(command in script for command in ("INCRBY", "TTL", "EXPIRE"))

    async def test_under_limit_passes(self, client):
        """Requests within the limit return the normal response."""
        with patch("src.lians.cache._get_redis") as mock_redis:
            r = AsyncMock()
            r.eval = AsyncMock(return_value=1)
            r.expire = AsyncMock()
            r.ping = AsyncMock(return_value=True)
            mock_redis.return_value = r
            resp = await client.get("/health")
        # Health is exempt from rate limiting â€” always passes
        assert resp.status_code in (200, 503)

    async def test_over_limit_returns_429(self, client):
        """When Redis returns a count above the configured limit, respond with 429."""
        from src.lians.config import get_settings

        limit = get_settings().rate_limit_per_minute
        with patch("src.lians.cache._get_redis") as mock_redis:
            r = AsyncMock()
            # One past the configured limit - independent of the ambient value.
            r.eval = AsyncMock(return_value=limit + 1)
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
        from src.lians.config import get_settings

        limit = get_settings().rate_limit_per_minute
        with patch("src.lians.cache._get_redis") as mock_redis:
            r = AsyncMock()
            r.eval = AsyncMock(return_value=limit + 699)
            r.expire = AsyncMock()
            mock_redis.return_value = r

            resp = await client.post(
                "/v1/recall",
                json={"agent_id": "a", "query": "test"},
                headers={"X-API-Key": TEST_KEY},
            )

        assert resp.status_code == 429
        # The header reflects the *configured* limit - the regression this proves
        # is that the middleware is wired to the setting, not the hardcoded 300.
        assert resp.headers.get("X-RateLimit-Limit") == str(limit)
        assert resp.headers.get("X-RateLimit-Remaining") == "0"

    async def test_redis_down_fails_open(self, client):
        """The first local-fallback request passes when Redis is unreachable."""
        with patch("src.lians.cache._get_redis") as mock_redis:
            mock_redis.return_value.eval = AsyncMock(
                side_effect=ConnectionError("Redis down")
            )
            resp = await client.post(
                "/v1/recall",
                json={"agent_id": "a", "query": "test"},
                headers={"X-API-Key": TEST_KEY},
            )
        # Should get a normal response (401/200/422) â€” NOT 429
        assert resp.status_code != 429

    async def test_redis_down_uses_bounded_local_fallback(self):
        """Redis failure must not silently disable throttling."""
        from fastapi import FastAPI
        from src.lians.middleware import RateLimitMiddleware

        limited_app = FastAPI()

        @limited_app.get("/limited")
        async def limited():
            return {"ok": True}

        limited_app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=1,
            fingerprint_secret="test-rate-limit-fingerprint-secret",
        )
        with patch("src.lians.cache._get_redis") as mock_redis:
            mock_redis.return_value.eval = AsyncMock(
                side_effect=ConnectionError("Redis down")
            )
            async with AsyncClient(
                transport=ASGITransport(app=limited_app),
                base_url="http://test",
            ) as local_client:
                first = await local_client.get(
                    "/limited", headers={"X-API-Key": "fallback-key"}
                )
                second = await local_client.get(
                    "/limited", headers={"X-API-Key": "fallback-key"}
                )

        assert first.status_code == 200
        assert first.headers["X-RateLimit-Remaining"] == "0"
        assert second.status_code == 429
        assert second.headers["Retry-After"] == "60"

    async def test_redis_timeout_is_not_retried_and_records_one_fallback(self):
        """A timed-out mutating script may have committed, so never retry it."""
        from fastapi import FastAPI
        from src.lians.middleware import RateLimitMiddleware

        limited_app = FastAPI()

        @limited_app.get("/limited")
        async def limited():
            return {"ok": True}

        limited_app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=1,
            fingerprint_secret="test-rate-limit-fingerprint-secret",
        )
        with (
            patch("src.lians.cache._get_redis") as mock_redis,
            patch("src.lians.degradation.record_degradation") as degradation,
        ):
            redis = AsyncMock()
            redis.eval = AsyncMock(side_effect=TimeoutError("slow Redis reply"))
            mock_redis.return_value = redis
            async with AsyncClient(
                transport=ASGITransport(app=limited_app),
                base_url="http://test",
            ) as local_client:
                response = await local_client.get(
                    "/limited", headers={"X-API-Key": "fallback-key"}
                )

        assert response.status_code == 200
        assert response.headers["X-RateLimit-Remaining"] == "0"
        assert redis.eval.await_count == 1
        degradation.assert_called_once_with("rate_limit", "redis_unavailable")

    def test_api_key_discriminator_is_keyed_and_stable(self):
        """Bucket IDs must not expose a reusable digest of the API key."""
        from src.lians.middleware import RateLimitMiddleware

        middleware_a = RateLimitMiddleware(
            app=AsyncMock(),
            fingerprint_secret="fingerprint-secret-a",
        )
        middleware_b = RateLimitMiddleware(
            app=AsyncMock(),
            fingerprint_secret="fingerprint-secret-b",
        )

        first = middleware_a._api_key_discriminator("customer-api-key")
        repeated = middleware_a._api_key_discriminator("customer-api-key")
        other_key = middleware_a._api_key_discriminator("other-api-key")
        other_secret = middleware_b._api_key_discriminator("customer-api-key")

        assert first == repeated
        assert first.startswith("api:")
        assert len(first.removeprefix("api:")) == 32
        assert "customer-api-key" not in first
        assert first != other_key
        assert first != other_secret

    def test_api_key_discriminator_rejects_an_empty_secret(self):
        from src.lians.middleware import RateLimitMiddleware

        with pytest.raises(ValueError, match="fingerprint_secret must not be empty"):
            RateLimitMiddleware(app=AsyncMock(), fingerprint_secret="")

    async def test_health_exempt_from_rate_limit(self, client):
        """Health checks must never be rate-limited regardless of Redis state."""
        with patch("src.lians.cache._get_redis") as mock_redis:
            r = AsyncMock()
            r.eval = AsyncMock(return_value=9999)  # way over limit
            r.expire = AsyncMock()
            r.ping = AsyncMock(return_value=True)
            mock_redis.return_value = r
            resp = await client.get("/health")
        assert resp.status_code != 429

    async def test_no_api_key_skips_rate_limit(self, client):
        """Unauthenticated requests are handled by auth, not rate limiting."""
        with patch("src.lians.cache._get_redis") as mock_redis:
            r = AsyncMock()
            r.eval = AsyncMock(return_value=9999)
            r.expire = AsyncMock()
            mock_redis.return_value = r
            resp = await client.post(
                "/v1/recall",
                json={"agent_id": "a", "query": "test"},
            )
        # Auth middleware should 401, not rate limiter 429
        assert resp.status_code == 401

    async def test_admin_secret_guesses_are_rate_limited_by_client_ip(self, client):
        """Changing an admin-secret guess must not create a fresh rate bucket."""
        from src.lians.config import get_settings

        limit = get_settings().rate_limit_per_minute
        with patch("src.lians.cache._get_redis") as mock_redis:
            r = AsyncMock()
            r.eval = AsyncMock(return_value=limit + 1)
            r.expire = AsyncMock()
            mock_redis.return_value = r

            resp = await client.get(
                "/v1/admin/api-keys",
                headers={"X-Admin-Secret": "a-different-wrong-guess"},
            )

        assert resp.status_code == 429
        redis_key = r.eval.await_args.args[2]
        assert redis_key.startswith("agentmem:rl:admin:")

    async def test_provisioning_secret_guesses_are_rate_limited_by_client_ip(self, client):
        """Changing a provisioning-secret guess must not create a fresh bucket."""
        from src.lians.config import get_settings

        limit = get_settings().rate_limit_per_minute
        with patch("src.lians.cache._get_redis") as mock_redis:
            r = AsyncMock()
            r.eval = AsyncMock(return_value=limit + 1)
            r.expire = AsyncMock()
            mock_redis.return_value = r

            resp = await client.get(
                "/v1/provisioning/api-keys?namespace=ns_test",
                headers={
                    "X-Provisioning-Secret": "a-different-wrong-guess",
                    "X-Lians-Namespace": "ns_test",
                },
            )

        assert resp.status_code == 429
        redis_key = r.eval.await_args.args[2]
        assert redis_key.startswith("agentmem:rl:provisioning:")
        assert "a-different-wrong-guess" not in redis_key

    @pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
    async def test_hosted_mcp_bearer_requests_are_rate_limited(self, path):
        """Both canonical and slash-normalized MCP paths share bearer throttling."""
        from fastapi import FastAPI
        from src.lians.middleware import RateLimitMiddleware

        limited_app = FastAPI()

        @limited_app.post("/mcp")
        @limited_app.post("/mcp/")
        async def mcp_endpoint():
            return {"ok": True}

        limited_app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=1,
            fingerprint_secret="test-rate-limit-fingerprint-secret",
        )
        with patch("src.lians.cache._get_redis") as mock_redis:
            redis = AsyncMock()
            redis.eval = AsyncMock(return_value=2)
            redis.expire = AsyncMock()
            mock_redis.return_value = redis
            async with AsyncClient(
                transport=ASGITransport(app=limited_app),
                base_url="http://test",
            ) as local_client:
                response = await local_client.post(
                    path,
                    headers={"Authorization": "Bearer test-oauth-token"},
                )

        assert response.status_code == 429
        redis_key = redis.eval.await_args.args[2]
        assert redis_key.startswith("agentmem:rl:mcp-client:")
        assert "test-oauth-token" not in redis_key

    async def test_rotating_invalid_mcp_tokens_cannot_create_fresh_rate_buckets(self):
        from fastapi import FastAPI
        from src.lians.middleware import RateLimitMiddleware

        limited_app = FastAPI()

        @limited_app.post("/mcp")
        async def mcp_endpoint():
            return {"ok": True}

        limited_app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=10,
            fingerprint_secret="test-rate-limit-fingerprint-secret",
        )
        with patch("src.lians.cache._get_redis") as mock_redis:
            redis = AsyncMock()
            redis.eval = AsyncMock(side_effect=[1, 2])
            redis.expire = AsyncMock()
            mock_redis.return_value = redis
            async with AsyncClient(
                transport=ASGITransport(app=limited_app),
                base_url="http://test",
            ) as local_client:
                for token in ("first-invalid-token", "second-invalid-token"):
                    response = await local_client.post(
                        "/mcp",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    assert response.status_code == 200

        bucket_keys = [call.args[2] for call in redis.eval.await_args_list]
        assert len(set(bucket_keys)) == 1
        assert bucket_keys[0].startswith("agentmem:rl:mcp-client:")
        assert all(token not in bucket_keys[0] for token in ("first", "second"))

    def test_configured_limit_is_wired_into_the_middleware(self):
        """RATE_LIMIT_PER_MINUTE must actually reach the middleware.

        Regression: the app added RateLimitMiddleware with no argument, so the
        middleware used its hardcoded 300 default and the documented, tunable
        RATE_LIMIT_PER_MINUTE setting had no effect in any deployment.
        """
        from src.lians.main import app
        from src.lians.middleware import RateLimitMiddleware
        from src.lians.config import get_settings

        entry = next(m for m in app.user_middleware if m.cls is RateLimitMiddleware)
        wired = entry.kwargs.get("requests_per_minute")
        if wired is None and entry.args:
            wired = entry.args[0]
        assert wired == get_settings().rate_limit_per_minute, (
            "RateLimitMiddleware is not wired to the configured limit - "
            "RATE_LIMIT_PER_MINUTE is being ignored"
        )
        assert entry.kwargs.get("fingerprint_secret") == get_settings().api_secret_seed


@pytest.mark.asyncio
async def test_production_security_headers_are_applied():
    from fastapi import FastAPI
    from src.lians.middleware import SecurityHeadersMiddleware

    secured_app = FastAPI()

    @secured_app.get("/resource")
    async def resource():
        return {"ok": True}

    secured_app.add_middleware(SecurityHeadersMiddleware, production=True)
    async with AsyncClient(
        transport=ASGITransport(app=secured_app), base_url="https://test"
    ) as security_client:
        response = await security_client.get("/resource")

    assert response.headers["Strict-Transport-Security"].startswith("max-age=")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]


@pytest.mark.asyncio
async def test_oversized_request_body_returns_413(client):
    """Reject oversized uploads before JSON parsing or route execution."""
    from src.lians.config import get_settings

    payload = b'{"agent_id":"a","content":"' + (
        b"x" * get_settings().max_request_body_bytes
    ) + b'"}'
    resp = await client.post(
        "/v1/memories",
        content=payload,
        headers={"X-API-Key": TEST_KEY, "Content-Type": "application/json"},
    )

    assert resp.status_code == 413
    assert resp.json()["detail"] == "Request body too large"
