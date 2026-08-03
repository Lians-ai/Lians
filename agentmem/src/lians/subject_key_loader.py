"""Bounded, cache-aware subject-key loading for read-side decryption.

Read paths must never unwrap every active tenant key merely to decrypt a small
candidate window.  This module keeps the key lookup proportional to the rows a
caller actually selected and chunks bind parameters for portable execution.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .crypto import unwrap_subject_key
from .dek_cache import cache_dek, get_cached_dek
from .metrics import record_best_effort_failure
from .models import SubjectKey

_SUBJECT_KEY_BIND_BATCH = 500
logger = logging.getLogger(__name__)


async def load_subject_keys(
    db: AsyncSession,
    namespace: str,
    subject_ids: Iterable[str | None],
) -> dict[str, bytes]:
    """Return active DEKs for the requested persisted subject references only."""
    requested = sorted({str(value) for value in subject_ids if value})
    if not requested:
        return {}

    keys: dict[str, bytes] = {}
    unwrap_failures = 0
    for start in range(0, len(requested), _SUBJECT_KEY_BIND_BATCH):
        chunk = requested[start : start + _SUBJECT_KEY_BIND_BATCH]
        # Always re-establish the durable active-key boundary. A process-local
        # cached DEK must not bypass an erasure committed by another worker.
        rows = (
            await db.execute(
                select(SubjectKey).where(
                    SubjectKey.namespace == namespace,
                    SubjectKey.subject_id.in_(chunk),
                    SubjectKey.destroyed_at.is_(None),
                )
            )
        ).scalars()
        for row in rows:
            cached = get_cached_dek(namespace, row.subject_id)
            if cached is not None:
                keys[row.subject_id] = cached
                continue
            try:
                plaintext = unwrap_subject_key(bytes(row.enc_key))
            except Exception:
                # Decryption remains fail closed for this row.  The caller will
                # surface null content rather than plaintext from another key.
                unwrap_failures += 1
                continue
            cache_dek(namespace, row.subject_id, plaintext)
            keys[row.subject_id] = plaintext
    if unwrap_failures:
        record_best_effort_failure(
            "subject_key_unwrap",
            count=unwrap_failures,
        )
        logger.warning(
            "One or more subject keys could not be unwrapped; affected content withheld"
        )
    return keys
