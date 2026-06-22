"""
Prometheus metrics scrape endpoint.

    GET /metrics

Returns the full AgentMem metric set in Prometheus text exposition format
(text/plain; version=0.0.4).  No authentication is required — the endpoint
is intended to be scraped by an in-cluster Prometheus server, protected at the
network layer by the existing Kubernetes NetworkPolicy (which only admits traffic
from the monitoring namespace).

To disable the endpoint entirely set ``METRICS_ENABLED=false`` in the
environment.  Any scrape will receive 404 while the flag is off.

Metrics emitted (see src/agentmem/metrics.py for full list):

    agentmem_memory_writes_total{namespace, relation}
    agentmem_memory_recalls_total{namespace, router, cache_hit}
    agentmem_memories_erased_total{namespace}
    agentmem_erasure_requests_total{namespace}
    agentmem_add_duration_seconds{namespace}       — histogram
    agentmem_recall_duration_seconds{namespace}    — histogram

Prometheus scrape config (kubernetes):

    - job_name: agentmem
      kubernetes_sd_configs:
        - role: pod
      relabel_configs:
        - source_labels: [__meta_kubernetes_pod_label_app]
          action: keep
          regex: agentmem
      metrics_path: /metrics
      scrape_interval: 15s
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..config import get_settings
from ..metrics import generate_metrics, _PROM_AVAILABLE

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """
    Prometheus scrape endpoint.

    Returns metric families in text exposition format.  When
    ``prometheus-client`` is not installed, returns a 200 with a plain-text
    comment to avoid breaking Prometheus scrape jobs.
    """
    settings = get_settings()
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics endpoint disabled (METRICS_ENABLED=false)")

    content, content_type = generate_metrics()
    return Response(content=content, media_type=content_type)
