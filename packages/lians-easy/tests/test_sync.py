from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from lians_easy.store import MemoryStore
from lians_easy.sync import (
    DeviceIdentity,
    DeviceRevokedError,
    OpaqueRevisionLog,
    PendingEnrollment,
    SyncPreconditionError,
    SyncProtocolError,
    SyncState,
    accept_enrollment,
    acknowledge_revision,
    apply_device_grant,
    apply_key_rotation,
    apply_revision,
    approve_enrollment,
    create_enrollment_request,
    prepare_key_rotation,
    prepare_revision,
)

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)  # noqa: UP017


def _device(tmp_path, name: str):
    store = MemoryStore(tmp_path / name / "memory.sqlite3")
    identity = DeviceIdentity.from_store(store, name)
    return store, identity


def _push(store, identity, state, cloud, *, now=NOW):
    revision = prepare_revision(store, state, identity, now=now)
    cloud.push(revision)
    acknowledge_revision(state, revision)
    return revision


def _pull(store, state, cloud):
    reports = []
    for revision in cloud.revisions_after(state.workspace_id, state.head_revision):
        reports.append(apply_revision(store, state, revision))
    return reports


def _enroll_second_device(tmp_path, first_state, first_identity, cloud):
    second_store, second_identity = _device(tmp_path, "Marketing laptop")
    request = create_enrollment_request(second_identity, now=NOW)
    approval = approve_enrollment(first_state, first_identity, request, now=NOW)
    assert approval["verification_code"] == request["verification_code"]
    cloud.register_approval(approval)
    second_state = accept_enrollment(
        second_store,
        second_identity,
        request,
        approval,
        tmp_path / "Marketing laptop" / "sync-state.json",
        now=NOW,
    )
    return second_store, second_identity, second_state, request, approval


def test_two_devices_share_encrypted_memory_and_propagate_forgetting(tmp_path):
    first_store, first_identity = _device(tmp_path, "Main PC")
    first_state = SyncState.create(first_identity)
    cloud = OpaqueRevisionLog()
    cloud.create_workspace(first_state)

    original = first_store.remember(
        "Never use em dashes in my writing.",
        kind="preference",
        scope="global",
        source_client="cursor",
    )
    first_revision = _push(first_store, first_identity, first_state, cloud)
    encoded = json.dumps(first_revision).encode()
    assert b"em dashes" not in encoded
    assert set(first_revision).isdisjoint({"memories", "activity", "receipts"})

    second_store, second_identity, second_state, _, _ = _enroll_second_device(
        tmp_path, first_state, first_identity, cloud
    )
    _pull(second_store, second_state, cloud)
    assert second_store.recall("writing preference")[0]["content"] == (
        "Never use em dashes in my writing."
    )

    second_store.remember(
        "The launch project uses FastAPI.",
        kind="project",
        scope="project",
        project_id="launch",
        source_client="codex",
    )
    _push(second_store, second_identity, second_state, cloud)
    _pull(first_store, first_state, cloud)
    assert any(
        item["content"] == "The launch project uses FastAPI."
        for item in first_store.recall("FastAPI", project_id="launch")
    )

    first_store.forget(original["id"], confirmed=True)
    _push(first_store, first_identity, first_state, cloud)
    reports = _pull(second_store, second_state, cloud)
    forgotten = next(item for item in second_store.list(state="forgotten") if item["id"] == original["id"])
    assert forgotten["content"] is None
    assert forgotten["content_sha256"] is None
    assert reports[-1]["updated"]["memories"] == 1


def test_state_is_local_key_protected_signed_and_bound_to_device(tmp_path):
    store, identity = _device(tmp_path, "Main PC")
    state = SyncState.create(identity)
    path = tmp_path / "state.json"
    state.save(path, store.cipher, identity)
    encoded = path.read_bytes()
    assert state.workspace_key not in encoded
    assert SyncState.load(path, store.cipher, identity).workspace_key == state.workspace_key

    document = json.loads(encoded)
    document["head"]["revision"] = 4
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SyncProtocolError, match="signature"):
        SyncState.load(path, store.cipher, identity)

    other_store, other_identity = _device(tmp_path, "Other PC")
    with pytest.raises(SyncProtocolError):
        SyncState.load(path, other_store.cipher, other_identity)


def test_pending_enrollment_is_encrypted_resumable_and_device_bound(tmp_path):
    store, identity = _device(tmp_path, "New laptop")
    pending = PendingEnrollment.create(identity)
    path = tmp_path / "New laptop" / "pending-enrollment.json"
    pending.save(path, store.cipher, identity)

    encoded = path.read_bytes()
    assert pending.request["verification_code"].encode() not in encoded
    assert pending.request["device"]["display_name"].encode() not in encoded
    loaded = PendingEnrollment.load(path, store.cipher, identity)
    assert loaded.request == pending.request

    other_store, other_identity = _device(tmp_path, "Other laptop")
    with pytest.raises(SyncProtocolError, match="different local device"):
        PendingEnrollment.load(path, other_store.cipher, other_identity)


def test_revision_tampering_replay_and_stale_push_fail_closed(tmp_path):
    first_store, first_identity = _device(tmp_path, "Main PC")
    first_state = SyncState.create(first_identity)
    cloud = OpaqueRevisionLog()
    cloud.create_workspace(first_state)
    first_store.remember("Campaign audience is university students")
    revision = prepare_revision(first_store, first_state, first_identity, now=NOW)

    tampered = json.loads(json.dumps(revision))
    tampered["ciphertext"] = ("A" if tampered["ciphertext"][0] != "A" else "B") + tampered[
        "ciphertext"
    ][1:]
    with pytest.raises(SyncProtocolError, match="hash"):
        cloud.push(tampered)

    cloud.push(revision)
    acknowledge_revision(first_state, revision)
    with pytest.raises(SyncPreconditionError, match="pull"):
        cloud.push(revision)

    second_store, _, second_state, _, _ = _enroll_second_device(
        tmp_path, first_state, first_identity, cloud
    )
    apply_revision(second_store, second_state, revision)
    with pytest.raises(SyncPreconditionError, match="replayed"):
        apply_revision(second_store, second_state, revision)


def test_divergent_corrections_surface_conflict_without_partial_merge(tmp_path):
    first_store, first_identity = _device(tmp_path, "Main PC")
    first_state = SyncState.create(first_identity)
    cloud = OpaqueRevisionLog()
    cloud.create_workspace(first_state)
    original = first_store.remember("The project uses Flask", scope="global")
    _push(first_store, first_identity, first_state, cloud)
    second_store, second_identity, second_state, _, _ = _enroll_second_device(
        tmp_path, first_state, first_identity, cloud
    )
    _pull(second_store, second_state, cloud)

    first_store.correct(original["id"], "The project uses FastAPI")
    second_store.correct(original["id"], "The project uses Django")
    _push(first_store, first_identity, first_state, cloud)

    before = second_store.list(state="all", limit=50)
    with pytest.raises(ValueError, match="Sync conflict for memory ID"):
        _pull(second_store, second_state, cloud)
    assert second_state.head_revision == 1
    assert second_store.list(state="all", limit=50) == before

    stale = prepare_revision(second_store, second_state, second_identity, now=NOW)
    with pytest.raises(SyncPreconditionError, match="pull"):
        cloud.push(stale)


def test_forget_tombstone_erases_an_offline_correction_branch(tmp_path):
    first_store, first_identity = _device(tmp_path, "Main PC")
    first_state = SyncState.create(first_identity)
    cloud = OpaqueRevisionLog()
    cloud.create_workspace(first_state)
    original = first_store.remember("The project uses Flask", scope="global")
    _push(first_store, first_identity, first_state, cloud)
    second_store, second_identity, second_state, _, _ = _enroll_second_device(
        tmp_path, first_state, first_identity, cloud
    )
    _pull(second_store, second_state, cloud)

    offline_replacement = second_store.correct(original["id"], "The project uses Django")
    first_store.forget(original["id"], confirmed=True)
    _push(first_store, first_identity, first_state, cloud)
    _pull(second_store, second_state, cloud)

    records = {item["id"]: item for item in second_store.list(state="all", limit=50)}
    assert records[original["id"]]["content"] is None
    assert records[offline_replacement["id"]]["content"] is None
    assert records[offline_replacement["id"]]["state"] == "forgotten"

    # The offline device can now publish the combined tombstones; no plaintext
    # from the forgotten branch is returned to the other device.
    _push(second_store, second_identity, second_state, cloud)
    _pull(first_store, first_state, cloud)
    assert all(item["content"] is None for item in first_store.list(state="all", limit=50))


def test_transitive_signed_device_grant_can_be_applied_before_revision(tmp_path):
    _, first_identity = _device(tmp_path, "Main PC")
    first_state = SyncState.create(first_identity)
    cloud = OpaqueRevisionLog()
    cloud.create_workspace(first_state)
    _, second_identity, second_state, _, _ = _enroll_second_device(
        tmp_path, first_state, first_identity, cloud
    )

    third_store, third_identity = _device(tmp_path, "Work laptop")
    request = create_enrollment_request(third_identity, now=NOW)
    approval = approve_enrollment(second_state, second_identity, request, now=NOW)
    cloud.register_approval(approval)
    third_state = accept_enrollment(
        third_store,
        third_identity,
        request,
        approval,
        tmp_path / "Work laptop" / "sync-state.json",
        now=NOW,
    )

    # A device that was offline learns the signed registry chain without a key
    # or plaintext memory ever passing through the cloud service.
    for item in cloud.grants(first_state.workspace_id):
        apply_device_grant(first_state, item["grant"], item["signature"])
    assert third_identity.device_id in first_state.trusted_devices
    assert third_state.workspace_key == first_state.workspace_key


def test_signed_removal_rotates_key_for_survivors_and_excludes_removed_device(tmp_path):
    first_store, first_identity = _device(tmp_path, "Main PC")
    first_state = SyncState.create(first_identity)
    cloud = OpaqueRevisionLog()
    cloud.create_workspace(first_state)
    first_store.remember("Launch notes from before removal", scope="global")
    _push(first_store, first_identity, first_state, cloud)

    second_store, second_identity, second_state, _, _ = _enroll_second_device(
        tmp_path, first_state, first_identity, cloud
    )
    _pull(second_store, second_state, cloud)
    third_store, third_identity = _device(tmp_path, "Work laptop")
    request = create_enrollment_request(third_identity, now=NOW)
    approval = approve_enrollment(first_state, first_identity, request, now=NOW)
    cloud.register_approval(approval)
    third_state = accept_enrollment(
        third_store,
        third_identity,
        request,
        approval,
        tmp_path / "Work laptop" / "sync-state.json",
        now=NOW,
    )
    for item in cloud.grants(first_state.workspace_id):
        apply_device_grant(second_state, item["grant"], item["signature"])

    previous_key = first_state.workspace_key
    pair = prepare_key_rotation(
        first_state,
        first_identity,
        second_identity.device_id,
        now=NOW,
    )
    result = cloud.remove_device(first_state.workspace_id, second_identity.device_id, pair)
    assert result["future_memory_protected"] is True
    assert result["encrypted_revisions_deleted"] == 1
    assert previous_key not in json.dumps(pair).encode()

    first_report = apply_key_rotation(first_state, first_identity, pair)
    third_report = apply_key_rotation(third_state, third_identity, pair)
    assert first_report["epoch"] == third_report["epoch"] == 2
    assert first_state.workspace_key == third_state.workspace_key != previous_key
    assert second_identity.device_id in first_state.revoked_device_ids
    with pytest.raises(DeviceRevokedError, match="removed"):
        apply_key_rotation(second_state, second_identity, pair)

    first_store.remember("Future launch phrase is cobalt", scope="global")
    future = _push(first_store, first_identity, first_state, cloud)
    apply_revision(third_store, third_state, future)
    assert third_store.recall("future launch phrase")[0]["content"] == (
        "Future launch phrase is cobalt"
    )
    with pytest.raises(SyncProtocolError, match="another workspace"):
        apply_revision(second_store, second_state, future)

    fourth_store, fourth_identity = _device(tmp_path, "New tablet")
    fourth_request = create_enrollment_request(fourth_identity, now=NOW)
    fourth_approval = approve_enrollment(
        first_state, first_identity, fourth_request, now=NOW
    )
    assert fourth_approval["grant"]["version"] == 2
    registry_ids = {
        device["device_id"]
        for device in fourth_approval["grant"]["trusted_devices"]
    }
    assert second_identity.device_id not in registry_ids
    assert registry_ids == {
        first_identity.device_id,
        third_identity.device_id,
        fourth_identity.device_id,
    }
    cloud.register_approval(fourth_approval)
    fourth_state = accept_enrollment(
        fourth_store,
        fourth_identity,
        fourth_request,
        fourth_approval,
        tmp_path / "New tablet" / "sync-state.json",
        now=NOW,
    )
    assert fourth_state.epoch == 2
    assert fourth_state.workspace_key == first_state.workspace_key
    assert set(fourth_state.active_devices) == registry_ids

    tampered = json.loads(json.dumps(pair))
    ciphertext = tampered["rotation"]["key_wraps"][0]["ciphertext"]
    tampered["rotation"]["key_wraps"][0]["ciphertext"] = (
        ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]
    )
    replay_state = SyncState(
        workspace_id=third_state.workspace_id,
        epoch=1,
        workspace_key=previous_key,
        device=third_state.device,
        trusted_devices=third_state.trusted_devices,
    )
    with pytest.raises(SyncProtocolError, match="signature"):
        apply_key_rotation(replay_state, third_identity, tampered)
