"""
Production middleware: request IDs, structured JSON access logging, rate limiting.

RequestIDMiddleware   — assigns X-Request-ID to every request; propagates via ContextVar
AccessLogMiddleware   — logs one JSON line per request with method/path/status/duration_ms
RateLimitMiddleware   — sliding-window per-API-key limit backed by Redis; fails open

All three are registered in main.py before any route middleware so they wrap
every request uniformly, including 4xx/5xx responses from FastAPI's own validation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from contextvars import ContextVar
from datetime import datetime, timezone
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    collapse_addresses,
    ip_address,
    ip_network,
)

from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .metrics import record_http_request

# Propagates the request ID through async call chains so service-layer code
# can attach it to log records without threading the value through every call.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_access_log = logging.getLogger("lians.access")

_IPAddress = IPv4Address | IPv6Address
_IPNetwork = IPv4Network | IPv6Network
_MAX_FORWARDED_FOR_LENGTH = 2_048
_MAX_FORWARDED_FOR_HOPS = 32
_MAX_TRUSTED_PROXY_CIDRS_LENGTH = 4_096
_MAX_TRUSTED_PROXY_CIDRS = 64
_MAX_REQUEST_ID_LENGTH = 128
_SAFE_REQUEST_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def _route_template(request: Request) -> str:
    """Return only developer-controlled route text for logs and metrics."""

    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not isinstance(template, str) or not template:
        return "__unmatched__"
    # Route definitions are developer-controlled and bounded. Cap this
    # defensively in case a third-party router injects an arbitrary value.
    return template if len(template) <= 160 else "__oversized_template__"


def _request_id(raw_value: str | None) -> str:
    """Accept a bounded opaque correlation token or replace it safely."""

    if (
        raw_value is not None
        and len(raw_value) <= _MAX_REQUEST_ID_LENGTH
        and _SAFE_REQUEST_ID.fullmatch(raw_value) is not None
    ):
        return raw_value
    return str(uuid.uuid4())


class NoStoreResponseMiddleware:
    """Force no-store on capability-bearing endpoints, including error paths."""

    def __init__(self, app, *, paths: Sequence[str]):
        self.app = app
        self.paths = frozenset(paths)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in self.paths:
            await self.app(scope, receive, send)
            return

        async def send_no_store(message):
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["Pragma"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_no_store)


def _normalized_ip(value: str | None) -> _IPAddress | None:
    """Parse one bare IP literal and normalize IPv4-mapped IPv6 addresses."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 64 or "%" in candidate:
        return None
    try:
        parsed = ip_address(candidate)
    except ValueError:
        return None
    if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def parse_trusted_proxy_cidrs(value: str) -> tuple[_IPNetwork, ...]:
    """Parse the explicit proxy trust boundary; empty means trust no proxy."""
    if not isinstance(value, str):
        raise TypeError("TRUSTED_PROXY_CIDRS must be a comma-separated string")
    raw = value.strip()
    if not raw:
        return ()
    if len(raw) > _MAX_TRUSTED_PROXY_CIDRS_LENGTH:
        raise ValueError("TRUSTED_PROXY_CIDRS exceeds 4096 characters")

    entries = [entry.strip() for entry in raw.split(",")]
    if len(entries) > _MAX_TRUSTED_PROXY_CIDRS:
        raise ValueError("TRUSTED_PROXY_CIDRS may contain at most 64 networks")
    if any(not entry for entry in entries):
        raise ValueError("TRUSTED_PROXY_CIDRS contains an empty network")

    networks: list[_IPNetwork] = []
    for entry in entries:
        if "*" in entry or "/" not in entry:
            raise ValueError(
                "TRUSTED_PROXY_CIDRS entries must be explicit IPv4 or IPv6 CIDRs"
            )
        try:
            network = ip_network(entry, strict=True)
        except ValueError as exc:
            raise ValueError(
                f"TRUSTED_PROXY_CIDRS contains invalid CIDR {entry!r}"
            ) from exc
        if network.prefixlen == 0:
            raise ValueError(
                "TRUSTED_PROXY_CIDRS may not contain world-open /0 networks"
            )
        if (
            isinstance(network, IPv6Network)
            and network.network_address.ipv4_mapped is not None
        ):
            raise ValueError(
                "TRUSTED_PROXY_CIDRS must express IPv4-mapped ranges as IPv4 CIDRs"
            )
        if network in networks:
            raise ValueError(
                f"TRUSTED_PROXY_CIDRS contains duplicate CIDR {entry!r}"
            )
        networks.append(network)

    for version in (4, 6):
        family = [network for network in networks if network.version == version]
        if any(network.prefixlen == 0 for network in collapse_addresses(family)):
            raise ValueError(
                "TRUSTED_PROXY_CIDRS may not collectively cover the entire address space"
            )
    return tuple(networks)


def _is_trusted_proxy(address: _IPAddress, networks: Sequence[_IPNetwork]) -> bool:
    return any(
        address.version == network.version and address in network
        for network in networks
    )


def derive_client_address(
    peer_host: str | None,
    forwarded_for_values: Sequence[str],
    trusted_proxy_networks: Sequence[_IPNetwork],
) -> str:
    """Return a canonical client IP without ever returning raw header content.

    X-Forwarded-For is considered only when the socket peer belongs to an
    explicitly trusted CIDR. The walk proceeds from the nearest forwarded hop
    to the left and stops at the first untrusted address, preventing a caller's
    prepended spoofed value from becoming the rate-limit identity.
    """
    peer = _normalized_ip(peer_host)
    if peer is None:
        return "unknown"
    peer_text = str(peer)
    if not trusted_proxy_networks or not _is_trusted_proxy(
        peer,
        trusted_proxy_networks,
    ):
        return peer_text

    # Multiple physical header fields have inconsistent merge semantics across
    # proxies. Accept one conventional comma-separated field or fail safely to
    # the immediate peer.
    if len(forwarded_for_values) != 1:
        return peer_text
    forwarded_for = forwarded_for_values[0]
    if not forwarded_for or len(forwarded_for) > _MAX_FORWARDED_FOR_LENGTH:
        return peer_text

    tokens = [token.strip() for token in forwarded_for.split(",")]
    if (
        not tokens
        or len(tokens) > _MAX_FORWARDED_FOR_HOPS
        or any(not token for token in tokens)
    ):
        return peer_text

    hops: list[_IPAddress] = []
    for token in tokens:
        hop = _normalized_ip(token)
        if hop is None:
            return peer_text
        hops.append(hop)

    client = peer
    for hop in reversed(hops):
        if not _is_trusted_proxy(client, trusted_proxy_networks):
            break
        client = hop
    return str(client)


# ── JSON log formatter ───────────────────────────────────────────────────────

class _JSONFormatter(logging.Formatter):
    """Emit one JSON object per log record — compatible with Datadog, Splunk, CloudWatch."""

    _EXTRA_FIELDS = (
        "request_id", "method", "path", "status",
        "duration_ms", "tenant_ref", "error_type", "error_code",
        "error_digest", "memories_pruned", "remaining",
        "cutoff_date", "namespaces_scanned", "total_pruned", "errors",
        "elapsed_ms", "cursor_pending",
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
            # Exception messages and tracebacks can contain query parameters,
            # provider responses, tenant identifiers, or secret material. Keep
            # production JSON logs useful without serializing that text.
            exc_type = record.exc_info[0]
            qualified_type = (
                f"{exc_type.__module__}.{exc_type.__qualname__}"
                if exc_type is not None
                else "unknown"
            )
            entry["error_type"] = qualified_type
            entry["error_digest"] = hashlib.sha256(
                f"{record.name}:{qualified_type}".encode("utf-8")
            ).hexdigest()[:16]
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
        supplied = request.headers.getlist("X-Request-ID")
        req_id = _request_id(supplied[0] if len(supplied) == 1 else None)
        token = request_id_var.set(req_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = req_id
            return response
        finally:
            # ASGI servers normally allocate a fresh task per request, but
            # restoring the prior value also protects task reuse and tests.
            request_id_var.reset(token)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log one structured JSON line per request after the response is sent."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            _access_log.info(
                "",
                extra={
                    "request_id": request_id_var.get(),
                    "method": request.method,
                    "path": _route_template(request),
                    "status": status_code,
                    "duration_ms": duration_ms,
                },
            )


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """Measure HTTP outcomes; metrics collapse templates to closed route groups."""

    _KNOWN_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}

    @staticmethod
    def _route_template(request: Request) -> str:
        return _route_template(request)

    async def dispatch(self, request: Request, call_next) -> Response:
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            method = request.method.upper()
            if method not in self._KNOWN_METHODS:
                method = "OTHER"
            status_class = (
                f"{status_code // 100}xx"
                if 100 <= status_code <= 599
                else "other"
            )
            record_http_request(
                self._route_template(request),
                method,
                status_class,
                time.perf_counter() - started,
            )


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
    Layered distributed abuse control for authenticated and invalid requests.

    Every request consumes a client-network bucket, so changing a guessed API
    key or bearer token cannot evade throttling. Authenticated traffic also
    consumes a hashed-credential bucket here and a rotation-stable principal
    bucket after authentication. Redis owns the cross-worker counts; bounded
    local counters remain warm as the configured backend-failure fallback.
    """

    _INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""

    def __init__(
        self,
        app,
        requests_per_minute: int = 300,
        network_multiplier: int = 20,
        admin_requests_per_minute: int = 60,
        backend_failure_mode: str = "local",
        trusted_proxy_cidrs: str = "",
    ):
        super().__init__(app)
        self._limit = max(1, requests_per_minute)
        self._network_limit = self._limit * max(1, network_multiplier)
        self._admin_limit = max(1, admin_requests_per_minute)
        self._failure_mode = backend_failure_mode.strip().lower()
        if self._failure_mode not in {"local", "deny", "open"}:
            raise ValueError("backend_failure_mode must be local, deny, or open")
        self._window = 60  # seconds
        self._trusted_proxy_networks = parse_trusted_proxy_cidrs(trusted_proxy_cidrs)
        self._local_buckets: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self._local_lock = asyncio.Lock()
        self._max_local_buckets = 20_000

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    async def _increment_local(self, key: str) -> int:
        window = int(time.time()) // self._window
        async with self._local_lock:
            prior = self._local_buckets.get(key)
            count = prior[1] + 1 if prior is not None and prior[0] == window else 1
            self._local_buckets[key] = (window, count)
            self._local_buckets.move_to_end(key)
            while len(self._local_buckets) > self._max_local_buckets:
                self._local_buckets.popitem(last=False)
            return count

    async def _distributed_counts(self, keys: list[str]) -> tuple[list[int], bool]:
        # Keep local counters warm so a Redis outage never grants a fresh window.
        local_counts = await asyncio.gather(*(self._increment_local(key) for key in keys))
        try:
            from .cache import _get_redis

            redis = _get_redis()
            counts = await asyncio.gather(
                *(
                    redis.eval(
                        self._INCREMENT_SCRIPT,
                        1,
                        f"agentmem:rl:{key}",
                        self._window,
                    )
                    for key in keys
                )
            )
            return [int(count) for count in counts], True
        except Exception:
            if self._failure_mode == "deny":
                return [], False
            if self._failure_mode == "open":
                return [0 for _ in keys], True
            return list(local_counts), True

    def _limited_response(self, limit: int) -> Response:
        return Response(
            content=json.dumps(
                {
                    "detail": f"Rate limit exceeded ({limit} requests/minute).",
                    "code": "rate_limit_exceeded",
                }
            ),
            status_code=429,
            headers={
                "Content-Type": "application/json",
                "Retry-After": str(self._window),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )

    @staticmethod
    def _backend_unavailable_response() -> Response:
        return Response(
            content=json.dumps(
                {
                    "detail": "Rate-limit backend unavailable; request denied by policy",
                    "code": "rate_limit_backend_unavailable",
                }
            ),
            status_code=503,
            headers={"Content-Type": "application/json", "Retry-After": "5"},
        )

    async def _dispatch_layered(
        self,
        request: Request,
        call_next,
        client_address: str,
    ) -> Response:
        network_key = "network:" + self._digest(client_address)
        is_admin = request.url.path.startswith("/v1/admin/")
        keys = [network_key]
        limits = [self._admin_limit if is_admin else self._network_limit]
        primary_index = 0

        if not is_admin:
            raw_key = request.headers.get("X-API-Key", "")
            authorization = request.headers.get("Authorization", "")
            if raw_key:
                credential_key = "api:" + self._digest(raw_key)
            elif authorization.lower().startswith("bearer "):
                credential_key = "oidc:" + self._digest(authorization[7:].strip())
            else:
                credential_key = "anonymous:" + self._digest(client_address)
            keys.append(credential_key)
            limits.append(self._limit)
            primary_index = 1

        counts, backend_available = await self._distributed_counts(keys)
        if not backend_available:
            return self._backend_unavailable_response()
        for count, limit in zip(counts, limits, strict=True):
            if count > limit:
                return self._limited_response(limit)

        remaining = None
        if not (self._failure_mode == "open" and not any(counts)):
            remaining = max(0, limits[primary_index] - counts[primary_index])
        primary_limit = limits[primary_index]
        response = await call_next(request)
        principal_quota = getattr(request.state, "principal_rate_limit", None)
        if isinstance(principal_quota, dict):
            response.headers["X-RateLimit-Limit"] = str(principal_quota["limit"])
            response.headers["X-RateLimit-Remaining"] = str(
                principal_quota["remaining"]
            )
        elif remaining is not None:
            response.headers["X-RateLimit-Limit"] = str(primary_limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    async def dispatch(self, request: Request, call_next) -> Response:
        client_address = derive_client_address(
            request.client.host if request.client else None,
            request.headers.getlist("x-forwarded-for"),
            self._trusted_proxy_networks,
        )
        # AccessLogMiddleware reads only this canonical value after the response;
        # raw forwarded headers are never copied into logs or rate-limit keys.
        request.state.client_address = client_address

        # Health checks are exempt — LB probes must never be rate-limited
        if request.url.path in {"/health", "/livez", "/readyz"}:
            return await call_next(request)

        return await self._dispatch_layered(request, call_next, client_address)
