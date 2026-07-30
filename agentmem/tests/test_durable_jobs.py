from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.lians.durable_jobs import (
    complete_job,
    enqueue_job,
    fail_job,
    lease_jobs,
)
from src.lians.models import DurableJob, Memory


@pytest.mark.asyncio
async def test_durable_job_is_deduplicated_leased_and_completed(db):
    first = await enqueue_job(
        db,
        namespace="tenant-a",
        kind="webhook",
        payload={"delivery_id": "delivery-1"},
        dedupe_key="delivery-1",
    )
    second = await enqueue_job(
        db,
        namespace="tenant-a",
        kind="webhook",
        payload={"delivery_id": "delivery-1"},
        dedupe_key="delivery-1",
    )
    assert first.id == second.id
    await db.commit()

    leased = await lease_jobs(
        db,
        worker_id="worker-a",
        kinds=["webhook"],
        lease_seconds=30,
    )
    assert [row.id for row in leased] == [first.id]
    assert leased[0].attempts == 1

    assert await complete_job(db, first.id, "other-worker") is False
    assert await complete_job(db, first.id, "worker-a") is True
    row = await db.get(DurableJob, first.id)
    assert row.status == "completed"
    assert row.completed_at is not None


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_and_failures_dead_letter(db):
    row = await enqueue_job(
        db,
        namespace="tenant-a",
        kind="siem",
        payload={"event_id": "event-1"},
        max_attempts=2,
    )
    await db.commit()

    first = (
        await lease_jobs(db, worker_id="worker-a", kinds=["siem"])
    )[0]
    first.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()

    second = (
        await lease_jobs(db, worker_id="worker-b", kinds=["siem"])
    )[0]
    assert second.id == row.id
    assert second.attempts == 2
    assert await fail_job(
        db,
        row.id,
        "worker-b",
        reason="ConnectionError",
    )
    failed = await db.get(DurableJob, row.id)
    assert failed.status == "dead"
    assert failed.last_error == "ConnectionError"


@pytest.mark.asyncio
async def test_metering_event_is_transactional_and_contains_no_api_secret(
    db, monkeypatch
):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_never_store_me")
    from src.lians.config import get_settings
    from src.lians.metering import enqueue_usage_event

    get_settings.cache_clear()
    await enqueue_usage_event(
        db,
        namespace="tenant-a",
        event_name="lians_memory_write",
        customer_id="cus_123",
        quantity=1,
        identifier="w:memory-1",
    )
    await db.commit()

    row = (
        await db.execute(
            select(DurableJob).where(DurableJob.kind == "metering.stripe")
        )
    ).scalar_one()
    assert row.payload["identifier"] == "w:memory-1"
    assert row.payload["customer_id"] == "cus_123"
    assert "sk_test_never_store_me" not in str(row.payload)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_durable_adjudication_reloads_content_and_can_restore_memory(
    db, monkeypatch
):
    import uuid

    from src.lians.durable_jobs import enqueue_job
    from src.lians.supersession import handle_llm_adjudication_job
    import src.lians.supersession as supersession_mod

    now = datetime.now(timezone.utc)
    old = Memory(
        id=uuid.uuid4(),
        namespace="tenant-a",
        agent_id="agent-1",
        content_encrypted=b"preferred timezone is UTC",
        embedding=[0.0] * 8,
        metadata_={},
        event_time=now,
        ingestion_time=now,
        valid_from=now,
        valid_to=now,
        importance=0.5,
        content_hash="old-hash",
    )
    new = Memory(
        id=uuid.uuid4(),
        namespace="tenant-a",
        agent_id="agent-1",
        content_encrypted=b"the preferred timezone remains UTC",
        embedding=[0.0] * 8,
        metadata_={},
        event_time=now,
        ingestion_time=now,
        valid_from=now,
        importance=0.5,
        content_hash="new-hash",
    )
    db.add_all([old, new])
    await db.flush()
    old.superseded_by = new.id

    job = await enqueue_job(
        db,
        namespace="tenant-a",
        kind="supersession.adjudicate",
        payload={
            "old_memory_id": str(old.id),
            "new_memory_id": str(new.id),
        },
        dedupe_key=f"{old.id}:{new.id}",
    )
    await db.commit()

    async def confirms(**kwargs):
        return "CONFIRMS", 0.98, "Same durable fact"

    monkeypatch.setattr(supersession_mod, "llm_adjudicate", confirms)
    await handle_llm_adjudication_job(db, job)
    await db.refresh(old)

    assert old.valid_to is None
    assert old.superseded_by is None
    assert "preferred timezone" not in str(job.payload)
