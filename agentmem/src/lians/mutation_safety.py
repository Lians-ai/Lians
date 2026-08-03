"""Shared mutation preconditions and explicit non-replayable contracts.

The helpers in this module deliberately do not make a mutation idempotent. A
route outside the replay ledger must reject an ``Idempotency-Key`` rather than
imply that an ambiguous response can be replayed. This is especially important
for one-time secret/capability and destructive operations. Callers reconcile
through the resource's read/audit surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Header, HTTPException, status


class MutationVersionConflict(RuntimeError):
    """The caller's persisted concurrency token is no longer current."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def assert_expected_updated_at(current: datetime, expected: datetime) -> None:
    """Compare a persisted timestamp token after the row is locked for update."""
    if _utc(current) != _utc(expected):
        raise MutationVersionConflict("Resource version conflict")


def reject_non_replayable_idempotency_key(
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            description=(
                "Not accepted for this non-replayable operation; "
                "reconcile its authoritative resource before retrying."
            ),
        ),
    ] = None,
) -> None:
    """Fail closed when retry metadata is supplied to a non-replayable route."""
    if idempotency_key is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Idempotency-Key is not supported for this non-replayable operation; "
                "reconcile authoritative state before issuing a new request"
            ),
        )
