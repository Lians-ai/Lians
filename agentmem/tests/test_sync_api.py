"""Authenticated opaque storage contract for zero-knowledge profile sync."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient
from src.lians.db import get_db
from src.lians.main import app
from src.lians.models import ApiKey, SyncDevice, SyncRevision, SyncWorkspace

NAMESPACE = "opaque-sync-test"
OTHER_NAMESPACE = "opaque-sync-other"
SYNC_KEY = "opaque-sync-key"
OTHER_KEY = "opaque-sync-other-key"
NO_SYNC_KEY = "opaque-no-sync-key"
BARRIER_KEY = "opaque-barrier-sync-key"


def _headers(key: str = SYNC_KEY) -> dict[str, str]:
    return {"X-API-Key": key}


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _raw_public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _device(name: str):
    signing = Ed25519PrivateKey.generate()
    signing_public = _raw_public(signing)
    exchange_public = os.urandom(32)
    device_id = hashlib.sha256(
        b"lians-device-v1\0" + exchange_public + signing_public
    ).hexdigest()
    return signing, {
        "device_id": device_id,
        "display_name": name,
        "exchange_public_key": base64.b64encode(exchange_public).decode(),
        "signing_public_key": base64.b64encode(signing_public).decode(),
    }


def _signature(private: Ed25519PrivateKey, value: dict) -> dict[str, str]:
    return {
        "algorithm": "Ed25519",
        "value": base64.b64encode(private.sign(_canonical(value))).decode(),
    }


def _grant(workspace_id, epoch, approver_key, approver, recipient):
    grant = {
        "format": "lians-device-grant",
        "version": 1,
        "workspace_id": workspace_id,
        "epoch": epoch,
        "request_id": str(uuid.uuid4()),
        "recipient_device": recipient,
        "approver_device": approver,
        "issued_at": datetime.now(UTC).isoformat(),
    }
    return {"grant": grant, "signature": _signature(approver_key, grant)}


def _revision(workspace_id, epoch, revision, previous_hash, private, device):
    signed = {
        "format": "lians-encrypted-profile-revision",
        "version": 1,
        "workspace_id": workspace_id,
        "epoch": epoch,
        "revision": revision,
        "previous_hash": previous_hash,
        "device_id": device["device_id"],
        "created_at": datetime.now(UTC).isoformat(),
        "cipher": {
            "name": "AES-256-GCM",
            "nonce": base64.b64encode(os.urandom(12)).decode(),
        },
        "ciphertext": base64.b64encode(os.urandom(64)).decode(),
    }
    protected = {**signed, "signature": _signature(private, signed)}
    return {
        **protected,
        "object_hash": hashlib.sha256(_canonical(protected)).hexdigest(),
    }


@pytest_asyncio.fixture
async def client(db):
    for key, namespace, scopes in (
        (SYNC_KEY, NAMESPACE, ["sync"]),
        (OTHER_KEY, OTHER_NAMESPACE, ["sync"]),
        (NO_SYNC_KEY, NAMESPACE, ["read", "write"]),
    ):
        db.add(
            ApiKey(
                hashed_key=hashlib.sha256(key.encode()).hexdigest(),
                namespace=namespace,
                scopes=scopes,
            )
        )
    db.add(
        ApiKey(
            hashed_key=hashlib.sha256(BARRIER_KEY.encode()).hexdigest(),
            namespace=NAMESPACE,
            scopes=["sync"],
            barrier_group="restricted-desk",
        )
    )
    await db.commit()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, db
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_opaque_workspace_device_and_revision_lifecycle(client):
    http, db = client
    workspace_id = str(uuid.uuid4())
    root_key, root = _device("Main PC")
    second_key, second = _device("Laptop")

    created = await http.post(
        "/v1/sync/workspaces",
        headers=_headers(),
        json={"workspace_id": workspace_id, "epoch": 1, "root_device": root},
    )
    assert created.status_code == 201, created.text
    assert created.json()["head"] == {"revision": 0, "object_hash": None}
    assert (await db.get(SyncWorkspace, workspace_id)).namespace == NAMESPACE
    assert (await db.get(SyncDevice, (workspace_id, root["device_id"]))).grant is None

    grant = _grant(workspace_id, 1, root_key, root, second)
    enrolled = await http.post(
        f"/v1/sync/workspaces/{workspace_id}/devices",
        headers=_headers(),
        json=grant,
    )
    assert enrolled.status_code == 201, enrolled.text
    assert enrolled.json()["device"]["device_id"] == second["device_id"]

    revision_one = _revision(workspace_id, 1, 1, None, root_key, root)
    pushed = await http.post(
        f"/v1/sync/workspaces/{workspace_id}/revisions",
        headers=_headers(),
        json={"envelope": revision_one},
    )
    assert pushed.status_code == 201, pushed.text
    assert pushed.json()["encrypted"] is True

    revision_two = _revision(
        workspace_id,
        1,
        2,
        revision_one["object_hash"],
        second_key,
        second,
    )
    pushed = await http.post(
        f"/v1/sync/workspaces/{workspace_id}/revisions",
        headers=_headers(),
        json={"envelope": revision_two},
    )
    assert pushed.status_code == 201, pushed.text

    pulled = await http.get(
        f"/v1/sync/workspaces/{workspace_id}/revisions",
        headers=_headers(),
        params={"after": 0},
    )
    assert pulled.status_code == 200
    assert pulled.json()["revisions"] == [revision_one, revision_two]
    assert pulled.json()["head"]["object_hash"] == revision_two["object_hash"]
    stored = await db.get(SyncRevision, (workspace_id, 2))
    assert stored.envelope == revision_two
    assert "memories" not in stored.envelope

    grants = await http.get(
        f"/v1/sync/workspaces/{workspace_id}/devices/grants",
        headers=_headers(),
    )
    assert grants.json()["grants"] == [grant]

    head = await http.get(
        f"/v1/sync/workspaces/{workspace_id}/head",
        headers=_headers(),
    )
    assert head.json()["active_devices"] == 2
    assert head.json()["head"]["revision"] == 2


@pytest.mark.asyncio
async def test_sync_rejects_tampering_stale_writes_wrong_scope_and_other_tenant(client):
    http, _ = client
    workspace_id = str(uuid.uuid4())
    root_key, root = _device("Main PC")
    await http.post(
        "/v1/sync/workspaces",
        headers=_headers(),
        json={"workspace_id": workspace_id, "epoch": 1, "root_device": root},
    )
    revision = _revision(workspace_id, 1, 1, None, root_key, root)

    tampered = json.loads(json.dumps(revision))
    tampered["ciphertext"] = base64.b64encode(os.urandom(64)).decode()
    rejected = await http.post(
        f"/v1/sync/workspaces/{workspace_id}/revisions",
        headers=_headers(),
        json={"envelope": tampered},
    )
    assert rejected.status_code == 422
    assert "hash" in rejected.json()["detail"]

    accepted = await http.post(
        f"/v1/sync/workspaces/{workspace_id}/revisions",
        headers=_headers(),
        json={"envelope": revision},
    )
    assert accepted.status_code == 201
    stale = await http.post(
        f"/v1/sync/workspaces/{workspace_id}/revisions",
        headers=_headers(),
        json={"envelope": revision},
    )
    assert stale.status_code == 412
    assert stale.json()["detail"]["head"]["revision"] == 1

    forbidden = await http.get(
        f"/v1/sync/workspaces/{workspace_id}/head",
        headers=_headers(NO_SYNC_KEY),
    )
    assert forbidden.status_code == 403
    barrier_forbidden = await http.get(
        f"/v1/sync/workspaces/{workspace_id}/head",
        headers=_headers(BARRIER_KEY),
    )
    assert barrier_forbidden.status_code == 403
    isolated = await http.get(
        f"/v1/sync/workspaces/{workspace_id}/head",
        headers=_headers(OTHER_KEY),
    )
    assert isolated.status_code == 404


@pytest.mark.asyncio
async def test_cloud_workspace_deletion_requires_exact_confirmation(client):
    http, db = client
    workspace_id = str(uuid.uuid4())
    root_key, root = _device("Main PC")
    await http.post(
        "/v1/sync/workspaces",
        headers=_headers(),
        json={"workspace_id": workspace_id, "epoch": 1, "root_device": root},
    )
    revision = _revision(workspace_id, 1, 1, None, root_key, root)
    await http.post(
        f"/v1/sync/workspaces/{workspace_id}/revisions",
        headers=_headers(),
        json={"envelope": revision},
    )

    refused = await http.request(
        "DELETE",
        f"/v1/sync/workspaces/{workspace_id}",
        headers=_headers(),
        json={"confirmed": True, "confirmation": "delete"},
    )
    assert refused.status_code == 400

    deleted = await http.request(
        "DELETE",
        f"/v1/sync/workspaces/{workspace_id}",
        headers=_headers(),
        json={
            "confirmed": True,
            "confirmation": "DELETE ENCRYPTED LIANS CLOUD MEMORY",
        },
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["encrypted_revisions_deleted"] == 1
    assert await db.get(SyncWorkspace, workspace_id) is None
    assert await db.get(SyncRevision, (workspace_id, 1)) is None
