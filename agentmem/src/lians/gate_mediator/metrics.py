"""Isolated, fixed-cardinality Prometheus metrics for the mediator process."""

from __future__ import annotations

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Histogram,
        generate_latest,
    )

    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False

_OUTCOMES = ("success", "client_error", "server_error", "outcome_unknown")
_LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)


class _Noop:
    def labels(self, **_: object) -> _Noop:
        return self

    def inc(self, amount: float = 1) -> None:
        pass

    def observe(self, amount: float) -> None:
        pass


if _PROM_AVAILABLE:
    REGISTRY = CollectorRegistry()
    _upstream_requests = Counter(
        "lians_gate_mediator_upstream_requests_total",
        "Post-consumption provider dispatches by bounded outcome",
        ["outcome"],
        registry=REGISTRY,
    )
    _upstream_duration = Histogram(
        "lians_gate_mediator_upstream_duration_seconds",
        "Post-consumption provider dispatch latency by bounded outcome",
        ["outcome"],
        buckets=_LATENCY_BUCKETS,
        registry=REGISTRY,
    )
    for _outcome in _OUTCOMES:
        _upstream_requests.labels(outcome=_outcome)
        _upstream_duration.labels(outcome=_outcome)
else:
    REGISTRY = None  # type: ignore[assignment]
    _upstream_requests = _Noop()
    _upstream_duration = _Noop()


def observe_upstream(outcome: str, seconds: float) -> None:
    """Record one provider attempt without route, tenant, or target labels."""

    bounded = outcome if outcome in _OUTCOMES else "outcome_unknown"
    _upstream_requests.labels(outcome=bounded).inc()
    _upstream_duration.labels(outcome=bounded).observe(max(0.0, seconds))


def generate_metrics() -> tuple[bytes, str]:
    """Return only the standalone mediator registry."""

    if not _PROM_AVAILABLE:
        return (
            b"# prometheus_client is not installed; mediator metrics unavailable.\n",
            "text/plain; charset=utf-8",
        )
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
