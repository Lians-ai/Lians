"""Engine-owned outcome learning and governed reflection tests."""

import hashlib
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.lians.db import get_db
from src.lians.main import app
from src.lians.models import ApiKey


NS = "experience-ns"
KEY = "experience-key"
AGENT = "experience-agent"


@pytest_asyncio.fixture
async def client(db):
    db.add(
        ApiKey(
            hashed_key=hashlib.sha256(KEY.encode()).hexdigest(),
            namespace=NS,
            scopes=["read", "write", "admin"],
        )
    )
    await db.commit()

    async def override():
        yield db

    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as http:
            yield http
    finally:
        app.dependency_overrides.clear()


def headers():
    return {"X-API-Key": KEY}


@pytest.mark.asyncio
async def test_completed_experience_changes_context_ranking_transparently(client):
    ids = []
    for content in ("Alpha preference", "Beta preference"):
        response = await client.post(
            "/v1/memories",
            headers=headers(),
            json={
                "agent_id": AGENT,
                "content": content,
                "event_time": datetime.now(timezone.utc).isoformat(),
            },
        )
        ids.append(response.json()["id"])

    created = await client.post(
        "/v1/experiences",
        headers=headers(),
        json={
            "agent_id": AGENT,
            "task": "Choose a preference",
            "decision": {"choice": "beta"},
            "context_memory_ids": [ids[1]],
        },
    )
    assert created.status_code == 201, created.text
    completed = await client.patch(
        f"/v1/experiences/{created.json()['id']}/outcome",
        headers=headers(),
        json={"outcome": {"accepted": True}, "reward": 1.0},
    )
    assert completed.status_code == 200

    context = await client.post(
        "/v1/context",
        headers=headers(),
        json={
            "agent_id": AGENT,
            "query": "preference",
            "k": 10,
            "max_tokens": 800,
        },
    )
    body = context.json()
    assert body["learning_applied"] is True
    assert body["ranking_policy"] == "relevance-plus-reviewed-outcomes-v1"
    learned = next(item for item in body["memories"] if item["id"] == ids[1])
    assert learned["metadata"]["_learning"]["completed_uses"] == 1
    assert learned["metadata"]["_base_score"] <= learned["score"]


@pytest.mark.asyncio
async def test_reflection_requires_review_before_promotion(client):
    memory = await client.post(
        "/v1/memories",
        headers=headers(),
        json={
            "agent_id": AGENT,
            "content": "Use verified sources",
            "event_time": datetime.now(timezone.utc).isoformat(),
        },
    )
    memory_id = memory.json()["id"]
    for index in range(2):
        experience = await client.post(
            "/v1/experiences",
            headers=headers(),
            json={
                "agent_id": AGENT,
                "task": "Prepare a source-backed answer",
                "decision": {"attempt": index},
                "context_memory_ids": [memory_id],
            },
        )
        await client.patch(
            f"/v1/experiences/{experience.json()['id']}/outcome",
            headers=headers(),
            json={"outcome": {"accepted": True}, "reward": 0.9},
        )

    generated = await client.post(
        "/v1/reflections/generate",
        headers=headers(),
        json={"agent_id": AGENT},
    )
    assert generated.status_code == 201, generated.text
    proposal = generated.json()["proposals"][0]
    assert proposal["status"] == "pending"
    assert proposal["promoted_memory_id"] is None

    reviewed = await client.patch(
        f"/v1/reflections/{proposal['id']}",
        headers=headers(),
        json={"action": "approve", "reviewer": "human@example.com"},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "approved"
    assert reviewed.json()["promoted_memory_id"]


@pytest.mark.asyncio
async def test_message_ingestion_role_scope_is_enforced_by_engine(client):
    response = await client.post(
        "/v1/memories/messages",
        headers=headers(),
        json={
            "agent_id": AGENT,
            "messages": [
                {"role": "user", "content": "My preferred timezone is UTC."},
                {"role": "assistant", "content": "I will use UTC."},
            ],
            "roles": ["user"],
            "metadata": {"conversation_id": "thread-1"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["added"] == 1
    assert body["memories"][0]["content"] == "My preferred timezone is UTC."
    assert body["memories"][0]["metadata"]["role"] == "user"
    assert body["memories"][0]["metadata"]["message_index"] == 0
