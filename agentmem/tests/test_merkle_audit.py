from uuid import uuid4

import pytest

from lians.merkle_audit import MerkleWindow, _merkle_root, verify_merkle_batch
from lians.models import EventLog, MerkleAnchor


def test_merkle_window_rejects_unbounded_batches() -> None:
    with pytest.raises(ValueError):
        MerkleWindow(batch_size=1)
    with pytest.raises(ValueError):
        MerkleWindow(batch_size=4097)


@pytest.mark.asyncio
async def test_merkle_verifier_resolves_the_exact_committed_membership(db) -> None:
    namespace = "merkle-verifier"
    first_id = uuid4()
    second_id = uuid4()
    anchor_id = uuid4()
    first_hash = "a" * 64
    second_hash = "b" * 64
    root = _merkle_root([first_hash, second_hash])

    db.add_all(
        [
            EventLog(
                id=first_id,
                namespace=namespace,
                agent_id="agent",
                op="add",
                payload={},
                prev_hash="0" * 64,
                row_hash=first_hash,
                hash_version=2,
                chain_position=1,
            ),
            EventLog(
                id=second_id,
                namespace=namespace,
                agent_id="agent",
                op="add",
                payload={},
                prev_hash=first_hash,
                row_hash=second_hash,
                hash_version=2,
                chain_position=2,
            ),
            MerkleAnchor(
                id=anchor_id,
                namespace=namespace,
                root_hash=root,
                window_size=2,
            ),
            EventLog(
                namespace=namespace,
                agent_id="__merkle__",
                op="merkle_anchor",
                payload={
                    "anchor_id": str(anchor_id),
                    "root_hash": root,
                    "window_size": 2,
                    "event_ids": [str(first_id), str(second_id)],
                },
                prev_hash=second_hash,
                row_hash="c" * 64,
                hash_version=2,
                chain_position=3,
            ),
        ]
    )
    await db.commit()

    report = await verify_merkle_batch(db, namespace, anchor_id)

    assert report["status"] == "ok"
    assert report["rows_found"] == 2
    assert report["recomputed_root"] == root
