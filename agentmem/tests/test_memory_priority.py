from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from src.lians.memory_priority import (
    SCHEMA_VERSION,
    apply_memory_priority,
    assess_memory_priority,
)
from src.lians.schemas import MemoryAdd, MemoryOut


def test_personal_preferences_receive_a_durable_importance_floor():
    priority = assess_memory_priority(
        "I prefer concise answers with the decision first.",
        {"role": "user"},
        0.5,
    )

    assert priority.kind == "preference"
    assert priority.tier == "durable"
    assert priority.durable
    assert priority.importance == 0.9
    assert "personal-preference" in priority.signals


@pytest.mark.parametrize(
    "content",
    [
        "My timezone is America/New_York.",
        "Call me Jay.",
        "I'm allergic to peanuts.",
        "Please always use bullet points for action items.",
        "Don't use emojis in responses.",
        "I want all answers to begin with the decision.",
    ],
)
def test_explicit_personal_facts_and_response_preferences_are_durable(content):
    priority = assess_memory_priority(content, {"role": "user"}, 0.5)
    assert priority.durable
    assert priority.importance >= 0.88


def test_transient_chatter_and_questions_are_distinguished_without_overriding_intent():
    acknowledgement = assess_memory_priority("Thanks!", {"role": "user"}, 0.5)
    question = assess_memory_priority("Can you check the deployment?", {"role": "user"}, 0.5)
    explicitly_high = assess_memory_priority("Thanks!", {"role": "user"}, 0.8)

    assert acknowledgement.tier == "transient"
    assert acknowledgement.importance == 0.15
    assert question.tier == "contextual"
    assert question.importance == 0.3
    assert explicitly_high.importance == 0.8


def test_priority_application_replaces_caller_controlled_reserved_metadata():
    req = MemoryAdd(
        agent_id="agent",
        content="I prefer dark mode.",
        event_time=datetime.now(UTC),
        metadata={"_memory_priority": {"tier": "transient", "importance": 0.0}},
    )

    priority = apply_memory_priority(req)

    assert req.importance == 0.9
    assert req.metadata["_memory_priority"] == priority.metadata()
    assert req.metadata["_memory_priority"]["schema"] == SCHEMA_VERSION


def test_caller_memory_kind_remains_authoritative_while_priority_stays_durable():
    priority = assess_memory_priority(
        "I prefer dark mode.",
        {"memory_type": "fact"},
        0.5,
    )

    assert priority.kind == "fact"
    assert priority.durable
    assert priority.importance == 0.88


@pytest.mark.asyncio
async def test_batch_ingest_uses_one_provider_call_and_preserves_order(monkeypatch):
    import src.lians.memory_service as service

    class Provider:
        def __init__(self):
            self.calls = []

        async def embed(self, texts):
            self.calls.append(list(texts))
            return [[float(index)] for index, _text in enumerate(texts)]

    provider = Provider()
    seen = []

    async def fake_add(_db, _namespace, req, **kwargs):
        seen.append((req.content, kwargs["precomputed_embedding"]))
        return MemoryOut(
            id="00000000-0000-0000-0000-000000000001",
            namespace="test",
            agent_id=req.agent_id,
            content=req.content,
            subject_id=None,
            event_time=req.event_time,
            ingestion_time=req.event_time,
            valid_from=req.event_time,
            valid_to=None,
            superseded_by=None,
            supersession_confidence=None,
            importance=req.importance,
            source=req.source,
            content_hash="hash",
            erased_at=None,
            metadata=req.metadata,
        )

    monkeypatch.setattr(service, "get_embedding_provider", lambda: provider)
    monkeypatch.setattr(service, "add_memory", fake_add)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(admission_mode="monitor", admission_blocked_sources=""),
    )
    now = datetime.now(UTC)
    reqs = [
        MemoryAdd(agent_id="agent", content="first memory", event_time=now),
        MemoryAdd(agent_id="agent", content="second memory", event_time=now),
        MemoryAdd(agent_id="agent", content="third memory", event_time=now),
    ]

    result = await service.batch_add_memories(object(), "test", reqs)

    assert result.added == 3
    assert provider.calls == [["first memory", "second memory", "third memory"]]
    assert seen == [
        ("first memory", [0.0]),
        ("second memory", [1.0]),
        ("third memory", [2.0]),
    ]


@pytest.mark.asyncio
async def test_single_write_batches_parent_and_preference_clause_embeddings(db, monkeypatch):
    import src.lians.memory_service as service

    class Provider:
        def __init__(self):
            self.calls = []

        async def embed(self, texts):
            self.calls.append(list(texts))
            return [[0.0] * 1024 for _text in texts]

        async def embed_one(self, _text):
            raise AssertionError("the parent and clause should share embed()")

    provider = Provider()
    monkeypatch.setattr(service, "get_embedding_provider", lambda: provider)
    turn = (
        "User: Let's finish the deployment checklist. By the way, I prefer concise "
        "status updates, so please keep the summary short."
    )
    req = MemoryAdd(
        agent_id="preference-agent",
        content=turn,
        event_time=datetime.now(UTC),
        source="conversation",
    )

    await service.add_memory(db, "preference-batch", req)

    assert len(provider.calls) == 1
    assert provider.calls[0][0] == turn
    assert any("prefer concise" in text for text in provider.calls[0][1:])
