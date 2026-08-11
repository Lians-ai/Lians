"""Handler registry for database-backed Lians work items."""

from __future__ import annotations

from .durable_jobs import JobHandler
from .metering import handle_metering_job
from .siem import handle_siem_job
from .supersession import handle_llm_adjudication_job
from .webhook_service import handle_webhook_job
from .cache_invalidation import (
    RECALL_INVALIDATION_JOB,
    handle_recall_invalidation_job,
)
from .memory_enrichment import MEMORY_ENRICHMENT_JOB, handle_memory_enrichment_job


def default_job_handlers() -> dict[str, JobHandler]:
    """Return the production handler registry."""
    return {
        "metering.stripe": handle_metering_job,
        "siem.event": handle_siem_job,
        "supersession.adjudicate": handle_llm_adjudication_job,
        "webhook.delivery": handle_webhook_job,
        RECALL_INVALIDATION_JOB: handle_recall_invalidation_job,
        MEMORY_ENRICHMENT_JOB: handle_memory_enrichment_job,
    }
