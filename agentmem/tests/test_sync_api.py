"""Authenticated opaque storage contract for zero-knowledge profile sync."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient
from mcp.server.auth.provider import AccessToken
from src.lians.cloud_sync_oauth import (
    CloudSyncOAuthRuntime,
    principal_from_sync_access_token,
)
from src.lians.db import get_db
from src.lians.main import app
from src.lians.models import (
    ApiKey,
    SyncDevice,
    SyncEnrollment,
    SyncRevision,
    SyncWorkspace,
)

NAMESPACE = "opaque-sync-test"
OTHER_NAMESPACE = "opaque-sync-other"
SYNC_KEY = "opaque-sync-key"
OTHER_KEY = "opaque-sync-other-key"
NO_SYNC_KEY = "opaque-no-sync-key"
BARRIER_KEY = "opaque-barrier-sync-key"
OAUTH_SECRET = "test-only-cloud-sync-namespace-secret-32-bytes"


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


def _grant(
    workspace_id,
    epoch,
    approver_key,
    approver,
    recipient,
    *,
    request_id=None,
    issued_at=None,
):
    grant = {
        "format": "lians-device-grant",
        "version": 1,
        "workspace_id": workspace_id,
        "epoch": epoch,
        "request_id": request_id or str(uuid.uuid4()),
        "recipient_device": recipient,
        "approver_device": approver,
        "issued_at": (issued_at or datetime.now(UTC)).isoformat(),
    }
    return {"grant": grant, "signature": _signature(approver_key, grant)}


def _enrollment_request(device, *, now=None):
    created = now or datetime.now(UTC)
    request = {
        "format": "lians-device-enrollment-request",
        "version": 1,
        "request_id": str(uuid.uuid4()),
        "device": device,
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(minutes=10)).isoformat(),
    }
    digest = hashlib.sha256(
        b"lians-enrollment-code-v1\0" + _canonical(request)
    ).hexdigest()
    request["verification_code"] = f"{digest[:4]}-{digest[4:8]}".upper()
    return request


def _enrollment_approval(workspace_id, epoch, approver_key, approver, request):
    grant = _grant(
        workspace_id,
        epoch,
        approver_key,
        approver,
        request["device"],
        request_id=request["request_id"],
    )
    return {
        "format": "lians-device-enrollment-approval",
        "version": 1,
        "request_id": request["request_id"],
        "verification_code": request["verification_code"],
        "grant": grant["grant"],
        "grant_signature": grant["signature"],
        "key_wrap": {
            "cipher": "X25519-HKDF-SHA256+A256GCM",
            "workspace_id": workspace_id,
            "epoch": epoch,
            "request_id": request["request_id"],
            "recipient_device_id": request["device"]["device_id"],
            "approver_device_id": approver["device_id"],
            "ephemeral_public_key": base64.b64encode(os.urandom(32)).decode(),
            "nonce": base64.b64encode(os.urandom(12)).decode(),
            "ciphertext": base64.b64encode(os.urandom(48)).decode(),
        },
    }


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
async def test_short_code_enrollment_exchange_registers_device_without_exposing_a_key(client):
    http, db = client
    workspace_id = str(uuid.uuid4())
    root_key, root = _device("Main PC")
    _, laptop = _device("Laptop")
    await http.post(
        "/v1/sync/workspaces",
        headers=_headers(),
        json={"workspace_id": workspace_id, "epoch": 1, "root_device": root},
    )
    request = _enrollment_request(laptop)

    created = await http.post(
        "/v1/sync/enrollments",
        headers=_headers(),
        json={"request": request},
    )
    assert created.status_code == 201, created.text
    assert created.json()["state"] == "waiting_for_approval"
    assert created.json()["verification_code"] == request["verification_code"]
    assert "workspace_key" not in json.dumps(created.json())

    pending = await http.get("/v1/sync/enrollments", headers=_headers())
    assert pending.status_code == 200
    assert [item["request_id"] for item in pending.json()["enrollments"]] == [
        request["request_id"]
    ]

    approval = _enrollment_approval(workspace_id, 1, root_key, root, request)
    approved = await http.post(
        f"/v1/sync/enrollments/{request['request_id']}/approval",
        headers=_headers(),
        json={"approval": approval},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "registered"
    assert approved.json()["state"] == "approved"
    assert approved.json()["approval"] == approval

    status_response = await http.get(
        f"/v1/sync/enrollments/{request['request_id']}", headers=_headers()
    )
    assert status_response.status_code == 200
    assert status_response.json()["approval"] == approval
    row = await db.get(SyncEnrollment, request["request_id"])
    assert row.namespace == NAMESPACE
    assert row.workspace_id == workspace_id
    device = await db.get(SyncDevice, (workspace_id, laptop["device_id"]))
    assert device.descriptor == laptop
    assert "workspace_key" not in json.dumps(row.request)
    assert "workspace_key" not in json.dumps(row.approval)

    refused = await http.request(
        "DELETE",
        f"/v1/sync/enrollments/{request['request_id']}",
        headers=_headers(),
        json={"confirmed": False},
    )
    assert refused.status_code == 400
    deleted = await http.request(
        "DELETE",
        f"/v1/sync/enrollments/{request['request_id']}",
        headers=_headers(),
        json={"confirmed": True},
    )
    assert deleted.status_code == 200
    assert await db.get(SyncEnrollment, request["request_id"]) is None


@pytest.mark.asyncio
async def test_enrollment_exchange_is_tenant_scoped_expiring_and_tamper_rejecting(client):
    http, db = client
    workspace_id = str(uuid.uuid4())
    root_key, root = _device("Main PC")
    _, laptop = _device("Laptop")
    await http.post(
        "/v1/sync/workspaces",
        headers=_headers(),
        json={"workspace_id": workspace_id, "epoch": 1, "root_device": root},
    )
    request = _enrollment_request(laptop)
    tampered_request = {**request, "verification_code": "0000-0000"}
    rejected_request = await http.post(
        "/v1/sync/enrollments",
        headers=_headers(),
        json={"request": tampered_request},
    )
    assert rejected_request.status_code == 422

    created = await http.post(
        "/v1/sync/enrollments",
        headers=_headers(),
        json={"request": request},
    )
    assert created.status_code == 201
    other_list = await http.get("/v1/sync/enrollments", headers=_headers(OTHER_KEY))
    assert other_list.json()["enrollments"] == []
    other_status = await http.get(
        f"/v1/sync/enrollments/{request['request_id']}",
        headers=_headers(OTHER_KEY),
    )
    assert other_status.status_code == 404
    wrong_scope = await http.get(
        "/v1/sync/enrollments", headers=_headers(NO_SYNC_KEY)
    )
    assert wrong_scope.status_code == 403

    approval = _enrollment_approval(workspace_id, 1, root_key, root, request)
    approval["verification_code"] = "FFFF-FFFF"
    rejected_approval = await http.post(
        f"/v1/sync/enrollments/{request['request_id']}/approval",
        headers=_headers(),
        json={"approval": approval},
    )
    assert rejected_approval.status_code == 422
    assert await db.get(SyncDevice, (workspace_id, laptop["device_id"])) is None

    row = await db.get(SyncEnrollment, request["request_id"])
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    expired = await http.get(
        f"/v1/sync/enrollments/{request['request_id']}", headers=_headers()
    )
    assert expired.status_code == 410


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


@pytest.mark.asyncio
async def test_consumer_oauth_sync_is_scoped_opaque_and_api_key_compatible(
    client, monkeypatch
):
    http, db = client

    class FakeVerifier:
        async def verify_token(self, value):
            if value == "invalid-token":
                return None
            return AccessToken(
                token="verified",
                client_id="lians-native",
                scopes=[] if value == "no-scope-token" else ["memory:sync"],
                expires_at=2_000_000_000,
                resource="https://api.lians.ai",
                subject="auth0|consumer-1",
                claims={"iss": "https://login.example/", "tenant": ""},
            )

    runtime = CloudSyncOAuthRuntime(
        verifier=FakeVerifier(),
        resource_url="https://api.lians.ai",
    )
    monkeypatch.setattr(
        "src.lians.cloud_sync_oauth.get_cloud_sync_oauth_runtime",
        lambda: runtime,
    )
    monkeypatch.setenv("API_SECRET_SEED", OAUTH_SECRET)
    from src.lians.config import get_settings

    get_settings.cache_clear()
    workspace_id = str(uuid.uuid4())
    _, root = _device("Consumer laptop")
    created = await http.post(
        "/v1/sync/workspaces",
        headers={"Authorization": "Bearer consumer-token"},
        json={"workspace_id": workspace_id, "epoch": 1, "root_device": root},
    )
    assert created.status_code == 201, created.text
    token = await FakeVerifier().verify_token("consumer-token")
    principal = principal_from_sync_access_token(token, OAUTH_SECRET)
    assert (await db.get(SyncWorkspace, workspace_id)).namespace == principal.namespace
    assert "consumer-1" not in principal.namespace

    no_scope = await http.get(
        f"/v1/sync/workspaces/{workspace_id}/head",
        headers={"Authorization": "Bearer no-scope-token"},
    )
    assert no_scope.status_code == 403
    invalid = await http.get(
        f"/v1/sync/workspaces/{workspace_id}/head",
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert invalid.status_code == 401
    ambiguous = await http.get(
        f"/v1/sync/workspaces/{workspace_id}/head",
        headers={"Authorization": "Bearer consumer-token", "X-API-Key": SYNC_KEY},
    )
    assert ambiguous.status_code == 400

    # Existing developer credentials continue to use their own tenant boundary.
    developer_workspace = str(uuid.uuid4())
    _, developer_root = _device("Developer PC")
    developer_created = await http.post(
        "/v1/sync/workspaces",
        headers=_headers(),
        json={
            "workspace_id": developer_workspace,
            "epoch": 1,
            "root_device": developer_root,
        },
    )
    assert developer_created.status_code == 201
    assert (await db.get(SyncWorkspace, developer_workspace)).namespace == NAMESPACE


@pytest.mark.asyncio
async def test_sync_missing_credentials_returns_bearer_challenge(client):
    http, _ = client
    response = await http.get(f"/v1/sync/workspaces/{uuid.uuid4()}/head")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer scope="memory:sync"'
    assert "API-Key" in response.json()["detail"]
