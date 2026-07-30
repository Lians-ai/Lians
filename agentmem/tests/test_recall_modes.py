from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.lians.memory_compiler import METADATA_KEY
from src.lians.memory_service import add_memory, recall_memories
from src.lians.models import EventLog
from src.lians.schemas import MemoryAdd, RecallRequest

NS = "mode-test"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=30)


@pytest.mark.asyncio
async def test_write_compiles_typed_memory_with_provenance(db):
    result = await add_memory(
        db,
        NS,
        MemoryAdd(
            agent_id="agent",
            content="Alice prefers concise answers.",
            event_time=T0,
            source="conversation://turn/1",
        ),
    )
    compiled = result.metadata[METADATA_KEY]
    assert compiled["kind"] == "preference"
    assert compiled["schema"] == "lians.memory-artifact.v1"
    assert compiled["source"]["content_sha256"] == result.content_hash
    assert compiled["source"]["source"] == "conversation://turn/1"


@pytest.mark.asyncio
async def test_fast_mode_returns_content_addressed_receipt(db):
    await add_memory(
        db,
        NS,
        MemoryAdd(
            agent_id="agent",
            content="The deployment region is us-east-1.",
            event_time=T0,
        ),
    )
    result = await recall_memories(
        db,
        NS,
        RecallRequest(agent_id="agent", query="deployment region", mode="fast"),
    )
    assert result.mode == "fast"
    assert result.strategy == "standard"
    assert len(result.receipt_sha256) == 64
    assert result.provenance_coverage == 1.0
    assert result.latency_budget_ms == 100.0


@pytest.mark.asyncio
async def test_deep_mode_forces_adaptive_multi_facet_context(db):
    await add_memory(
        db,
        NS,
        MemoryAdd(
            agent_id="agent",
            content="Yesterday Alice changed the incident workflow.",
            event_time=T0,
        ),
    )
    await add_memory(
        db,
        NS,
        MemoryAdd(
            agent_id="agent",
            content="First open the incident, then export its evidence receipt.",
            event_time=T1,
        ),
    )
    result = await recall_memories(
        db,
        NS,
        RecallRequest(
            agent_id="agent",
            query="When did Alice change the workflow and what are all the steps?",
            mode="deep",
            k=5,
        ),
    )
    assert result.mode == "deep"
    assert result.strategy == "adaptive"
    assert len(result.query_variants) > 1
    assert result.latency_budget_ms == 800.0
    assert result.receipt_sha256


@pytest.mark.asyncio
async def test_reconstruct_mode_preserves_point_in_time_correctness(db):
    old = await add_memory(
        db,
        NS,
        MemoryAdd(
            agent_id="agent",
            content="ACME policy version is v1.",
            event_time=T0,
            metadata={"entity": "ACME", "metric": "policy_version"},
        ),
    )
    new = await add_memory(
        db,
        NS,
        MemoryAdd(
            agent_id="agent",
            content="ACME policy version is v2.",
            event_time=T1,
            metadata={"entity": "ACME", "metric": "policy_version"},
        ),
    )
    result = await recall_memories(
        db,
        NS,
        RecallRequest(
            agent_id="agent",
            query="ACME policy version",
            as_of=T0 + timedelta(days=1),
            mode="reconstruct",
            k=10,
        ),
    )
    ids = {memory.id for memory in result.memories}
    assert old.id in ids
    assert new.id not in ids
    assert result.mode == "reconstruct"
    assert result.strategy == "adaptive"
    assert result.latency_budget_ms == 2000.0
    assert result.provenance_coverage == 1.0


@pytest.mark.asyncio
async def test_keyed_fast_path_commits_the_same_receipt_evidence(db):
    namespace = "mode-keyed-receipt"
    metadata = {"ticker": "ACME", "metric": "policy_version"}
    await add_memory(
        db,
        namespace,
        MemoryAdd(
            agent_id="agent",
            content="ACME policy version is v2.",
            event_time=T0,
            source="policy://acme/v2",
            metadata=metadata,
        ),
    )
    result = await recall_memories(
        db,
        namespace,
        RecallRequest(
            agent_id="agent",
            query="What is the ACME policy version?",
            filters=metadata,
        ),
    )
    event = (
        await db.execute(
            select(EventLog).where(
                EventLog.namespace == namespace,
                EventLog.op == "recall",
            )
        )
    ).scalar_one()
    assert event.payload["router"] == "keyed"
    assert event.payload["receipt_sha256"] == result.receipt_sha256
    assert event.payload["receipt"] == result.receipt
