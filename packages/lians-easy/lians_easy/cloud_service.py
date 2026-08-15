"""Safe orchestration between local memory, OAuth, and opaque cloud storage."""

from __future__ import annotations

import errno
import os
import platform
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .cloud_auth import CloudAuthError, NativeCloudAuth
from .store import MemoryStore
from .sync import (
    DeviceIdentity,
    SyncPreconditionError,
    SyncProtocolError,
    SyncState,
    acknowledge_revision,
    apply_device_grant,
    apply_revision,
    prepare_revision,
)
from .sync_http import OpaqueSyncHTTPClient, SyncCloudError

_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


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
    ) -> None:
        self.store = store
        self.auth = auth
        self.state_path = Path(
            state_path or store.path.with_name("sync-state.json")
        ).expanduser()
        self.lock_path = self.state_path.with_name(f".{self.state_path.name}.lock")
        self.device_name = (device_name or platform.node() or "This device")[:80]
        self._client_factory = client_factory
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

    def status(self) -> dict[str, Any]:
        auth_status = self._auth_status()
        if not self.state_path.exists():
            return {
                **auth_status,
                "sync_state": "not_started",
                "head_revision": 0,
                "device_count": 1,
            }
        try:
            state = self._load(self._identity())
        except (OSError, SyncProtocolError):
            return {
                **auth_status,
                "state": "needs_attention",
                "sync_state": "invalid",
                "message": "Local cloud-sync state needs repair before syncing.",
            }
        assert state is not None
        return {
            **auth_status,
            "sync_state": "ready",
            "head_revision": state.head_revision,
            "device_count": len(state.trusted_devices),
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
            if self.auth.status()["state"] not in {"connected", "refresh_required"}:
                raise ValueError("Sign in to Lians Cloud before syncing")
            identity = self._identity()
            state = self._load(identity)
            if state is None:
                return {
                    "state": "not_started",
                    "revisions_pulled": 0,
                    "head_revision": 0,
                    "message": "Cloud sync will start after the first saved memory.",
                }
            pulled = self._pull(self._client(), state, identity)
            return {
                "state": "current",
                "revisions_pulled": pulled,
                "head_revision": state.head_revision,
                "device_count": len(state.trusted_devices),
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
        try:
            return {**self.pull_now(), "attempted": True}
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
            # our pull and push. Divergent edits still fail atomically in merge.
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
            return {
                "state": "synced",
                "workspace_created": created,
                "revisions_pulled": pulled,
                "revision_pushed": pushed,
                "head_revision": state.head_revision,
                "device_count": len(state.trusted_devices),
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
        try:
            return {
                **self.sync_now(),
                "attempted": True,
                "memory_scope": "everywhere",
                "pending": False,
            }
        except (CloudAuthError, OSError, SyncCloudError, SyncProtocolError, ValueError):
            return {
                "state": "pending",
                "attempted": True,
                "memory_scope": "local",
                "pending": True,
                "message": "Saved locally. Encrypted cloud sync will retry when available.",
            }

    def delete_cloud_memory(self, *, confirmed: bool = False) -> dict[str, Any]:
        """Delete the exact remote workspace, then remove its local key state."""

        if not confirmed:
            raise ValueError("Cloud memory deletion requires confirmed=true")
        with self._exclusive():
            identity = self._identity()
            state = self._load(identity)
            if state is None:
                raise LookupError("No Lians Cloud workspace is connected")
            result = self._client().delete_workspace(state.workspace_id, confirmed=True)
            self.state_path.unlink(missing_ok=True)
            return {
                "state": "deleted",
                "local_memory_preserved": True,
                "encrypted_revisions_deleted": result.get("encrypted_revisions_deleted", 0),
                "devices_deleted": result.get("devices_deleted", 0),
                "message": "Encrypted cloud memory was deleted. Local memory remains here.",
            }
