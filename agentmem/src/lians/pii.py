"""
Subject identification and PII tagging.

Layer 1: explicit subject_id on write (preferred - caller knows).
Layer 2: hook for future PII detection (stub for now).
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SubjectKey
from .crypto import generate_subject_key, wrap_subject_key, unwrap_subject_key


class SubjectKeyDestroyedError(ValueError):
    """Write attempted for a subject whose key was crypto-shredded.

    A destroyed key is never re-created: minting a fresh key for the same
    subject_id would let new content accumulate under an identity the
    controller already erased (GDPR Art. 17). Callers should surface this
    as HTTP 410 Gone.
    """


async def get_or_create_subject_key(
    db: AsyncSession,
    subject_id: str,
    namespace: str,
) -> bytes:
    """Return the plaintext content key for a subject, creating it if necessary.

    Scoped by (namespace, subject_id): the same subject_id in two tenants is two
    distinct keys, so one tenant can never read or shred another tenant's data.
    """
    row = await db.get(SubjectKey, (namespace, subject_id))
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
        raise SubjectKeyDestroyedError(
            f"Subject key for {subject_id!r} has been crypto-shredded"
        )

    return unwrap_subject_key(bytes(row.enc_key))


async def destroy_subject_key(
    db: AsyncSession,
    subject_id: str,
    namespace: str,
) -> None:
    """Crypto-shred: overwrite key with zeros, mark destroyed (this tenant only)."""
    row = await db.get(SubjectKey, (namespace, subject_id))
    if row is None:
        return
    row.enc_key = b"\x00" * len(bytes(row.enc_key))
    row.destroyed_at = datetime.now(timezone.utc)


def detect_pii(content: str) -> Optional[str]:
    """
    Stub PII detector - returns a suggested subject_id prefix if personal data
    is detected, None otherwise.  Replace with a real detector (Presidio, etc.)
    before handling actual PII.
    """
    # Placeholder: future implementation uses Microsoft Presidio or similar
    return None
