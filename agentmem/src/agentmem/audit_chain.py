"""
Audit log hash chain — tamper-evidence for SEC 17a-4 / FINRA 4511 compliance.

Each event_log row stores:
  prev_hash — row_hash of the most recently committed EventLog row in this
               namespace at the time of insert (or GENESIS_HASH for the first row)
  row_hash  — SHA-256 of the canonical string:
               prev_hash | id | namespace | agent_id | op | memory_id |
               content_hash | created_at (UTC, no timezone suffix)

Any modification or deletion of a historical row is detectable by re-running
verify_chain(), which recomputes every row_hash from scratch and checks for
orphaned prev_hash references (indicator of deleted rows).

Concurrent inserts by different agents in the same namespace may produce forks
(two rows with the same prev_hash).  Forks are legitimate and do NOT indicate
tampering — they are caused by parallel writes and are reported separately.

Timezone normalisation note
───────────────────────────
chain_log() computes the hash using datetime.now(timezone.utc) — a timezone-aware
datetime whose .isoformat() includes "+00:00".  SQLite stores datetimes without
timezone and returns them as naive datetimes whose .isoformat() has no suffix.
PostgreSQL returns timezone-aware UTC datetimes.  _fmt_dt() converts all three
representations to the same "%Y-%m-%dT%H:%M:%S.%f" string (naive UTC) so that
verify_chain() recomputes identical hashes regardless of the backend.
"""
from __future__ import annotations

import hashlib
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import EventLog

GENESIS_HASH = "0" * 64


# ── Datetime normalisation ───────────────────────────────────────────────────

def _fmt_dt(dt) -> str:
    """Stable UTC string from any datetime representation.

    Handles:
      - timezone-aware UTC datetime (Python original, isoformat includes +00:00)
      - naive datetime (SQLite round-trip, assumed UTC)
      - None → "null"
      - str  → passed through (shouldn't happen but defensive)
    """
    if dt is None:
        return "null"
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime) and dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


# ── Hash computation ─────────────────────────────────────────────────────────

def _canonical(
    prev_hash: str,
    row_id: str,
    namespace: str,
    agent_id: str,
    op: str,
    memory_id: Optional[str],
    content_hash: Optional[str],
    created_at_utc: str,
) -> str:
    fields = [
        prev_hash,
        row_id,
        namespace,
        agent_id,
        op,
        memory_id if memory_id is not None else "null",
        content_hash if content_hash is not None else "null",
        created_at_utc,
    ]
    return "|".join(fields)


def compute_row_hash(row: EventLog, prev_hash: str) -> str:
    """Recompute the hash for *row* using *prev_hash* as the chain predecessor.

    Safe to call on rows loaded from any DB backend — _fmt_dt() normalises the
    created_at representation before hashing.
    """
    canonical = _canonical(
        prev_hash=prev_hash,
        row_id=str(row.id),
        namespace=row.namespace,
        agent_id=row.agent_id,
        op=row.op,
        memory_id=str(row.memory_id) if row.memory_id is not None else None,
        content_hash=row.content_hash,
        created_at_utc=_fmt_dt(row.created_at),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── Write side ───────────────────────────────────────────────────────────────

async def get_chain_tip(db: AsyncSession, namespace: str) -> str:
    """Return the row_hash of the most recently flushed EventLog row in this namespace."""
    stmt = (
        select(EventLog.row_hash)
        .where(EventLog.namespace == namespace)
        .order_by(EventLog.created_at.desc(), EventLog.id.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    tip = result.scalar_one_or_none()
    return tip if tip is not None else GENESIS_HASH


async def chain_log(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    op: str,
    memory_id: Optional[UUID] = None,
    content_hash: Optional[str] = None,
    payload: Optional[dict] = None,
) -> EventLog:
    """
    Create an EventLog row with prev_hash and row_hash wired into the chain.

    The hash is computed from:
      - the Python-generated UUID (row.id, stable before flush)
      - the captured `now` datetime formatted via _fmt_dt() (stable, no timezone
        suffix — matches what verify_chain() sees after SQLite/PG round-trip)
      - all other row fields (namespace, agent_id, op, memory_id, content_hash)

    The row is added to the session and flushed so that subsequent chain_log
    calls within the same transaction see it as the new chain tip.  Callers
    must NOT call db.add() on the returned row — it is already in the session.
    The enclosing transaction's db.commit() persists everything atomically.

    The UUID is pre-generated in Python (not left to the DB default) so the
    row_hash can be computed BEFORE the flush — at flush time row.id would still
    be None because SQLAlchemy invokes Python-side column defaults during the
    INSERT, not when the object is instantiated.
    """
    now = datetime.now(timezone.utc)
    created_at_utc = _fmt_dt(now)          # stable string, no timezone suffix
    prev_hash = await get_chain_tip(db, namespace)
    row_id = _uuid.uuid4()                 # pre-generate so hash uses the real UUID

    row = EventLog(
        id=row_id,
        namespace=namespace,
        agent_id=agent_id,
        op=op,
        memory_id=memory_id,
        content_hash=content_hash,
        payload=payload or {},
        created_at=now,
        prev_hash=prev_hash,
        row_hash="",
    )
    db.add(row)

    row.row_hash = hashlib.sha256(
        _canonical(
            prev_hash=prev_hash,
            row_id=str(row_id),
            namespace=namespace,
            agent_id=agent_id,
            op=op,
            memory_id=str(memory_id) if memory_id is not None else None,
            content_hash=content_hash,
            created_at_utc=created_at_utc,
        ).encode()
    ).hexdigest()

    await db.flush()
    return row


# ── Verification (read side) ─────────────────────────────────────────────────

class ChainViolation:
    __slots__ = ("row_id", "kind", "detail")

    def __init__(self, row_id: str, kind: str, detail: str) -> None:
        self.row_id = row_id
        self.kind = kind
        self.detail = detail

    def to_dict(self) -> dict:
        return {"row_id": self.row_id, "kind": self.kind, "detail": self.detail}


async def verify_chain(
    db: AsyncSession,
    namespace: str,
    limit: int = 50_000,
) -> dict:
    """
    Walk the event_log chain for *namespace* and return a verification report.

    Detected violations:
      hash_mismatch   — row_hash stored on disk does not match recomputed value
                        (indicates the row was modified after insert)
      orphaned_parent — prev_hash does not match any row's row_hash in the set
                        (indicates a row was deleted from the middle of the chain)

    Returns::

        {
          "namespace": str,
          "rows_checked": int,
          "status": "ok" | "tampered",
          "violations": [{"row_id", "kind", "detail"}, ...]
        }

    Rows with NULL hashes (written before migration 0006) are skipped rather
    than reported as violations.
    """
    stmt = (
        select(EventLog)
        .where(EventLog.namespace == namespace)
        .order_by(EventLog.created_at.asc(), EventLog.id.asc())
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    known_hashes: set[str] = {GENESIS_HASH}
    violations: list[ChainViolation] = []

    for row in rows:
        row_id = str(row.id)

        if row.row_hash is None or row.prev_hash is None:
            # Pre-chain rows (before migration 0006) — add hash to known set
            # so they don't cause false orphan reports for newer rows.
            if row.row_hash:
                known_hashes.add(row.row_hash)
            continue

        # 1. Detect deleted predecessor
        if row.prev_hash not in known_hashes:
            violations.append(ChainViolation(
                row_id=row_id,
                kind="orphaned_parent",
                detail=(
                    f"prev_hash {row.prev_hash[:16]}… not found among preceding rows; "
                    f"a row may have been deleted from the chain"
                ),
            ))

        # 2. Detect content modification — recompute hash from DB-loaded values
        recomputed = compute_row_hash(row, row.prev_hash)
        if recomputed != row.row_hash:
            violations.append(ChainViolation(
                row_id=row_id,
                kind="hash_mismatch",
                detail=(
                    f"stored={row.row_hash[:16]}…  recomputed={recomputed[:16]}…  "
                    f"op={row.op!r} at {row.created_at}"
                ),
            ))

        known_hashes.add(row.row_hash)

    return {
        "namespace": namespace,
        "rows_checked": len(rows),
        "status": "ok" if not violations else "tampered",
        "violations": [v.to_dict() for v in violations],
    }
