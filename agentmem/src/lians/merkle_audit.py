"""Experimental secondary Merkle anchors for the serial audit chain.

This module does not replace the transactionally serialized EventLog chain.
It groups already chained row hashes into a secondary process-local Merkle
window for development and evaluation. Production startup rejects this mode
until window registration and external anchor publication are durable.

How it works
------------
Events accumulate in an in-process ``MerkleWindow`` per namespace.  When the
window reaches ``batch_size`` entries (or is explicitly flushed), a Merkle tree
is computed over the leaf hashes.  The root is written to ``merkle_anchors`` and
an ``op="merkle_anchor"`` EventLog entry is appended to the serial chain,
carrying the root + window size in its payload.

The anchor EventLog payload stores the ordered event IDs committed into the
root, allowing a verifier to retrieve the exact leaves without mutating the
immutable source events.

Guarantees preserved
--------------------
- **Tamper-evidence**: any leaf modification changes the root → anchor mismatch.
- **Append-only immutability**: each anchor's EventLog binding participates in
  the existing ``prev_hash`` / ``row_hash`` serial chain.
- **Deletion detection**: a missing leaf changes the root; a missing anchor
  breaks the serial chain's prev_hash references.

Verification (``verify_merkle_batch``) resolves that bounded ordered ID list,
recomputes the root, and compares it with both durable anchor representations.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid as _uuid
from typing import Optional
from uuid import UUID

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

_WINDOW_SIZE = 64  # override via config.merkle_batch_size
_MAX_WINDOW_SIZE = 4096
_EVENT_QUERY_BATCH = 500


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _merkle_root(leaves: list[str]) -> str:
    """Compute Merkle root from a list of hex-digest leaf strings."""
    if not leaves:
        return "0" * 64
    nodes = list(leaves)
    while len(nodes) > 1:
        if len(nodes) % 2 == 1:
            nodes.append(nodes[-1])  # duplicate last node for odd-length layers
        nodes = [_sha256(nodes[i] + nodes[i + 1]) for i in range(0, len(nodes), 2)]
    return nodes[0]


def merkle_proof(leaves: list[str], index: int) -> list[tuple[str, str]]:
    """Return the Merkle inclusion proof for the leaf at *index*.

    Each step is ``(sibling_hash, position)`` where position is ``"left"`` if
    the sibling is to the left of the current node (meaning current node goes
    right), or ``"right"`` otherwise.
    """
    nodes = list(leaves)
    proof: list[tuple[str, str]] = []
    while len(nodes) > 1:
        if len(nodes) % 2 == 1:
            nodes.append(nodes[-1])
        level = [_sha256(nodes[i] + nodes[i + 1]) for i in range(0, len(nodes), 2)]
        sibling_idx = index ^ 1
        sibling_hash = nodes[sibling_idx]
        position = "left" if sibling_idx < index else "right"
        proof.append((sibling_hash, position))
        index //= 2
        nodes = level
    return proof


def verify_proof(leaf: str, proof: list[tuple[str, str]], root: str) -> bool:
    """Verify that *leaf* is included in the tree whose root is *root*."""
    h = leaf
    for sibling, side in proof:
        h = _sha256(sibling + h) if side == "left" else _sha256(h + sibling)
    return h == root


class MerkleWindow:
    """Accumulate audit event hashes for a single batch window."""

    def __init__(self, batch_size: int = _WINDOW_SIZE):
        if not 2 <= batch_size <= _MAX_WINDOW_SIZE:
            raise ValueError(
                f"Merkle batch_size must be between 2 and {_MAX_WINDOW_SIZE}"
            )
        self._batch_size = batch_size
        self._leaves: list[str] = []
        self._event_ids: list[str] = []

    def add(self, event_id: str, row_hash: str) -> int:
        """Append a leaf.  Returns the 0-based leaf index."""
        idx = len(self._leaves)
        self._leaves.append(row_hash)
        self._event_ids.append(event_id)
        return idx

    def is_full(self) -> bool:
        return len(self._leaves) >= self._batch_size

    def size(self) -> int:
        return len(self._leaves)

    def root(self) -> str:
        return _merkle_root(self._leaves)

    def proof_for(self, index: int) -> list[tuple[str, str]]:
        return merkle_proof(self._leaves, index)

    def drain(self) -> tuple[str, list[str], list[str]]:
        """Compute root, return (root, event_ids, leaves), then reset."""
        r = _merkle_root(self._leaves)
        ids = self._event_ids[:]
        leaves = self._leaves[:]
        self._leaves.clear()
        self._event_ids.clear()
        return r, ids, leaves


# Per-namespace windows — one per running process
_windows: dict[str, MerkleWindow] = {}
_window_lock = asyncio.Lock()


def get_window(namespace: str, batch_size: int = _WINDOW_SIZE) -> MerkleWindow:
    if namespace not in _windows:
        _windows[namespace] = MerkleWindow(batch_size)
    return _windows[namespace]


async def flush_window(
    db: AsyncSession,
    namespace: str,
) -> Optional[str]:
    """Flush the current window to a MerkleAnchor row if non-empty.

    Returns the Merkle root hash, or None if the window was empty.
    Writes an ``op="merkle_anchor"`` EventLog entry to continue the chain.
    """
    from .audit_chain import chain_log
    from .models import MerkleAnchor

    async with _window_lock:
        window = _windows.get(namespace)
        if window is None or window.size() == 0:
            return None

        root, event_ids, _leaves = window.drain()
        anchor_id = _uuid.uuid4()
        anchor = MerkleAnchor(
            id=anchor_id,
            namespace=namespace,
            root_hash=root,
            window_size=len(event_ids),
        )
        db.add(anchor)

        await chain_log(
            db,
            namespace=namespace,
            agent_id="__merkle__",
            op="merkle_anchor",
            payload={
                "anchor_id": str(anchor_id),
                "root_hash": root,
                "window_size": len(event_ids),
                "event_ids": event_ids,
            },
        )
        return root


async def verify_merkle_batch(
    db: AsyncSession,
    namespace: str,
    anchor_id: UUID,
) -> dict:
    """Re-derive the Merkle root from stored EventLog rows and compare.

    Returns ``{"status": "ok"|"tampered", "anchor_id": ..., ...}``.
    """
    from .models import MerkleAnchor, EventLog

    anchor = await db.get(MerkleAnchor, anchor_id)
    if anchor is None or anchor.namespace != namespace:
        return {"status": "error", "detail": "anchor not found"}

    def failure(detail: str, *, rows_found: int = 0) -> dict:
        return {
            "status": "tampered",
            "anchor_id": str(anchor_id),
            "detail": detail,
            "stored_root": anchor.root_hash,
            "recomputed_root": None,
            "window_size": anchor.window_size,
            "rows_found": rows_found,
        }

    anchor_filters = [
        EventLog.namespace == namespace,
        EventLog.op == "merkle_anchor",
    ]
    if db.get_bind().dialect.name == "postgresql":
        anchor_filters.append(
            cast(EventLog.payload, JSONB).contains({"anchor_id": str(anchor_id)})
        )
    else:
        anchor_filters.append(
            EventLog.payload["anchor_id"].as_string() == str(anchor_id)
        )
    anchor_events = list(
        (
            await db.execute(
                select(EventLog)
                .where(*anchor_filters)
                .order_by(EventLog.chain_position, EventLog.id)
                .limit(2)
            )
        ).scalars().all()
    )
    if len(anchor_events) != 1:
        return failure("anchor must have exactly one immutable EventLog binding")

    payload = anchor_events[0].payload
    event_ids = payload.get("event_ids") if isinstance(payload, dict) else None
    if (
        not isinstance(event_ids, list)
        or not event_ids
        or len(event_ids) != anchor.window_size
        or len(event_ids) > _MAX_WINDOW_SIZE
        or len({str(value) for value in event_ids}) != len(event_ids)
        or payload.get("root_hash") != anchor.root_hash
        or payload.get("window_size") != anchor.window_size
    ):
        return failure("anchor membership manifest is malformed or inconsistent")
    try:
        ordered_ids = [UUID(str(value)) for value in event_ids]
    except (TypeError, ValueError):
        return failure("anchor membership contains an invalid event identifier")

    rows_by_id = {}
    for offset in range(0, len(ordered_ids), _EVENT_QUERY_BATCH):
        rows = list(
            (
                await db.execute(
                    select(EventLog).where(
                        EventLog.namespace == namespace,
                        EventLog.id.in_(
                            ordered_ids[offset : offset + _EVENT_QUERY_BATCH]
                        ),
                    )
                )
            ).scalars().all()
        )
        rows_by_id.update((str(row.id), row) for row in rows)

    leaves: list[str] = []
    for event_id in ordered_ids:
        row = rows_by_id.get(str(event_id))
        if row is None or not row.row_hash:
            return failure(
                "one or more committed Merkle leaves are missing",
                rows_found=len(rows_by_id),
            )
        leaves.append(row.row_hash)

    recomputed = _merkle_root(leaves)
    ok = recomputed == anchor.root_hash

    return {
        "status": "ok" if ok else "tampered",
        "anchor_id": str(anchor_id),
        "stored_root": anchor.root_hash,
        "recomputed_root": recomputed,
        "window_size": anchor.window_size,
        "rows_found": len(rows_by_id),
    }
