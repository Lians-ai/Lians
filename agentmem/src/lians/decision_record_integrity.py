"""Authenticated, immutable integrity boundary for consequential decisions.

DecisionRecord v2 hashes cover the durable business record and the identity of
the credential that actually recorded it. DecisionRecord v3 additionally
binds the server-derived principal type, named role, and effective scopes that
authorized the write. The caller-supplied ``agent_id`` is only a claimed
workload label. Portable exports additionally require the matching immutable
``decision_recorded`` audit event so a database row cannot be freshly signed
after being inserted outside the supported write path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from .audit_chain import GENESIS_HASH, compute_row_hash
from .models import DecisionRecord, EventLog

DECISION_RECORD_HASH_VERSION = 3
DECISION_RECORD_SUPPORTED_HASH_VERSIONS = frozenset({2, 3})
DECISION_RECORD_SCHEMAS = {
    2: "lians.decision-record.v2",
    3: "lians.decision-record.v3",
}
DECISION_RECORD_BINDING_SCHEMA = "lians.decision-record-binding.v1"
VERIFIED_INTEGRITY_STATUS = "verified"
LEGACY_UNVERIFIED_STATUS = "legacy_unverified"
LEGACY_UNVERIFIED_PRINCIPAL = "lians:principal:v1:legacy-unverified"
LEGACY_UNVERIFIED_AUTH_METHOD = "legacy_unverified"

_AUTH_METHOD = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PRINCIPAL_TYPE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
_AUTHORIZATION_SCOPE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AUTHORIZATION_ROLES = frozenset({"owner", "analyst", "compliance", "readonly"})


class DecisionRecordIntegrityError(ValueError):
    """A decision cannot satisfy the authenticated record contract."""


def _utc_timestamp(value: datetime) -> str:
    """Canonical UTC timestamp stable across SQLite and PostgreSQL round trips."""
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_credential_ref(auth_method: str, credential_id: str) -> str:
    """Return a stable non-secret reference without exposing a raw credential ID."""
    normalized_method = auth_method.strip().lower()
    if not _AUTH_METHOD.fullmatch(normalized_method):
        raise DecisionRecordIntegrityError("Authenticated recorder method is invalid")
    raw_id = credential_id.strip()
    if not raw_id:
        raise DecisionRecordIntegrityError("Authenticated recorder credential is required")
    digest = hashlib.sha256(
        b"lians/recording-credential/v1\0"
        + normalized_method.encode("utf-8")
        + b"\0"
        + raw_id.encode("utf-8")
    ).hexdigest()
    return f"lians:credential:v1:sha256:{digest}"


def authenticated_recorder_provenance(
    *,
    principal_ref: str | None,
    auth_method: str,
    credential_id: str | None,
) -> tuple[str, str, str]:
    """Validate and canonicalize server-derived recorder provenance."""
    principal = (principal_ref or "").strip()
    if (
        not principal.startswith("lians:principal:v1:")
        or principal == LEGACY_UNVERIFIED_PRINCIPAL
    ):
        raise DecisionRecordIntegrityError(
            "A canonical authenticated recorder principal is required"
        )
    normalized_method = auth_method.strip().lower()
    if normalized_method == LEGACY_UNVERIFIED_AUTH_METHOD:
        raise DecisionRecordIntegrityError("Legacy provenance cannot record new decisions")
    credential_ref = canonical_credential_ref(
        normalized_method,
        credential_id or "",
    )
    return principal, normalized_method, credential_ref


def authenticated_recorder_authorization_snapshot(
    *,
    principal_type: str | None,
    role: str | None,
    effective_scopes: list[str] | tuple[str, ...] | set[str],
) -> tuple[str, str | None, list[str]]:
    """Canonicalize the server-derived authorization state for a v3 write."""

    normalized_principal_type = (principal_type or "").strip()
    if not _PRINCIPAL_TYPE.fullmatch(normalized_principal_type):
        raise DecisionRecordIntegrityError(
            "Authenticated recorder principal type is invalid"
        )
    if role is not None and not isinstance(role, str):
        raise DecisionRecordIntegrityError("Authenticated recorder role is invalid")
    normalized_role = role.strip() if isinstance(role, str) else None
    if normalized_role == "":
        normalized_role = None
    if normalized_role is not None and normalized_role not in _AUTHORIZATION_ROLES:
        raise DecisionRecordIntegrityError("Authenticated recorder role is invalid")

    normalized_scopes: set[str] = set()
    for scope in effective_scopes:
        if not isinstance(scope, str) or not _AUTHORIZATION_SCOPE.fullmatch(scope):
            raise DecisionRecordIntegrityError(
                "Authenticated recorder authorization scope is invalid"
            )
        normalized_scopes.add(scope)
    scopes = sorted(normalized_scopes)
    if not 1 <= len(scopes) <= 50 or "write" not in normalized_scopes:
        raise DecisionRecordIntegrityError(
            "Authenticated recorder authorization snapshot must contain write"
        )
    return normalized_principal_type, normalized_role, scopes


def decision_record_hash_payload(row: DecisionRecord) -> dict[str, Any]:
    """Return every immutable field in its versioned, domain-separated shape."""
    try:
        hash_version = int(row.record_hash_version)
    except (TypeError, ValueError) as exc:
        raise DecisionRecordIntegrityError("Decision record hash version is malformed") from exc
    schema = DECISION_RECORD_SCHEMAS.get(hash_version)
    if schema is None:
        raise DecisionRecordIntegrityError(
            f"Decision record hash version {row.record_hash_version!r} is unsupported"
        )
    recorded_by: dict[str, Any] = {
        "principal_ref": row.recorded_by_principal_ref,
        "auth_method": row.recorded_by_auth_method,
        "credential_ref": row.recorded_by_credential_ref,
    }
    if hash_version == 3:
        principal_type, role, scopes = authenticated_recorder_authorization_snapshot(
            principal_type=getattr(row, "recorded_by_principal_type", None),
            role=getattr(row, "recorded_by_role", None),
            effective_scopes=list(getattr(row, "recorded_by_scopes", None) or []),
        )
        recorded_by.update(
            {
                "principal_type": principal_type,
                "role": role,
                "scopes": scopes,
            }
        )
    return {
        "schema": schema,
        "record_hash_version": hash_version,
        "record_integrity_status": row.record_integrity_status,
        "id": str(row.id),
        "namespace": row.namespace,
        "barrier_group": row.barrier_group,
        "claimed_agent_id": row.agent_id,
        "recorded_by": recorded_by,
        "decision_type": row.decision_type,
        "outcome": row.outcome,
        "reason_codes": list(row.reason_codes or []),
        "regime": row.regime,
        "subject_id": row.subject_id,
        "session_id": row.session_id,
        "model_id": row.model_id,
        "model_version": row.model_version,
        "policy_version": row.policy_version,
        "decided_at": _utc_timestamp(row.decided_at),
        "recorded_at": _utc_timestamp(row.recorded_at),
        "knowledge_as_of": _utc_timestamp(row.knowledge_as_of),
        "knowledge_recorded_as_of": (
            _utc_timestamp(row.knowledge_recorded_as_of)
            if row.knowledge_recorded_as_of is not None
            else None
        ),
        "evidence_memory_ids": [str(value) for value in (row.evidence_memory_ids or [])],
        "input_hash": row.input_hash,
        "output_hash": row.output_hash,
        "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
        "metadata": dict(row.metadata_ or {}),
    }


def compute_decision_record_hash(row: DecisionRecord) -> str:
    """Compute the lowercase SHA-256 digest for one supported decision record."""
    try:
        hash_version = int(row.record_hash_version)
    except (TypeError, ValueError) as exc:
        raise DecisionRecordIntegrityError("Decision record hash version is malformed") from exc
    if hash_version not in DECISION_RECORD_SUPPORTED_HASH_VERSIONS:
        raise DecisionRecordIntegrityError(
            f"Decision record hash version {row.record_hash_version!r} is unsupported"
        )
    return hashlib.sha256(
        _canonical_json(decision_record_hash_payload(row)).encode("utf-8")
    ).hexdigest()


def assert_decision_record_hash(row: DecisionRecord) -> None:
    """Fail closed unless a row has supported provenance and an exact hash."""
    try:
        hash_version = int(row.record_hash_version)
    except (TypeError, ValueError) as exc:
        raise DecisionRecordIntegrityError("Decision record hash version is malformed") from exc
    if (
        row.record_integrity_status != VERIFIED_INTEGRITY_STATUS
        or hash_version not in DECISION_RECORD_SUPPORTED_HASH_VERSIONS
    ):
        raise DecisionRecordIntegrityError(
            "Decision record has legacy or otherwise unverified provenance"
        )
    principal = str(row.recorded_by_principal_ref or "")
    credential_ref = str(row.recorded_by_credential_ref or "")
    if (
        not principal.startswith("lians:principal:v1:")
        or principal == LEGACY_UNVERIFIED_PRINCIPAL
        or not credential_ref.startswith("lians:credential:v1:sha256:")
        or not _LOWER_SHA256.fullmatch(credential_ref.rsplit(":", 1)[-1])
    ):
        raise DecisionRecordIntegrityError(
            "Decision record is missing authenticated recorder provenance"
        )
    if hash_version == 2:
        if (
            getattr(row, "recorded_by_principal_type", None) is not None
            or getattr(row, "recorded_by_role", None) is not None
            or list(getattr(row, "recorded_by_scopes", None) or [])
        ):
            raise DecisionRecordIntegrityError(
                "DecisionRecord v2 cannot contain an authorization snapshot"
            )
    else:
        authenticated_recorder_authorization_snapshot(
            principal_type=getattr(row, "recorded_by_principal_type", None),
            role=getattr(row, "recorded_by_role", None),
            effective_scopes=list(getattr(row, "recorded_by_scopes", None) or []),
        )
    stored_hash = str(row.record_hash or "")
    if not _LOWER_SHA256.fullmatch(stored_hash):
        raise DecisionRecordIntegrityError("Decision record hash is malformed")
    expected = compute_decision_record_hash(row)
    if not hmac.compare_digest(stored_hash, expected):
        raise DecisionRecordIntegrityError("Decision record hash verification failed")


def decision_record_binding_payload(row: DecisionRecord) -> dict[str, str]:
    """Minimal non-PII payload for the immutable audit-chain binding."""
    return {
        "schema": DECISION_RECORD_BINDING_SCHEMA,
        "decision_id": str(row.id),
        "record_hash": row.record_hash,
    }


async def _assert_event_hash(db: AsyncSession, event: EventLog) -> None:
    try:
        hash_version = int(event.hash_version)
    except (TypeError, ValueError) as exc:
        raise DecisionRecordIntegrityError(
            "Decision audit binding has malformed integrity metadata"
        ) from exc
    if (
        not _LOWER_SHA256.fullmatch(str(event.prev_hash or ""))
        or not _LOWER_SHA256.fullmatch(str(event.row_hash or ""))
        or hash_version not in {1, 2, 3}
    ):
        raise DecisionRecordIntegrityError(
            "Decision audit binding has malformed integrity metadata"
        )
    if db.get_bind().dialect.name == "postgresql" and hash_version == 3:
        expected = await db.scalar(
            text(
                """SELECT public.lians_event_row_hash_v3(
                           prev_hash, chain_position, id, namespace, agent_id,
                           op, memory_id, content_hash, created_at, payload::jsonb
                       )
                   FROM event_log
                   WHERE id = CAST(:event_id AS uuid) AND namespace = :namespace"""
            ),
            {"event_id": str(event.id), "namespace": event.namespace},
        )
    else:
        expected = compute_row_hash(event, event.prev_hash)
    if expected is None or not hmac.compare_digest(str(event.row_hash), str(expected)):
        raise DecisionRecordIntegrityError(
            "Decision audit binding failed event-hash verification"
        )
    if event.prev_hash != GENESIS_HASH:
        predecessor = await db.scalar(
            select(EventLog.id).where(
                EventLog.namespace == event.namespace,
                EventLog.row_hash == event.prev_hash,
            )
        )
        if predecessor is None:
            raise DecisionRecordIntegrityError(
                "Decision audit binding has no verifiable predecessor"
            )


async def _assert_review_projection(db: AsyncSession, row: DecisionRecord) -> None:
    """Verify the immutable review head and its projection in one fixed snapshot.

    The database serializes appends, validates each predecessor, and rejects
    UPDATE/DELETE on review events. Those constraints make the latest immutable
    event a transitive chain checkpoint; routine decision reads therefore never
    need to materialize a tenant-controlled history. The dedicated paginated
    history endpoint still verifies every returned event and cursor anchor.
    """
    from .control_models import DecisionReviewEvent
    from .decision_review_service import verify_decision_review_event

    latest_subquery = (
        select(DecisionReviewEvent)
        .where(
            DecisionReviewEvent.namespace == row.namespace,
            DecisionReviewEvent.decision_id == row.id,
        )
        .order_by(DecisionReviewEvent.sequence.desc())
        .limit(1)
        .subquery()
    )
    latest_event = aliased(DecisionReviewEvent, latest_subquery)
    projection = (
        await db.execute(
            select(
                DecisionRecord.human_review_status,
                DecisionRecord.human_reviewer,
                DecisionRecord.human_reviewed_at,
                latest_event,
            )
            .select_from(DecisionRecord)
            .outerjoin(latest_event, true())
            .where(
                DecisionRecord.namespace == row.namespace,
                DecisionRecord.id == row.id,
            )
        )
    ).one_or_none()
    if projection is None:
        raise DecisionRecordIntegrityError(
            "Decision review projection has no decision record"
        )
    status, reviewer, reviewed_at, latest = projection
    if latest is None:
        if status != "not_requested" or reviewer is not None or reviewed_at is not None:
            raise DecisionRecordIntegrityError(
                "Decision review projection has no immutable review event"
            )
        return

    if latest.sequence < 1 or not verify_decision_review_event(latest):
        raise DecisionRecordIntegrityError(
            "Decision review projection has an invalid immutable event head"
        )
    if (
        status != latest.status
        or reviewer != latest.reviewer_principal_id
        or reviewed_at != latest.reviewed_at
    ):
        raise DecisionRecordIntegrityError(
            "Decision review projection does not match its latest immutable event"
        )


async def assert_decision_record_integrity(
    db: AsyncSession,
    row: DecisionRecord,
) -> EventLog:
    """Verify the record hash and its unique immutable audit-event binding."""
    assert_decision_record_hash(row)
    result = await db.execute(
        select(EventLog)
        .where(
            EventLog.namespace == row.namespace,
            EventLog.op == "decision_recorded",
            EventLog.content_hash == row.record_hash,
            EventLog.payload["decision_id"].as_string() == str(row.id),
        )
        .order_by(EventLog.created_at, EventLog.id)
        .limit(2)
        .execution_options(populate_existing=True)
    )
    events = list(result.scalars().all())
    expected_payload = decision_record_binding_payload(row)
    matching = [
        event
        for event in events
        if dict(event.payload or {}) == expected_payload
        and event.agent_id == row.recorded_by_principal_ref
    ]
    if len(matching) != 1 or len(events) != 1:
        raise DecisionRecordIntegrityError(
            "Decision record has no unique authenticated decision_recorded audit binding"
        )
    await _assert_event_hash(db, matching[0])
    await _assert_review_projection(db, row)
    return matching[0]
