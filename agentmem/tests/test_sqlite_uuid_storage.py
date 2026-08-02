"""Regression coverage for UUID storage in the SQLite unit-test backend."""

import hashlib
import uuid

import pytest
from sqlalchemy import select, text

from src.lians.models import ApiKey


@pytest.mark.asyncio
async def test_numeric_uuid_hex_is_preserved_as_text(db):
    """SQLite must not coerce a UUID that happens to look numeric into a number."""
    key_id = uuid.UUID(int=1)
    hashed_key = hashlib.sha256(b"numeric-looking-uuid").hexdigest()
    db.add(
        ApiKey(
            id=key_id,
            hashed_key=hashed_key,
            namespace="uuid-storage-test",
            scopes=["read"],
        )
    )
    await db.commit()

    raw_id, storage_type = (
        await db.execute(
            text("SELECT id, typeof(id) FROM api_keys WHERE hashed_key = :hashed_key"),
            {"hashed_key": hashed_key},
        )
    ).one()
    assert raw_id == key_id.hex
    assert storage_type == "text"

    db.expunge_all()
    restored = (
        await db.execute(select(ApiKey).where(ApiKey.hashed_key == hashed_key))
    ).scalar_one()
    assert restored.id == key_id
