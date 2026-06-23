"""
Subject identification and PII tagging.

Layer 1: explicit subject_id on write (preferred — caller knows).
Layer 2: hook for future PII detection (stub for now).
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SubjectKey
from .crypto import generate_subject_key, wrap_subject_key, unwrap_subject_key


async def get_or_create_subject_key(
    db: AsyncSession,
    subject_id: str,
    namespace: str,
) -> bytes:
    """Return the plaintext content key for a subject, creating it if necessary."""
    row = await db.get(SubjectKey, subject_id)
    if row is None:
        raw_key = generate_subject_key()
        wrapped = wrap_subject_key(raw_key)
        row = SubjectKey(
            subject_id=subject_id,
            namespace=namespace,
            enc_key=wrapped,
        )
        db.add(row)
        await db.flush()
        return raw_key

    if row.destroyed_at is not None:
        raise ValueError(f"Subject key for {subject_id!r} has been crypto-shredded")

    return unwrap_subject_key(bytes(row.enc_key))


async def destroy_subject_key(
    db: AsyncSession,
    subject_id: str,
) -> None:
    """Crypto-shred: overwrite key with zeros, mark destroyed."""
    row = await db.get(SubjectKey, subject_id)
    if row is None:
        return
    row.enc_key = b"\x00" * len(bytes(row.enc_key))
    row.destroyed_at = datetime.now(timezone.utc)


def detect_pii(content: str) -> Optional[str]:
    """
    Stub PII detector — returns a suggested subject_id prefix if personal data
    is detected, None otherwise.  Replace with a real detector (Presidio, etc.)
    before handling actual PII.
    """
    # Placeholder: future implementation uses Microsoft Presidio or similar
    return None
