"""
Prometheus metrics for AgentMem.

Exposes counters and histograms for the memory write, recall, and erasure hot
paths.  All metric definitions live here so callers only import thin helpers.

Install the optional extra to activate real metrics:

    pip install agentmem[metrics]      # pulls in prometheus-client>=0.19

Without the extra every helper is a no-op — zero overhead, zero import errors.

Scraped by:

    GET /metrics     (text/plain; version=0.0.4 — Prometheus exposition format)

Grafana dashboard template: see k8s/grafana/agentmem-dashboard.json
"""
from __future__ import annotations

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        CollectorRegistry,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False

# ── No-op stubs ───────────────────────────────────────────────────────────────

_WRITE_BUCKETS = (.001, .005, .01, .025, .05, .1, .25, .5, 1.0, 2.5)
_RECALL_BUCKETS = (.0005, .001, .005, .01, .025, .05, .1, .25, .5, 1.0)


class _Noop:
    """Drop-in for any Prometheus metric when prometheus-client is absent."""

    def labels(self, **_: object) -> "_Noop":
        return self

    def inc(self, amount: float = 1) -> None:
        pass

    def dec(self, amount: float = 1) -> None:
        pass

    def set(self, value: float) -> None:
        pass

    def observe(self, amount: float) -> None:
        pass


_NOOP = _Noop()

# ── Registry and metric objects ───────────────────────────────────────────────

if _PROM_AVAILABLE:
    # Isolated registry — avoids polluting the default REGISTRY and prevents
    # double-registration errors when tests reload modules.
    REGISTRY = CollectorRegistry()

    _writes = Counter(
        "agentmem_memory_writes_total",
        "Memory write operations by supersession outcome",
        ["namespace", "relation"],
        registry=REGISTRY,
    )
    _recalls = Counter(
        "agentmem_memory_recalls_total",
        "Recall operations by router path and Redis cache outcome",
        ["namespace", "router", "cache_hit"],
        registry=REGISTRY,
    )
    _erased = Counter(
        "agentmem_memories_erased_total",
        "Individual memory records destroyed by GDPR crypto-shred",
        ["namespace"],
        registry=REGISTRY,
    )
    _erase_requests = Counter(
        "agentmem_erasure_requests_total",
        "Data-subject erasure requests processed",
        ["namespace"],
        registry=REGISTRY,
    )
    _add_hist = Histogram(
        "agentmem_add_duration_seconds",
        "add_memory() wall time including supersession and DB commit",
        ["namespace"],
        buckets=_WRITE_BUCKETS,
        registry=REGISTRY,
    )
    _recall_hist = Histogram(
        "agentmem_recall_duration_seconds",
        "recall_memories() wall time including embed, ANN search, and decrypt",
        ["namespace"],
        buckets=_RECALL_BUCKETS,
        registry=REGISTRY,
    )
    _conflicts_detected = Counter(
        "agentmem_conflicts_detected_total",
        "Same-time structured-fact disagreements flagged for human review",
        ["namespace"],
        registry=REGISTRY,
    )
    _conflicts_resolved = Counter(
        "agentmem_conflicts_resolved_total",
        "Conflict flags closed by human resolution",
        ["namespace", "resolution"],
        registry=REGISTRY,
    )
    _conflict_queue = Gauge(
        "agentmem_conflict_queue_depth",
        "Current number of open (unresolved) conflict flags",
        ["namespace"],
        registry=REGISTRY,
    )
else:
    REGISTRY = None  # type: ignore[assignment]
    _writes = _NOOP  # type: ignore[assignment]
    _recalls = _NOOP  # type: ignore[assignment]
    _erased = _NOOP  # type: ignore[assignment]
    _erase_requests = _NOOP  # type: ignore[assignment]
    _add_hist = _NOOP  # type: ignore[assignment]
    _recall_hist = _NOOP  # type: ignore[assignment]
    _conflicts_detected = _NOOP  # type: ignore[assignment]
    _conflicts_resolved = _NOOP  # type: ignore[assignment]
    _conflict_queue = _NOOP  # type: ignore[assignment]


# ── Public helpers (called by memory_service.py) ──────────────────────────────

def record_write(namespace: str, relation: str) -> None:
    """Increment the write counter for *namespace* with the supersession *relation*."""
    _writes.labels(namespace=namespace, relation=relation).inc()


def observe_add(namespace: str, seconds: float) -> None:
    """Record add_memory() wall time in the histogram."""
    _add_hist.labels(namespace=namespace).observe(seconds)


def record_recall(namespace: str, router: str, cache_hit: bool) -> None:
    """
    Increment the recall counter.

    *router* is one of ``"cache"``, ``"keyed"``, ``"semantic"``.
    *cache_hit* is True when the Redis cache was hit (router=="cache").
    """
    _recalls.labels(
        namespace=namespace,
        router=router,
        cache_hit="true" if cache_hit else "false",
    ).inc()


def observe_recall(namespace: str, seconds: float) -> None:
    """Record recall_memories() wall time in the histogram."""
    _recall_hist.labels(namespace=namespace).observe(seconds)


def record_erase(namespace: str, count: int) -> None:
    """Record an erasure request and the number of memory records destroyed."""
    _erase_requests.labels(namespace=namespace).inc()
    if count:
        _erased.labels(namespace=namespace).inc(count)


def record_conflict_detected(namespace: str, count: int = 1) -> None:
    """Increment conflict detection counter and open-queue gauge."""
    _conflicts_detected.labels(namespace=namespace).inc(count)
    _conflict_queue.labels(namespace=namespace).inc(count)


def record_conflict_resolved(namespace: str, resolution: str) -> None:
    """Increment resolution counter and decrement the open-queue gauge."""
    _conflicts_resolved.labels(namespace=namespace, resolution=resolution).inc()
    _conflict_queue.labels(namespace=namespace).dec()


# ── Scrape output ─────────────────────────────────────────────────────────────

def generate_metrics() -> tuple[bytes, str]:
    """
    Return ``(body, content_type)`` for the ``GET /metrics`` response.

    When prometheus-client is not installed returns a plain-text comment
    explaining the situation rather than raising an error.
    """
    if not _PROM_AVAILABLE:
        body = (
            b"# AgentMem: prometheus_client not installed.\n"
            b"# Install with: pip install agentmem[metrics]\n"
        )
        return body, "text/plain; charset=utf-8"
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
