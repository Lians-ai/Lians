"""Redis hot cache for recall results.

Architecture:

* Generation: ``agentmem:recall-generation:{namespace_agent_hash}``
* Value key: ``agentmem:recall:{schema}:{namespace_agent_hash}:{generation}:...``
* TTL: ``config.recall_cache_ttl_seconds`` (default 60 seconds)

Invalidation is O(1): a write increments the pair's generation. Old-generation
entries become unreachable immediately and expire under their normal TTL. This
avoids a keyspace scan and stays correct when reads and writes race across
processes.

The key includes the complete retrieval policy, not only filters. A fast result
can never be served for a deep or reconstruction request.

Lookups return the generation they observed. A later cache fill writes only to
that captured generation, so a concurrent write/erase cannot make stale work
reachable by advancing the generation between the lookup and fill.

An invalidation failure quarantines that agent from cache reads in-process.
Privacy-sensitive callers can additionally request fail-closed behavior so an
erase/prune operation never reports clean completion without a generation bump.
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Optional

_redis_client: Any = None
logger = logging.getLogger("agentmem.cache")
_CACHE_SCHEMA_VERSION = "scoring-v2"
_FIXED_WINDOW_INCREMENT_LUA = """
local count = redis.call('INCRBY', KEYS[1], ARGV[1])
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return count
"""
_cache_bypass_pairs: set[str] = set()
_cache_disabled: ContextVar[bool] = ContextVar(
    "lians_recall_cache_disabled", default=False
)


class RecallCacheInvalidationError(RuntimeError):
    """Raised when a required generation bump cannot be confirmed."""


@contextmanager
def recall_cache_disabled() -> Iterator[None]:
    """Disable the shared recall cache for the current execution context.

    The local SDK runs the service layer in-process and must never inherit a
    hosted process's Redis setting.  A context variable keeps that guarantee
    scoped to the local call without mutating global settings for concurrent
    hosted requests.
    """
    token = _cache_disabled.set(True)
    try:
        yield
    finally:
        _cache_disabled.reset(token)


@dataclass(frozen=True)
class RecallCacheLookup:
    """A cache read bound to the generation observed before recall starts."""

    payload: Optional[str]
    generation: str


def _get_redis() -> Any:
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis
        from .config import get_settings

        _redis_client = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    return _redis_client


async def _redis_fixed_window_increment(
    redis: Any,
    key: str,
    *,
    amount: int,
    window_seconds: int,
) -> int:
    """Atomically increment a fixed window and repair any missing TTL."""
    return int(
        await redis.eval(
            _FIXED_WINDOW_INCREMENT_LUA,
            1,
            key,
            amount,
            window_seconds,
        )
    )


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _pair_hash(namespace: str, agent_id: str) -> str:
    return hashlib.sha256(f"{namespace}\0{agent_id}".encode()).hexdigest()[:32]


def _generation_key(namespace: str, agent_id: str) -> str:
    return f"agentmem:recall-generation:{_pair_hash(namespace, agent_id)}"


def _recall_key(
    namespace: str,
    agent_id: str,
    generation: str,
    query: str,
    as_of: Optional[datetime],
    k: int,
    filters: Optional[dict],
    policy: Optional[dict],
) -> str:
    as_of_str = as_of.isoformat() if as_of else "none"
    filters_str = json.dumps(filters or {}, sort_keys=True)
    policy_str = json.dumps(policy or {}, sort_keys=True)
    return (
        f"agentmem:recall:{_CACHE_SCHEMA_VERSION}:"
        f"{_pair_hash(namespace, agent_id)}:{generation}:"
        f"{_h(query)}:{as_of_str}:{k}:{_h(filters_str)}:{_h(policy_str)}"
    )


def _enabled() -> bool:
    from .config import get_settings

    return not _cache_disabled.get() and get_settings().recall_cache_enabled


async def get_cached_recall(
    namespace: str,
    agent_id: str,
    query: str,
    as_of: Optional[datetime],
    k: int,
    filters: Optional[dict],
    policy: Optional[dict] = None,
) -> Optional[RecallCacheLookup]:
    pair = _pair_hash(namespace, agent_id)
    if not _enabled() or pair in _cache_bypass_pairs:
        return None
    try:
        redis = _get_redis()
        generation_key = _generation_key(namespace, agent_id)
        generation = await redis.get(generation_key) or "0"
        key = _recall_key(
            namespace, agent_id, generation, query, as_of, k, filters, policy
        )
        payload = await redis.get(key)
        if payload is not None:
            # Linearize a hit after reading its value. If invalidation completed
            # between the generation read and value read, discard the old hit
            # and let recall compute against the newer database state.
            current_generation = await redis.get(generation_key) or "0"
            if current_generation != generation:
                return RecallCacheLookup(
                    payload=None,
                    generation=current_generation,
                )
        if pair in _cache_bypass_pairs:
            return None
        return RecallCacheLookup(payload=payload, generation=generation)
    except Exception:
        return None


async def set_cached_recall(
    namespace: str,
    agent_id: str,
    query: str,
    as_of: Optional[datetime],
    k: int,
    filters: Optional[dict],
    payload: str,
    ttl: int,
    policy: Optional[dict] = None,
    *,
    generation: str,
) -> None:
    pair = _pair_hash(namespace, agent_id)
    if not _enabled() or pair in _cache_bypass_pairs:
        return
    try:
        redis = _get_redis()
        key = _recall_key(
            namespace, agent_id, generation, query, as_of, k, filters, policy
        )
        await redis.setex(key, ttl, payload)
    except Exception:
        logger.debug("Recall cache write failed; continuing without cache", exc_info=True)


async def cache_generation_is_current(
    namespace: str,
    agent_id: str,
    generation: str,
) -> bool:
    """Revalidate a cache hit after the database invalidation-barrier check."""
    pair = _pair_hash(namespace, agent_id)
    if not _enabled() or pair in _cache_bypass_pairs:
        return False
    try:
        current = await _get_redis().get(_generation_key(namespace, agent_id)) or "0"
        return str(current) == str(generation) and pair not in _cache_bypass_pairs
    except Exception:
        return False


async def get_agent_cache_generation(
    namespace: str,
    agent_id: str,
) -> Optional[str]:
    """Return the shared generation that an in-process working set must match.

    ``None`` means no safe cross-worker clock is available, so callers must use
    the database rather than trusting process-local state.
    """
    pair = _pair_hash(namespace, agent_id)
    if not _enabled() or pair in _cache_bypass_pairs:
        return None
    try:
        generation = await _get_redis().get(_generation_key(namespace, agent_id))
        return str(generation or "0")
    except Exception:
        return None


async def invalidate_agent(
    namespace: str,
    agent_id: str,
    *,
    fail_closed: bool = False,
) -> bool:
    """Make every cached result for an agent unreachable in O(1).

    The local bypass is engaged before the Redis round trip, closing the window
    in which this process could serve an old generation.  It remains engaged on
    failure.  Privacy mutations pass ``fail_closed=True`` so their API does not
    claim successful cache-safe completion when the shared generation could not
    be advanced.
    """
    pair = _pair_hash(namespace, agent_id)
    _cache_bypass_pairs.add(pair)
    if not _enabled():
        _cache_bypass_pairs.discard(pair)
        return True
    try:
        await _get_redis().incr(_generation_key(namespace, agent_id))
    except Exception as exc:
        # Another concurrent successful invalidation may have removed the
        # bypass while this request was in flight. Re-engage it after failure;
        # a later generation bump can safely release it again.
        _cache_bypass_pairs.add(pair)
        log = logger.error if fail_closed else logger.warning
        log("Recall cache invalidation failed; cache bypass remains engaged", exc_info=True)
        if fail_closed:
            raise RecallCacheInvalidationError(
                f"required recall-cache invalidation failed for agent {agent_id!r}"
            ) from exc
        return False
    _cache_bypass_pairs.discard(pair)
    return True
