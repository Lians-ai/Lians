"""Structured, bounded visibility for fail-open and optional subsystem failures."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .metrics import record_degradation_metric


logger = logging.getLogger("lians.degradation")
_WINDOW = timedelta(minutes=5)
_LOCK = threading.Lock()


@dataclass
class _Degradation:
    reason: str
    count: int
    last_seen: datetime


_RECENT: dict[str, _Degradation] = {}


def record_degradation(component: str, reason: str) -> None:
    """Record a sanitized failure without leaking exception or tenant data."""
    now = datetime.now(timezone.utc)
    with _LOCK:
        current = _RECENT.get(component)
        _RECENT[component] = _Degradation(
            reason=reason,
            count=(current.count + 1) if current else 1,
            last_seen=now,
        )
    record_degradation_metric(component, reason)
    logger.warning(
        "Lians subsystem degraded",
        extra={"component": component, "reason": reason},
    )


def recent_degradations() -> list[dict[str, object]]:
    """Return sanitized failures seen during the rolling health window."""
    cutoff = datetime.now(timezone.utc) - _WINDOW
    with _LOCK:
        expired = [
            component
            for component, item in _RECENT.items()
            if item.last_seen < cutoff
        ]
        for component in expired:
            _RECENT.pop(component, None)
        return [
            {
                "component": component,
                "reason": item.reason,
                "count": item.count,
                "last_seen": item.last_seen.isoformat(),
            }
            for component, item in sorted(_RECENT.items())
        ]


def reset_degradations() -> None:
    """Clear process-local health history. Intended for tests only."""
    with _LOCK:
        _RECENT.clear()
