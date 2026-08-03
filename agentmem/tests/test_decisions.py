"""Decision ledger: cross-industry records and portable evidence packs."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from lians.db import get_db
from lians.main import app
from lians.metering_models import MeteringEvent
from lians.models import ApiKey, EventLog, NamespacePolicy
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
    assert decision["agent_id"] == "underwriter-1"
    assert decision["recorded_by_principal_ref"].startswith(
        "lians:principal:v1:api-key:"
    )
    assert decision["recorded_by_auth_method"] == "api_key"
    assert decision["recorded_by_credential_ref"].startswith(
        "lians:credential:v1:sha256:"
    )
    assert decision["record_hash_version"] == 3
    assert decision["record_integrity_status"] == "verified"

    pack_response = await client.get(f"/v1/decisions/{decision['id']}/evidence-pack", headers=headers())
    assert pack_response.status_code == 200, pack_response.text
    pack = pack_response.json()
    assert pack["schema"].endswith("evidence-pack/v1")
    assert pack["decision"]["reason_codes"] == ["DTI_HIGH"]
    assert pack["cited_evidence"][0]["content"] == "Verified income is 72000"
    assert pack["audit_chain"]["status"] == "ok"
    assert len(pack["pack_hash"]) == 64
    events = (
        await db.execute(select(EventLog).where(EventLog.namespace == NS))
    ).scalars().all()
    ops = [event.op for event in events]
    assert "decision_recorded" in ops
    assert "evidence_pack_exported" in ops
    recorded = next(event for event in events if event.op == "decision_recorded")
    assert recorded.agent_id == decision["recorded_by_principal_ref"]
    assert recorded.payload == {
        "schema": "lians.decision-record-binding.v1",
        "decision_id": decision["id"],
        "record_hash": decision["record_hash"],
    }


@pytest.mark.asyncio
async def test_decision_rejects_cross_namespace_evidence(client):
    response = await client.post("/v1/decisions", headers=headers(), json={
        "agent_id": "agent", "decision_type": "screening", "outcome": "pass",
        "decided_at": T0.isoformat(), "evidence_memory_ids": ["00000000-0000-0000-0000-000000000001"],
    })
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("naive", [False, True])
async def test_decision_rejects_future_recording_cutoff(client, naive):
    cutoff = datetime.now(timezone.utc) + timedelta(days=1)
    if naive:
        cutoff = cutoff.replace(tzinfo=None)
    response = await client.post("/v1/decisions", headers=headers(), json={
        "agent_id": "agent", "decision_type": "screening", "outcome": "pass",
        "decided_at": T0.isoformat(),
        "knowledge_recorded_as_of": cutoff.isoformat(),
    })
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "knowledge_recorded_as_of cannot be later than recorded_at"
    )


@pytest.mark.asyncio
async def test_decision_normalizes_naive_recording_cutoff_to_utc(client):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None)
    response = await client.post("/v1/decisions", headers=headers(), json={
        "agent_id": "agent", "decision_type": "screening", "outcome": "pass",
        "decided_at": T0.isoformat(),
        "knowledge_recorded_as_of": cutoff.isoformat(),
    })
    assert response.status_code == 200, response.text
    returned = datetime.fromisoformat(response.json()["knowledge_recorded_as_of"])
    assert returned.tzinfo is not None
    assert returned.utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_human_review_is_audited(client):
    created = (await client.post("/v1/decisions", headers=headers(), json={
        "agent_id": "agent", "decision_type": "candidate_screen", "outcome": "reject",
        "reason_codes": ["MINIMUM_EXPERIENCE"], "regime": "EMPLOYMENT",
        "decided_at": T0.isoformat(),
    })).json()
    response = await client.post(f"/v1/decisions/{created['id']}/review", headers=headers(), json={
        "status": "overturned", "note": "Experience verified",
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


@pytest.mark.asyncio
async def test_decision_and_ledger_mutations_are_body_bound_and_replayable(client, db):
    # A billing destination makes the decision mutation exercise its durable
    # commercial obligation, while provider delivery remains disabled locally.
    db.add(NamespacePolicy(namespace=NS, stripe_customer_id="cus_decision_contract"))
    await db.commit()
    decision_body = {
        "agent_id": "idempotent-agent",
        "decision_type": "screening",
        "outcome": "pass",
        "decided_at": T0.isoformat(),
    }
    decision_headers = headers() | {"Idempotency-Key": "decision-once"}
    first = await client.post(
        "/v1/decisions", headers=decision_headers, json=decision_body
    )
    replay = await client.post(
        "/v1/decisions", headers=decision_headers, json=decision_body
    )
    conflict = await client.post(
        "/v1/decisions",
        headers=decision_headers,
        json=decision_body | {"outcome": "deny"},
    )
    assert first.status_code == replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]
    assert conflict.status_code == 409
    decision_meter_events = list(
        (
            await db.execute(
                select(MeteringEvent).where(
                    MeteringEvent.namespace == NS,
                    MeteringEvent.event_name == "lians_authoritative_decision",
                )
            )
        ).scalars()
    )
    assert len(decision_meter_events) == 1
    assert decision_meter_events[0].quantity == 1

    event_body = {
        "event_type": "inference",
        "agent_id": "idempotent-agent",
        "occurred_at": T0.isoformat(),
        "payload": {"result": "pass"},
    }
    event_headers = headers() | {"Idempotency-Key": "ledger-once"}
    event_first = await client.post(
        "/v1/records/events", headers=event_headers, json=event_body
    )
    event_replay = await client.post(
        "/v1/records/events", headers=event_headers, json=event_body
    )
    assert event_first.status_code == event_replay.status_code == 200
    assert event_first.json()["id"] == event_replay.json()["id"]


@pytest.mark.asyncio
async def test_review_replay_preserves_the_original_review_projection(client):
    created = (
        await client.post(
            "/v1/decisions",
            headers=headers(),
            json={
                "agent_id": "review-agent",
                "decision_type": "screening",
                "outcome": "hold",
                "decided_at": T0.isoformat(),
            },
        )
    ).json()
    path = f"/v1/decisions/{created['id']}/review"
    first_headers = headers() | {"Idempotency-Key": "review-requested"}
    first = await client.post(
        path,
        headers=first_headers,
        json={"status": "requested", "note": "Escalate"},
    )
    later = await client.post(
        path,
        headers=headers() | {"Idempotency-Key": "review-affirmed"},
        json={"status": "affirmed", "note": "Reviewed"},
    )
    replay = await client.post(
        path,
        headers=first_headers,
        json={"status": "requested", "note": "Escalate"},
    )
    assert first.status_code == later.status_code == replay.status_code == 200
    assert later.json()["human_review_status"] == "affirmed"
    assert replay.json()["human_review_status"] == "requested"
    assert replay.json()["human_reviewed_at"] == first.json()["human_reviewed_at"]
