"""Safe orchestration between local memory, OAuth, and opaque cloud storage."""

from __future__ import annotations

import errno
import hmac
import json
import math
import os
import platform
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .cloud_auth import CloudAuthError, NativeCloudAuth
from .store import MemoryStore
from .sync import (
    DeviceIdentity,
    DeviceRevokedError,
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
    prepare_key_rotation,
    prepare_revision,
)
from .sync_http import OpaqueSyncHTTPClient, SyncCloudError

_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()
_RETRY_STATE_VERSION = 1
_RETRY_STATE_MAX_BYTES = 1024
_RETRY_BASE_SECONDS = 5
_RETRY_CAP_SECONDS = 300
_RETRY_MAX_FAILURES = 32
_RETRYABLE_SYNC_ERRORS = (CloudAuthError, OSError, SyncCloudError)


def _process_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


@contextmanager
def _portable_file_lock(path: Path, *, timeout_seconds: float = 15.0) -> Iterator[None]:
    """Serialize Bridge, MCP, and hook sync processes on one local profile."""

    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        acquired = False
        try:
            while not acquired:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as exc:
                    if os.name != "nt" and exc.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise
                    if time.monotonic() >= deadline:
                        raise SyncCloudError("Another Lians sync is still finishing") from exc
                    time.sleep(0.05)
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class CloudSyncService:
    """Perform bounded pull-merge-push sync without exposing memory plaintext."""

    def __init__(
        self,
        store: MemoryStore,
        auth: NativeCloudAuth,
        *,
        state_path: str | Path | None = None,
        device_name: str | None = None,
        client_factory: Any = OpaqueSyncHTTPClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.auth = auth
        self.state_path = Path(state_path or store.path.with_name("sync-state.json")).expanduser()
        self.lock_path = self.state_path.with_name(f".{self.state_path.name}.lock")
        self.pending_path = self.state_path.with_name("pending-device-enrollment.json")
        self.retry_path = self.state_path.with_name("cloud-retry.json")
        self.device_name = (device_name or platform.node() or "This device")[:80]
        self._client_factory = client_factory
        self._clock = clock
        self._lock = _process_lock(self.lock_path)

    @classmethod
    def for_store(cls, store: MemoryStore) -> CloudSyncService:
        return cls(store, NativeCloudAuth.for_store(store))

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with self._lock, _portable_file_lock(self.lock_path):
            yield

    def _identity(self) -> DeviceIdentity:
        return DeviceIdentity.from_store(self.store, self.device_name)

    def _load(self, identity: DeviceIdentity) -> SyncState | None:
        if not self.state_path.exists():
            return None
        return SyncState.load(self.state_path, self.store.cipher, identity)

    def _save(self, state: SyncState, identity: DeviceIdentity) -> None:
        state.save(self.state_path, self.store.cipher, identity)

    def _load_pending(self, identity: DeviceIdentity) -> PendingEnrollment | None:
        if not self.pending_path.exists():
            return None
        return PendingEnrollment.load(self.pending_path, self.store.cipher, identity)

    def _require_connected(self) -> None:
        if self.auth.status()["state"] not in {"connected", "refresh_required"}:
            raise ValueError("Sign in to Lians Cloud before managing devices")

    def _client(self) -> OpaqueSyncHTTPClient:
        return self._client_factory(
            self.auth.config.cloud_url,
            bearer_token_provider=self.auth.access_token,
        )

    def _auth_status(self) -> dict[str, Any]:
        try:
            status = self.auth.status()
        except (CloudAuthError, OSError, ValueError):
            return {
                "state": "needs_attention",
                "configured": True,
                "message": "Sign in again to repair cloud sync.",
            }
        if not isinstance(status, dict) or not isinstance(status.get("state"), str):
            return {
                "state": "needs_attention",
                "configured": True,
                "message": "Sign in again to repair cloud sync.",
            }
        return status

    def _load_retry(self) -> dict[str, int | float] | None:
        """Read a bounded, non-secret retry marker and fail open if it is invalid."""

        try:
            with self.retry_path.open("rb") as handle:
                encoded = handle.read(_RETRY_STATE_MAX_BYTES + 1)
            if not encoded or len(encoded) > _RETRY_STATE_MAX_BYTES:
                return None
            document = json.loads(encoded)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(document, dict) or set(document) != {
            "version",
            "failures",
            "retry_after",
        }:
            return None
        version = document.get("version")
        failures = document.get("failures")
        retry_after = document.get("retry_after")
        if (
            type(version) is not int
            or version != _RETRY_STATE_VERSION
            or type(failures) is not int
            or not 1 <= failures <= _RETRY_MAX_FAILURES
            or type(retry_after) not in {int, float}
            or not math.isfinite(retry_after)
            or not 0 <= retry_after <= 10**12
        ):
            return None
        return {"failures": failures, "retry_after": float(retry_after)}

    def _retry_status(self) -> dict[str, int | bool]:
        retry = self._load_retry()
        if retry is None:
            return {"active": False, "failures": 0, "retry_after_seconds": 0}
        remaining = max(0, math.ceil(float(retry["retry_after"]) - self._clock()))
        return {
            "active": remaining > 0,
            "failures": int(retry["failures"]),
            "retry_after_seconds": remaining,
        }

    def _record_retry(self) -> None:
        """Persist cross-process exponential backoff without storing cloud details."""

        previous = self._load_retry()
        failures = min(
            int(previous["failures"]) + 1 if previous is not None else 1,
            _RETRY_MAX_FAILURES,
        )
        exponent = min(failures - 1, 30)
        delay = min(_RETRY_BASE_SECONDS * (2**exponent), _RETRY_CAP_SECONDS)
        document = json.dumps(
            {
                "version": _RETRY_STATE_VERSION,
                "failures": failures,
                "retry_after": self._clock() + delay,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        if len(document) > _RETRY_STATE_MAX_BYTES:
            return
        temporary: Path | None = None
        descriptor: int | None = None
        try:
            self.retry_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.retry_path.name}.",
                suffix=".tmp",
                dir=self.retry_path.parent,
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(document)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, self.retry_path)
        except OSError:
            # Retry metadata must never replace the original cloud failure or
            # make a successful local memory operation fail.
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _clear_retry(self) -> None:
        try:
            self.retry_path.unlink(missing_ok=True)
        except OSError:
            # A stale marker can only delay automatic cloud work; manual Sync
            # remains available and local memory is never affected.
            pass

    def _automatic_retry_pause(self) -> dict[str, Any] | None:
        retry = self._retry_status()
        if not retry["active"]:
            return None
        return {
            "state": "pending",
            "attempted": False,
            "pending": True,
            "retry_after_seconds": retry["retry_after_seconds"],
            "message": "Working locally for now. Encrypted cloud sync will retry automatically.",
        }

    def status(self) -> dict[str, Any]:
        auth_status = self._auth_status()
        retry = self._retry_status()
        if not self.state_path.exists():
            return {
                **auth_status,
                "sync_state": "not_started",
                "head_revision": 0,
                "device_count": 1,
                "sync_retry": retry,
            }
        try:
            state = self._load(self._identity())
        except (OSError, SyncProtocolError):
            return {
                **auth_status,
                "state": "needs_attention",
                "sync_state": "invalid",
                "message": "Local cloud-sync state needs repair before syncing.",
                "sync_retry": retry,
            }
        assert state is not None
        return {
            **auth_status,
            "sync_state": "ready",
            "head_revision": state.head_revision,
            "device_count": len(state.active_devices),
            "sync_retry": retry,
        }

    @staticmethod
    def _pending_summary(request: dict[str, Any], *, state: str) -> dict[str, Any]:
        return {
            "state": state,
            "request_id": request["request_id"],
            "verification_code": request["verification_code"],
            "device": {
                "device_id": request["device"]["device_id"],
                "display_name": request["device"]["display_name"],
            },
            "expires_at": request["expires_at"],
        }

    def start_device_enrollment(self) -> dict[str, Any]:
        """Publish a resumable short-code request from a new signed-in device."""

        with self._exclusive():
            self._require_connected()
            identity = self._identity()
            if self._load(identity) is not None:
                raise ValueError("This device is already connected to Lians Cloud")
            pending = self._load_pending(identity)
            if pending is None:
                pending = PendingEnrollment.create(identity)
                client = self._client()
                client.create_enrollment(pending.request)
                try:
                    pending.save(self.pending_path, self.store.cipher, identity)
                except (OSError, SyncProtocolError):
                    try:
                        client.delete_enrollment(pending.request["request_id"], confirmed=True)
                    except (OSError, SyncCloudError, ValueError):
                        pass
                    raise
            return {
                **self._pending_summary(pending.request, state="waiting_for_approval"),
                "message": ("On a connected device, open Lians and approve this matching code."),
            }

    def device_enrollment_status(self) -> dict[str, Any]:
        """Poll and atomically accept an approved request on this new device."""

        with self._exclusive():
            self._require_connected()
            identity = self._identity()
            state = self._load(identity)
            if state is not None:
                self.pending_path.unlink(missing_ok=True)
                return {
                    "state": "connected",
                    "device": state.device,
                    "device_count": len(state.active_devices),
                    "head_revision": state.head_revision,
                    "message": "This device can use encrypted memory everywhere.",
                }
            try:
                pending = self._load_pending(identity)
            except SyncProtocolError as exc:
                if "expired" not in str(exc).lower():
                    raise
                self.pending_path.unlink(missing_ok=True)
                return {
                    "state": "expired",
                    "message": "That code expired. Start Add Device again for a new code.",
                }
            if pending is None:
                return {
                    "state": "not_requested",
                    "message": "Choose Add this device to begin.",
                }
            client = self._client()
            try:
                remote = client.enrollment(pending.request["request_id"])
            except SyncCloudError as exc:
                if exc.status not in {404, 410}:
                    raise
                self.pending_path.unlink(missing_ok=True)
                return {
                    "state": "expired" if exc.status == 410 else "cancelled",
                    "message": "That device request is no longer available. Start again.",
                }
            approval = remote.get("approval")
            if approval is None:
                return {
                    **self._pending_summary(pending.request, state="waiting_for_approval"),
                    "message": "Waiting for approval on a connected device.",
                }
            if not isinstance(approval, dict):
                raise SyncCloudError("Lians Cloud returned an invalid device approval")
            state = accept_enrollment(
                self.store,
                identity,
                pending.request,
                approval,
                self.state_path,
            )
            pulled = self._pull(client, state, identity)
            pushed = False
            for attempt in range(2):
                revision = prepare_revision(self.store, state, identity)
                try:
                    client.push(state.workspace_id, revision)
                except SyncPreconditionError:
                    if attempt:
                        raise
                    pulled += self._pull(client, state, identity)
                    continue
                acknowledge_revision(state, revision)
                self._save(state, identity)
                pushed = True
                break
            try:
                client.delete_enrollment(pending.request["request_id"], confirmed=True)
            except SyncCloudError:
                # The short-lived opaque exchange can safely expire server-side.
                pass
            self.pending_path.unlink(missing_ok=True)
            return {
                "state": "connected",
                "device": state.device,
                "device_count": len(state.active_devices),
                "revisions_pulled": pulled,
                "revision_pushed": pushed,
                "head_revision": state.head_revision,
                "message": "This device can now use your encrypted memory everywhere.",
            }

    def pending_device_requests(self) -> dict[str, Any]:
        """Return bounded public summaries for approval on an existing device."""

        with self._exclusive():
            self._require_connected()
            identity = self._identity()
            state = self._load(identity)
            if state is None:
                raise ValueError("Connect this device before approving another device")
            requests: list[dict[str, Any]] = []
            for item in self._client().enrollments():
                request = item.get("request")
                if not isinstance(request, dict) or not isinstance(request.get("device"), dict):
                    raise SyncCloudError("Lians Cloud returned an invalid device request")
                if request["device"].get("device_id") == identity.device_id:
                    continue
                try:
                    requests.append(self._pending_summary(request, state="waiting_for_approval"))
                except (KeyError, TypeError) as exc:
                    raise SyncCloudError("Lians Cloud returned an invalid device request") from exc
            return {"state": "ready", "requests": requests, "count": len(requests)}

    def approve_device_request(
        self,
        request_id: str,
        verification_code: str,
        *,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Approve only after an explicit, matching out-of-band short code."""

        if not confirmed:
            raise ValueError("Approving a device requires confirmed=true")
        rendered_code = verification_code.strip().upper()
        with self._exclusive():
            self._require_connected()
            identity = self._identity()
            state = self._load(identity)
            if state is None:
                raise ValueError("Connect this device before approving another device")
            client = self._client()
            remote = client.enrollment(request_id)
            request = remote.get("request")
            if not isinstance(request, dict):
                raise SyncCloudError("Lians Cloud returned an invalid device request")
            expected_code = request.get("verification_code")
            if not isinstance(expected_code, str) or not hmac.compare_digest(
                rendered_code, expected_code
            ):
                raise ValueError("The verification code does not match this device request")
            approval = approve_enrollment(state, identity, request)
            client.approve_enrollment(request_id, approval)
            self._save(state, identity)
            recipient = approval["grant"]["recipient_device"]
            return {
                "state": "approved",
                "request_id": request_id,
                "device": {
                    "device_id": recipient["device_id"],
                    "display_name": recipient["display_name"],
                },
                "device_count": len(state.active_devices),
                "message": f"{recipient['display_name']} can now finish connecting.",
            }

    def cancel_device_enrollment(self, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Cancelling Add Device requires confirmed=true")
        with self._exclusive():
            self._require_connected()
            identity = self._identity()
            pending = self._load_pending(identity)
            if pending is None:
                raise LookupError("No Add Device request is waiting")
            try:
                self._client().delete_enrollment(pending.request["request_id"], confirmed=True)
            except SyncCloudError as exc:
                if exc.status not in {404, 410}:
                    raise
            self.pending_path.unlink(missing_ok=True)
            return {
                "state": "cancelled",
                "message": "The device request was cancelled. Nothing was connected.",
            }

    def connected_devices(self) -> dict[str, Any]:
        """Return a key-free device list after applying signed registry changes."""

        with self._exclusive():
            self._require_connected()
            identity = self._identity()
            state = self._load(identity)
            if state is None:
                raise ValueError("Start or join cloud memory before managing devices")
            client = self._client()
            try:
                self._pull(client, state, identity)
            except DeviceRevokedError:
                return {
                    "state": "device_removed",
                    "devices": [],
                    "count": 0,
                    "message": (
                        "This device no longer receives future cloud memory. "
                        "Local memory remains here."
                    ),
                }
            document = client.devices(state.workspace_id)
            if document.get("epoch") != state.epoch:
                raise SyncProtocolError("Cloud device registry uses another key epoch")
            values = document.get("devices")
            if not isinstance(values, list):
                raise SyncCloudError("Lians Cloud returned an invalid device registry")
            devices: list[dict[str, Any]] = []
            active_count = 0
            for value in values:
                if not isinstance(value, dict) or not isinstance(value.get("device"), dict):
                    raise SyncCloudError("Lians Cloud returned an invalid device registry")
                descriptor = value["device"]
                device_id = descriptor.get("device_id")
                display_name = descriptor.get("display_name")
                device_state = value.get("state")
                if (
                    not isinstance(device_id, str)
                    or not isinstance(display_name, str)
                    or device_state not in {"active", "revoked"}
                ):
                    raise SyncProtocolError("Cloud device registry does not match signed trust")
                if device_state == "active":
                    if state.active_devices.get(device_id) != descriptor:
                        raise SyncProtocolError("Cloud device registry does not match signed trust")
                elif device_id not in state.revoked_device_ids:
                    # A device enrolled after an older rotation has no local
                    # trust path for that historical descriptor. Do not turn a
                    # server-only claim into a verified receipt in the App.
                    continue
                elif state.trusted_devices.get(device_id) != descriptor:
                    raise SyncProtocolError("Cloud device registry does not match signed trust")
                if device_state == "active":
                    active_count += 1
                revocation = value.get("revocation")
                public_revocation = None
                if isinstance(revocation, dict):
                    public_revocation = {
                        "rotation_id": revocation.get("rotation_id"),
                        "epoch": revocation.get("epoch"),
                        "initiator_device_id": revocation.get("initiator_device_id"),
                        "created_at": revocation.get("created_at"),
                        "verified": True,
                    }
                devices.append(
                    {
                        "device_id": device_id,
                        "display_name": display_name,
                        "state": device_state,
                        "current": device_id == identity.device_id,
                        "can_remove": device_state == "active" and device_id != identity.device_id,
                        "enrolled_at": value.get("enrolled_at"),
                        "revoked_at": value.get("revoked_at"),
                        "revocation": public_revocation,
                    }
                )
            return {
                "state": "ready",
                "devices": devices,
                "count": active_count,
                "epoch": state.epoch,
                "message": "Only active devices receive the current future-memory key.",
            }

    def remove_device(
        self,
        device_id: str,
        *,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Exclude one device and rotate the shared key before the next revision."""

        if not confirmed:
            raise ValueError("Protecting future memory requires confirmed=true")
        with self._exclusive():
            self._require_connected()
            identity = self._identity()
            state = self._load(identity)
            if state is None:
                raise ValueError("Start or join cloud memory before managing devices")
            client = self._client()
            self._pull(client, state, identity)
            target = state.active_devices.get(device_id)
            if target is None:
                raise LookupError("Choose an active connected device")
            if device_id == identity.device_id:
                raise ValueError("Use Turn off sync on this device instead")
            pair = prepare_key_rotation(state, identity, device_id)
            result = client.remove_device(
                state.workspace_id,
                device_id,
                pair,
                confirmed=True,
            )
            apply_key_rotation(state, identity, pair)
            self._save(state, identity)
            pushed = False
            for attempt in range(2):
                revision = prepare_revision(self.store, state, identity)
                try:
                    client.push(state.workspace_id, revision)
                except SyncPreconditionError:
                    if attempt:
                        raise
                    self._pull(client, state, identity)
                    continue
                acknowledge_revision(state, revision)
                self._save(state, identity)
                pushed = True
                break
            return {
                "state": "removed",
                "device": {
                    "device_id": target["device_id"],
                    "display_name": target["display_name"],
                },
                "device_count": len(state.active_devices),
                "epoch": state.epoch,
                "revision_pushed": pushed,
                "encrypted_revisions_deleted": result.get("encrypted_revisions_deleted", 0),
                "future_memory_protected": True,
                "already_received_may_remain": True,
                "message": (
                    f"{target['display_name']} cannot decrypt future cloud memory. "
                    "Memory already received on that device may remain there."
                ),
            }

    @staticmethod
    def _apply_grants(state: SyncState, grants: list[dict[str, Any]]) -> int:
        pending = list(grants)
        applied = 0
        while pending:
            progress = False
            deferred: list[dict[str, Any]] = []
            for item in pending:
                try:
                    before = len(state.trusted_devices)
                    apply_device_grant(state, item["grant"], item["signature"])
                    applied += len(state.trusted_devices) - before
                    progress = True
                except (KeyError, TypeError) as exc:
                    raise SyncProtocolError("Cloud device registry is invalid") from exc
                except SyncProtocolError as exc:
                    if "trusted device" in str(exc):
                        deferred.append(item)
                    else:
                        raise
            if not deferred:
                break
            if not progress:
                raise SyncProtocolError("Cloud device registry has an untrusted grant chain")
            pending = deferred
        return applied

    def _pull(
        self,
        client: OpaqueSyncHTTPClient,
        state: SyncState,
        identity: DeviceIdentity,
    ) -> int:
        rotation_page = client.key_rotations(state.workspace_id, after=state.epoch)
        rotations = rotation_page.get("rotations")
        if not isinstance(rotations, list) or not all(
            isinstance(rotation, dict) for rotation in rotations
        ):
            raise SyncCloudError("Lians Cloud returned an invalid key-rotation list")
        for rotation in rotations:
            apply_key_rotation(state, identity, rotation)
            self._save(state, identity)
        remote_epoch = rotation_page.get("epoch")
        if type(remote_epoch) is not int or remote_epoch != state.epoch:
            raise SyncProtocolError("Cloud key-rotation history is incomplete")
        self._apply_grants(state, client.grants(state.workspace_id))
        self._save(state, identity)
        pulled = 0
        while True:
            page = client.revisions_after(state.workspace_id, state.head_revision)
            revisions = page.get("revisions")
            if not isinstance(revisions, list) or not all(
                isinstance(revision, dict) for revision in revisions
            ):
                raise SyncCloudError("Lians Cloud returned an invalid revision page")
            for revision in revisions:
                apply_revision(self.store, state, revision)
                self._save(state, identity)
                pulled += 1
            if page.get("has_more") is not True:
                break
            if not revisions:
                raise SyncCloudError("Lians Cloud returned an invalid pagination cursor")
        return pulled

    def pull_now(self) -> dict[str, Any]:
        """Apply remote revisions without publishing an unchanged local snapshot."""

        with self._exclusive():
            try:
                if self.auth.status()["state"] not in {"connected", "refresh_required"}:
                    raise ValueError("Sign in to Lians Cloud before syncing")
                identity = self._identity()
                state = self._load(identity)
                if state is None:
                    self._clear_retry()
                    return {
                        "state": "not_started",
                        "revisions_pulled": 0,
                        "head_revision": 0,
                        "message": "Cloud sync will start after the first saved memory.",
                    }
                pulled = self._pull(self._client(), state, identity)
            except _RETRYABLE_SYNC_ERRORS:
                self._record_retry()
                raise
            self._clear_retry()
            return {
                "state": "current",
                "revisions_pulled": pulled,
                "head_revision": state.head_revision,
                "device_count": len(state.active_devices),
                "message": "The latest encrypted memory is available on this device.",
            }

    def pull_if_connected(self) -> dict[str, Any]:
        """Best-effort recall preflight that never makes local memory unavailable."""

        auth_status = self._auth_status()
        if auth_status.get("state") not in {"connected", "refresh_required"}:
            return {
                "state": auth_status.get("state", "unavailable"),
                "attempted": False,
                "revisions_pulled": 0,
            }
        paused = self._automatic_retry_pause()
        if paused is not None:
            return {**paused, "revisions_pulled": 0}
        try:
            return {**self.pull_now(), "attempted": True}
        except DeviceRevokedError:
            self._clear_retry()
            return {
                "state": "device_removed",
                "attempted": True,
                "revisions_pulled": 0,
                "message": "This device no longer receives future cloud memory. Local memory remains here.",
            }
        except (CloudAuthError, OSError, SyncCloudError, SyncProtocolError, ValueError):
            return {
                "state": "pending",
                "attempted": True,
                "revisions_pulled": 0,
                "message": "Cloud memory is temporarily unavailable; using local memory.",
            }

    def sync_now(self) -> dict[str, Any]:
        """Pull, validate, merge, and publish one encrypted full-state revision."""

        with self._exclusive():
            try:
                if self.auth.status()["state"] not in {"connected", "refresh_required"}:
                    raise ValueError("Sign in to Lians Cloud before syncing")
                identity = self._identity()
                state = self._load(identity)
                client = self._client()
                created = state is None
                if state is None:
                    state = SyncState.create(identity)
                    client.create_workspace(state)
                    self._save(state, identity)

                pulled = self._pull(client, state, identity)
                pushed = False
                # A bounded retry handles one writer that advanced the head between
                # our pull and push. Concurrent correction branches are normalized
                # into Trust Review; malformed same-ID mutations still fail atomically.
                for attempt in range(2):
                    revision = prepare_revision(self.store, state, identity)
                    try:
                        client.push(state.workspace_id, revision)
                    except SyncPreconditionError:
                        if attempt:
                            raise
                        pulled += self._pull(client, state, identity)
                        continue
                    acknowledge_revision(state, revision)
                    self._save(state, identity)
                    pushed = True
                    break
            except _RETRYABLE_SYNC_ERRORS:
                self._record_retry()
                raise
            self._clear_retry()
            return {
                "state": "synced",
                "workspace_created": created,
                "revisions_pulled": pulled,
                "revision_pushed": pushed,
                "head_revision": state.head_revision,
                "device_count": len(state.active_devices),
                "message": "Encrypted memory is up to date across connected devices.",
            }

    def sync_if_connected(self) -> dict[str, Any]:
        """Best-effort write-through while preserving a successful local mutation."""

        auth_status = self._auth_status()
        if auth_status.get("state") not in {"connected", "refresh_required"}:
            needs_retry = auth_status.get("state") == "needs_attention"
            return {
                "state": auth_status.get("state", "unavailable"),
                "attempted": False,
                "memory_scope": "local",
                "pending": needs_retry,
            }
        paused = self._automatic_retry_pause()
        if paused is not None:
            return {**paused, "memory_scope": "local"}
        try:
            return {
                **self.sync_now(),
                "attempted": True,
                "memory_scope": "everywhere",
                "pending": False,
            }
        except DeviceRevokedError:
            self._clear_retry()
            return {
                "state": "device_removed",
                "attempted": True,
                "memory_scope": "local",
                "pending": False,
                "message": ("Saved locally. This device no longer receives future cloud memory."),
            }
        except (CloudAuthError, OSError, SyncCloudError, SyncProtocolError, ValueError):
            return {
                "state": "pending",
                "attempted": True,
                "memory_scope": "local",
                "pending": True,
                "message": "Saved locally. Encrypted cloud sync will retry when available.",
            }

    def recover_from_backup(self, *, confirmed: bool = False) -> dict[str, Any]:
        """Start fresh encrypted cloud memory after a verified local backup import.

        A clean device has no workspace key state, so recovery creates a new
        workspace from the locally restored profile. If this device still has
        an active workspace, recovery stops instead of silently forking it. A
        state that is cryptographically invalid or signed as removed can be
        replaced because the user has explicitly confirmed backup recovery.
        """

        if not confirmed:
            raise ValueError("Recovering cloud memory requires confirmed=true")
        auth_status = self._auth_status()
        if auth_status.get("state") not in {"connected", "refresh_required"}:
            return {
                "state": "sign_in_required",
                "local_memory_recovered": True,
                "cloud_memory_started": False,
                "old_cloud_copy_may_remain": True,
                "message": (
                    "Memory was recovered on this device. Sign in to start new encrypted "
                    "cloud memory."
                ),
            }

        replaced_unusable_state = False
        with self._exclusive():
            identity = self._identity()
            try:
                state = self._load(identity)
            except SyncProtocolError:
                self.state_path.unlink(missing_ok=True)
                self.pending_path.unlink(missing_ok=True)
                state = None
                replaced_unusable_state = True
            except OSError:
                return {
                    "state": "needs_attention",
                    "local_memory_recovered": True,
                    "cloud_memory_started": False,
                    "old_cloud_copy_may_remain": True,
                    "message": (
                        "Memory was recovered locally, but Lians could not safely read this "
                        "device's existing cloud-memory state."
                    ),
                }
            if state is not None:
                try:
                    self._pull(self._client(), state, identity)
                except DeviceRevokedError:
                    self.state_path.unlink(missing_ok=True)
                    self.pending_path.unlink(missing_ok=True)
                    replaced_unusable_state = True
                except (CloudAuthError, OSError, SyncCloudError, SyncProtocolError, ValueError):
                    return {
                        "state": "needs_attention",
                        "local_memory_recovered": True,
                        "cloud_memory_started": False,
                        "old_cloud_copy_may_remain": True,
                        "message": (
                            "Memory was recovered locally, but Lians could not safely determine "
                            "whether this device still has active cloud memory."
                        ),
                    }
                else:
                    self._save(state, identity)
                    return {
                        "state": "active_workspace",
                        "local_memory_recovered": True,
                        "cloud_memory_started": False,
                        "old_cloud_copy_may_remain": False,
                        "memory_scope": "local",
                        "message": (
                            "The backup was imported locally, but this device already has "
                            "active cloud memory. Nothing in cloud was replaced."
                        ),
                    }
            self.pending_path.unlink(missing_ok=True)

        sync = self.sync_if_connected()
        cloud_started = sync.get("state") == "synced"
        return {
            "state": "recovered" if cloud_started else "recovery_pending",
            "local_memory_recovered": True,
            "cloud_memory_started": cloud_started,
            "old_cloud_copy_may_remain": True,
            "replaced_unusable_device_state": replaced_unusable_state,
            "memory_scope": sync.get("memory_scope", "local"),
            "message": (
                "Memory is recovered here and a new encrypted cloud memory is ready. "
                "An inaccessible old encrypted cloud copy may remain until account deletion."
                if cloud_started
                else "Memory is recovered locally. New encrypted cloud memory will retry when available."
            ),
        }

    def delete_cloud_memory(self, *, confirmed: bool = False) -> dict[str, Any]:
        """Delete all account-scoped sync objects, then remove local key state."""

        if not confirmed:
            raise ValueError("Cloud memory deletion requires confirmed=true")
        with self._exclusive():
            if self.auth.status()["state"] not in {"connected", "refresh_required"}:
                raise ValueError("Sign in to Lians Cloud before deleting cloud data")
            result = self._client().delete_account_data(confirmed=True)
            self.state_path.unlink(missing_ok=True)
            self.pending_path.unlink(missing_ok=True)
            self._clear_retry()
            self.auth.sign_out(confirmed=True)
            return {
                "state": "deleted",
                "local_memory_preserved": True,
                "sync_turned_off": True,
                "workspaces_deleted": result.get("workspaces_deleted", 0),
                "encrypted_revisions_deleted": result.get("encrypted_revisions_deleted", 0),
                "devices_deleted": result.get(
                    "device_records_deleted",
                    result.get("devices_deleted", 0),
                ),
                "device_enrollments_deleted": result.get("enrollment_records_deleted", 0),
                "message": (
                    "All encrypted Lians cloud data was deleted and sync was turned off. "
                    "Local memory remains here."
                ),
            }
