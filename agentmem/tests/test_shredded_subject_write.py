"""A write for a crypto-shredded subject is a 410 Gone, never a 500.

The subject's key is destroyed by /v1/erase and is never re-created —
minting a fresh key for the same subject_id would let new content
accumulate under an identity the controller already erased.
"""
import pytest

from test_api import _h, AGENT, T0, client  # noqa: F401

SUBJ = "shredded-subject-001"


@pytest.mark.asyncio
async def test_write_after_erase_is_410_gone(client):  # noqa: F811
    resp = await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "Patient record before erasure",
        "event_time": T0.isoformat(),
        "subject_id": SUBJ,
        "metadata": {},
    })
    assert resp.status_code in (200, 201)

    resp = await client.post("/v1/erase", headers=_h(), json={
        "subject_id": SUBJ, "request_ref": "GDPR-req-410",
    })
    assert resp.status_code == 202
    assert resp.json()["key_destroyed_at"] is not None

    resp = await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT,
        "content": "New data for the erased subject",
        "event_time": T0.isoformat(),
        "subject_id": SUBJ,
        "metadata": {},
    })
    assert resp.status_code == 410
    body = resp.json()
    assert body["code"] == "subject_crypto_shredded"
    assert "crypto-shredded" in body["detail"]


@pytest.mark.asyncio
async def test_batch_write_after_erase_is_410_gone(client):  # noqa: F811
    subj = SUBJ + "-batch"
    await client.post("/v1/memories", headers=_h(), json={
        "agent_id": AGENT, "content": "x", "event_time": T0.isoformat(),
        "subject_id": subj, "metadata": {},
    })
    await client.post("/v1/erase", headers=_h(), json={
        "subject_id": subj, "request_ref": "GDPR-req-410b",
    })
    resp = await client.post("/v1/memories/batch", headers=_h(), json={
        "memories": [{
            "agent_id": AGENT, "content": "y", "event_time": T0.isoformat(),
            "subject_id": subj, "metadata": {},
        }],
    })
    assert resp.status_code == 410
