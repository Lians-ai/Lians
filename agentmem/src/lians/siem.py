"""
SIEM audit streaming — forward every tamper-evident audit event to an external
collector (Splunk HEC, Datadog, Elastic, a generic HTTP intake) in real time.

Export (``/v1/admin/audit/export``) gives examiners the chain on demand; this
gives the security team a live feed for alerting and retention in their SIEM —
without granting them database access. Forwarding is fire-and-forget and never
blocks or fails the write path: a SIEM outage cannot stop memory writes (the
tamper-evident chain in Postgres remains the source of truth).
"""
from __future__ import annotations

from typing import Any

import httpx

from .config import get_settings


def siem_enabled() -> bool:
    return bool(get_settings().siem_url)


async def stream_event(event: dict[str, Any]) -> bool:
    """
    POST a single audit event to the configured SIEM collector.

    Returns True if delivered (2xx), False otherwise. Never raises — a SIEM
    failure must not affect the request that produced the event.
    """
    settings = get_settings()
    url = settings.siem_url
    if not url:
        return False

    headers = {"Content-Type": "application/json"}
    if settings.siem_token:
        headers["Authorization"] = settings.siem_token

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json={"source": "lians.audit", "event": event}, headers=headers)
        return 200 <= resp.status_code < 300
    except Exception:
        return False
