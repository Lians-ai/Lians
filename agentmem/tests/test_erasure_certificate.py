"""
Tests for GET /v1/erase/{subject_id}/certificate â€” cryptographic proof-of-erasure.

Covers: 404 before erasure, all certificate fields present after erasure,
content_hashes preserved, chain_status ok, certificate_id stability,
multiple subjects independent, admin scope required.
"""
import hashlib
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker

from lians.main import app
from lians.db import get_db
from lians.models import ApiKey
from lians.subject_erasure_service import (
    claim_due_subject_erasure_jobs,
    process_subject_erasure_job,
)

TEST_NS = "cert-test-ns"
TEST_KEY = "cert-test-key-xyz"
AGENT = "privacy-agent"
T0 = datetime(2025, 6, 1, tzinfo=timezone.utc)


def _h():
    return {"X-API-Key": TEST_KEY}


@pytest_asyncio.fixture
async def client(db):
    hashed = hashlib.sha256(TEST_KEY.encode()).hexdigest()
    db.add(ApiKey(hashed_key=hashed, namespace=TEST_NS, scopes=["read", "write", "admin"]))
    await db.commit()

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


async def _add(client, content, subject_id, event_time=T0):
    r = await client.post("/v1/memories", json={
        "agent_id": AGENT,
        "content": content,
        "event_time": event_time.isoformat(),
        "subject_id": subject_id,
        "metadata": {},
    }, headers=_h())
    assert r.status_code == 200, r.text
    return r.json()


async def _erase(client, db, subject_id, request_ref="GDPR-001"):
    r = await client.post("/v1/erase", json={
        "subject_id": subject_id,
        "request_ref": request_ref,
    }, headers=_h())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["key_destroyed_at"] is not None

    worker_id = f"certificate-test-{body['job_id']}"
    claims = await claim_due_subject_erasure_jobs(
        db,
        worker_id=worker_id,
        batch_size=100,
        lease_seconds=60,
    )
    claim = next(claim for claim in claims if str(claim.job_id) == body["job_id"])
    session_factory = async_sessionmaker(db.bind, expire_on_commit=False)
    await process_subject_erasure_job(
        session_factory,
        claim=claim,
        worker_id=worker_id,
        page_size=100,
        max_pages=20,
        lease_seconds=60,
    )
    await db.run_sync(lambda session: session.expire_all())

    status = await client.get(f"/v1/erase/jobs/{body['job_id']}", headers=_h())
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "completed"
    return status.json()


async def _cert(client, subject_id):
    return await client.get(f"/v1/erase/{subject_id}/certificate", headers=_h())


# â”€â”€ Tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.asyncio
async def test_certificate_404_before_erasure(client):
    r = await _cert(client, "subject-never-erased")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_certificate_200_after_erasure(client, db):
    await _add(client, "Sensitive data", "subject-alpha")
    await _erase(client, db, "subject-alpha")
    r = await _cert(client, "subject-alpha")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_certificate_fields_present(client, db):
    await _add(client, "User data", "subject-beta")
    await _erase(client, db, "subject-beta", request_ref="CCPA-2026-001")
    r = await _cert(client, "subject-beta")
    body = r.json()
    assert "certificate_id" in body
    assert "job_id" in body
    assert "subject_ref" in body
    assert "namespace" in body
    assert "key_destroyed_at" in body
    assert "completed_at" in body
    assert "memories_erased" in body
    assert "content_hashes" in body
    assert "manifest_sha256" in body
    assert "audit_row_hash" in body
    assert "chain_status" in body
    assert "generated_at" in body


@pytest.mark.asyncio
async def test_certificate_memories_erased_count(client, db):
    await _add(client, "Record 1", "subject-gamma")
    await _add(client, "Record 2", "subject-gamma")
    await _add(client, "Record 3", "subject-gamma")
    await _erase(client, db, "subject-gamma")
    body = (await _cert(client, "subject-gamma")).json()
    assert body["memories_erased"] == 3
    assert body["hashes_total"] == 3
    assert body["hashes_complete"] is True


@pytest.mark.asyncio
async def test_certificate_content_hashes_preserved(client, db):
    """content_hashes proves what existed even though content is gone."""
    mem = await _add(client, "PII data here", "subject-delta")
    original_hash = mem["content_hash"]
    await _erase(client, db, "subject-delta")
    body = (await _cert(client, "subject-delta")).json()
    assert original_hash in body["content_hashes"]


@pytest.mark.asyncio
async def test_certificate_subject_reference_is_opaque(client, db):
    await _add(client, "Data", "subject-epsilon")
    await _erase(client, db, "subject-epsilon")
    body = (await _cert(client, "subject-epsilon")).json()
    assert body["subject_ref"].startswith("lians:subject:v2:hmac-sha256:")
    assert "subject-epsilon" not in body["subject_ref"]
    assert "subject_id" not in body
    assert body["namespace"] == TEST_NS


@pytest.mark.asyncio
async def test_certificate_id_is_stable(client, db):
    """Calling the endpoint twice returns the same certificate_id."""
    await _add(client, "Data", "subject-zeta")
    await _erase(client, db, "subject-zeta")
    body1 = (await _cert(client, "subject-zeta")).json()
    body2 = (await _cert(client, "subject-zeta")).json()
    assert body1["certificate_id"] == body2["certificate_id"]


@pytest.mark.asyncio
async def test_certificate_chain_status_is_explicitly_unchecked(client, db):
    """Certificate reads do not conflate bounded evidence with full-chain verification."""
    await _add(client, "Sensitive record", "subject-eta")
    await _erase(client, db, "subject-eta")
    body = (await _cert(client, "subject-eta")).json()
    assert body["chain_status"] == "unchecked"


@pytest.mark.asyncio
async def test_certificate_independent_per_subject(client, db):
    """Two subjects have independent certificates."""
    await _add(client, "Subject A data", "subject-iota")
    await _add(client, "Subject B data", "subject-kappa")
    await _erase(client, db, "subject-iota", request_ref="REF-A")
    await _erase(client, db, "subject-kappa", request_ref="REF-B")
    body_a = (await _cert(client, "subject-iota")).json()
    body_b = (await _cert(client, "subject-kappa")).json()
    assert body_a["certificate_id"] != body_b["certificate_id"]
    assert body_a["memories_erased"] == 1
    assert body_b["memories_erased"] == 1


@pytest.mark.asyncio
async def test_certificate_requires_admin_scope(client, db):
    """Certificate endpoint requires admin scope."""
    # Create a read-only key
    read_key = "read-only-cert-key"
    hashed = hashlib.sha256(read_key.encode()).hexdigest()
    db.add(ApiKey(hashed_key=hashed, namespace=TEST_NS, scopes=["read"]))
    await db.commit()

    await _add(client, "Data", "subject-lambda")
    await _erase(client, db, "subject-lambda")
    r = await client.get("/v1/erase/subject-lambda/certificate",
                         headers={"X-API-Key": read_key})
    assert r.status_code in (401, 403)
