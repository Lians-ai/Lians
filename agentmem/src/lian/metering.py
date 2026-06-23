"""
Stripe usage metering — per-namespace event reporting for memory writes and recalls.

Architecture
────────────
queue_usage_event() — synchronous, non-blocking (put_nowait).  Called on the hot
  path after add_memory / recall_memories.  Silently drops events when the queue
  is full (circuit-breaker) and is a no-op when STRIPE_API_KEY is empty.

run_metering_worker() — asyncio.Task started in FastAPI lifespan.  Drains the
  queue and sends events to Stripe Meters API.  Cancelled on shutdown.

Customer ID cache
─────────────────
NamespacePolicy.stripe_customer_id is looked up once per namespace then cached
for CACHE_TTL seconds.  Call invalidate_customer_cache(namespace) after an admin
billing update so the next hot-path call reads the fresh value.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("agentmem.metering")

_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10_000)

_CACHE_TTL = 60.0
_customer_cache: dict[str, tuple[Optional[str], float]] = {}
_MISS = object()


# ── Customer ID cache ────────────────────────────────────────────────────────

def _cache_get(namespace: str) -> object:
    entry = _customer_cache.get(namespace)
    if entry is not None and time.monotonic() - entry[1] < _CACHE_TTL:
        return entry[0]  # may be None (no customer) or a str
    return _MISS


def _cache_set(namespace: str, customer_id: Optional[str]) -> None:
    _customer_cache[namespace] = (customer_id, time.monotonic())


def invalidate_customer_cache(namespace: str) -> None:
    """Call this after setting/clearing a namespace's stripe_customer_id."""
    _customer_cache.pop(namespace, None)


async def get_customer_id(db: AsyncSession, namespace: str) -> Optional[str]:
    """Return the Stripe customer ID for a namespace (cached for 60 s)."""
    cached = _cache_get(namespace)
    if cached is not _MISS:
        return cached  # type: ignore[return-value]

    from .models import NamespacePolicy
    pol = await db.get(NamespacePolicy, namespace)
    cid = pol.stripe_customer_id if pol is not None else None
    _cache_set(namespace, cid)
    return cid


# ── Hot-path event queuing ───────────────────────────────────────────────────

def queue_usage_event(
    event_name: str,
    customer_id: str,
    quantity: int,
    identifier: str,
) -> None:
    """
    Enqueue a Stripe Meter event.  Synchronous and non-blocking.

    No-op when STRIPE_API_KEY is empty (checked via get_settings()).
    Drops silently when the queue is full (10 000 events) so the hot path
    is never back-pressured by metering.
    """
    from .config import get_settings
    if not get_settings().stripe_api_key:
        return
    try:
        _queue.put_nowait({
            "event_name": event_name,
            "customer_id": customer_id,
            "quantity": quantity,
            "identifier": identifier[:100],  # Stripe max 100 chars
        })
    except asyncio.QueueFull:
        logger.warning("Metering queue full — event dropped", extra={"event": event_name})


# ── Background worker ────────────────────────────────────────────────────────

async def run_metering_worker(
    api_key: str,
    write_event: str,
    recall_event: str,
) -> None:
    """
    Drain the usage event queue and forward events to Stripe Meters API.

    Exits immediately (without entering the loop) if api_key is empty or the
    stripe SDK is not installed — callers should check those conditions before
    creating the task, but the worker is safe to call regardless.

    Cancelled cleanly by task.cancel() during lifespan shutdown.
    """
    if not api_key:
        logger.info("STRIPE_API_KEY not set — metering worker disabled")
        return

    try:
        import stripe as _stripe  # type: ignore[import]
    except ImportError:
        logger.warning(
            "stripe SDK not installed — metering disabled. "
            "Run: pip install 'agentmem[billing]'"
        )
        return

    _stripe.api_key = api_key
    logger.info(
        "Metering worker started",
        extra={"write_event": write_event, "recall_event": recall_event},
    )

    try:
        while True:
            event = await _queue.get()
            try:
                await _stripe.billing.MeterEvent.create_async(
                    event_name=event["event_name"],
                    payload={
                        "stripe_customer_id": event["customer_id"],
                        "value": str(event["quantity"]),
                    },
                    identifier=event["identifier"],
                )
            except Exception as exc:
                logger.error(
                    "Stripe meter event failed",
                    extra={"event_name": event["event_name"], "error": str(exc)},
                )
            finally:
                _queue.task_done()
    except asyncio.CancelledError:
        logger.info("Metering worker stopped")
        raise
