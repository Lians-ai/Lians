"""
Production middleware: request IDs, structured JSON access logging, rate limiting.

RequestIDMiddleware   — assigns X-Request-ID to every request; propagates via ContextVar
AccessLogMiddleware   — logs one JSON line per request with method/path/status/duration_ms
RateLimitMiddleware   — sliding-window per-API-key limit backed by Redis; fails open

All three are registered in main.py before any route middleware so they wrap
every request uniformly, including 4xx/5xx responses from FastAPI's own validation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limit keyed by API key hash (300 req/min default).

    Uses Redis INCR + EXPIRE for atomic counting across multiple workers.
    Fails open — if Redis is unavailable, requests pass through unthrottled
    rather than taking the service down with it.

    The raw API key is never written to Redis; only the first 16 hex chars
    of its SHA-256 hash are used as the key discriminator.
    """

    def __init__(self, app, requests_per_minute: int = 300):
        super().__init__(app)
        self._limit = requests_per_minute
        self._window = 60  # seconds

    async def dispatch(self, request: Request, call_next) -> Response:
        # Health checks are exempt — LB probes must never be rate-limited
        if request.url.path == "/health":
            return await call_next(request)

        raw_key = request.headers.get("X-API-Key", "")
        if not raw_key:
            # Unauthenticated requests are rejected by auth middleware before
            # they reach any route handler; no need to rate-limit here.
            return await call_next(request)

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
        redis_key = f"agentmem:rl:{key_hash}"

        try:
            from .cache import _get_redis
            r = _get_redis()
            count = await r.incr(redis_key)
            if count == 1:
                await r.expire(redis_key, self._window)

            remaining = max(0, self._limit - count)
            if count > self._limit:
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
        except Exception:
            remaining = None  # Redis down — can't compute, skip headers

        response = await call_next(request)

        if remaining is not None:
            response.headers["X-RateLimit-Limit"] = str(self._limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
