from __future__ import annotations

import io
import json
import urllib.error

import pytest
from lians_easy.store import MemoryStore
from lians_easy.sync import DeviceIdentity, SyncPreconditionError, SyncState
from lians_easy.sync_http import OpaqueSyncHTTPClient, SyncCloudError


class Response:
    def __init__(self, document, *, status=200):
        self.status = status
        self.body = json.dumps(document).encode()

    def read(self, amount=-1):
        return self.body if amount < 0 else self.body[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_http_transport_sends_only_scoped_envelopes_and_redacts_credential(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    identity = DeviceIdentity.from_store(store, "Main PC")
    state = SyncState.create(identity)
    calls = []

    def opener(request, *, timeout):
        calls.append((request, timeout))
        return Response({"status": "created"}, status=201)

    client = OpaqueSyncHTTPClient(
        "https://cloud.lians.ai",
        "lians-secret-test-credential",
        opener=opener,
    )
    assert client.create_workspace(state) == {"status": "created"}
    request, timeout = calls[0]
    assert request.full_url == "https://cloud.lians.ai/v1/sync/workspaces"
    assert request.headers["X-api-key"] == "lians-secret-test-credential"
    assert json.loads(request.data) == {
        "workspace_id": state.workspace_id,
        "epoch": state.epoch,
        "root_device": state.device,
    }
    assert timeout == 15
    assert "lians-secret-test-credential" not in repr(client)


def test_http_transport_uses_rotating_oauth_bearer_without_retaining_it():
    calls = []
    tokens = iter(["first-access-token", "rotated-access-token"])

    def opener(request, *, timeout):
        calls.append(request)
        return Response({"status": "ok"})

    client = OpaqueSyncHTTPClient(
        "https://cloud.lians.ai",
        bearer_token_provider=lambda: next(tokens),
        opener=opener,
    )
    workspace_id = "2446b8a9-0f7c-4ea6-9434-8fb1857aa10d"
    client.head(workspace_id)
    client.head(workspace_id)
    assert calls[0].headers["Authorization"] == "Bearer first-access-token"
    assert calls[1].headers["Authorization"] == "Bearer rotated-access-token"
    assert "X-api-key" not in calls[0].headers
    assert "access-token" not in repr(client)


def test_http_transport_supports_the_short_code_device_exchange():
    request_id = "2446b8a9-0f7c-4ea6-9434-8fb1857aa10d"
    enrollment = {"request_id": request_id, "device": {"display_name": "Laptop"}}
    calls = []

    def opener(request, *, timeout):
        calls.append(request)
        if request.get_method() == "GET" and request.full_url.endswith("/enrollments"):
            return Response({"enrollments": [enrollment]})
        return Response({"status": "ok", **enrollment})

    client = OpaqueSyncHTTPClient(
        "https://cloud.lians.ai",
        bearer_token_provider=lambda: "access-token",
        opener=opener,
    )
    assert client.create_enrollment(enrollment)["status"] == "ok"
    assert client.enrollments() == [enrollment]
    assert client.enrollment(request_id)["request_id"] == request_id
    assert client.approve_enrollment(request_id, {"signed": True})["status"] == "ok"
    assert client.delete_enrollment(request_id, confirmed=True)["status"] == "ok"

    assert [request.get_method() for request in calls] == [
        "POST",
        "GET",
        "GET",
        "POST",
        "DELETE",
    ]
    assert json.loads(calls[0].data) == {"request": enrollment}
    assert json.loads(calls[3].data) == {"approval": {"signed": True}}
    assert json.loads(calls[4].data) == {"confirmed": True}
    with pytest.raises(ValueError, match="confirmed=true"):
        client.delete_enrollment(request_id)


def test_http_transport_supports_key_free_device_management_and_rotation():
    workspace_id = "2446b8a9-0f7c-4ea6-9434-8fb1857aa10d"
    device_id = "a" * 64
    pair = {"rotation": {"rotation_id": workspace_id}, "signature": {"value": "signed"}}
    calls = []

    def opener(request, *, timeout):
        calls.append(request)
        if request.full_url.endswith("/devices"):
            return Response({"epoch": 2, "devices": []})
        if "key-rotations" in request.full_url:
            return Response({"epoch": 2, "rotations": [pair]})
        return Response({"status": "removed", "future_memory_protected": True})

    client = OpaqueSyncHTTPClient(
        "https://cloud.lians.ai",
        bearer_token_provider=lambda: "access-token",
        opener=opener,
    )
    assert client.devices(workspace_id)["devices"] == []
    assert client.key_rotations(workspace_id, after=1)["rotations"] == [pair]
    with pytest.raises(ValueError, match="confirmed=true"):
        client.remove_device(workspace_id, device_id, pair)
    removed = client.remove_device(
        workspace_id,
        device_id,
        pair,
        confirmed=True,
    )
    assert removed["future_memory_protected"] is True
    assert calls[1].full_url.endswith("/key-rotations?after=1")
    assert calls[2].full_url.endswith(f"/devices/{device_id}/remove")
    assert json.loads(calls[2].data) == pair


def test_http_transport_requires_exactly_one_auth_mode():
    with pytest.raises(ValueError, match="exactly one"):
        OpaqueSyncHTTPClient("https://cloud.lians.ai")
    with pytest.raises(ValueError, match="exactly one"):
        OpaqueSyncHTTPClient(
            "https://cloud.lians.ai",
            "developer-key",
            bearer_token_provider=lambda: "consumer-token",
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://cloud.lians.ai",
        "https://user:password@cloud.lians.ai",
        "https://cloud.lians.ai/path",
        "https://cloud.lians.ai?token=secret",
    ],
)
def test_http_transport_rejects_unsafe_cloud_origins(url):
    with pytest.raises(ValueError):
        OpaqueSyncHTTPClient(url, "credential")

    # Loopback HTTP remains available for hermetic local integration tests.
    assert OpaqueSyncHTTPClient("http://127.0.0.1:8787", "credential").base_url.startswith(
        "http://127.0.0.1"
    )


def test_http_transport_maps_stale_head_and_sanitizes_remote_failures():
    def stale(request, *, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            412,
            "Precondition Failed",
            {},
            io.BytesIO(
                json.dumps(
                    {"detail": {"message": "Cloud sync head changed; pull before retrying"}}
                ).encode()
            ),
        )

    client = OpaqueSyncHTTPClient("https://cloud.lians.ai", "credential", opener=stale)
    with pytest.raises(SyncPreconditionError, match="pull before retrying"):
        client.head("2446b8a9-0f7c-4ea6-9434-8fb1857aa10d")

    def hostile(request, *, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            500,
            "Failure",
            {},
            io.BytesIO(b'{"detail":"api_key=should-not-be-reflected\\n"}'),
        )

    client = OpaqueSyncHTTPClient("https://cloud.lians.ai", "credential", opener=hostile)
    with pytest.raises(SyncCloudError, match="status 500") as captured:
        client.head("2446b8a9-0f7c-4ea6-9434-8fb1857aa10d")
    assert "api_key" not in str(captured.value)


def test_cloud_delete_requires_local_confirmation():
    client = OpaqueSyncHTTPClient("https://cloud.lians.ai", "credential")
    with pytest.raises(ValueError, match="confirmed=true"):
        client.delete_workspace("2446b8a9-0f7c-4ea6-9434-8fb1857aa10d")
    with pytest.raises(ValueError, match="workspace ID"):
        client.head("../another-account")


def test_account_cloud_data_delete_uses_bounded_exact_confirmation():
    calls = []

    def opener(request, *, timeout):
        calls.append((request, timeout))
        return Response({"status": "deleted", "workspaces_deleted": 2})

    client = OpaqueSyncHTTPClient(
        "https://cloud.lians.ai",
        bearer_token_provider=lambda: "access-token",
        opener=opener,
    )
    with pytest.raises(ValueError, match="confirmed=true"):
        client.delete_account_data()

    result = client.delete_account_data(confirmed=True)

    assert result["workspaces_deleted"] == 2
    request, timeout = calls[0]
    assert request.get_method() == "DELETE"
    assert request.full_url == "https://cloud.lians.ai/v1/sync/account-data"
    assert timeout == 15
    assert json.loads(request.data) == {
        "confirmed": True,
        "confirmation": "DELETE ALL LIANS CLOUD DATA",
    }
