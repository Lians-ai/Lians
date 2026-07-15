"""Decision ledger: cross-industry records and portable evidence packs."""
import hashlib
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.lians.db import get_db
from src.lians.main import app
from src.lians.models import ApiKey, EventLog
from sqlalchemy import select

KEY = "decision-test-key"
NS = "decision-test"
T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def client(db):
    db.add(ApiKey(hashed_key=hashlib.sha256(KEY.encode()).hexdigest(), namespace=NS,
                  scopes=["read", "write", "admin"]))
    await db.commit()
    async def override(): yield db
    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def headers(): return {"X-API-Key": KEY}


@pytest.mark.asyncio
async def test_decision_evidence_pack_is_point_in_time_and_chained(client, db):
    memory = (await client.post("/v1/memories", headers=headers(), json={
        "agent_id": "underwriter-1", "content": "Verified income is 72000",
        "event_time": T0.isoformat(), "subject_id": "applicant-42",
        "metadata": {"field": "income"},
    })).json()
    response = await client.post("/v1/decisions", headers=headers(), json={
        "agent_id": "underwriter-1", "decision_type": "credit_application",
        "outcome": "declined", "reason_codes": ["DTI_HIGH"], "regime": "ECOA_REG_B",
        "subject_id": "applicant-42", "model_id": "credit-v3", "model_version": "3.2",
        "policy_version": "2026-06", "decided_at": T0.isoformat(),
        "knowledge_as_of": T0.isoformat(), "evidence_memory_ids": [memory["id"]],
    })
    assert response.status_code == 200, response.text
    decision = response.json()
    assert len(decision["record_hash"]) == 64

    pack_response = await client.get(f"/v1/decisions/{decision['id']}/evidence-pack", headers=headers())
    assert pack_response.status_code == 200, pack_response.text
    pack = pack_response.json()
    assert pack["schema"].endswith("evidence-pack/v1")
    assert pack["decision"]["reason_codes"] == ["DTI_HIGH"]
    assert pack["cited_evidence"][0]["content"] == "Verified income is 72000"
    assert pack["audit_chain"]["status"] == "ok"
    assert len(pack["pack_hash"]) == 64
    ops = (await db.execute(select(EventLog.op).where(EventLog.namespace == NS))).scalars().all()
    assert "decision_recorded" in ops
    assert "evidence_pack_exported" in ops


@pytest.mark.asyncio
async def test_decision_rejects_cross_namespace_evidence(client):
    response = await client.post("/v1/decisions", headers=headers(), json={
        "agent_id": "agent", "decision_type": "screening", "outcome": "pass",
        "decided_at": T0.isoformat(), "evidence_memory_ids": ["00000000-0000-0000-0000-000000000001"],
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_human_review_is_audited(client):
    created = (await client.post("/v1/decisions", headers=headers(), json={
        "agent_id": "agent", "decision_type": "candidate_screen", "outcome": "reject",
        "reason_codes": ["MINIMUM_EXPERIENCE"], "regime": "EMPLOYMENT",
        "decided_at": T0.isoformat(),
    })).json()
    response = await client.post(f"/v1/decisions/{created['id']}/review", headers=headers(), json={
        "status": "overturned", "reviewer": "reviewer@example.com", "note": "Experience verified",
    })
    assert response.status_code == 200
    assert response.json()["human_review_status"] == "overturned"


@pytest.mark.asyncio
async def test_first_class_record_event_taxonomy(client):
    response = await client.post("/v1/records/events", headers=headers(), json={
        "event_type": "inference", "agent_id": "risk-agent",
        "occurred_at": T0.isoformat(), "model_id": "risk-v4", "model_version": "4.1",
        "payload": {"operation": "risk_score", "result": "manual_review"},
    })
    assert response.status_code == 200, response.text
    event = response.json()
    assert event["event_type"] == "inference"
    assert len(event["event_hash"]) == 64
    listed = await client.get("/v1/records/events", headers=headers(), params={"event_type": "inference"})
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == event["id"]


@pytest.mark.asyncio
async def test_unknown_record_event_type_rejected(client):
    response = await client.post("/v1/records/events", headers=headers(), json={
        "event_type": "article_12_only", "agent_id": "agent", "occurred_at": T0.isoformat(),
    })
    assert response.status_code == 422
