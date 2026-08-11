"""Crash-safe deferred embedding enrichment for latency-sensitive writes."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import chain_log
from .durable_jobs import enqueue_job
from .embeddings import get_embedding_provider
from .models import DurableJob, LiveFact, Memory

MEMORY_ENRICHMENT_JOB = "memory.enrich"


async def enqueue_memory_enrichment(db: AsyncSession, memory: Memory) -> DurableJob:
    """Queue only governed identifiers; plaintext never enters the job payload."""
    return await enqueue_job(
        db,
        namespace=memory.namespace,
        kind=MEMORY_ENRICHMENT_JOB,
        payload={"memory_id": str(memory.id)},
        dedupe_key=str(memory.id),
        max_attempts=8,
    )


async def handle_memory_enrichment_job(db: AsyncSession, job: DurableJob) -> None:
    from .memory_service import _load_namespace_subject_keys
    from .ranking import _decrypt

    memory_id = UUID(str(dict(job.payload or {})["memory_id"]))
    memory = await db.get(Memory, memory_id)
    if memory is None or memory.namespace != job.namespace or memory.erased_at is not None:
        return
    if memory.embedding is not None and len(memory.embedding) > 0:
        return

    subject_keys = await _load_namespace_subject_keys(db, job.namespace)
    content = _decrypt(memory, subject_keys)
    if content is None:
        return
    embedding = await get_embedding_provider().embed_one(content)
    now = datetime.now(timezone.utc)
    metadata = dict(memory.metadata_ or {})
    metadata["_enrichment"] = {
        "status": "complete",
        "schema": "lians.memory-enrichment.v1",
        "completed_at": now.isoformat(),
    }
    memory.embedding = embedding
    memory.metadata_ = metadata
    await db.execute(
        update(LiveFact)
        .where(LiveFact.memory_id == memory.id)
        .values(embedding=embedding, metadata_=metadata)
    )
    await chain_log(
        db,
        namespace=job.namespace,
        agent_id=memory.agent_id,
        op="memory_enriched",
        memory_id=memory.id,
        content_hash=memory.content_hash,
        payload={"kind": "embedding", "job_id": str(job.id)},
    )
    await db.commit()
