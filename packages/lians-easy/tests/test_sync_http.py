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
