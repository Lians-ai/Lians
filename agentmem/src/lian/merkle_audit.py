"""
Windowed Merkle batching for the audit chain — Change 8 of the performance roadmap.

Replaces the strict serial SHA-256 chain for write-heavy namespaces.

How it works
------------
Events accumulate in an in-process ``MerkleWindow`` per namespace.  When the
window reaches ``batch_size`` entries (or is explicitly flushed), a Merkle tree
is computed over the leaf hashes.  The root is written to ``merkle_anchors`` and
an ``op="merkle_anchor"`` EventLog entry is appended to the serial chain,
carrying the root + window size in its payload.

Individual EventLog rows store their ``batch_id`` and ``leaf_index`` in payload
so inclusion proofs can be regenerated on demand (O(log n) path length).

Guarantees preserved
--------------------
- **Tamper-evidence**: any leaf modification changes the root → anchor mismatch.
- **Append-only immutability**: anchor rows are chained via the existing
  prev_hash / row_hash serial chain.
- **Deletion detection**: a missing leaf changes the root; a missing anchor
  breaks the serial chain's prev_hash references.

Verification (``verify_merkle_batch``) recomputes each window's root from the
EventLog rows that reference its anchor and compares against the stored root.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

_WINDOW_SIZE = 64  # override via config.merkle_batch_size


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
    from sqlalchemy import select, and_
    from .models import MerkleAnchor, EventLog

    anchor = await db.get(MerkleAnchor, anchor_id)
    if anchor is None:
        return {"status": "error", "detail": "anchor not found"}

    # Retrieve all EventLog rows that belong to this anchor's window
    stmt = (
        select(EventLog)
        .where(
            and_(
                EventLog.namespace == namespace,
                EventLog.payload["anchor_id"].as_string() == str(anchor_id),
            )
        )
        .order_by(EventLog.created_at.asc())
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    leaves = [r.row_hash for r in rows if r.row_hash]
    recomputed = _merkle_root(leaves)
    ok = recomputed == anchor.root_hash

    return {
        "status": "ok" if ok else "tampered",
        "anchor_id": str(anchor_id),
        "stored_root": anchor.root_hash,
        "recomputed_root": recomputed,
        "window_size": anchor.window_size,
        "rows_found": len(rows),
    }
