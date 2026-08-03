"""
Prometheus metrics scrape endpoint.

    GET /metrics

Returns the full Lians metric set in Prometheus text exposition format
(text/plain; version=0.0.4). A configured bearer token is always required when
the endpoint is enabled; Kubernetes NetworkPolicy provides an additional layer.

The endpoint is disabled by default and still requires a dedicated bearer token
when enabled. Application metric labels never contain tenant namespaces, IDs,
URLs, errors, or evidence content, but aggregate security/control volumes remain
sensitive operational data. Any scrape receives 404 while disabled.

Metrics emitted (see src/lians/metrics.py for full list):

    agentmem_memory_writes_total{relation}
    agentmem_memory_recalls_total{router,cache_hit}
    agentmem_memories_erased_total
    agentmem_erasure_requests_total
    agentmem_add_duration_seconds                  â€” histogram
    agentmem_recall_duration_seconds               â€” histogram
    lians_http_requests_total{route_group,method,status_class}
    lians_http_request_duration_seconds{route_group,method} â€” histogram
    lians_recorder_events_total{outcome}
    lians_integration_deliveries{status}
    lians_impact_jobs{status}
    lians_retention_cycles_total{outcome}
    lians_audit_append_boundary_attempts_total{outcome}
    lians_gate_evaluations_total{disposition}
    lians_gate_permit_events_total{outcome}

Prometheus scrape config (kubernetes):

    - job_name: agentmem
      kubernetes_sd_configs:
        - role: pod
      relabel_configs:
        - source_labels: [__meta_kubernetes_pod_label_app]
          action: keep
          regex: lians
      metrics_path: /metrics
      scrape_interval: 15s
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..config import get_settings
from ..metrics import generate_metrics

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request) -> Response:
    """
    Prometheus scrape endpoint.

    Returns metric families in text exposition format.  When
    ``prometheus-client`` is not installed, returns a 200 with a plain-text
    comment to avoid breaking Prometheus scrape jobs.
    """
    settings = get_settings()
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics endpoint disabled (METRICS_ENABLED=false)")
    expected = settings.metrics_bearer_token
    authorization = request.headers.get("Authorization", "")
    supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="Metrics authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    content, content_type = generate_metrics()
    return Response(content=content, media_type=content_type)
