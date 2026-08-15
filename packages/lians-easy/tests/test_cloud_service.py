from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from lians_easy.cloud_service import CloudSyncService
from lians_easy.mcp import call_tool
from lians_easy.portability import export_backup, import_backup
from lians_easy.store import MemoryStore
from lians_easy.sync import (
    DeviceIdentity,
    OpaqueRevisionLog,
    SyncState,
    accept_enrollment,
    approve_enrollment,
    create_enrollment_request,
)
from lians_easy.sync_http import SyncCloudError

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)  # noqa: UP017


class FakeAuth:
    config = SimpleNamespace(cloud_url="https://api.lians.ai")

    def __init__(self):
        self.signed_out = False

    def status(self):
        return {
            "state": "signed_out" if self.signed_out else "connected",
            "configured": True,
            "message": "Connected.",
        }

    def access_token(self):
        return "short-lived-access-token"

    def sign_out(self, *, confirmed=False):
        assert confirmed is True
        self.signed_out = True
        return {"state": "signed_out", "local_memory_preserved": True}


class HTTPShapeCloud:
    def __init__(self):
        self.log = OpaqueRevisionLog()
        self.deleted = False
        self.enrollments = {}

    def client_factory(self, base_url, *, bearer_token_provider):
        assert base_url == "https://api.lians.ai"
        assert bearer_token_provider() == "short-lived-access-token"
        outer = self

        class Client:
            def create_workspace(self, state):
                outer.log.create_workspace(state)
                return {"status": "created"}

            def create_enrollment(self, request):
                outer.enrollments[request["request_id"]] = {
                    "request": request,
                    "approval": None,
                }
                return {"status": "created"}

            def enrollments(self):
                return [
                    {
                        "request_id": request_id,
                        "request": document["request"],
                        "approval": document["approval"],
                    }
                    for request_id, document in outer.enrollments.items()
                    if document["approval"] is None
                ]

            def enrollment(self, request_id):
                return outer.enrollments[request_id]

            def approve_enrollment(self, request_id, approval):
                outer.log.register_approval(approval)
                outer.enrollments[request_id]["approval"] = approval
                return {"status": "registered"}

            def delete_enrollment(self, request_id, *, confirmed=False):
                assert confirmed is True
                del outer.enrollments[request_id]
                return {"status": "deleted"}

            def grants(self, workspace_id):
                return outer.log.grants(workspace_id)

            def key_rotations(self, workspace_id, *, after):
                return outer.log.key_rotations(workspace_id, after=after)

            def devices(self, workspace_id):
                return outer.log.devices(workspace_id)

            def remove_device(
                self, workspace_id, device_id, rotation, *, confirmed=False
            ):
                assert confirmed is True
                return outer.log.remove_device(workspace_id, device_id, rotation)

            def revisions_after(self, workspace_id, revision):
                rows = outer.log.revisions_after(workspace_id, revision)
                workspace = outer.log._workspace(workspace_id)
                head = workspace["revisions"][-1] if workspace["revisions"] else None
                return {
                    "revisions": rows,
                    "head": {
                        "revision": len(workspace["revisions"]),
                        "object_hash": head["object_hash"] if head else None,
                    },
                    "has_more": False,
                }

            def push(self, workspace_id, envelope):
                assert workspace_id == envelope["workspace_id"]
                return outer.log.push(envelope)

            def delete_workspace(self, workspace_id, *, confirmed=False):
                assert confirmed is True
                workspace = outer.log._workspace(workspace_id)
                revision_count = len(workspace["revisions"])
                device_count = len(workspace["devices"])
                del outer.log._workspaces[workspace_id]
                outer.deleted = True
                return {
                    "encrypted_revisions_deleted": revision_count,
                    "devices_deleted": device_count,
                }

        return Client()


def _service(store, auth, cloud, state_path, name):
    return CloudSyncService(
        store,
        auth,
        state_path=state_path,
        device_name=name,
        client_factory=cloud.client_factory,
    )


def test_service_provisions_pulls_merges_pushes_and_deletes_opaque_memory(tmp_path):
    auth = FakeAuth()
    cloud = HTTPShapeCloud()
    first_store = MemoryStore(tmp_path / "first" / "memory.sqlite3")
    first_state_path = tmp_path / "first" / "sync-state.json"
    first = _service(first_store, auth, cloud, first_state_path, "Main PC")
    first_store.remember(
        "Never use em dashes.",
        kind="preference",
        scope="global",
        source_client="cursor",
    )

    initial = first.sync_now()
    assert initial["workspace_created"] is True
    assert initial["head_revision"] == 1
    first_identity = DeviceIdentity.from_store(first_store, "Main PC")
    first_state = SyncState.load(first_state_path, first_store.cipher, first_identity)
    cloud_document = json.dumps(cloud.log._workspace(first_state.workspace_id))
    assert "Never use em dashes" not in cloud_document

    second_store = MemoryStore(tmp_path / "second" / "memory.sqlite3")
    second_state_path = tmp_path / "second" / "sync-state.json"
    second_identity = DeviceIdentity.from_store(second_store, "Laptop")
    request = create_enrollment_request(second_identity, now=NOW)
    approval = approve_enrollment(first_state, first_identity, request, now=NOW)
    cloud.log.register_approval(approval)
    accept_enrollment(
        second_store,
        second_identity,
        request,
        approval,
        second_state_path,
        now=NOW,
    )
    second = _service(second_store, auth, cloud, second_state_path, "Laptop")
    received = second.sync_now()
    assert received["revisions_pulled"] == 1
    assert second_store.recall("writing preference")[0]["content"] == "Never use em dashes."

    second_store.remember(
        "The project uses FastAPI.",
        kind="project",
        scope="global",
        source_client="codex",
    )
    second.sync_now()
    updated = first.sync_now()
    assert updated["revisions_pulled"] >= 1
    assert any(
        item["content"] == "The project uses FastAPI."
        for item in first_store.recall("FastAPI")
    )

    deleted = first.delete_cloud_memory(confirmed=True)
    assert deleted["state"] == "deleted"
    assert deleted["local_memory_preserved"] is True
    assert deleted["sync_turned_off"] is True
    assert auth.signed_out is True
    assert cloud.deleted is True
    assert first_state_path.exists() is False
    assert first_store.stats()["current"] == 2


def test_short_code_add_device_flow_needs_no_workspace_id_or_key_copy(tmp_path):
    auth = FakeAuth()
    cloud = HTTPShapeCloud()
    first_store = MemoryStore(tmp_path / "first" / "memory.sqlite3")
    first = _service(
        first_store,
        auth,
        cloud,
        tmp_path / "first" / "sync-state.json",
        "Main PC",
    )
    first_store.remember(
        "We use FastAPI and never write migrations manually.",
        kind="preference",
        scope="global",
        source_client="cursor",
    )
    first.sync_now()

    second_store = MemoryStore(tmp_path / "second" / "memory.sqlite3")
    second = _service(
        second_store,
        auth,
        cloud,
        tmp_path / "second" / "sync-state.json",
        "Marketing laptop",
    )
    request = second.start_device_enrollment()
    assert request["state"] == "waiting_for_approval"
    assert request["verification_code"].encode() not in second.pending_path.read_bytes()

    pending = first.pending_device_requests()
    assert pending["count"] == 1
    assert pending["requests"][0]["device"]["display_name"] == "Marketing laptop"
    with pytest.raises(ValueError, match="does not match"):
        first.approve_device_request(
            request["request_id"],
            "0000-0000",
            confirmed=True,
        )
    approved = first.approve_device_request(
        request["request_id"],
        request["verification_code"],
        confirmed=True,
    )
    assert approved["state"] == "approved"
    assert approved["device_count"] == 2

    connected = second.device_enrollment_status()
    assert connected["state"] == "connected"
    assert connected["revisions_pulled"] == 1
    assert second.pending_path.exists() is False
    assert second_store.recall("API migration policy")[0]["content"] == (
        "We use FastAPI and never write migrations manually."
    )
    assert cloud.enrollments == {}
    assert "FastAPI" not in json.dumps(cloud.log._workspaces)


def test_cursor_memory_reaches_codex_and_claude_with_correction_and_forgetting(tmp_path):
    """Prove the launch-critical cross-tool sequence across two local devices."""

    auth = FakeAuth()
    cloud = HTTPShapeCloud()
    cursor_project = tmp_path / "desktop-project"
    (cursor_project / ".git").mkdir(parents=True)
    cursor_store = MemoryStore(tmp_path / "desktop" / "memory.sqlite3")
    cursor_state_path = tmp_path / "desktop" / "sync-state.json"
    cursor_sync = _service(cursor_store, auth, cloud, cursor_state_path, "Main PC")

    remembered = call_tool(
        cursor_store,
        "remember",
        {
            "content": "We use FastAPI and never write migrations manually.",
            "kind": "preference",
            "scope": "global",
            "source_client": "cursor",
            "project_root": str(cursor_project),
        },
        cloud_sync=cursor_sync,
    )
    assert remembered["structuredContent"]["cloud_sync"]["state"] == "synced"
    assert "Remembered everywhere" in remembered["content"][0]["text"]

    cursor_identity = DeviceIdentity.from_store(cursor_store, "Main PC")
    cursor_state = SyncState.load(cursor_state_path, cursor_store.cipher, cursor_identity)
    cloud_document = json.dumps(cloud.log._workspace(cursor_state.workspace_id))
    assert "FastAPI" not in cloud_document
    assert "migrations manually" not in cloud_document

    codex_store = MemoryStore(tmp_path / "laptop" / "memory.sqlite3")
    codex_state_path = tmp_path / "laptop" / "sync-state.json"
    codex_identity = DeviceIdentity.from_store(codex_store, "Laptop")
    request = create_enrollment_request(codex_identity, now=NOW)
    approval = approve_enrollment(cursor_state, cursor_identity, request, now=NOW)
    cloud.log.register_approval(approval)
    accept_enrollment(
        codex_store,
        codex_identity,
        request,
        approval,
        codex_state_path,
        now=NOW,
    )
    codex_sync = _service(codex_store, auth, cloud, codex_state_path, "Laptop")

    recalled = call_tool(
        codex_store,
        "recall",
        {"query": "How do we build this API?", "client": "codex"},
        cloud_sync=codex_sync,
    )["structuredContent"]
    assert recalled["cloud_sync"]["revisions_pulled"] == 1
    assert recalled["receipt"]["client"] == "codex"
    assert recalled["receipt"]["memories"][0]["source_client"] == "cursor"
    assert recalled["memories"][0]["content"] == (
        "We use FastAPI and never write migrations manually."
    )

    corrected = call_tool(
        codex_store,
        "correct_memory",
        {
            "memory_id": recalled["memories"][0]["id"],
            "content": "We use FastAPI and only use reviewed Alembic migrations.",
        },
        cloud_sync=codex_sync,
    )["structuredContent"]
    assert corrected["cloud_sync"]["memory_scope"] == "everywhere"

    claude_recall = call_tool(
        cursor_store,
        "recall",
        {"query": "FastAPI migration policy", "client": "claude"},
        cloud_sync=cursor_sync,
    )["structuredContent"]
    assert claude_recall["receipt"]["client"] == "claude"
    assert [item["content"] for item in claude_recall["memories"]] == [
        "We use FastAPI and only use reviewed Alembic migrations."
    ]

    forgotten = call_tool(
        cursor_store,
        "forget_memory",
        {"memory_id": claude_recall["memories"][0]["id"], "confirmed": True},
        cloud_sync=cursor_sync,
    )["structuredContent"]
    assert forgotten["status"] == "forgotten"
    assert forgotten["cloud_sync"]["memory_scope"] == "everywhere"

    after_forgetting = call_tool(
        codex_store,
        "recall",
        {"query": "FastAPI migration policy", "client": "codex"},
        cloud_sync=codex_sync,
    )["structuredContent"]
    assert after_forgetting["memories"] == []
    assert after_forgetting["receipt"]["memory_count"] == 0


def test_device_management_rotates_future_key_without_claiming_remote_erasure(tmp_path):
    auth = FakeAuth()
    cloud = HTTPShapeCloud()
    first_store = MemoryStore(tmp_path / "first" / "memory.sqlite3")
    first_path = tmp_path / "first" / "sync-state.json"
    first = _service(first_store, auth, cloud, first_path, "Main PC")
    first_store.remember("Shared before removal", scope="global", source_client="cursor")
    first.sync_now()

    second_store = MemoryStore(tmp_path / "second" / "memory.sqlite3")
    second_path = tmp_path / "second" / "sync-state.json"
    second = _service(second_store, auth, cloud, second_path, "Old laptop")
    enrollment = second.start_device_enrollment()
    first.approve_device_request(
        enrollment["request_id"],
        enrollment["verification_code"],
        confirmed=True,
    )
    second.device_enrollment_status()
    second_identity = DeviceIdentity.from_store(second_store, "Old laptop")
    old_state = SyncState.load(second_path, second_store.cipher, second_identity)

    registry = first.connected_devices()
    assert registry["count"] == 2
    old_laptop = next(
        item for item in registry["devices"] if item["display_name"] == "Old laptop"
    )
    assert old_laptop["can_remove"] is True
    assert all("signing_public_key" not in item for item in registry["devices"])

    removed = first.remove_device(old_laptop["device_id"], confirmed=True)
    assert removed["future_memory_protected"] is True
    assert removed["already_received_may_remain"] is True
    assert "already received" in removed["message"]
    first_identity = DeviceIdentity.from_store(first_store, "Main PC")
    rotated_state = SyncState.load(first_path, first_store.cipher, first_identity)
    assert rotated_state.epoch == 2
    assert rotated_state.workspace_key != old_state.workspace_key

    first_store.remember("Future-only launch color is cobalt", scope="global")
    first.sync_now()
    denied = second.pull_if_connected()
    assert denied["state"] == "device_removed"
    assert second_store.recall("future-only launch color") == []
    assert second_store.recall("shared before removal")[0]["content"] == (
        "Shared before removal"
    )
    second_store.remember("Private note after removal", scope="global")
    local_only = second.sync_if_connected()
    assert local_only == {
        "state": "device_removed",
        "attempted": True,
        "memory_scope": "local",
        "pending": False,
        "message": "Saved locally. This device no longer receives future cloud memory.",
    }
    assert second_store.recall("private note after removal")[0]["content"] == (
        "Private note after removal"
    )
    cloud_document = json.dumps(cloud.log._workspaces)
    assert "Future-only launch color" not in cloud_document
    assert "Private note after removal" not in cloud_document

    # Explicit backup recovery can replace this device's now-useless old key
    # state and create a new workspace. It must not claim to delete the old one.
    workspace_count = len(cloud.log._workspaces)
    recovered = second.recover_from_backup(confirmed=True)
    assert recovered["state"] == "recovered"
    assert recovered["replaced_unusable_device_state"] is True
    assert recovered["old_cloud_copy_may_remain"] is True
    assert len(cloud.log._workspaces) == workspace_count + 1
    assert second_store.recall("private note after removal")[0]["content"] == (
        "Private note after removal"
    )


def test_encrypted_backup_recovers_after_every_trusted_device_is_lost(tmp_path):
    cloud = HTTPShapeCloud()
    source_store = MemoryStore(tmp_path / "lost-device" / "memory.sqlite3")
    source = _service(
        source_store,
        FakeAuth(),
        cloud,
        tmp_path / "lost-device" / "sync-state.json",
        "Lost laptop",
    )
    source_store.remember(
        "Recovered preference: use FastAPI and reviewed Alembic migrations.",
        scope="global",
        source_client="cursor",
    )
    source.sync_now()
    old_workspace_ids = set(cloud.log._workspaces)
    backup = tmp_path / "recovery.liansbackup"
    passphrase = "a separate disaster recovery passphrase"
    export_backup(source_store, backup, passphrase)

    # Only the encrypted backup and passphrase reach this clean device. The old
    # device identity and sync-state file are deliberately unavailable.
    recovered_store = MemoryStore(tmp_path / "clean-device" / "memory.sqlite3")
    imported = import_backup(recovered_store, backup, passphrase)
    assert imported["imported"]["memories"] == 1
    recovered = _service(
        recovered_store,
        FakeAuth(),
        cloud,
        tmp_path / "clean-device" / "sync-state.json",
        "Replacement laptop",
    )
    report = recovered.recover_from_backup(confirmed=True)

    assert report["state"] == "recovered"
    assert report["local_memory_recovered"] is True
    assert report["cloud_memory_started"] is True
    assert report["old_cloud_copy_may_remain"] is True
    assert report["memory_scope"] == "everywhere"
    assert len(cloud.log._workspaces) == 2
    assert old_workspace_ids < set(cloud.log._workspaces)
    assert (tmp_path / "clean-device" / "sync-state.json").is_file()

    recalled = call_tool(
        recovered_store,
        "recall",
        {"query": "FastAPI migration policy", "client": "codex"},
        cloud_sync=recovered,
    )["structuredContent"]
    assert recalled["memories"][0]["content"] == (
        "Recovered preference: use FastAPI and reviewed Alembic migrations."
    )
    assert recalled["receipt"]["client"] == "codex"
    assert "Recovered preference" not in json.dumps(cloud.log._workspaces)

    # Repeating recovery on an active workspace must not silently fork it.
    repeated = recovered.recover_from_backup(confirmed=True)
    assert repeated["state"] == "active_workspace"
    assert repeated["cloud_memory_started"] is False
    assert repeated["memory_scope"] == "local"
    assert len(cloud.log._workspaces) == 2


def test_backup_recovery_requires_confirmation_and_preserves_unreadable_state(
    tmp_path, monkeypatch
):
    cloud = HTTPShapeCloud()
    store = MemoryStore(tmp_path / "memory.sqlite3")
    state_path = tmp_path / "sync-state.json"
    state_path.write_text("existing state", encoding="utf-8")
    service = _service(store, FakeAuth(), cloud, state_path, "Replacement laptop")

    with pytest.raises(ValueError, match="confirmed=true"):
        service.recover_from_backup()

    def unreadable_state(_identity):
        raise OSError("private path and operating-system details")

    monkeypatch.setattr(service, "_load", unreadable_state)
    report = service.recover_from_backup(confirmed=True)
    assert report == {
        "state": "needs_attention",
        "local_memory_recovered": True,
        "cloud_memory_started": False,
        "old_cloud_copy_may_remain": True,
        "message": (
            "Memory was recovered locally, but Lians could not safely read this device's "
            "existing cloud-memory state."
        ),
    }
    assert state_path.read_text(encoding="utf-8") == "existing state"


def test_cloud_failure_never_blocks_or_leaks_from_a_local_remember(tmp_path):
    class FailingCloud:
        def client_factory(self, _base_url, *, bearer_token_provider):
            assert bearer_token_provider() == "short-lived-access-token"

            class Client:
                def create_workspace(self, _state):
                    raise SyncCloudError("access-token=C:/private/person/token")

            return Client()

    store = MemoryStore(tmp_path / "memory.sqlite3")
    cloud = FailingCloud()
    sync = _service(store, FakeAuth(), cloud, tmp_path / "sync-state.json", "Laptop")
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)

    result = call_tool(
        store,
        "remember",
        {
            "content": "Never use em dashes.",
            "kind": "preference",
            "scope": "global",
            "source_client": "cursor",
            "project_root": str(project),
        },
        cloud_sync=sync,
    )

    assert result["isError"] is False
    assert result["structuredContent"]["cloud_sync"]["state"] == "pending"
    assert result["structuredContent"]["cloud_sync"]["pending"] is True
    assert store.recall("writing style")[0]["content"] == "Never use em dashes."
    assert "access-token" not in json.dumps(result)
    assert "private/person" not in json.dumps(result)


def test_broken_local_cloud_session_never_blocks_a_local_remember(tmp_path):
    class BrokenStatusAuth(FakeAuth):
        def status(self):
            raise OSError("C:/private/person/cloud-session.json")

    store = MemoryStore(tmp_path / "memory.sqlite3")
    sync = CloudSyncService(
        store,
        BrokenStatusAuth(),
        state_path=tmp_path / "sync-state.json",
    )

    result = call_tool(
        store,
        "remember",
        {
            "content": "Use concise sentences.",
            "kind": "preference",
            "scope": "global",
        },
        cloud_sync=sync,
    )

    cloud = result["structuredContent"]["cloud_sync"]
    assert cloud == {
        "state": "needs_attention",
        "attempted": False,
        "memory_scope": "local",
        "pending": True,
    }
    assert store.recall("writing")[0]["content"] == "Use concise sentences."
    assert "private/person" not in json.dumps(result)
