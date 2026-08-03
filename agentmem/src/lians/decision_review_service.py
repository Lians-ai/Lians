"""Immutable, encrypted, hash-chained review events for decision records."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .control_models import DecisionReviewEvent
from .models import DecisionRecord
from .schemas import DecisionReviewEventOut
from .secret_storage import seal_text, unseal_text

DECISION_REVIEW_NOTE_PURPOSE = "decision-review-event-note"


class DecisionReviewIntegrityError(ValueError):
    """The persisted review chain does not satisfy its integrity contract."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def reviewer_ref_hash(principal_id: str) -> str:
    return hashlib.sha256(
        b"lians/decision-reviewer/v1\0" + principal_id.encode("utf-8")
    ).hexdigest()


def _note_hash(note: str | None) -> str | None:
    return hashlib.sha256(note.encode("utf-8")).hexdigest() if note is not None else None


def _note_context(row_id, namespace: str, decision_id) -> str:
    return f"{namespace}:{row_id}:{decision_id}"


def decision_review_event_payload(row: DecisionReviewEvent) -> dict[str, Any]:
    """Canonical event content.  Sensitive plaintext is represented only by hashes."""
    return {
        "schema": "lians.decision-review-event.v1",
        "id": str(row.id),
        "namespace": row.namespace,
        "barrier_group": row.barrier_group,
        "decision_id": str(row.decision_id),
        "sequence": row.sequence,
        "status": row.status,
        "reviewer_ref": reviewer_ref_hash(row.reviewer_principal_id),
        "reviewer_principal_type": row.reviewer_principal_type,
        "reviewer_role": row.reviewer_role,
        "auth_method": row.auth_method,
        "credential_ref": (
            reviewer_ref_hash(row.credential_id) if row.credential_id else None
        ),
        "note_hash": row.note_hash,
        "prior_event_hash": row.prior_event_hash,
        "reviewed_at": _utc(row.reviewed_at).isoformat(),
    }


def verify_decision_review_event(row: DecisionReviewEvent) -> bool:
    return row.event_hash == _sha256_json(decision_review_event_payload(row))


async def _serialize_review_chain(
    db: AsyncSession, namespace: str, decision_id
) -> None:
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"lians:decision-review:{namespace}:{decision_id}"},
        )


async def create_decision_review_event(
    db: AsyncSession,
    *,
    decision: DecisionRecord,
    status: str,
    note: str | None,
    reviewer_principal_id: str,
    reviewer_principal_type: str | None,
    reviewer_role: str | None,
    auth_method: str,
    credential_id: str | None,
) -> DecisionReviewEvent:
    await _serialize_review_chain(db, decision.namespace, decision.id)
    latest = (
        await db.execute(
            select(DecisionReviewEvent)
            .where(
                DecisionReviewEvent.namespace == decision.namespace,
                DecisionReviewEvent.decision_id == decision.id,
            )
            .order_by(DecisionReviewEvent.sequence.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is not None and not verify_decision_review_event(latest):
        raise DecisionReviewIntegrityError(
            "The latest decision-review event failed integrity verification"
        )

    row_id = uuid.uuid4()
    now = datetime.now(UTC)
    row = DecisionReviewEvent(
        id=row_id,
        namespace=decision.namespace,
        barrier_group=decision.barrier_group,
        decision_id=decision.id,
        sequence=1 if latest is None else latest.sequence + 1,
        status=status,
        reviewer_principal_id=reviewer_principal_id,
        reviewer_principal_type=reviewer_principal_type,
        reviewer_role=reviewer_role,
        auth_method=auth_method,
        credential_id=credential_id,
        note_hash=_note_hash(note),
        prior_event_hash=latest.event_hash if latest else None,
        reviewed_at=now,
    )
    if note is not None:
        row.note_encrypted = seal_text(
            note,
            purpose=DECISION_REVIEW_NOTE_PURPOSE,
            context=_note_context(row_id, decision.namespace, decision.id),
        )
    row.event_hash = _sha256_json(decision_review_event_payload(row))
    db.add(row)
    # The projection guard installed by migration 0032 reads this row, so make
    # the immutable event visible before changing DecisionRecord's legacy view.
    await db.flush()
    decision.human_review_status = row.status
    decision.human_reviewer = row.reviewer_principal_id
    decision.human_reviewed_at = row.reviewed_at
    await db.flush()
    return row


def decision_review_event_out(
    row: DecisionReviewEvent, *, include_note: bool = True
) -> DecisionReviewEventOut:
    note = None
    if include_note and row.note_encrypted:
        note = unseal_text(
            row.note_encrypted,
            purpose=DECISION_REVIEW_NOTE_PURPOSE,
            context=_note_context(row.id, row.namespace, row.decision_id),
        )
    return DecisionReviewEventOut(
        id=row.id,
        namespace=row.namespace,
        barrier_group=row.barrier_group,
        decision_id=row.decision_id,
        sequence=row.sequence,
        status=row.status,
        reviewer_principal_id=row.reviewer_principal_id,
        reviewer_principal_type=row.reviewer_principal_type,
        reviewer_role=row.reviewer_role,
        auth_method=row.auth_method,
        credential_id=row.credential_id,
        note=note,
        note_hash=row.note_hash,
        prior_event_hash=row.prior_event_hash,
        event_hash=row.event_hash,
        reviewed_at=row.reviewed_at,
    )
