"""Redis hot cache for present-time recall results.

Every cached value is keyed by a Redis-backed generation token for its
``(namespace, agent_id)``.  A write replaces that token *before* its
database commit while holding the matching PostgreSQL advisory lock.  Recall
holds the shared form of that lock while it reads the generation and until its
audit event commits.  Consequently, a recall can linearize either before or
after a write, but cannot return the write's superseded generation afterwards.

Old generations are not scanned or deleted on the write path; their normal
result TTL reclaims them.  The small generation key is intentionally durable.

Read/write cache failures are observable and degrade to a database miss.
Invalidation is different: failure is raised so the caller can abort the
database write.  Serving uncached results is safe; committing a write without
advancing its cache generation is not.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger("lians.cache")

_redis_client: Any = None  # redis.asyncio.Redis, lazily initialised


class RecallCacheInvalidationError(RuntimeError):
    """The durable write must not commit because cache coherence was not fenced."""


def _get_redis() -> Any:
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis

        from .config import get_settings

        redis_url = get_settings().redis_url
        tls_options: dict[str, Any] = {}
        if urlsplit(redis_url).scheme.casefold() == "rediss":
            tls_options = {
                "ssl_cert_reqs": "required",
                "ssl_check_hostname": True,
            }
        _redis_client = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            **tls_options,
        )
    return _redis_client


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _exception_digest(exc: BaseException) -> str:
    """Return a stable, non-sensitive correlation value for cache failures."""

    error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
    return _h(error_type)[:16]


def _agent_prefix(namespace: str, agent_id: str) -> str:
    # Hash caller-controlled identifiers so Redis key syntax and glob
    # characters cannot affect cache isolation or invalidation.
    return f"agentmem:recall:v2:{_h(namespace)}:{_h(agent_id)}"


def _generation_key(namespace: str, agent_id: str) -> str:
    return f"{_agent_prefix(namespace, agent_id)}:generation"


def _namespace_generation_key(namespace: str) -> str:
    return f"agentmem:recall:v2:{_h(namespace)}:namespace-generation"


def _recall_key(
    namespace: str,
    agent_id: str,
    generation: str,
    query: str,
    as_of: datetime | None,
    k: int,
    filters: dict | None,
    candidate_contract: str,
) -> str:
    as_of_str = as_of.isoformat() if as_of else "none"
    filters_str = json.dumps(filters or {}, sort_keys=True, separators=(",", ":"))
    return (
        f"{_agent_prefix(namespace, agent_id)}:{generation}:"
        f"{_h(query)}:{_h(as_of_str)}:{k}:{_h(filters_str)}:{_h(candidate_contract)}"
    )


def _enabled() -> bool:
    from .config import get_settings

    return get_settings().recall_cache_enabled


async def get_agent_cache_generation(namespace: str, agent_id: str) -> str | None:
    """Return the shared generation, or ``None`` when caching is unavailable.

    An absent key is initialized with a random token via ``SET NX``. Random
    tokens, rather than a resettable counter, prevent generation reuse if Redis
    evicts just the generation key while old TTL-bound result keys remain. A
    Redis failure disables both Redis and process-local cache use for this
    recall.
    """
    if not _enabled():
        return None
    try:
        redis = _get_redis()
        namespace_key = _namespace_generation_key(namespace)
        agent_key = _generation_key(namespace, agent_id)
        namespace_value = await redis.get(namespace_key)
        agent_value = await redis.get(agent_key)
        if namespace_value is None:
            await redis.set(namespace_key, secrets.token_hex(16), nx=True)
        if agent_value is None:
            await redis.set(agent_key, secrets.token_hex(16), nx=True)
        namespace_value = await redis.get(namespace_key)
        agent_value = await redis.get(agent_key)
        if not namespace_value or not agent_value:
            raise RuntimeError("Redis did not retain the recall generation token")
        return f"{namespace_value}:{agent_value}"
    except Exception as exc:
        logger.warning(
            "recall cache generation read failed; bypassing caches",
            extra={"error_digest": _exception_digest(exc)},
        )
        return None


async def get_cached_recall(
    namespace: str,
    agent_id: str,
    query: str,
    as_of: datetime | None,
    k: int,
    filters: dict | None,
    *,
    generation: str,
    candidate_contract: str = "legacy-unbounded",
) -> str | None:
    if not _enabled():
        return None
    try:
        key = _recall_key(
            namespace,
            agent_id,
            generation,
            query,
            as_of,
            k,
            filters,
            candidate_contract,
        )
        return await _get_redis().get(key)
    except Exception as exc:
        logger.warning(
            "recall cache read failed; falling back to the database",
            extra={"error_digest": _exception_digest(exc)},
        )
        return None


async def set_cached_recall(
    namespace: str,
    agent_id: str,
    query: str,
    as_of: datetime | None,
    k: int,
    filters: dict | None,
    payload: str,
    ttl: int,
    *,
    generation: str,
    candidate_contract: str = "legacy-unbounded",
) -> None:
    if not _enabled():
        return
    try:
        key = _recall_key(
            namespace,
            agent_id,
            generation,
            query,
            as_of,
            k,
            filters,
            candidate_contract,
        )
        await _get_redis().setex(key, ttl, payload)
    except Exception as exc:
        logger.warning(
            "recall cache write failed; result was not cached",
            extra={"error_digest": _exception_digest(exc)},
        )


async def invalidate_agent(namespace: str, agent_id: str) -> str | None:
    """Fence all older cache entries for an agent before the database commit.

    Callers must hold the agent's exclusive PostgreSQL transaction advisory
    lock and invoke this before committing the associated durable mutation.
    Failure raises :class:`RecallCacheInvalidationError`; callers must let that
    exception abort/roll back the database transaction.
    """
    if not _enabled():
        return None
    try:
        generation = secrets.token_hex(16)
        stored = await _get_redis().set(_generation_key(namespace, agent_id), generation)
        if stored is not True and stored != "OK":
            raise RuntimeError("Redis did not acknowledge recall cache invalidation")
        return generation
    except Exception as exc:
        logger.error(
            "recall cache invalidation failed; durable write must abort",
            extra={"error_digest": _exception_digest(exc)},
        )
        raise RecallCacheInvalidationError(
            "recall cache invalidation failed; durable write was not committed"
        ) from exc


async def invalidate_namespace(namespace: str) -> str | None:
    """Fence every agent cache in a tenant without scanning Redis keys.

    The caller must hold the exclusive namespace cache advisory lock until its
    database transaction commits. Recall obtains the shared form before it
    reads the compound namespace/agent generation, preventing an in-flight
    read from publishing plaintext into the new generation during erasure.
    """

    if not _enabled():
        return None
    try:
        generation = secrets.token_hex(16)
        stored = await _get_redis().set(_namespace_generation_key(namespace), generation)
        if stored is not True and stored != "OK":
            raise RuntimeError("Redis did not acknowledge namespace invalidation")
        return generation
    except Exception as exc:
        logger.error(
            "namespace recall cache invalidation failed; durable write must abort",
            extra={"error_digest": _exception_digest(exc)},
        )
        raise RecallCacheInvalidationError(
            "namespace recall cache invalidation failed; durable write was not committed"
        ) from exc
