"""Durability contract for audit-event forwarding."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.lians.audit_chain import chain_log
from src.lians.models import DurableJob
from src.lians.siem import handle_siem_job


@pytest.mark.asyncio
async def test_chain_log_enqueues_siem_in_same_transaction(db, monkeypatch):
    monkeypatch.setenv("SIEM_URL", "https://siem.example.test/intake")
    from src.lians.config import get_settings
    get_settings.cache_clear()

    row = await chain_log(db, namespace="audit-ns", agent_id="agent", op="write")
    job = (
        await db.execute(
            select(DurableJob).where(DurableJob.kind == "siem.event")
        )
    ).scalar_one()

    assert job.namespace == "audit-ns"
    assert job.dedupe_key == str(row.id)
    assert job.payload["event"]["row_hash"] == row.row_hash
    assert "token" not in str(job.payload).lower()


@pytest.mark.asyncio
async def test_siem_handler_retries_rejected_event(db):
    job = DurableJob(
        namespace="audit-ns",
        kind="siem.event",
        payload={"event": {"id": "evt-1"}},
        attempts=1,
    )
    with patch("src.lians.siem.stream_event", new=AsyncMock(return_value=False)):
        with pytest.raises(RuntimeError, match="did not accept"):
            await handle_siem_job(db, job)
