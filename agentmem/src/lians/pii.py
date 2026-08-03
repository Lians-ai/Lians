"""
Subject identity and crypto-shred key lifecycle.

``subject_id`` is an explicit controller-supplied identity boundary. Lians does
not guess a durable data-subject identity from free text: probabilistic PII
detection can redact capture, but it cannot safely decide which person owns a
record or which key an erasure request must destroy.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .crypto import generate_subject_key, unwrap_subject_key, wrap_subject_key
from .models import SubjectKey
from .subject_privacy import subject_reference


class SubjectKeyDestroyedError(ValueError):
    """Write attempted for a subject whose key was crypto-shredded.

    A destroyed key is never re-created: minting a fresh key for the same
    subject_id would let new content accumulate under an identity the
    controller already erased (GDPR Art. 17). Callers should surface this
    as HTTP 410 Gone.
    """


async def _lock_subject_key(
    db: AsyncSession,
    namespace: str,
    persisted_subject_ref: str,
) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    lock_key = int.from_bytes(
        hashlib.sha256(
            f"lians-subject-key\0{namespace}\0{persisted_subject_ref}".encode()
        ).digest()[:8],
        "big",
        signed=True,
    )
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:subject_key)"),
        {"subject_key": lock_key},
    )


async def lock_subject_key_for_update(
    db: AsyncSession,
    subject_id: str,
    namespace: str,
) -> str:
    """Serialize subject-bearing writes and erasure for the transaction."""
    persisted_subject_ref = subject_reference(namespace, subject_id)
    if persisted_subject_ref is None:
        raise ValueError("subject_id must not be empty")
    await _lock_subject_key(db, namespace, persisted_subject_ref)
    return persisted_subject_ref


async def assert_subject_not_erased(
    db: AsyncSession,
    subject_id: str,
    namespace: str,
) -> str:
    """Hold the subject lock and reject writes after an erasure tombstone."""
    persisted_subject_ref = await lock_subject_key_for_update(
        db, subject_id, namespace
    )
    for candidate in {persisted_subject_ref, subject_id}:
        row = await db.get(SubjectKey, (namespace, candidate))
        if row is not None and row.destroyed_at is not None:
            raise SubjectKeyDestroyedError(
                "The data subject has been crypto-shredded"
            )
    return persisted_subject_ref


async def get_or_create_subject_key(
    db: AsyncSession,
    subject_id: str,
    namespace: str,
    *,
    legacy_subject_id: str | None = None,
) -> bytes:
    """Return the plaintext content key for a subject, creating it if necessary.

    Scoped by (namespace, subject_id): the same subject_id in two tenants is two
    distinct keys, so one tenant can never read or shred another tenant's data.
    """
    persisted_subject_ref = await lock_subject_key_for_update(
        db, subject_id, namespace
    )
    row = await db.get(SubjectKey, (namespace, persisted_subject_ref))
    if row is None and legacy_subject_id and legacy_subject_id != persisted_subject_ref:
        legacy = await db.get(SubjectKey, (namespace, legacy_subject_id))
        if legacy is not None:
            if legacy.destroyed_at is not None:
                raise SubjectKeyDestroyedError(
                    "The data subject's legacy key has been crypto-shredded"
                )
            # Preserve decryptability for pre-reference rows while all new rows
            # use the canonical reference. Erasure destroys both aliases.
            row = SubjectKey(
                subject_id=persisted_subject_ref,
                namespace=namespace,
                enc_key=bytes(legacy.enc_key),
                created_at=legacy.created_at,
            )
            db.add(row)
            await db.flush()
    if row is None:
        raw_key = generate_subject_key()
        wrapped = wrap_subject_key(raw_key)
        row = SubjectKey(
            subject_id=persisted_subject_ref,
            namespace=namespace,
            enc_key=wrapped,
        )
        db.add(row)
        await db.flush()
        return raw_key

    if row.destroyed_at is not None:
        raise SubjectKeyDestroyedError(
            "The data subject's key has been crypto-shredded"
        )

    return unwrap_subject_key(bytes(row.enc_key))


async def destroy_subject_key(
    db: AsyncSession,
    subject_id: str,
    namespace: str,
) -> None:
    """Crypto-shred: overwrite key with zeros, mark destroyed (this tenant only)."""
    persisted_subject_ref = subject_reference(namespace, subject_id)
    if persisted_subject_ref is None:
        return
    await _lock_subject_key(db, namespace, persisted_subject_ref)
    candidates = {persisted_subject_ref, subject_id}
    now = datetime.now(timezone.utc)
    for candidate in candidates:
        row = await db.get(SubjectKey, (namespace, candidate))
        if row is None or row.destroyed_at is not None:
            continue
        row.enc_key = b"\x00" * len(bytes(row.enc_key or b""))
        row.destroyed_at = now
    canonical = await db.get(SubjectKey, (namespace, persisted_subject_ref))
    if canonical is None:
        db.add(
            SubjectKey(
                namespace=namespace,
                subject_id=persisted_subject_ref,
                enc_key=b"",
                destroyed_at=now,
            )
        )
        await db.flush()
