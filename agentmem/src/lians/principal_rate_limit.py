"""Distributed quotas keyed by authenticated, rotation-stable principals."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict

from .cache import _get_redis
from .config import get_settings

_INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""
_WINDOW_SECONDS = 60
_MAX_LOCAL_BUCKETS = 20_000
_local_buckets: OrderedDict[str, tuple[int, int]] = OrderedDict()
_local_lock = asyncio.Lock()


class PrincipalRateLimitExceeded(RuntimeError):
    def __init__(self, limit: int):
        super().__init__("authenticated principal rate limit exceeded")
        self.limit = limit


class PrincipalRateLimitBackendUnavailable(RuntimeError):
    """The configured fail-closed distributed counter could not be reached."""


def _bucket_key(namespace: str, principal_id: str, *, admin: bool) -> str:
    material = f"lians-principal-rate-v1\0{namespace}\0{principal_id}".encode()
    digest = hashlib.sha256(material).hexdigest()
    tier = "admin" if admin else "standard"
    return f"agentmem:rl:principal:v1:{tier}:{digest}"


async def _increment_local(key: str) -> int:
    window = int(time.time()) // _WINDOW_SECONDS
    async with _local_lock:
        prior = _local_buckets.get(key)
        count = prior[1] + 1 if prior is not None and prior[0] == window else 1
        _local_buckets[key] = (window, count)
        _local_buckets.move_to_end(key)
        while len(_local_buckets) > _MAX_LOCAL_BUCKETS:
            _local_buckets.popitem(last=False)
        return count


async def enforce_principal_rate_limit(
    namespace: str,
    principal_id: str,
    *,
    admin: bool,
) -> tuple[int, int]:
    """Consume one stable-principal quota unit and return ``(limit, remaining)``.

    The network/credential middleware still bounds unauthenticated work. This
    second layer runs only after credential verification, so OIDC token refresh
    and API-key rotation cannot mint a fresh principal bucket.
    """
    settings = get_settings()
    limit = max(
        1,
        settings.rate_limit_admin_per_minute
        if admin
        else settings.rate_limit_per_minute,
    )
    key = _bucket_key(namespace, principal_id, admin=admin)
    local_count = await _increment_local(key)
    failure_mode = settings.rate_limit_backend_failure_mode.strip().casefold()
    try:
        count = int(
            await _get_redis().eval(
                _INCREMENT_SCRIPT,
                1,
                key,
                _WINDOW_SECONDS,
            )
        )
    except Exception as exc:
        if failure_mode == "deny":
            raise PrincipalRateLimitBackendUnavailable from exc
        if failure_mode == "open":
            count = 0
        else:
            count = local_count

    if count > limit:
        raise PrincipalRateLimitExceeded(limit)
    return limit, max(0, limit - count)
