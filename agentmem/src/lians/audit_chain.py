"""
Audit log hash chain — tamper-evidence for SEC 17a-4 / FINRA 4511 compliance.

Each event_log row stores:
  prev_hash — row_hash of the most recently committed EventLog row in this
               namespace at the time of insert (or GENESIS_HASH for the first row)
  row_hash  — SHA-256 of the versioned canonical string:
               prev_hash | id | namespace | agent_id | op | memory_id |
               content_hash | created_at (UTC, no timezone suffix)

Hash format v2 additionally includes canonical JSON ``payload``. Historical v1
rows remain verifiable without rewriting or invalidating the existing chain.

Modification of a hash-covered field or deletion of a historical row is
detectable by re-running verify_chain(), which recomputes every row_hash from
scratch and checks for orphaned prev_hash references. V2 covers payloads; the
legacy v1 format does not.

PostgreSQL writes lock a database-owned per-namespace head row and assign a
monotonic ``chain_position``; wall time is evidence, never ordering authority.
A transaction-scoped advisory lock preserves the migration boundary and unique
``(namespace, prev_hash)`` and ``(namespace, chain_position)`` constraints add
database-enforced fork/order guards. Verification treats any legacy fork as an
integrity violation because a defensible audit trail must have one total order.

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
import json
import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import Text, cast, func, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database_role_posture import database_role_posture_status
from .models import EventLog

GENESIS_HASH = "0" * 64
CURRENT_HASH_VERSION = 2
POSTGRES_HASH_VERSION = 3
logger = logging.getLogger(__name__)


async def audit_append_boundary_status(db: AsyncSession) -> dict:
    """Inspect the production audit boundary from the active database role."""
    backend = db.get_bind().dialect.name
    if backend != "postgresql":
        return {
            "backend": backend,
            "enforced": False,
            "checks": {"postgresql_backend": False},
        }

    role_posture = await database_role_posture_status(db)
    role = role_posture["attributes"]
    relation = (
        await db.execute(
            text(
                """SELECT c.relforcerowsecurity,
                          pg_get_userbyid(c.relowner) AS table_owner,
                          c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
                              AS current_role_owns_table,
                          has_table_privilege(current_user, c.oid, 'INSERT') AS can_insert,
                          has_table_privilege(current_user, c.oid, 'UPDATE') AS can_update,
                          has_table_privilege(current_user, c.oid, 'DELETE') AS can_delete,
                          has_table_privilege(current_user, c.oid, 'TRUNCATE') AS can_truncate
                   FROM pg_class AS c
                   JOIN pg_namespace AS n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'public' AND c.relname = 'event_log'"""
            )
        )
    ).mappings().one_or_none()
    head_relation = (
        await db.execute(
            text(
                """SELECT head.relowner = event.relowner AS owner_matches_event_log,
                          head.relowner = (
                              SELECT oid FROM pg_roles WHERE rolname = current_user
                          ) AS current_role_owns_head,
                          has_table_privilege(current_user, head.oid, 'SELECT') AS can_select,
                          has_table_privilege(current_user, head.oid, 'INSERT') AS can_insert,
                          has_table_privilege(current_user, head.oid, 'UPDATE') AS can_update,
                          has_table_privilege(current_user, head.oid, 'DELETE') AS can_delete,
                          has_table_privilege(current_user, head.oid, 'TRUNCATE') AS can_truncate
                   FROM pg_class AS head
                   JOIN pg_namespace AS namespace ON namespace.oid = head.relnamespace
                   JOIN pg_class AS event ON event.oid = 'public.event_log'::regclass
                   WHERE namespace.nspname = 'public'
                     AND head.relname = 'audit_chain_heads'"""
            )
        )
    ).mappings().one_or_none()
    columns = (
        await db.execute(
            text(
                """SELECT COUNT(*) = 3 AND bool_and(attribute.attnotnull) AS hashes_not_null
                   FROM pg_attribute AS attribute
                   WHERE attribute.attrelid = 'public.event_log'::regclass
                     AND attribute.attname IN ('prev_hash', 'row_hash', 'chain_position')
                     AND NOT attribute.attisdropped"""
            )
        )
    ).mappings().one()
    constraints = (
        await db.execute(
            text(
                """SELECT COUNT(*) = 3 AS hash_constraints_present
                   FROM pg_constraint
                   WHERE conrelid = 'public.event_log'::regclass
                     AND conname IN (
                         'ck_event_log_hash_lengths',
                         'ck_event_log_hash_version',
                         'uq_event_log_namespace_chain_position'
                     )"""
            )
        )
    ).mappings().one()
    triggers = (
        await db.execute(
            text(
                """SELECT COUNT(*) = 4
                              AND bool_and(trigger.tgenabled <> 'D')
                              AND bool_and(
                                  CASE
                                      WHEN trigger.tgname IN (
                                          'trg_event_log_insert_boundary',
                                          'trg_event_log_advance_head'
                                      ) THEN function.prosecdef
                                      ELSE true
                                  END
                              ) AS guards_enabled
                   FROM pg_trigger AS trigger
                   JOIN pg_proc AS function ON function.oid = trigger.tgfoid
                   WHERE trigger.tgrelid = 'public.event_log'::regclass
                     AND NOT trigger.tgisinternal
                     AND trigger.tgname IN (
                         'trg_event_log_insert_boundary',
                         'trg_event_log_advance_head',
                         'trg_event_log_reject_mutation',
                         'trg_event_log_reject_truncate'
                     )"""
            )
        )
    ).mappings().one()
    forced_rls = (
        await db.execute(
            text(
                """SELECT COUNT(*) = 4 AND bool_and(c.relforcerowsecurity)
                              AS protected_tables_force_rls
                   FROM pg_class AS c
                   JOIN pg_namespace AS n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'public'
                     AND c.relname IN (
                         'event_log', 'subject_keys', 'agent_barrier_groups',
                         'namespace_policies'
                     )"""
            )
        )
    ).mappings().one()
    append_function = (
        await db.execute(
            text(
                """SELECT proc.prosecdef,
                          proc.proowner = relation.relowner AS owner_matches_table,
                          proc.proowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
                              AS current_role_owns_function,
                          has_function_privilege(current_user, proc.oid, 'EXECUTE')
                              AS current_role_can_execute,
                          has_function_privilege('lians_runtime', proc.oid, 'EXECUTE')
                              AS capability_can_execute,
                          EXISTS (
                              SELECT 1
                              FROM aclexplode(
                                  COALESCE(proc.proacl, acldefault('f', proc.proowner))
                              ) AS acl
                              WHERE acl.grantee = 0
                                AND acl.privilege_type = 'EXECUTE'
                          ) AS public_can_execute
                   FROM pg_proc AS proc
                   JOIN pg_class AS relation
                     ON relation.oid = 'public.event_log'::regclass
                   WHERE proc.oid = to_regprocedure(
                       'public.lians_append_event_v3(uuid,text,text,text,uuid,text,jsonb)'
                   )"""
            )
        )
    ).mappings().one_or_none()
    hash_function = (
        await db.execute(
            text(
                """SELECT proc.provolatile = 'i' AS immutable,
                          has_function_privilege(current_user, proc.oid, 'EXECUTE')
                              AS current_role_can_execute,
                          has_function_privilege('lians_runtime', proc.oid, 'EXECUTE')
                              AS capability_can_execute,
                          EXISTS (
                              SELECT 1
                              FROM aclexplode(
                                  COALESCE(proc.proacl, acldefault('f', proc.proowner))
                              ) AS acl
                              WHERE acl.grantee = 0
                                AND acl.privilege_type = 'EXECUTE'
                          ) AS public_can_execute
                   FROM pg_proc AS proc
                   WHERE proc.oid = to_regprocedure(
                       'public.lians_event_row_hash_v3(text,bigint,uuid,text,text,text,uuid,text,timestamptz,jsonb)'
                   )"""
            )
        )
    ).mappings().one_or_none()

    relation = relation or {}
    head_relation = head_relation or {}
    append_function = append_function or {}
    hash_function = hash_function or {}
    checks = {
        **role_posture["checks"],
        "postgresql_backend": True,
        "runtime_not_superuser": not bool(role.get("rolsuper", True)),
        "runtime_not_bypassrls": not bool(role.get("rolbypassrls", True)),
        "capability_role_exists": bool(role.get("capability_exists", False)),
        "capability_role_no_login": not bool(role.get("capability_login", True)),
        "capability_role_not_superuser": not bool(role.get("capability_super", True)),
        "capability_role_not_bypassrls": not bool(role.get("capability_bypass", True)),
        "runtime_is_capability_member": bool(role.get("capability_member", False)),
        "runtime_does_not_own_event_log": not bool(
            relation.get("current_role_owns_table", True)
        ),
        # INSERT is intentionally retained for the 0.4.2 -> 0.5 rolling
        # window. It is safe only while the always-on, SECURITY DEFINER
        # trigger pair canonicalizes the row and advances the protected head.
        "runtime_has_rolling_insert_capability": bool(
            relation.get("can_insert", False)
        ),
        "runtime_has_no_event_log_mutation_dml": not any(
            bool(relation.get(privilege, True))
            for privilege in ("can_update", "can_delete", "can_truncate")
        ),
        "head_owner_matches_event_log": bool(
            head_relation.get("owner_matches_event_log", False)
        ),
        "runtime_does_not_own_chain_head": not bool(
            head_relation.get("current_role_owns_head", True)
        ),
        "runtime_has_no_direct_chain_head_access": not any(
            bool(head_relation.get(privilege, True))
            for privilege in (
                "can_select",
                "can_insert",
                "can_update",
                "can_delete",
                "can_truncate",
            )
        ),
        "event_log_force_rls": bool(relation.get("relforcerowsecurity", False)),
        "hash_columns_not_null": bool(columns["hashes_not_null"]),
        "hash_constraints_present": bool(constraints["hash_constraints_present"]),
        "mutation_guards_enabled": bool(triggers["guards_enabled"]),
        "protected_tables_force_rls": bool(forced_rls["protected_tables_force_rls"]),
        "append_function_security_definer": bool(append_function.get("prosecdef", False)),
        "append_owner_matches_table": bool(
            append_function.get("owner_matches_table", False)
        ),
        "runtime_does_not_own_append_function": not bool(
            append_function.get("current_role_owns_function", True)
        ),
        "runtime_can_execute_append": bool(
            append_function.get("current_role_can_execute", False)
        ),
        "capability_can_execute_append": bool(
            append_function.get("capability_can_execute", False)
        ),
        "public_cannot_execute_append": not bool(
            append_function.get("public_can_execute", True)
        ),
        "v3_hash_function_immutable": bool(hash_function.get("immutable", False)),
        "runtime_can_execute_v3_hash": bool(
            hash_function.get("current_role_can_execute", False)
        ),
        "capability_can_execute_v3_hash": bool(
            hash_function.get("capability_can_execute", False)
        ),
        "public_cannot_execute_v3_hash": not bool(
            hash_function.get("public_can_execute", True)
        ),
    }
    return {
        "backend": backend,
        "role": role.get("rolname"),
        "event_log_owner": relation.get("table_owner"),
        "enforced": all(checks.values()),
        "checks": checks,
    }


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


def _canonical_payload(payload: Optional[dict]) -> str:
    """Stable JSON representation used by hash format v2."""
    return json.dumps(
        payload or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def compute_row_hash(row: EventLog, prev_hash: str) -> str:
    """Recompute the hash for *row* using *prev_hash* as the chain predecessor.

    Safe to call on rows loaded from any DB backend — _fmt_dt() normalises the
    created_at representation before hashing.
    """
    version = int(getattr(row, "hash_version", 1) or 1)
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
    if version >= 2:
        # Version prefix provides domain separation from the legacy format.
        canonical = f"v{version}|{canonical}|{_canonical_payload(row.payload)}"
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── Write side ───────────────────────────────────────────────────────────────

async def get_chain_tip(db: AsyncSession, namespace: str) -> str:
    """Return the row_hash of the most recently flushed EventLog row in this namespace."""
    stmt = (
        select(EventLog.row_hash)
        .where(EventLog.namespace == namespace)
        .order_by(EventLog.chain_position.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    tip = result.scalar_one_or_none()
    if tip is not None:
        return tip
    exists = await db.scalar(
        select(EventLog.id).where(EventLog.namespace == namespace).limit(1)
    )
    if exists is not None:
        raise RuntimeError("Audit chain contains a row with a missing row_hash")
    return GENESIS_HASH


async def chain_log(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    op: str,
    memory_id: Optional[UUID] = None,
    content_hash: Optional[str] = None,
    payload: Optional[dict] = None,
    *,
    _merkle: bool = True,
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
    row_id = _uuid.uuid4()
    from .subject_privacy import sanitize_audit_payload

    payload_value = sanitize_audit_payload(namespace, payload or {})
    async def _append_at_authoritative_boundary() -> EventLog:
        if db.get_bind().dialect.name == "postgresql":
            # The production role has no direct INSERT authority. This
            # SECURITY-DEFINER function serializes the namespace, computes v3
            # from database-canonical JSON, inserts one immutable row, and
            # returns its ID. The enclosing transaction still owns commit.
            inserted = await db.scalar(
                text(
                    """SELECT id FROM public.lians_append_event_v3(
                        CAST(:row_id AS uuid), :namespace, :agent_id, :operation,
                        CAST(:memory_id AS uuid), :content_hash,
                        CAST(:payload AS jsonb)
                    )"""
                ),
                {
                    "row_id": str(row_id),
                    "namespace": namespace,
                    "agent_id": agent_id,
                    "operation": op,
                    "memory_id": str(memory_id) if memory_id is not None else None,
                    "content_hash": content_hash,
                    "payload": json.dumps(payload_value, ensure_ascii=False, default=str),
                },
            )
            if inserted != row_id:
                raise RuntimeError(
                    "Database audit append boundary returned an unexpected row"
                )
            persisted = await db.scalar(select(EventLog).where(EventLog.id == row_id))
            if persisted is None:
                raise RuntimeError(
                    "Database audit append boundary did not persist its row"
                )
            return persisted

        # SQLite is a local/test profile. Its single writer lock serializes
        # inserts, so retain Python v2 hashes for portable unit tests.
        now = datetime.now(timezone.utc)
        prev_hash = await get_chain_tip(db, namespace)
        previous_position = await db.scalar(
            select(EventLog.chain_position)
            .where(EventLog.namespace == namespace)
            .order_by(EventLog.chain_position.desc())
            .limit(1)
        )
        local_row = EventLog(
            id=row_id,
            namespace=namespace,
            agent_id=agent_id,
            op=op,
            memory_id=memory_id,
            content_hash=content_hash,
            payload=payload_value,
            created_at=now,
            prev_hash=prev_hash,
            row_hash="",
            hash_version=2,
            chain_position=int(previous_position or 0) + 1,
        )
        local_row.row_hash = compute_row_hash(local_row, prev_hash)
        db.add(local_row)
        await db.flush()
        return local_row

    from .metrics import record_audit_append_boundary

    try:
        row = await _append_at_authoritative_boundary()
    except BaseException:
        record_audit_append_boundary("rejected")
        raise
    record_audit_append_boundary("accepted")

    now = row.created_at
    created_at_utc = _fmt_dt(now)

    # Transactional integration outbox bridge. Matching SIEM/GRC/ticketing/
    # billing deliveries are inserted in the same transaction as this audit
    # row, so a process crash can neither lose the source event nor publish a
    # delivery for a rolled-back mutation. Payloads remain hash/reference-only
    # unless the deployment explicitly opts into encrypted audit payload copy.
    from .db import current_barrier_group
    from .integration_service import enqueue_audit_event

    await enqueue_audit_event(
        db,
        event_id=row_id,
        namespace=namespace,
        agent_id=agent_id,
        operation=op,
        memory_id=memory_id,
        content_hash=content_hash,
        row_hash=row.row_hash,
        payload=payload_value,
        occurred_at=now,
        barrier_group=current_barrier_group.get(),
    )

    # Optionally register this already-serialized event with the experimental
    # secondary Merkle window. The anchor EventLog binding is excluded from its
    # own window to avoid recursion; production rejects process-local windows.
    if _merkle and op != "merkle_anchor":
        try:
            from .config import get_settings
            from .merkle_audit import flush_window, get_window
            settings = get_settings()
            if settings.merkle_batch_enabled:
                window = get_window(namespace, settings.merkle_batch_size)
                window.add(str(row_id), row.row_hash)
                if window.is_full():
                    await flush_window(db, namespace)
        except Exception:
            from .metrics import record_best_effort_failure

            record_best_effort_failure("merkle_batch")
            logger.warning(
                "Optional Merkle batching failed; authoritative audit append retained"
            )

    # Fire-and-forget SIEM streaming — never blocks or fails the write path.
    try:
        from .siem import siem_enabled, stream_event
        if siem_enabled():
            import asyncio
            asyncio.create_task(stream_event({
                "id": str(row_id),
                "namespace": namespace,
                "agent_id": agent_id,
                "op": op,
                "memory_id": str(memory_id) if memory_id is not None else None,
                "content_hash": content_hash,
                "row_hash": row.row_hash,
                "created_at": created_at_utc,
            }))
    except Exception:
        from .metrics import record_best_effort_failure

        record_best_effort_failure("siem_schedule")
        logger.warning(
            "Optional SIEM scheduling failed; authoritative audit append retained"
        )

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


class AuditCapacityExceeded(RuntimeError):
    """A deterministic audit read cannot fit the configured byte budget."""

    def __init__(
        self,
        code: str,
        public_message: str,
        *,
        estimated_bytes: int,
        byte_limit: int,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.estimated_bytes = estimated_bytes
        self.byte_limit = byte_limit


class AuditCursorInvalid(ValueError):
    """An audit snapshot watermark is ahead of the committed namespace head."""

    code = "audit_snapshot_watermark_invalid"


def _event_log_row_bytes():
    """Conservative serialized/materialized size without loading event rows."""

    return (
        literal(1_024)
        + 4 * func.coalesce(func.length(EventLog.namespace), 0)
        + 4 * func.coalesce(func.length(EventLog.agent_id), 0)
        + 4 * func.coalesce(func.length(EventLog.op), 0)
        + 4 * func.coalesce(func.length(EventLog.content_hash), 0)
        + 4 * func.coalesce(func.length(EventLog.prev_hash), 0)
        + 4 * func.coalesce(func.length(EventLog.row_hash), 0)
        + 4 * func.coalesce(func.length(cast(EventLog.payload, Text)), 0)
    )


async def _measure_event_log_page_bytes(
    db: AsyncSession,
    conditions,
    *,
    limit: int,
) -> tuple[int, int]:
    """Return row count and bytes for a deterministic bounded prefix."""

    bounded = (
        select(_event_log_row_bytes().label("estimated_bytes"))
        .where(*conditions)
        .order_by(EventLog.chain_position.asc())
        .limit(limit)
        .subquery()
    )
    row_count, estimated_bytes = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(bounded.c.estimated_bytes), 0),
            ).select_from(bounded)
        )
    ).one()
    return int(row_count or 0), int(estimated_bytes or 0)


async def verify_chain(
    db: AsyncSession,
    namespace: str,
    limit: int = 50_000,
    *,
    max_response_bytes: int | None = None,
    through_chain_position: int | None = None,
) -> dict:
    """
    Walk the event_log chain for *namespace* and return a verification report.

    Detected violations:
      hash_mismatch   — row_hash stored on disk does not match recomputed value
                        (indicates the row was modified after insert)
      orphaned_parent — prev_hash does not match any row's row_hash in the set
                        (indicates a row was deleted from the middle of the chain)
      forked_parent   — more than one row points at the same predecessor
                        (the namespace no longer has one defensible total order)

    Returns::

        {
          "namespace": str,
          "rows_checked": int,
          "status": "ok" | "partial" | "tampered",
          "truncated": bool,
          "chain_tip": str | null,
          "violations": [{"row_id", "kind", "detail"}, ...]
        }

    Missing, malformed, or unknown-version hashes are integrity violations. A
    verifier must never turn unverifiable history into an ``ok`` report.
    """
    byte_limit = (
        get_settings().audit_export_page_bytes_limit
        if max_response_bytes is None
        else max_response_bytes
    )
    conditions = [EventLog.namespace == namespace]
    if through_chain_position is not None:
        conditions.append(EventLog.chain_position <= through_chain_position)
    measured_rows, estimated_bytes = await _measure_event_log_page_bytes(
        db,
        conditions,
        limit=limit + 1,
    )
    if estimated_bytes > byte_limit:
        raise AuditCapacityExceeded(
            "audit_verification_byte_capacity_exceeded",
            "The requested audit verification window exceeds the byte budget",
            estimated_bytes=estimated_bytes,
            byte_limit=byte_limit,
        )
    stmt = (
        select(EventLog)
        .where(*conditions)
        .order_by(EventLog.chain_position.asc())
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    result = await db.execute(stmt)
    fetched_rows = result.scalars().all()
    truncated = measured_rows > limit
    rows = fetched_rows

    database_v3_hashes: dict[str, str] = {}
    if db.get_bind().dialect.name == "postgresql" and any(
        row.hash_version == 3 for row in rows
    ):
        recompute_sql = (
            text(
                """SELECT id::text,
                          public.lians_event_row_hash_v3(
                              prev_hash, chain_position, id, namespace, agent_id,
                              op, memory_id, content_hash, created_at, payload::jsonb
                          ) AS recomputed_hash
                   FROM event_log
                   WHERE namespace = :namespace
                     AND chain_position <= :through_chain_position
                   ORDER BY chain_position ASC
                   LIMIT :row_limit"""
            )
            if through_chain_position is not None
            else text(
                """SELECT id::text,
                          public.lians_event_row_hash_v3(
                              prev_hash, chain_position, id, namespace, agent_id,
                              op, memory_id, content_hash, created_at, payload::jsonb
                          ) AS recomputed_hash
                   FROM event_log
                   WHERE namespace = :namespace
                   ORDER BY chain_position ASC
                   LIMIT :row_limit"""
            )
        )
        recomputed_result = await db.execute(
            recompute_sql,
            {
                "namespace": namespace,
                "row_limit": limit,
                "through_chain_position": through_chain_position,
            },
        )
        database_v3_hashes = {
            str(row_id): recomputed_hash
            for row_id, recomputed_hash in recomputed_result.all()
        }

    # Build a global set of all row_hashes present for independent orphan
    # detection. Monotonic chain_position is the authoritative order.
    all_row_hashes: set[str] = {GENESIS_HASH}
    all_row_hashes.update(r.row_hash for r in rows if r.row_hash is not None)

    violations: list[ChainViolation] = []

    children_by_parent: dict[str, list[str]] = {}
    for row in rows:
        if row.prev_hash is not None:
            children_by_parent.setdefault(row.prev_hash, []).append(str(row.id))
    for parent_hash, child_ids in children_by_parent.items():
        if len(child_ids) > 1:
            violations.append(
                ChainViolation(
                    row_id=child_ids[0],
                    kind="forked_parent",
                    detail=(
                        f"prev_hash {parent_hash[:16]}… has {len(child_ids)} children: "
                        + ", ".join(child_ids[:10])
                    ),
                )
            )

    for index, row in enumerate(rows):
        row_id = str(row.id)

        expected_position = index + 1
        if row.chain_position != expected_position:
            violations.append(
                ChainViolation(
                    row_id=row_id,
                    kind="chain_position_gap",
                    detail=(
                        f"stored chain_position={row.chain_position!r}; "
                        f"expected {expected_position}"
                    ),
                )
            )
        expected_parent = GENESIS_HASH if index == 0 else rows[index - 1].row_hash
        if row.prev_hash != expected_parent:
            violations.append(
                ChainViolation(
                    row_id=row_id,
                    kind="ordered_parent_mismatch",
                    detail="prev_hash does not match the prior monotonic chain position",
                )
            )

        if row.row_hash is None or row.prev_hash is None:
            violations.append(
                ChainViolation(
                    row_id=row_id,
                    kind="missing_hash",
                    detail="prev_hash and row_hash are mandatory for every audit event",
                )
            )
            continue

        # 1. Detect deleted predecessor — prev_hash must point to an existing row
        if (
            len(row.row_hash) != 64
            or len(row.prev_hash) != 64
            or any(character not in "0123456789abcdef" for character in row.row_hash)
            or any(character not in "0123456789abcdef" for character in row.prev_hash)
        ):
            violations.append(
                ChainViolation(
                    row_id=row_id,
                    kind="malformed_hash",
                    detail="audit hashes must be 64 lowercase hexadecimal characters",
                )
            )
            continue

        if row.hash_version not in {1, 2, 3}:
            violations.append(
                ChainViolation(
                    row_id=row_id,
                    kind="unknown_hash_version",
                    detail=f"hash_version {row.hash_version!r} is not verifiable",
                )
            )
            continue

        if row.prev_hash not in all_row_hashes:
            violations.append(ChainViolation(
                row_id=row_id,
                kind="orphaned_parent",
                detail=(
                    f"prev_hash {row.prev_hash[:16]}… not found in namespace; "
                    f"a row may have been deleted from the chain"
                ),
            ))

        # 2. Detect content modification — recompute hash from DB-loaded values
        recomputed = (
            database_v3_hashes.get(row_id)
            if row.hash_version == 3
            else compute_row_hash(row, row.prev_hash)
        )
        if recomputed is None:
            violations.append(
                ChainViolation(
                    row_id=row_id,
                    kind="hash_verifier_unavailable",
                    detail="the database v3 hash verifier did not return this row",
                )
            )
            continue
        if recomputed != row.row_hash:
            violations.append(ChainViolation(
                row_id=row_id,
                kind="hash_mismatch",
                detail=(
                    f"stored={row.row_hash[:16]}…  recomputed={recomputed[:16]}…  "
                    f"op={row.op!r} at {row.created_at}"
                ),
            ))

    status = "tampered" if violations else "partial" if truncated else "ok"
    return {
        "namespace": namespace,
        "rows_checked": len(rows),
        "status": status,
        "truncated": truncated,
        "chain_tip": rows[-1].row_hash if rows else GENESIS_HASH,
        "violations": [v.to_dict() for v in violations],
    }


# ── Bulk export (for regulatory examination) ─────────────────────────────────

def _row_to_dict(row: EventLog) -> dict:
    return {
        "id": str(row.id),
        "namespace": row.namespace,
        "agent_id": row.agent_id,
        "op": row.op,
        "memory_id": str(row.memory_id) if row.memory_id is not None else None,
        "content_hash": row.content_hash,
        "payload": row.payload if row.payload is not None else {},
        "created_at": row.created_at,
        "prev_hash": row.prev_hash,
        "row_hash": row.row_hash,
        "hash_version": row.hash_version,
        "chain_position": row.chain_position,
    }


async def export_audit_log(
    db: AsyncSession,
    namespace: str,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    limit: int = 10_000,
    include_chain_status: bool = False,
    after_chain_position: int | None = None,
    through_chain_position: int | None = None,
    max_page_bytes: int | None = None,
) -> dict:
    """
    Export event_log rows for *namespace* in the given time window.

    Parameters
    ----------
    from_dt:
        Lower bound on created_at (inclusive).  None = earliest row.
    to_dt:
        Upper bound on created_at (inclusive).  None = latest row.
    limit:
        Maximum number of rows returned (hard cap). Continue with the returned
        ``next_chain_position`` as ``after_chain_position``.
    include_chain_status:
        When True, also runs verify_chain() and includes the result.
        Verification is independently bounded by ``limit`` and reports
        ``partial`` when more chain rows exist.
    through_chain_position:
        Immutable namespace-head watermark returned by the first page. Retain
        it across continuation calls so concurrent appends are excluded.

    Returns a dict matching AuditExportResult schema.
    """
    from sqlalchemy import and_

    observed_head = int(
        (
            await db.execute(
                select(func.max(EventLog.chain_position)).where(
                    EventLog.namespace == namespace
                )
            )
        ).scalar_one_or_none()
        or 0
    )
    if through_chain_position is not None and (
        through_chain_position < 0 or through_chain_position > observed_head
    ):
        raise AuditCursorInvalid(
            "Audit snapshot watermark is ahead of the committed namespace head"
        )
    snapshot_max_chain_position = (
        observed_head if through_chain_position is None else through_chain_position
    )
    if (
        after_chain_position is not None
        and after_chain_position > snapshot_max_chain_position
    ):
        raise AuditCursorInvalid(
            "Audit page cursor is ahead of its fixed snapshot watermark"
        )
    base_filters = [
        EventLog.namespace == namespace,
        EventLog.chain_position <= snapshot_max_chain_position,
    ]
    if from_dt is not None:
        base_filters.append(EventLog.created_at >= from_dt)
    if to_dt is not None:
        base_filters.append(EventLog.created_at <= to_dt)
    page_filters = list(base_filters)
    if after_chain_position is not None:
        page_filters.append(EventLog.chain_position > after_chain_position)

    total_rows = int(
        (
            await db.execute(
                select(func.count(EventLog.id)).where(and_(*base_filters))
            )
        ).scalar_one()
        or 0
    )

    byte_limit = (
        get_settings().audit_export_page_bytes_limit
        if max_page_bytes is None
        else max_page_bytes
    )
    measured_rows, estimated_bytes = await _measure_event_log_page_bytes(
        db,
        page_filters,
        limit=limit + 1,
    )
    if estimated_bytes > byte_limit:
        raise AuditCapacityExceeded(
            "audit_export_page_byte_capacity_exceeded",
            "The requested audit export page exceeds the byte budget",
            estimated_bytes=estimated_bytes,
            byte_limit=byte_limit,
        )

    stmt = (
        select(EventLog)
        .where(and_(*page_filters))
        .order_by(EventLog.chain_position.asc())
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    result = await db.execute(stmt)
    fetched_rows = result.scalars().all()
    has_more = measured_rows > limit
    rows = fetched_rows

    chain_status: Optional[str] = None
    chain_violations: Optional[list] = None
    chain_rows_checked: Optional[int] = None
    chain_truncated: Optional[bool] = None
    chain_tip: Optional[str] = None
    if include_chain_status:
        verify_result = await verify_chain(
            db,
            namespace=namespace,
            limit=limit,
            max_response_bytes=max(0, byte_limit - estimated_bytes),
            through_chain_position=snapshot_max_chain_position,
        )
        chain_status = verify_result["status"]
        chain_violations = verify_result["violations"]
        chain_rows_checked = int(verify_result["rows_checked"])
        chain_truncated = bool(verify_result["truncated"])
        chain_tip = verify_result["chain_tip"]

    return {
        "namespace": namespace,
        "from_": from_dt,
        "to": to_dt,
        "total_rows": total_rows,
        "returned_rows": len(rows),
        "has_more": has_more,
        "complete": after_chain_position is None and not has_more,
        "next_chain_position": rows[-1].chain_position if has_more and rows else None,
        "snapshot_max_chain_position": snapshot_max_chain_position,
        "chain_status": chain_status,
        "chain_violations": chain_violations,
        "chain_rows_checked": chain_rows_checked,
        "chain_truncated": chain_truncated,
        "chain_tip": chain_tip,
        "events": [_row_to_dict(r) for r in rows],
    }
