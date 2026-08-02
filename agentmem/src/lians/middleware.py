"""
Production middleware: request IDs, structured JSON access logging, rate limiting.

RequestIDMiddleware   — assigns X-Request-ID to every request; propagates via ContextVar
AccessLogMiddleware   — logs one JSON line per request with method/path/status/duration_ms
RateLimitMiddleware   — Redis-backed limit with a bounded local fallback

All three are registered in main.py before any route middleware so they wrap
every request uniformly, including 4xx/5xx responses from FastAPI's own validation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from collections import OrderedDict
from contextvars import ContextVar
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Propagates the request ID through async call chains so service-layer code
# can attach it to log records without threading the value through every call.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_access_log = logging.getLogger("agentmem.access")


# ── JSON log formatter ───────────────────────────────────────────────────────

class _JSONFormatter(logging.Formatter):
    """Emit one JSON object per log record — compatible with Datadog, Splunk, CloudWatch."""

    _EXTRA_FIELDS = (
        "request_id", "method", "path", "status",
        "duration_ms", "namespace", "agent_id",
    )

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        msg = record.getMessage()
        if msg:
            entry["msg"] = msg
        for field in self._EXTRA_FIELDS:
            val = getattr(record, field, None)
            if val is not None:
                entry[field] = val
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """
    Configure the root logger.  Call once at startup before the app starts
    handling requests.  Replaces uvicorn's access log with our middleware so
    every request line is structured JSON.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        _JSONFormatter() if json_logs else logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Suppress uvicorn's built-in access log — our middleware replaces it
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False

    # Quiet down noisy libraries that are not useful in production logs
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ── Middleware ───────────────────────────────────────────────────────────────

class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Accept X-Request-ID from the caller (useful when a gateway already stamps
    requests) or generate a fresh UUID4.  Always echo it back in the response
    so clients can correlate their request with server-side logs.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request_id_var.set(req_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log one structured JSON line per request after the response is sent."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        _access_log.info(
            "",
            extra={
                "request_id": request_id_var.get(),
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply browser and transport hardening to every API response."""

    def __init__(self, app, *, production: bool = False):
        super().__init__(app)
        self._production = production

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        if self._production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )
        return response


class RequestBodyLimitMiddleware:
    """Streaming ASGI request-body cap; also handles chunked requests."""

    def __init__(self, app, max_bytes: int = 2_000_000):
        self.app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self._max_bytes:
                    return await self._reject(scope, receive, send)
            except ValueError:
                return await self._reject(scope, receive, send)

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            return await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if not response_started:
                return await self._reject(scope, receive, send)
            raise

    async def _reject(self, scope, receive, send):
        response = Response(
            content=json.dumps({"detail": "Request body too large"}),
            status_code=413,
            media_type="application/json",
        )
        return await response(scope, receive, send)


class _RequestBodyTooLarge(Exception):
    pass


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limit keyed by API key hash (300 req/min default).

    Uses Redis INCR + EXPIRE for atomic counting across multiple workers. If
    Redis is unavailable, a bounded per-process fallback keeps throttling
    active instead of silently disabling the control.

    The raw API key is never written to Redis. A server-keyed HMAC produces
    a stable, non-reversible bucket discriminator shared by all workers.
    """

    def __init__(
        self,
        app,
        requests_per_minute: int = 300,
        *,
        fingerprint_secret: str,
    ):
        super().__init__(app)
        if not fingerprint_secret:
            raise ValueError("fingerprint_secret must not be empty")
        self._limit = requests_per_minute
        self._window = 60  # seconds
        self._fingerprint_secret = fingerprint_secret.encode()
        self._fallback: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self._fallback_max_buckets = 10_000

    def _api_key_discriminator(self, raw_key: str) -> str:
        """Return a stable keyed bucket ID without exposing the API key."""
        digest = hmac.new(
            self._fingerprint_secret,
            b"lians-rate-limit-v1\0" + raw_key.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"api:{digest[:32]}"

    def _fallback_increment(self, discriminator: str) -> int:
        """Return the local count using a bounded fixed-minute window."""
        window_id = int(time.monotonic() // self._window)
        previous_window, previous_count = self._fallback.get(
            discriminator, (-1, 0)
        )
        count = previous_count + 1 if previous_window == window_id else 1
        self._fallback[discriminator] = (window_id, count)
        self._fallback.move_to_end(discriminator)
        while len(self._fallback) > self._fallback_max_buckets:
            self._fallback.popitem(last=False)
        return count

    def _limit_response(self) -> Response:
        return Response(
            content=json.dumps({
                "detail": f"Rate limit exceeded ({self._limit} req/min). "
                          f"Retry after {self._window} seconds."
            }),
            status_code=429,
            headers={
                "Content-Type": "application/json",
                "Retry-After": str(self._window),
                "X-RateLimit-Limit": str(self._limit),
                "X-RateLimit-Remaining": "0",
            },
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        # Health checks are exempt — LB probes must never be rate-limited
        if request.url.path == "/health":
            return await call_next(request)

        raw_key = request.headers.get("X-API-Key", "")
        if raw_key:
            discriminator = self._api_key_discriminator(raw_key)
        elif request.url.path.startswith("/v1/admin/"):
            # Admin auth uses a separate header. Keying this bucket by the
            # supplied secret would let an attacker evade throttling by changing
            # every guess, so use the network client identity instead.
            client_host = request.client.host if request.client else "unknown"
            discriminator = (
                "admin:"
                + hashlib.sha256(client_host.encode()).hexdigest()[:16]
            )
        else:
            # Unauthenticated requests are rejected by auth middleware before
            # they reach any route handler; no need to rate-limit here.
            return await call_next(request)

        redis_key = f"agentmem:rl:{discriminator}"

        try:
            from .cache import _get_redis
            r = _get_redis()
            count = await r.incr(redis_key)
            if count == 1:
                await r.expire(redis_key, self._window)

            remaining = max(0, self._limit - count)
            if count > self._limit:
                return self._limit_response()
        except Exception:
            from .degradation import record_degradation
            record_degradation("rate_limit", "redis_unavailable")
            count = self._fallback_increment(discriminator)
            remaining = max(0, self._limit - count)
            if count > self._limit:
                return self._limit_response()

        response = await call_next(request)

        if remaining is not None:
            response.headers["X-RateLimit-Limit"] = str(self._limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
