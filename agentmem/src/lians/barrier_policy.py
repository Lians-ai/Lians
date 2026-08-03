"""Information-barrier names reserved for fail-closed internal provenance."""

from __future__ import annotations

LEGACY_RESTRICTED_BARRIER_GROUP = "__legacy_restricted__"


def is_reserved_barrier_group(value: str | None) -> bool:
    """Return true when a credential must never assume this internal barrier."""

    return value == LEGACY_RESTRICTED_BARRIER_GROUP


__all__ = ["LEGACY_RESTRICTED_BARRIER_GROUP", "is_reserved_barrier_group"]
