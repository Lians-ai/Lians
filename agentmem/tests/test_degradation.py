from lians.degradation import (
    recent_degradations,
    record_degradation,
    reset_degradations,
)
from lians.metrics import generate_metrics


def test_degradation_is_sanitized_counted_and_exposed():
    reset_degradations()
    record_degradation("rate_limit", "redis_unavailable")
    record_degradation("rate_limit", "redis_unavailable")

    recent = recent_degradations()
    assert recent == [
        {
            "component": "rate_limit",
            "reason": "redis_unavailable",
            "count": 2,
            "last_seen": recent[0]["last_seen"],
        }
    ]
    assert "redis" not in recent[0]["last_seen"]


def test_degradation_metric_is_exported_when_prometheus_is_available():
    reset_degradations()
    record_degradation("subject_keys", "unwrap_failed")
    body, _ = generate_metrics()
    if b"prometheus_client not installed" not in body:
        assert b"lians_subsystem_degradations_total" in body
