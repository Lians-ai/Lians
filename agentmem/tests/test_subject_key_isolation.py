"""Cross-tenant SubjectKey isolation (regression for the 0019 composite-PK fix).

Before 0019 the subject_keys PK was subject_id alone, so two namespaces sharing
a subject_id shared one AES DEK — and one tenant's erase crypto-shredded the
other tenant's data and 500'd their next write. These tests pin the isolation.
"""
import asyncio
import gc
import weakref
from datetime import UTC, datetime

import pytest
from src.lians import dek_cache, memory_service
from src.lians.memory_service import add_memory, erase_subject, recall_memories
from src.lians.pii import get_or_create_subject_key
from src.lians.schemas import MemoryAdd, RecallRequest

T0 = datetime(2026, 1, 1, tzinfo=UTC)
SUBJECT = "customer-42"          # deliberately identical across the two tenants
NS_A, NS_B = "tenant-a", "tenant-b"


@pytest.fixture(autouse=True)
def _clear_dek_cache():
    dek_cache._dek_cache.clear()
    yield
    dek_cache._dek_cache.clear()


def test_dek_cache_can_be_bypassed_for_one_task():
    namespace = "hosted-tenant"
    subject_id = "hosted-memory"
    cached_key = b"a" * 32
    replacement_key = b"b" * 32
    dek_cache.cache_dek(namespace, subject_id, cached_key)

    with dek_cache.dek_cache_disabled():
        assert dek_cache.get_cached_dek(namespace, subject_id) is None
        dek_cache.cache_dek(namespace, subject_id, replacement_key)

    assert dek_cache.get_cached_dek(namespace, subject_id) == cached_key


@pytest.mark.asyncio
async def test_idle_project_write_locks_are_not_retained():
    loop = asyncio.get_running_loop()
    key = (id(loop), "hosted-tenant", "caller-selected-project")
    lock = await memory_service._get_in_process_lock(key[1], key[2])
    lock_reference = weakref.ref(lock)
    assert memory_service._write_locks[key] is lock

    del lock
    gc.collect()

    assert lock_reference() is None
    assert key not in memory_service._write_locks


@pytest.mark.asyncio
async def test_same_subject_id_different_namespaces_get_distinct_keys(db):
    key_a = await get_or_create_subject_key(db, SUBJECT, NS_A)
    key_b = await get_or_create_subject_key(db, SUBJECT, NS_B)
    assert key_a != key_b, "a shared subject_id must not collapse to one tenant key"


@pytest.mark.asyncio
async def test_erase_in_one_tenant_does_not_touch_the_other(db):
    # Both tenants store a memory for the same subject_id.
    await add_memory(db, NS_A, MemoryAdd(
        agent_id="agent-a", content="tenant A secret for customer 42",
        event_time=T0, subject_id=SUBJECT, metadata={"ticker": "AAA", "metric": "x"}))
    await add_memory(db, NS_B, MemoryAdd(
        agent_id="agent-b", content="tenant B secret for customer 42",
        event_time=T0, subject_id=SUBJECT, metadata={"ticker": "BBB", "metric": "y"}))

    # Tenant A erases the subject.
    erased = await erase_subject(db, NS_A, SUBJECT, request_ref="gdpr-a")
    assert erased == 1

    # Tenant A's content is gone…
    ra = await recall_memories(db, NS_A, RecallRequest(agent_id="agent-a", query="secret", k=5))
    a_content = [m.content for m in ra.memories]
    assert all(c is None or "tenant A secret" not in c for c in a_content)

    # …but Tenant B can still read its own content and still write for the subject.
    rb = await recall_memories(db, NS_B, RecallRequest(agent_id="agent-b", query="secret", k=5))
    b_content = [m.content for m in rb.memories]
    assert any(c and "tenant B secret" in c for c in b_content), \
        "tenant B's data was collateral-damaged by tenant A's erase"

    # The write that used to 500 ("key has been crypto-shredded") now succeeds.
    again = await add_memory(db, NS_B, MemoryAdd(
        agent_id="agent-b", content="tenant B follow-up for customer 42",
        event_time=T0, subject_id=SUBJECT, metadata={"ticker": "BBB", "metric": "z"}))
    assert again.id is not None
