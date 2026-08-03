"""Transactional idempotency for authoritative API mutations.

The ledger intentionally stores no raw client key and no request/response body.
It records only domain-separated hashes plus stable resource identifiers.  A
PostgreSQL transaction advisory lock serializes a key before any mutation; the
completed ledger row is then inserted in the same transaction as the resource.
Consequently there is no visible ``pending`` state to recover and no crash gap
between the authoritative write and its replay contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LegacyMemoryIdempotency, OperationIdempotency

IDEMPOTENCY_KEY_MAX_BYTES = 255
IDEMPOTENCY_OPERATION_MAX_BYTES = 100
IDEMPOTENCY_RESOURCE_LIMIT = 100
LEGACY_UNVERIFIED_REQUEST_DIGEST = "0" * 64


class InvalidIdempotencyKey(ValueError):
    """The supplied key cannot be represented by the public contract."""


class InvalidIdempotencyRequest(ValueError):
    """A keyed request cannot be represented by deterministic canonical JSON."""


class IdempotencyConflict(ValueError):
    """The same scoped key was already committed for a different request."""


class IdempotencyReplayUnavailable(RuntimeError):
    """A completed claim references a resource that is no longer available."""


def validate_idempotency_key(value: str | None) -> str | None:
    """Accept bounded visible ASCII without silently normalizing client keys."""
    if value is None:
        return None
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        from .metrics import record_idempotency_outcome

        record_idempotency_outcome("invalid_key")
        raise InvalidIdempotencyKey(
            "Idempotency-Key must contain visible ASCII characters only"
        ) from exc
    if not encoded or len(encoded) > IDEMPOTENCY_KEY_MAX_BYTES:
        from .metrics import record_idempotency_outcome

        record_idempotency_outcome("invalid_key")
        raise InvalidIdempotencyKey(
            f"Idempotency-Key must be 1-{IDEMPOTENCY_KEY_MAX_BYTES} bytes"
        )
    if any(byte < 0x21 or byte > 0x7E for byte in encoded):
        from .metrics import record_idempotency_outcome

        record_idempotency_outcome("invalid_key")
        raise InvalidIdempotencyKey(
            "Idempotency-Key must contain visible ASCII characters without whitespace"
        )
    return value


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python", by_alias=True, exclude_none=False))
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, datetime):
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Unsupported idempotency request value: {type(value).__name__}")


def canonical_request_digest(value: Any) -> str:
    payload = {
        "schema": "lians.operation-request.v1",
        "request": _normalize(value),
    }
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidIdempotencyRequest(
            "A keyed request must have a finite canonical JSON representation"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def scoped_key_hash(namespace: str, operation: str, key: str) -> str:
    """Hash a key with both tenant and operation domains; raw keys never persist."""
    material = (
        b"lians/operation-idempotency-key/v1\0"
        + namespace.encode("utf-8")
        + b"\0"
        + operation.encode("utf-8")
        + b"\0"
        + key.encode("ascii")
    )
    return hashlib.sha256(material).hexdigest()


@dataclass
class _LocalLockState:
    lock: asyncio.Lock
    users: int = 0


_local_lock_registry: dict[str, _LocalLockState] = {}
_local_lock_registry_guard = asyncio.Lock()


@asynccontextmanager
async def _local_serialization(key: str) -> AsyncIterator[None]:
    """Single-process fallback for SQLite/local profiles.

    Production deployments require PostgreSQL and use the cross-replica
    advisory lock below.  Reference counts keep attacker-controlled keys from
    growing this fallback registry without bound.
    """
    async with _local_lock_registry_guard:
        state = _local_lock_registry.get(key)
        if state is None:
            state = _LocalLockState(lock=asyncio.Lock())
            _local_lock_registry[key] = state
        state.users += 1
    acquired = False
    try:
        await state.lock.acquire()
        acquired = True
        yield
    finally:
        if acquired:
            state.lock.release()
        async with _local_lock_registry_guard:
            state.users -= 1
            if state.users == 0:
                _local_lock_registry.pop(key, None)


async def _acquire_postgres_lock(db: AsyncSession, lock_hash: str) -> None:
    lock_id = int.from_bytes(bytes.fromhex(lock_hash[:16]), "big", signed=True)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": lock_id},
    )


@dataclass
class OperationClaim:
    db: AsyncSession
    namespace: str
    operation: str
    key_hash: str | None
    raw_key: str | None = field(repr=False)
    request_digest: str
    replay: OperationIdempotency | None = None
    _completed: bool = False

    @property
    def enabled(self) -> bool:
        return self.key_hash is not None

    @property
    def is_replay(self) -> bool:
        return self.replay is not None

    @property
    def resource_ids(self) -> list[UUID]:
        if self.replay is None:
            return []
        try:
            return [UUID(str(value)) for value in (self.replay.resource_ids or [])]
        except (TypeError, ValueError) as exc:
            raise IdempotencyReplayUnavailable(
                "The committed idempotency result contains invalid resource identifiers"
            ) from exc

    async def _complete(
        self,
        *,
        resource_kind: str,
        resource_ids: Iterable[UUID | str],
        response_status: int,
    ) -> None:
        if self.is_replay:
            raise RuntimeError("A replayed idempotency claim cannot be completed again")
        if not self.enabled:
            self._completed = True
            return
        ids = [str(UUID(str(value))) for value in resource_ids]
        if not ids or len(ids) > IDEMPOTENCY_RESOURCE_LIMIT:
            raise ValueError(
                f"Idempotency results require 1-{IDEMPOTENCY_RESOURCE_LIMIT} resource IDs"
            )
        if len(set(ids)) != len(ids):
            raise ValueError("Idempotency result resource IDs must be unique")
        if not resource_kind or len(resource_kind.encode("utf-8")) > 64:
            raise ValueError("Idempotency resource_kind must be 1-64 bytes")
        if not 100 <= response_status <= 599:
            raise ValueError("Idempotency response status must be a valid HTTP status")
        completed_at = datetime.now(UTC)
        self.db.add(
            OperationIdempotency(
                namespace=self.namespace,
                operation=self.operation,
                key_hash=self.key_hash,
                request_digest=self.request_digest,
                legacy_unverified_request=False,
                resource_kind=resource_kind,
                resource_ids=ids,
                response_status=response_status,
                created_at=completed_at,
            )
        )
        # Flush the hashed ledger first. The PostgreSQL compatibility trigger
        # takes the same advisory lock and accepts this subsequent raw-key row
        # only when both representations name the exact same memory.
        await self.db.flush()
        if (
            self.operation == "memory.create"
            and self.raw_key is not None
            and resource_kind == "memory"
            and len(ids) == 1
        ):
            self.db.add(
                LegacyMemoryIdempotency(
                    key=self.raw_key,
                    namespace=self.namespace,
                    memory_id=UUID(ids[0]),
                    created_at=completed_at,
                )
            )
            await self.db.flush()
        self._completed = True

    async def complete_and_commit(
        self,
        *,
        resource_kind: str,
        resource_ids: Iterable[UUID | str],
        response_status: int,
    ) -> None:
        """Persist the completion with its mutation and publish a post-commit metric."""
        await self._complete(
            resource_kind=resource_kind,
            resource_ids=resource_ids,
            response_status=response_status,
        )
        await self.db.commit()
        if self.enabled:
            from .metrics import record_idempotency_outcome

            record_idempotency_outcome("claim_completed")

    def replay_served(self) -> None:
        if not self.is_replay:
            raise RuntimeError("Only an existing claim can be recorded as a replay")
        from .metrics import record_idempotency_outcome

        record_idempotency_outcome("replay")


@asynccontextmanager
async def operation_claim(
    db: AsyncSession,
    *,
    namespace: str,
    operation: str,
    key: str | None,
    request: Any,
) -> AsyncIterator[OperationClaim]:
    """Serialize, check, and record one operation.

    New callers finish with ``claim.complete_and_commit``. On PostgreSQL the
    advisory lock is transaction-scoped, so it remains held through that commit.
    """
    normalized_key = validate_idempotency_key(key)
    if not operation or len(operation.encode("utf-8")) > IDEMPOTENCY_OPERATION_MAX_BYTES:
        raise ValueError(
            f"Idempotency operation must be 1-{IDEMPOTENCY_OPERATION_MAX_BYTES} bytes"
        )
    if normalized_key is None:
        claim = OperationClaim(
            db=db,
            namespace=namespace,
            operation=operation,
            key_hash=None,
            raw_key=None,
            request_digest="",
        )
        yield claim
        return

    digest = canonical_request_digest(request)
    key_hash = scoped_key_hash(namespace, operation, normalized_key)
    dialect = db.get_bind().dialect.name
    local_context = (
        _local_serialization(f"{namespace}:{operation}:{key_hash}")
        if dialect != "postgresql"
        else _null_context()
    )
    async with local_context:
        if dialect == "postgresql":
            await _acquire_postgres_lock(db, key_hash)
        existing = await db.get(
            OperationIdempotency,
            (namespace, operation, key_hash),
        )
        legacy_memory_claim = None
        if operation == "memory.create":
            legacy_memory_claim = await db.get(
                LegacyMemoryIdempotency,
                (normalized_key, namespace),
            )
        if existing is None and legacy_memory_claim is not None:
            # SQLite/local profiles have no SHA-256 trigger, and this explicit
            # check also defends a partially reconciled restored database.
            raise IdempotencyReplayUnavailable(
                "A legacy idempotency claim prevents a duplicate mutation but "
                "cannot safely replay its unverified original request"
            )
        if existing is not None and existing.legacy_unverified_request:
            # The predecessor table did not retain the authenticated request
            # fingerprint. Its row must continue to block a duplicate write,
            # but returning the old resource to a possibly different principal,
            # barrier, body, or subject would manufacture an unverified replay.
            raise IdempotencyReplayUnavailable(
                "A migrated idempotency claim prevents a duplicate mutation but "
                "cannot safely replay its unverified original request"
            )
        if existing is not None and existing.request_digest != digest:
            from .metrics import record_idempotency_outcome

            record_idempotency_outcome("request_conflict")
            raise IdempotencyConflict(
                "Idempotency-Key was already used for a different request in this operation"
            )
        claim = OperationClaim(
            db,
            namespace,
            operation,
            key_hash,
            normalized_key,
            digest,
            replay=existing,
        )
        yield claim
        if existing is None and claim.enabled and not claim._completed:
            raise RuntimeError(
                "A new idempotency claim exited without recording its result"
            )


@asynccontextmanager
async def _null_context() -> AsyncIterator[None]:
    yield
