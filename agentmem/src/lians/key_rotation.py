"""Transactional inventory and rewrap primitives for the offline key operator.

This module deliberately exposes no HTTP surface.  The operator command under
``ops/keys`` is the only supported mutation entry point and requires a verified
backup plus PostgreSQL table-owner privileges.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from cryptography.exceptions import InvalidTag
from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from .control_models import (
    ControlClosureAttestation,
    DecisionReviewEvent,
    GateApprovalAttestation,
)
from .control_service import verify_closure_attestation_integrity
from .crypto import (
    rewrap_subject_key,
    unwrap_subject_key,
    wrapped_subject_key_version,
)
from .decision_review_service import verify_decision_review_event
from .immutable_attestation_service import verify_approval_attestation_integrity
from .integration_models import IntegrationDestination, IntegrationOutboxEvent
from .kms import get_master_keyring
from .models import PendingAdmission, SubjectKey, WebhookEndpoint
from .subject_erasure_models import SubjectErasureJob
from .version import EXPECTED_ALEMBIC_HEAD
from .secret_storage import (
    CONTROL_CLOSURE_STATEMENT_PURPOSE,
    PENDING_CONTENT_PURPOSE,
    SUBJECT_ERASURE_LOCATOR_PURPOSE,
    WEBHOOK_SIGNING_PURPOSE,
    rewrap_sealed_text,
    seal_text,
    sealed_text_version,
    unseal_text,
)

INTEGRATION_SECRET_PURPOSE = "integration-destination-secret-config"
INTEGRATION_PAYLOAD_PURPOSE = "integration-outbox-event-payload"
GATE_APPROVAL_STATEMENT_PURPOSE = "gate-approval-attestation-statement"
DECISION_REVIEW_NOTE_PURPOSE = "decision-review-event-note"
ROTATION_LOCK_NAME = "lians:master-key-rotation:v1"
EXPECTED_ROTATION_REVISION = EXPECTED_ALEMBIC_HEAD

# The order is an operational contract: every fence transition and committed
# rewrap takes write-conflicting locks in exactly this order to avoid operator
# deadlocks.  These are the only tables containing master-key-derived values.
MASTER_KEY_VALUE_TABLES: tuple[str, ...] = (
    "control_closure_attestations",
    "decision_review_events",
    "gate_approval_attestations",
    "integration_destinations",
    "integration_outbox_events",
    "pending_admissions",
    "subject_keys",
    "subject_erasure_jobs",
    "webhook_endpoints",
)

FENCE_TRIGGERS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "subject_keys",
        "trg_subject_keys_master_key_fence",
        "lians_master_key_fence_subject",
        (),
    ),
    (
        "pending_admissions",
        "trg_pending_admissions_master_key_fence",
        "lians_master_key_fence_sealed",
        ("content", "required"),
    ),
    (
        "subject_erasure_jobs",
        "trg_subject_erasure_jobs_master_key_fence",
        "lians_master_key_fence_sealed",
        ("subject_locator_encrypted", "nullable"),
    ),
    (
        "webhook_endpoints",
        "trg_webhook_endpoints_master_key_fence",
        "lians_master_key_fence_sealed",
        ("secret", "required"),
    ),
    (
        "gate_approval_attestations",
        "trg_gate_approval_attestations_master_key_fence",
        "lians_master_key_fence_sealed",
        ("statement_encrypted", "nullable"),
    ),
    (
        "decision_review_events",
        "trg_decision_review_events_master_key_fence",
        "lians_master_key_fence_sealed",
        ("note_encrypted", "nullable"),
    ),
    (
        "integration_destinations",
        "trg_integration_destinations_master_key_fence",
        "lians_master_key_fence_sealed",
        ("secret_config_encrypted", "required"),
    ),
    (
        "integration_outbox_events",
        "trg_integration_outbox_events_master_key_fence",
        "lians_master_key_fence_sealed",
        ("payload_encrypted", "required"),
    ),
    (
        "control_closure_attestations",
        "trg_control_closure_attestations_master_key_fence",
        "lians_master_key_fence_closure",
        (),
    ),
)

FENCE_FUNCTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("lians_master_key_fence_allows", "text", "boolean", "v"),
    (
        "lians_master_key_fence_check_sealed",
        "text, boolean",
        "boolean",
        "v",
    ),
    ("lians_master_key_fence_sealed", "", "trigger", "v"),
    ("lians_master_key_fence_subject", "", "trigger", "v"),
    ("lians_master_key_fence_closure", "", "trigger", "v"),
)


class KeyRotationError(RuntimeError):
    """A fail-closed rotation precondition or verification failure."""


@dataclass(frozen=True, slots=True)
class WriteFenceStatus:
    """Non-secret persistent fence state safe for operator/readiness output."""

    phase: Literal["inactive", "prepared", "narrowed"]
    current_key_id: str | None = None
    previous_key_id: str | None = None
    generation: int = 0

    @property
    def active(self) -> bool:
        return self.phase != "inactive"

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "phase": self.phase,
            "current_key_id": self.current_key_id,
            "previous_key_id": self.previous_key_id,
            "generation": self.generation,
        }


@dataclass(frozen=True, slots=True)
class SealedFieldSpec:
    name: str
    model: type
    attribute: str
    purpose: str
    context: Callable[[Any], str]
    allow_plaintext: bool = False
    hash_attribute: str | None = None
    integrity: Callable[[Any], bool] | None = None
    immutable_hash_attribute: str | None = None
    verifier: Callable[[Any, str], bool] | None = None
    preserve_attributes: tuple[str, ...] = ()


@dataclass(slots=True)
class FieldInventory:
    rows: int = 0
    values: int = 0
    null_values: int = 0
    current: int = 0
    previous: int = 0
    legacy: int = 0
    plaintext: int = 0
    unknown: int = 0
    destroyed: int = 0
    rewrap_required: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "rows": self.rows,
            "values": self.values,
            "null_values": self.null_values,
            "current": self.current,
            "previous": self.previous,
            "legacy": self.legacy,
            "plaintext": self.plaintext,
            "unknown": self.unknown,
            "destroyed": self.destroyed,
            "rewrap_required": self.rewrap_required,
        }


@dataclass(slots=True)
class RotationInventory:
    fields: dict[str, FieldInventory] = field(default_factory=dict)
    verification_errors: list[str] = field(default_factory=list)
    inventory_sha256: str = ""

    @property
    def totals(self) -> FieldInventory:
        total = FieldInventory()
        for item in self.fields.values():
            for name in item.as_dict():
                setattr(total, name, getattr(total, name) + getattr(item, name))
        return total

    def as_dict(self) -> dict[str, Any]:
        return {
            "fields": {name: item.as_dict() for name, item in self.fields.items()},
            "totals": self.totals.as_dict(),
            "verification_error_count": len(self.verification_errors),
            "verification_errors": list(self.verification_errors),
            "inventory_sha256": self.inventory_sha256,
        }


SEALED_FIELDS: tuple[SealedFieldSpec, ...] = (
    SealedFieldSpec(
        "subject_erasure_jobs.subject_locator_encrypted",
        SubjectErasureJob,
        "subject_locator_encrypted",
        SUBJECT_ERASURE_LOCATOR_PURPOSE,
        lambda row: f"{row.namespace}:{row.id}",
    ),
    SealedFieldSpec(
        "pending_admissions.content",
        PendingAdmission,
        "content",
        PENDING_CONTENT_PURPOSE,
        lambda row: row.namespace,
        allow_plaintext=True,
    ),
    SealedFieldSpec(
        "webhook_endpoints.secret",
        WebhookEndpoint,
        "secret",
        WEBHOOK_SIGNING_PURPOSE,
        lambda row: row.namespace,
        allow_plaintext=True,
        preserve_attributes=("updated_at",),
    ),
    SealedFieldSpec(
        "gate_approval_attestations.statement_encrypted",
        GateApprovalAttestation,
        "statement_encrypted",
        GATE_APPROVAL_STATEMENT_PURPOSE,
        lambda row: f"{row.namespace}:{row.id}:{row.context_hash}",
        hash_attribute="statement_hash",
        integrity=verify_approval_attestation_integrity,
        immutable_hash_attribute="attestation_hash",
    ),
    SealedFieldSpec(
        "decision_review_events.note_encrypted",
        DecisionReviewEvent,
        "note_encrypted",
        DECISION_REVIEW_NOTE_PURPOSE,
        lambda row: f"{row.namespace}:{row.id}:{row.decision_id}",
        hash_attribute="note_hash",
        integrity=verify_decision_review_event,
        immutable_hash_attribute="event_hash",
    ),
    SealedFieldSpec(
        "integration_destinations.secret_config_encrypted",
        IntegrationDestination,
        "secret_config_encrypted",
        INTEGRATION_SECRET_PURPOSE,
        lambda row: f"{row.namespace}:{row.id}",
        verifier=lambda row, plaintext: _verify_destination_secret(row, plaintext),
    ),
    SealedFieldSpec(
        "integration_outbox_events.payload_encrypted",
        IntegrationOutboxEvent,
        "payload_encrypted",
        INTEGRATION_PAYLOAD_PURPOSE,
        lambda row: f"{row.namespace}:{row.id}",
        hash_attribute="payload_hash",
    ),
)


IMMUTABLE_TRIGGERS: tuple[tuple[str, str, str], ...] = (
    (
        "gate_approval_attestations",
        "trg_gate_approval_attestations_append_only",
        "lians_attestation_reject_mutation",
    ),
    (
        "decision_review_events",
        "trg_decision_review_events_append_only",
        "lians_attestation_reject_mutation",
    ),
    (
        "control_closure_attestations",
        "trg_control_closure_attestations_append_only",
        "lians_control_reject_mutation",
    ),
    (
        "integration_outbox_events",
        "trg_integration_outbox_events_append_only",
        "lians_integration_reject_mutation",
    ),
)


def _verify_destination_secret(row: IntegrationDestination, plaintext: str) -> bool:
    try:
        decoded = json.loads(plaintext)
    except (TypeError, ValueError):
        return False
    signing_secret = decoded.get("signing_secret") if isinstance(decoded, dict) else None
    return bool(
        isinstance(signing_secret, str)
        and hashlib.sha256(signing_secret.encode("utf-8")).hexdigest()
        == row.secret_fingerprint
    )


def _locator(row: Any) -> str:
    if isinstance(row, SubjectKey):
        raw = f"{row.namespace}\0{row.subject_id}"
    else:
        raw = str(row.id)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _record_digest(
    digest: Any, *, field_name: str, row: Any, classification: str, value: bytes | str
) -> None:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    digest.update(field_name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(_locator(row).encode("ascii"))
    digest.update(b"\0")
    digest.update(classification.encode("ascii"))
    digest.update(b"\0")
    digest.update(hashlib.sha256(raw).digest())
    digest.update(b"\n")


def _error(inventory: RotationInventory, field_name: str, row: Any, reason: str) -> None:
    inventory.verification_errors.append(f"{field_name}:{_locator(row)}:{reason}")


def _classify_version(version: int, key_id: str | None) -> str:
    ring = get_master_keyring()
    if version == 0:
        return "plaintext"
    if version == 1:
        return "legacy"
    if key_id == ring.current.key_id:
        return "current"
    if ring.previous is not None and key_id == ring.previous.key_id:
        return "previous"
    return "unknown"


async def _batches(session: AsyncSession, model: type, *, size: int = 500):
    primary_keys = tuple(model.__mapper__.primary_key)
    cursor: tuple[Any, ...] | None = None
    while True:
        statement = select(model).order_by(*primary_keys).limit(size)
        if cursor is not None:
            statement = statement.where(tuple_(*primary_keys) > cursor)
        rows = list((await session.execute(statement)).scalars().all())
        if not rows:
            return
        yield rows
        last = rows[-1]
        cursor = tuple(getattr(last, column.key) for column in primary_keys)


def _mark(item: FieldInventory, classification: str) -> None:
    setattr(item, classification, getattr(item, classification) + 1)
    if classification in {"legacy", "previous", "plaintext"}:
        item.rewrap_required += 1


async def inventory_master_key_material(
    session: AsyncSession, *, verify: bool = True, batch_size: int = 500
) -> RotationInventory:
    """Inventory every master-derived field without returning any stored value."""
    result = RotationInventory()
    digest = hashlib.sha256()

    subject_item = result.fields.setdefault("subject_keys.enc_key", FieldInventory())
    async for rows in _batches(session, SubjectKey, size=batch_size):
        for row in rows:
            subject_item.rows += 1
            if row.enc_key is None:
                subject_item.null_values += 1
                if row.destroyed_at is None:
                    _error(result, "subject_keys.enc_key", row, "live_key_missing_wrapper")
                continue
            wrapped = bytes(row.enc_key)
            subject_item.values += 1
            if row.destroyed_at is not None:
                classification = "destroyed"
                _mark(subject_item, classification)
                _record_digest(
                    digest,
                    field_name="subject_keys.enc_key",
                    row=row,
                    classification=classification,
                    value=wrapped,
                )
                if any(wrapped):
                    _error(result, "subject_keys.enc_key", row, "destroyed_wrapper_not_zeroed")
                continue
            try:
                version, key_id = wrapped_subject_key_version(wrapped)
                classification = _classify_version(version, key_id)
            except ValueError:
                classification = "unknown"
            _mark(subject_item, classification)
            _record_digest(
                digest,
                field_name="subject_keys.enc_key",
                row=row,
                classification=classification,
                value=wrapped,
            )
            if verify and classification != "unknown":
                try:
                    if len(unwrap_subject_key(wrapped)) != 32:
                        raise ValueError
                except (InvalidTag, UnicodeDecodeError, ValueError):
                    _error(result, "subject_keys.enc_key", row, "authentication_failed")
        session.expunge_all()

    for spec in SEALED_FIELDS:
        item = result.fields.setdefault(spec.name, FieldInventory())
        async for rows in _batches(session, spec.model, size=batch_size):
            for row in rows:
                item.rows += 1
                value = getattr(row, spec.attribute)
                if value is None:
                    item.null_values += 1
                    if (
                        spec.hash_attribute is not None
                        and getattr(row, spec.hash_attribute) is not None
                    ):
                        _error(result, spec.name, row, "hash_without_encrypted_value")
                    if spec.integrity is not None and not spec.integrity(row):
                        _error(result, spec.name, row, "immutable_hash_failed")
                    continue
                item.values += 1
                try:
                    version, key_id = sealed_text_version(value)
                    classification = _classify_version(version, key_id)
                except ValueError:
                    classification = "unknown"
                if classification == "plaintext" and not spec.allow_plaintext:
                    classification = "unknown"
                _mark(item, classification)
                _record_digest(
                    digest,
                    field_name=spec.name,
                    row=row,
                    classification=classification,
                    value=value,
                )
                if spec.integrity is not None and not spec.integrity(row):
                    _error(result, spec.name, row, "immutable_hash_failed")
                if verify and classification != "unknown":
                    try:
                        plaintext = (
                            value
                            if classification == "plaintext"
                            else unseal_text(
                                value,
                                purpose=spec.purpose,
                                context=spec.context(row),
                            )
                        )
                        stored_hash = (
                            getattr(row, spec.hash_attribute)
                            if spec.hash_attribute is not None
                            else None
                        )
                        if spec.hash_attribute is not None and (
                            stored_hash is None
                            or hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
                            != stored_hash
                        ):
                            raise ValueError
                        if spec.verifier is not None and not spec.verifier(row, plaintext):
                            raise ValueError
                    except (InvalidTag, UnicodeDecodeError, ValueError):
                        _error(result, spec.name, row, "verification_failed")
            session.expunge_all()

    closure_name = "control_closure_attestations.statement_encrypted"
    closure_item = result.fields.setdefault(closure_name, FieldInventory())
    async for rows in _batches(session, ControlClosureAttestation, size=batch_size):
        for row in rows:
            closure_item.rows += 1
            if not verify_closure_attestation_integrity(row):
                _error(result, closure_name, row, "immutable_hash_failed")
            if row.statement_encrypted is None:
                if row.statement is None:
                    closure_item.null_values += 1
                    _error(result, closure_name, row, "missing_statement")
                    continue
                value = row.statement
                classification = "plaintext"
                plaintext = row.statement
            else:
                value = row.statement_encrypted
                try:
                    version, key_id = sealed_text_version(value)
                    classification = _classify_version(version, key_id)
                except ValueError:
                    classification = "unknown"
                plaintext = None
            closure_item.values += 1
            _mark(closure_item, classification)
            _record_digest(
                digest,
                field_name=closure_name,
                row=row,
                classification=classification,
                value=value,
            )
            if verify and classification != "unknown":
                try:
                    if plaintext is None:
                        plaintext = unseal_text(
                            value,
                            purpose=CONTROL_CLOSURE_STATEMENT_PURPOSE,
                            context=f"{row.namespace}:{row.id}:{row.resource_type}:{row.resource_id}",
                        )
                    if row.statement_hash is not None and hashlib.sha256(
                        plaintext.encode("utf-8")
                    ).hexdigest() != row.statement_hash:
                        raise ValueError
                except (InvalidTag, UnicodeDecodeError, ValueError):
                    _error(result, closure_name, row, "verification_failed")
        session.expunge_all()

    result.inventory_sha256 = digest.hexdigest()
    result.verification_errors.sort()
    return result


async def _rewrap_values(session: AsyncSession, *, batch_size: int) -> int:
    rewritten = 0
    ring = get_master_keyring()

    async for rows in _batches(session, SubjectKey, size=batch_size):
        for row in rows:
            if row.enc_key is None or row.destroyed_at is not None:
                continue
            wrapped = bytes(row.enc_key)
            try:
                version, key_id = wrapped_subject_key_version(wrapped)
            except ValueError as exc:
                raise KeyRotationError("Malformed subject-key envelope encountered") from exc
            if version == 2 and key_id == ring.current.key_id:
                continue
            plaintext = unwrap_subject_key(wrapped)
            row.enc_key = rewrap_subject_key(wrapped)
            if unwrap_subject_key(bytes(row.enc_key)) != plaintext:
                raise KeyRotationError("Subject-key verification failed after rewrite")
            rewritten += 1
        await session.flush()
        session.expunge_all()

    for spec in SEALED_FIELDS:
        async for rows in _batches(session, spec.model, size=batch_size):
            for row in rows:
                value = getattr(row, spec.attribute)
                if value is None:
                    continue
                version, key_id = sealed_text_version(value)
                if version == 2 and key_id == ring.current.key_id:
                    continue
                original_immutable_hash = (
                    getattr(row, spec.immutable_hash_attribute)
                    if spec.immutable_hash_attribute is not None
                    else None
                )
                preserved_values = {
                    attribute: getattr(row, attribute)
                    for attribute in spec.preserve_attributes
                }
                if version == 0:
                    if not spec.allow_plaintext:
                        raise KeyRotationError(f"Unencrypted value found in {spec.name}")
                    plaintext = value
                    rewritten_value = seal_text(
                        plaintext,
                        purpose=spec.purpose,
                        context=spec.context(row),
                    )
                else:
                    plaintext = unseal_text(
                        value,
                        purpose=spec.purpose,
                        context=spec.context(row),
                    )
                    rewritten_value = rewrap_sealed_text(
                        value,
                        purpose=spec.purpose,
                        context=spec.context(row),
                    )
                setattr(row, spec.attribute, rewritten_value)
                for attribute, preserved_value in preserved_values.items():
                    setattr(row, attribute, preserved_value)
                    flag_modified(row, attribute)
                if unseal_text(
                    rewritten_value,
                    purpose=spec.purpose,
                    context=spec.context(row),
                ) != plaintext:
                    raise KeyRotationError(f"Verification failed after rewriting {spec.name}")
                if spec.hash_attribute is not None:
                    stored_hash = getattr(row, spec.hash_attribute)
                    if stored_hash is None or hashlib.sha256(
                        plaintext.encode("utf-8")
                    ).hexdigest() != stored_hash:
                        raise KeyRotationError(f"Stored hash mismatch in {spec.name}")
                if spec.verifier is not None and not spec.verifier(row, plaintext):
                    raise KeyRotationError(f"Semantic verification failed in {spec.name}")
                if spec.integrity is not None and not spec.integrity(row):
                    raise KeyRotationError(f"Immutable hash mismatch in {spec.name}")
                if spec.immutable_hash_attribute is not None and getattr(
                    row, spec.immutable_hash_attribute
                ) != original_immutable_hash:
                    raise KeyRotationError(f"Immutable hash changed in {spec.name}")
                rewritten += 1
            await session.flush()
            session.expunge_all()

    async for rows in _batches(session, ControlClosureAttestation, size=batch_size):
        for row in rows:
            original_attestation_hash = row.attestation_hash
            original_hash_version = int(row.hash_version or 1)
            if not verify_closure_attestation_integrity(row):
                raise KeyRotationError("Closure attestation failed pre-rewrite integrity verification")
            context = f"{row.namespace}:{row.id}:{row.resource_type}:{row.resource_id}"
            if row.statement_encrypted is None:
                if row.statement is None:
                    raise KeyRotationError("Closure attestation has no statement")
                plaintext = row.statement
                row.statement_encrypted = seal_text(
                    plaintext,
                    purpose=CONTROL_CLOSURE_STATEMENT_PURPOSE,
                    context=context,
                )
                row.statement = None
                row.statement_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
            else:
                version, key_id = sealed_text_version(row.statement_encrypted)
                if version == 2 and key_id == ring.current.key_id:
                    continue
                plaintext = unseal_text(
                    row.statement_encrypted,
                    purpose=CONTROL_CLOSURE_STATEMENT_PURPOSE,
                    context=context,
                )
                row.statement_encrypted = rewrap_sealed_text(
                    row.statement_encrypted,
                    purpose=CONTROL_CLOSURE_STATEMENT_PURPOSE,
                    context=context,
                )
                if row.statement_hash is None:
                    row.statement_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
            if unseal_text(
                row.statement_encrypted,
                purpose=CONTROL_CLOSURE_STATEMENT_PURPOSE,
                context=context,
            ) != plaintext:
                raise KeyRotationError("Closure statement verification failed after rewrite")
            if not verify_closure_attestation_integrity(row):
                raise KeyRotationError("Closure attestation hash changed during protection")
            if (
                row.attestation_hash != original_attestation_hash
                or int(row.hash_version or 1) != original_hash_version
            ):
                raise KeyRotationError(
                    "Closure attestation hash/version changed during protection"
                )
            rewritten += 1
        await session.flush()
        session.expunge_all()
    return rewritten


async def _validate_schema_and_privileges(session: AsyncSession) -> str:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        raise KeyRotationError("Master-key rotation is supported only on PostgreSQL")
    revisions = list(
        (await session.execute(text("SELECT version_num FROM alembic_version")))
        .scalars()
        .all()
    )
    if revisions != [EXPECTED_ROTATION_REVISION]:
        raise KeyRotationError(
            "Database must have the exact master-key fence Alembic head; "
            f"found {sorted(revisions)!r}"
        )

    expected_columns = {
        "subject_keys": {"namespace", "subject_id", "enc_key", "destroyed_at"},
        "pending_admissions": {"id", "namespace", "content"},
        "subject_erasure_jobs": {
            "id",
            "namespace",
            "subject_locator_encrypted",
        },
        "webhook_endpoints": {"id", "namespace", "secret"},
        "gate_approval_attestations": {
            "id",
            "statement_encrypted",
            "statement_hash",
        },
        "decision_review_events": {"id", "note_encrypted", "note_hash"},
        "integration_destinations": {
            "id",
            "secret_config_encrypted",
            "secret_fingerprint",
        },
        "integration_outbox_events": {"id", "payload_encrypted", "payload_hash"},
        "control_closure_attestations": {
            "id",
            "statement",
            "statement_encrypted",
            "statement_hash",
            "hash_version",
        },
        "master_key_rotation_state": {"singleton_id", "inventory_sha256"},
        "master_key_write_fence_state": {
            "singleton_id",
            "phase",
            "current_key_id",
            "previous_key_id",
            "generation",
            "prepared_at",
            "narrowed_at",
        },
    }
    column_rows = (
        await session.execute(
            text(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ANY(:tables)"
            ),
            {"tables": list(expected_columns)},
        )
    ).all()
    actual: dict[str, set[str]] = {}
    fence_columns: dict[str, tuple[str, str]] = {}
    for table_name, column_name, data_type, is_nullable in column_rows:
        actual.setdefault(table_name, set()).add(column_name)
        if table_name == "master_key_write_fence_state":
            fence_columns[column_name] = (data_type, is_nullable)
    missing = {
        table_name: sorted(columns - actual.get(table_name, set()))
        for table_name, columns in expected_columns.items()
        if columns - actual.get(table_name, set())
    }
    if missing:
        raise KeyRotationError(f"Rotation schema is incomplete: {missing}")
    if set(fence_columns) != expected_columns["master_key_write_fence_state"]:
        raise KeyRotationError("Master-key write-fence table has unexpected columns")
    expected_fence_types = {
        "singleton_id": ("smallint", "NO"),
        "phase": ("character varying", "NO"),
        "current_key_id": ("character varying", "NO"),
        "previous_key_id": ("character varying", "YES"),
        "generation": ("bigint", "NO"),
        "prepared_at": ("timestamp with time zone", "NO"),
        "narrowed_at": ("timestamp with time zone", "YES"),
    }
    if fence_columns != expected_fence_types:
        raise KeyRotationError("Master-key write-fence column contract has drifted")

    required_constraints = {
        ("gate_approval_attestations", "ck_gate_approval_statement_sealed"),
        ("decision_review_events", "ck_decision_review_note_sealed"),
        ("integration_destinations", "ck_integration_destination_secret_sealed"),
        ("integration_outbox_events", "ck_integration_payload_sealed"),
        ("control_closure_attestations", "ck_control_attestation_statement_storage"),
        ("control_closure_attestations", "ck_control_attestation_statement_sealed"),
        ("subject_erasure_jobs", "ck_subject_erasure_job_completion"),
        ("master_key_rotation_state", "ck_master_key_rotation_singleton"),
    }
    fence_constraint_names = {
        "master_key_write_fence_state_pkey",
        "ck_master_key_write_fence_singleton",
        "ck_master_key_write_fence_phase",
        "ck_master_key_write_fence_current_id",
        "ck_master_key_write_fence_previous_id",
        "ck_master_key_write_fence_generation",
        "ck_master_key_write_fence_phase_storage",
    }
    constraint_tables = sorted(
        {table for table, _ in required_constraints}
        | {"master_key_write_fence_state"}
    )
    constraints = (
        await session.execute(
            text(
                "SELECT c.relname, con.conname, con.convalidated, "
                "pg_get_constraintdef(con.oid) "
                "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND c.relname = ANY(:tables)"
            ),
            {"tables": constraint_tables},
        )
    ).all()
    constraint_map = {
        (table, name): (validated, definition)
        for table, name, validated, definition in constraints
    }
    for table_name, constraint_name in required_constraints:
        entry = constraint_map.get((table_name, constraint_name))
        if entry is None or not entry[0]:
            raise KeyRotationError(
                f"Required validated constraint {constraint_name} is absent on {table_name}"
            )
        if "sealed" in constraint_name and "lians-sealed:v2:" not in entry[1]:
            raise KeyRotationError(
                f"Constraint {constraint_name} does not accept the v2 envelope contract"
            )
    actual_fence_constraints = {
        name
        for (table_name, name), (validated, _) in constraint_map.items()
        if table_name == "master_key_write_fence_state" and validated
    }
    if actual_fence_constraints != fence_constraint_names:
        raise KeyRotationError("Master-key write-fence constraints have drifted")
    fence_definitions = "\n".join(
        constraint_map[("master_key_write_fence_state", name)][1]
        for name in sorted(fence_constraint_names)
    )
    for required_fragment in (
        "'prepared'",
        "'narrowed'",
        "previous_key_id IS NULL",
        "previous_key_id IS NOT NULL",
        "generation >= 1",
        "[A-Za-z0-9._-]",
    ):
        if required_fragment not in fence_definitions:
            raise KeyRotationError("Master-key write-fence constraint semantics have drifted")

    schema_name = str(
        (await session.execute(text("SELECT current_schema()"))).scalar_one()
    )
    quote = bind.dialect.identifier_preparer.quote
    quoted_schema = quote(schema_name)
    table_names = tuple(expected_columns)
    table_owner_rows = (
        await session.execute(
            text(
                "SELECT c.relname, c.relowner, "
                "pg_has_role(current_user, c.relowner, 'USAGE') "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND c.relname = ANY(:tables)"
            ),
            {"tables": list(table_names)},
        )
    ).all()
    table_owners = {name: owner for name, owner, _ in table_owner_rows}
    if set(table_owners) != set(table_names):
        raise KeyRotationError("Master-key rotation table ownership inventory is incomplete")
    for table_name, _, owns_table in table_owner_rows:
        if not owns_table:
            raise KeyRotationError(
                f"Current database role is not a member of the owner role for {table_name}"
            )

    for table_name in expected_columns:
        required_privileges = ["SELECT", "UPDATE"]
        if table_name in {
            "master_key_rotation_state",
            "master_key_write_fence_state",
        }:
            required_privileges.append("INSERT")
        for privilege in required_privileges:
            allowed = bool(
                (
                    await session.execute(
                        text(
                            "SELECT has_table_privilege(current_user, :table_name, :privilege)"
                        ),
                        {
                            "table_name": f"{quoted_schema}.{quote(table_name)}",
                            "privilege": privilege,
                        },
                    )
                ).scalar_one()
            )
            if not allowed:
                raise KeyRotationError(
                    f"Current database role lacks {privilege} on {table_name}"
                )

    expected_search_path = f"search_path=pg_catalog, {quoted_schema}"
    function_names = sorted({name for name, _, _, _ in FENCE_FUNCTIONS})
    function_rows = (
        await session.execute(
            text(
                "SELECT p.proname, pg_catalog.oidvectortypes(p.proargtypes), "
                "pg_catalog.format_type(p.prorettype, NULL), p.provolatile::text, "
                "p.prosecdef, p.proconfig, p.proowner, p.prosrc, "
                "NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE("
                "p.proacl, pg_catalog.acldefault('f', p.proowner))) acl "
                "WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE') "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = current_schema() AND ("
                "p.proname = ANY(:functions) OR "
                "p.proname LIKE 'lians_master_key_fence_%')"
            ),
            {"functions": function_names},
        )
    ).all()
    function_map = {
        (name, arguments): (
            returns,
            volatility,
            security_definer,
            config,
            owner,
            source,
            public_execute_revoked,
        )
        for (
            name,
            arguments,
            returns,
            volatility,
            security_definer,
            config,
            owner,
            source,
            public_execute_revoked,
        ) in function_rows
    }
    if set(function_map) != {(name, arguments) for name, arguments, _, _ in FENCE_FUNCTIONS}:
        raise KeyRotationError("Master-key write-fence function signatures have drifted")
    fence_owner = table_owners["master_key_write_fence_state"]
    for name, arguments, returns, volatility in FENCE_FUNCTIONS:
        (
            actual_return,
            actual_volatility,
            security_definer,
            config,
            owner,
            source,
            public_execute_revoked,
        ) = function_map[(name, arguments)]
        if (
            actual_return != returns
            or actual_volatility != volatility
            or not security_definer
            or list(config or []) != [expected_search_path]
            or owner != fence_owner
            or not public_execute_revoked
        ):
            raise KeyRotationError(
                f"Master-key write-fence function {name} has unsafe schema or owner state"
            )
        required_source_fragments = {
            "lians_master_key_fence_allows": (
                "master_key_write_fence_state",
                "FOR SHARE",
                "current_key_id",
                "previous_key_id",
            ),
            "lians_master_key_fence_check_sealed": (
                "lians-sealed:v2:",
                "lians_master_key_fence_allows",
                "mod(length(v_payload), 4)",
            ),
            "lians_master_key_fence_sealed": (
                "TG_NARGS <> 2",
                "lians_master_key_fence_check_sealed",
            ),
            "lians_master_key_fence_subject": (
                "6c69616e732d64656b3a763200",
                "74 + v_key_id_length",
                "NEW.destroyed_at IS NOT NULL",
            ),
            "lians_master_key_fence_closure": (
                "NEW.statement IS NOT NULL",
                "NEW.statement_encrypted IS NULL",
                "lians_master_key_fence_check_sealed",
            ),
        }[name]
        if any(fragment not in source for fragment in required_source_fragments):
            raise KeyRotationError(
                f"Master-key write-fence function {name} body has drifted"
            )

    trigger_function_names = sorted(
        {function for _, _, function, _ in FENCE_TRIGGERS}
    )
    fence_trigger_rows = (
        await session.execute(
            text(
                "SELECT c.relname, t.tgname, fnn.nspname, p.proname, "
                "t.tgenabled::text, t.tgtype, t.tgnargs, encode(t.tgargs, 'hex'), "
                "pg_get_triggerdef(t.oid), p.proowner "
                "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "JOIN pg_namespace fnn ON fnn.oid = p.pronamespace "
                "WHERE n.nspname = current_schema() AND c.relname = ANY(:tables) "
                "AND (t.tgname LIKE '%master_key_fence%' OR "
                "p.proname = ANY(:trigger_functions)) AND NOT t.tgisinternal"
            ),
            {
                "tables": list(MASTER_KEY_VALUE_TABLES),
                "trigger_functions": trigger_function_names,
            },
        )
    ).all()
    trigger_map = {
        (table, trigger): (
            function_schema,
            function,
            enabled,
            trigger_type,
            argument_count,
            argument_hex,
            definition,
            function_owner,
        )
        for (
            table,
            trigger,
            function_schema,
            function,
            enabled,
            trigger_type,
            argument_count,
            argument_hex,
            definition,
            function_owner,
        ) in fence_trigger_rows
    }
    if set(trigger_map) != {(table, trigger) for table, trigger, _, _ in FENCE_TRIGGERS}:
        raise KeyRotationError("Master-key write-fence trigger inventory has drifted")
    for table, trigger, function, arguments in FENCE_TRIGGERS:
        (
            function_schema,
            actual_function,
            enabled,
            trigger_type,
            argument_count,
            argument_hex,
            definition,
            function_owner,
        ) = trigger_map[(table, trigger)]
        expected_argument_hex = (
            b"\0".join(argument.encode("utf-8") for argument in arguments)
            + (b"\0" if arguments else b"")
        ).hex()
        if (
            function_schema != schema_name
            or actual_function != function
            or enabled != "O"
            or int(trigger_type) != 23
            or int(argument_count) != len(arguments)
            or argument_hex != expected_argument_hex
            or f"{function}(" not in definition
            or "BEFORE INSERT OR UPDATE" not in definition
            or "FOR EACH ROW" not in definition
            or any(f"'{argument}'" not in definition for argument in arguments)
            or function_owner != fence_owner
        ):
            raise KeyRotationError(
                f"Master-key write-fence trigger {trigger} on {table} has drifted"
            )

    for table_name, trigger_name, function_name in IMMUTABLE_TRIGGERS:
        trigger = (
            await session.execute(
                text(
                    "SELECT p.proname, t.tgenabled::text "
                    "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "JOIN pg_proc p ON p.oid = t.tgfoid "
                    "WHERE n.nspname = current_schema() AND c.relname = :table_name "
                    "AND t.tgname = :trigger_name AND NOT t.tgisinternal"
                ),
                {"table_name": table_name, "trigger_name": trigger_name},
            )
        ).one_or_none()
        if trigger is None or trigger[0] != function_name or trigger[1] != "O":
            raise KeyRotationError(
                f"Expected enabled immutable trigger {trigger_name} on {table_name} is absent or changed"
            )
    return str(revisions[0])


async def _configure_operator_session(
    session: AsyncSession, *, lock_timeout_seconds: int
) -> None:
    if not 1 <= lock_timeout_seconds <= 600:
        raise KeyRotationError("lock_timeout_seconds must be between 1 and 600")
    await session.execute(
        text("SELECT set_config('app.current_namespace', '__admin__', true)")
    )
    await session.execute(
        text("SELECT set_config('agentmem.barrier_group', '', true)")
    )
    await session.execute(
        text("SELECT set_config('lock_timeout', :timeout, true)"),
        {"timeout": f"{lock_timeout_seconds}s"},
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"),
        {"name": ROTATION_LOCK_NAME},
    )


async def _lock_master_key_value_tables(session: AsyncSession) -> None:
    """Wait out existing writers and retain write-conflicting locks to commit."""
    bind = session.get_bind()
    schema_name = str(
        (await session.execute(text("SELECT current_schema()"))).scalar_one()
    )
    quote = bind.dialect.identifier_preparer.quote
    qualified_tables = ", ".join(
        f"{quote(schema_name)}.{quote(table_name)}"
        for table_name in MASTER_KEY_VALUE_TABLES
    )
    await session.execute(
        text(
            f"LOCK TABLE {qualified_tables} "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )


async def read_master_key_write_fence(
    session: AsyncSession, *, for_update: bool = False
) -> WriteFenceStatus:
    statement = (
        text(
            "SELECT phase, current_key_id, previous_key_id, generation "
            "FROM master_key_write_fence_state WHERE singleton_id = 1 FOR UPDATE"
        )
        if for_update
        else text(
            "SELECT phase, current_key_id, previous_key_id, generation "
            "FROM master_key_write_fence_state WHERE singleton_id = 1"
        )
    )
    row = (
        await session.execute(statement)
    ).mappings().one_or_none()
    if row is None:
        return WriteFenceStatus(phase="inactive")
    phase = str(row["phase"])
    if phase not in {"prepared", "narrowed"}:
        raise KeyRotationError("Master-key write-fence phase is invalid")
    current_key_id = str(row["current_key_id"])
    previous_key_id = (
        str(row["previous_key_id"])
        if row["previous_key_id"] is not None
        else None
    )
    generation = int(row["generation"])
    if generation < 1:
        raise KeyRotationError("Master-key write-fence generation is invalid")
    if phase == "prepared" and previous_key_id is None:
        raise KeyRotationError("Prepared master-key write fence has no previous key")
    if phase == "narrowed" and previous_key_id is not None:
        raise KeyRotationError("Narrowed master-key write fence still allows a previous key")
    return WriteFenceStatus(
        phase=phase,
        current_key_id=current_key_id,
        previous_key_id=previous_key_id,
        generation=generation,
    )


def _fence_matches_configured_pair(
    fence: WriteFenceStatus, *, phase: Literal["prepared", "narrowed"]
) -> bool:
    ring = get_master_keyring()
    if phase == "prepared":
        return bool(
            ring.previous is not None
            and fence.phase == "prepared"
            and fence.current_key_id == ring.current.key_id
            and fence.previous_key_id == ring.previous.key_id
        )
    return bool(
        fence.phase == "narrowed"
        and fence.current_key_id == ring.current.key_id
        and fence.previous_key_id is None
    )


def _valid_sha256(value: str | None) -> bool:
    return bool(
        value is not None
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


async def _validate_backup_identity(
    session: AsyncSession,
    *,
    database_revision: str,
    backup_manifest_sha256: str | None,
    backup_source_database: str | None,
    backup_source_system_identifier: str | None,
    backup_alembic_revision: str | None,
    expected_database_id: str | None,
    backup_database_id: str | None,
) -> None:
    if not _valid_sha256(backup_manifest_sha256):
        raise KeyRotationError("A verified backup manifest digest is required")
    if not expected_database_id or expected_database_id != backup_database_id:
        raise KeyRotationError(
            "The explicit database identity does not match the verified backup"
        )
    if backup_alembic_revision != database_revision:
        raise KeyRotationError(
            "Verified backup Alembic head does not match the live database head"
        )
    if backup_source_system_identifier:
        identity = (
            await session.execute(
                text(
                    "SELECT current_database(), "
                    "CASE WHEN has_function_privilege(current_user, "
                    "'pg_control_system()', 'EXECUTE') "
                    "THEN (pg_control_system()).system_identifier::text ELSE NULL END"
                )
            )
        ).one()
    else:
        identity = (
            await session.execute(text("SELECT current_database(), NULL::text"))
        ).one()
    if backup_source_database != identity[0]:
        raise KeyRotationError("Verified backup belongs to a different database name")
    if backup_source_system_identifier:
        if identity[1] is None:
            raise KeyRotationError(
                "Current database role cannot verify the backup PostgreSQL system identifier"
            )
        if identity[1] != backup_source_system_identifier:
            raise KeyRotationError(
                "Verified backup belongs to a different PostgreSQL cluster"
            )


def _assert_bounded_rotation_keyring() -> None:
    if get_master_keyring().previous is None:
        raise KeyRotationError(
            "A bounded current/previous keyring is required for fence preparation and apply"
        )


async def prepare_master_key_write_fence(
    session: AsyncSession,
    *,
    backup_manifest_sha256: str | None,
    backup_source_database: str | None,
    backup_source_system_identifier: str | None,
    backup_alembic_revision: str | None,
    expected_database_id: str | None,
    backup_database_id: str | None,
    batch_size: int = 500,
    lock_timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Activate a bounded dual-ID fence only after waiting out every writer."""
    if not 1 <= batch_size <= 10_000:
        raise KeyRotationError("batch_size must be between 1 and 10000")
    _assert_bounded_rotation_keyring()
    await _configure_operator_session(
        session, lock_timeout_seconds=lock_timeout_seconds
    )
    database_revision = await _validate_schema_and_privileges(session)
    await _validate_backup_identity(
        session,
        database_revision=database_revision,
        backup_manifest_sha256=backup_manifest_sha256,
        backup_source_database=backup_source_database,
        backup_source_system_identifier=backup_source_system_identifier,
        backup_alembic_revision=backup_alembic_revision,
        expected_database_id=expected_database_id,
        backup_database_id=backup_database_id,
    )

    # This lock is deliberately acquired before reading inventory or changing
    # the singleton row.  It waits for old writers and blocks new ones until
    # the prepared fence becomes visible at commit.
    await _lock_master_key_value_tables(session)
    inventory = await inventory_master_key_material(
        session, verify=True, batch_size=batch_size
    )
    if inventory.verification_errors or inventory.totals.unknown:
        raise KeyRotationError(
            "Fence preparation inventory contains unverifiable or unknown-key values"
        )

    ring = get_master_keyring()
    if ring.previous is None:
        raise KeyRotationError(
            "Fence preparation requires a configured previous master key"
        )
    existing = await read_master_key_write_fence(session, for_update=True)
    if existing.phase == "inactive":
        generation = 1
    elif _fence_matches_configured_pair(existing, phase="prepared"):
        generation = existing.generation
    elif (
        existing.phase == "prepared"
        and {existing.current_key_id, existing.previous_key_id}
        == {ring.current.key_id, ring.previous.key_id}
    ):
        # Reversing target/previous roles does not broaden the allowed set and
        # supports a fully verified rewrap rollback through the same lifecycle.
        generation = existing.generation
    elif (
        existing.phase == "narrowed"
        and existing.current_key_id == ring.previous.key_id
        and existing.previous_key_id is None
    ):
        generation = existing.generation + 1
    else:
        raise KeyRotationError(
            "Write-fence transition does not continue from the configured previous key"
        )

    await session.execute(
        text(
            "INSERT INTO master_key_write_fence_state ("
            "singleton_id, phase, current_key_id, previous_key_id, generation, "
            "prepared_at, narrowed_at) VALUES ("
            "1, 'prepared', :current_key_id, :previous_key_id, :generation, "
            ":prepared_at, NULL) ON CONFLICT (singleton_id) DO UPDATE SET "
            "phase = EXCLUDED.phase, current_key_id = EXCLUDED.current_key_id, "
            "previous_key_id = EXCLUDED.previous_key_id, "
            "generation = EXCLUDED.generation, prepared_at = EXCLUDED.prepared_at, "
            "narrowed_at = NULL"
        ),
        {
            "current_key_id": ring.current.key_id,
            "previous_key_id": ring.previous.key_id,
            "generation": generation,
            "prepared_at": datetime.now(UTC),
        },
    )
    prepared = await read_master_key_write_fence(session, for_update=True)
    if not _fence_matches_configured_pair(prepared, phase="prepared"):
        raise KeyRotationError("Prepared write fence failed transactional readback")
    return {
        "schema": "urn:lians:ops:master-key-rotation-report:v1",
        "mode": "fence_prepare",
        "status": "verified",
        "current_key_id": ring.current.key_id,
        "previous_key_configured": True,
        "safe_to_remove_previous": False,
        "write_fence": prepared.as_dict(),
        "inventory": inventory.as_dict(),
        "backup_manifest_sha256": backup_manifest_sha256,
    }


async def inspect_master_key_write_fence(
    session: AsyncSession,
    *,
    assertion: Literal["prepared", "narrowed"] | None = None,
    lock_timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Return or assert the exact persistent fence/configuration relationship."""
    await _configure_operator_session(
        session, lock_timeout_seconds=lock_timeout_seconds
    )
    await _validate_schema_and_privileges(session)
    fence = await read_master_key_write_fence(session)
    if assertion is not None and not _fence_matches_configured_pair(
        fence, phase=assertion
    ):
        raise KeyRotationError(
            f"Master-key write fence is not {assertion} for the configured keyring"
        )
    ring = get_master_keyring()
    narrowed = _fence_matches_configured_pair(fence, phase="narrowed")
    return {
        "schema": "urn:lians:ops:master-key-rotation-report:v1",
        "mode": (
            f"fence_assert_{assertion}" if assertion is not None else "fence_status"
        ),
        "status": "verified",
        "current_key_id": ring.current.key_id,
        "previous_key_configured": ring.previous is not None,
        "safe_to_remove_previous": bool(ring.previous is not None and narrowed),
        "write_fence": fence.as_dict(),
    }


async def _require_prepared_fence(session: AsyncSession) -> WriteFenceStatus:
    _assert_bounded_rotation_keyring()
    fence = await read_master_key_write_fence(session, for_update=True)
    if not _fence_matches_configured_pair(fence, phase="prepared"):
        raise KeyRotationError(
            "Rotation apply requires a prepared write fence for the configured current/previous pair"
        )
    return fence


async def _narrow_write_fence(
    session: AsyncSession, *, prepared: WriteFenceStatus
) -> WriteFenceStatus:
    ring = get_master_keyring()
    result = await session.execute(
        text(
            "UPDATE master_key_write_fence_state SET phase = 'narrowed', "
            "previous_key_id = NULL, narrowed_at = :narrowed_at "
            "WHERE singleton_id = 1 AND phase = 'prepared' "
            "AND current_key_id = :current_key_id "
            "AND previous_key_id = :previous_key_id AND generation = :generation"
        ),
        {
            "narrowed_at": datetime.now(UTC),
            "current_key_id": ring.current.key_id,
            "previous_key_id": prepared.previous_key_id,
            "generation": prepared.generation,
        },
    )
    if result.rowcount != 1:
        raise KeyRotationError("Prepared write fence changed before atomic narrowing")
    narrowed = await read_master_key_write_fence(session, for_update=True)
    if not _fence_matches_configured_pair(narrowed, phase="narrowed"):
        raise KeyRotationError("Narrowed write fence failed transactional readback")
    return narrowed


async def _set_trigger_state(session: AsyncSession, *, enabled: bool) -> None:
    action = "ENABLE" if enabled else "DISABLE"
    for table_name, trigger_name, _ in IMMUTABLE_TRIGGERS:
        # Identifiers come only from the static audited registry above.
        await session.execute(
            text(f"ALTER TABLE {table_name} {action} TRIGGER {trigger_name}")
        )


async def _checkpoint(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    inventory: RotationInventory,
    rewritten: int,
    backup_manifest_sha256: str,
) -> None:
    ring = get_master_keyring()
    totals = inventory.totals
    status = (
        "verified"
        if not inventory.verification_errors
        and totals.legacy == 0
        and totals.previous == 0
        and totals.unknown == 0
        and totals.plaintext == 0
        else "blocked"
    )
    await session.execute(
        text(
            """INSERT INTO master_key_rotation_state (
                singleton_id, run_id, current_key_id, previous_key_id, status,
                total_values, rewritten_values, legacy_values_remaining,
                previous_values_remaining, unknown_values_remaining,
                plaintext_closures_remaining, inventory_sha256,
                backup_manifest_sha256, completed_at
            ) VALUES (
                1, :run_id, :current_key_id, :previous_key_id, :status,
                :total_values, :rewritten_values, :legacy_values,
                :previous_values, :unknown_values, :plaintext_closures,
                :inventory_sha256, :backup_manifest_sha256, :completed_at
            ) ON CONFLICT (singleton_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                current_key_id = EXCLUDED.current_key_id,
                previous_key_id = EXCLUDED.previous_key_id,
                status = EXCLUDED.status,
                total_values = EXCLUDED.total_values,
                rewritten_values = EXCLUDED.rewritten_values,
                legacy_values_remaining = EXCLUDED.legacy_values_remaining,
                previous_values_remaining = EXCLUDED.previous_values_remaining,
                unknown_values_remaining = EXCLUDED.unknown_values_remaining,
                plaintext_closures_remaining = EXCLUDED.plaintext_closures_remaining,
                inventory_sha256 = EXCLUDED.inventory_sha256,
                backup_manifest_sha256 = EXCLUDED.backup_manifest_sha256,
                completed_at = EXCLUDED.completed_at"""
        ),
        {
            "run_id": run_id,
            "current_key_id": ring.current.key_id,
            "previous_key_id": ring.previous.key_id if ring.previous else None,
            "status": status,
            "total_values": totals.values,
            "rewritten_values": rewritten,
            "legacy_values": totals.legacy,
            "previous_values": totals.previous,
            "unknown_values": totals.unknown,
            "plaintext_closures": inventory.fields[
                "control_closure_attestations.statement_encrypted"
            ].plaintext,
            "inventory_sha256": inventory.inventory_sha256,
            "backup_manifest_sha256": backup_manifest_sha256,
            "completed_at": datetime.now(UTC),
        },
    )
    if status != "verified":
        raise KeyRotationError("Post-rewrite inventory is not safe to commit")


async def run_rotation(
    session: AsyncSession,
    *,
    apply: bool,
    backup_manifest_sha256: str | None = None,
    backup_source_database: str | None = None,
    backup_source_system_identifier: str | None = None,
    backup_alembic_revision: str | None = None,
    expected_database_id: str | None = None,
    backup_database_id: str | None = None,
    batch_size: int = 500,
    lock_timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Run an advisory-locked dry inventory or one all-or-nothing rewrap."""
    if not 1 <= batch_size <= 10_000:
        raise KeyRotationError("batch_size must be between 1 and 10000")
    await _configure_operator_session(
        session, lock_timeout_seconds=lock_timeout_seconds
    )
    database_revision = await _validate_schema_and_privileges(session)

    if apply:
        await _validate_backup_identity(
            session,
            database_revision=database_revision,
            backup_manifest_sha256=backup_manifest_sha256,
            backup_source_database=backup_source_database,
            backup_source_system_identifier=backup_source_system_identifier,
            backup_alembic_revision=backup_alembic_revision,
            expected_database_id=expected_database_id,
            backup_database_id=backup_database_id,
        )
        # The prepared trigger fence is persistent; this lock closes the final
        # race by waiting out old writers and blocking all new writers through
        # inventory, rewrap, checkpoint, fence narrowing, and commit.
        await _lock_master_key_value_tables(session)
        prepared_fence = await _require_prepared_fence(session)
    else:
        prepared_fence = None

    before = await inventory_master_key_material(
        session, verify=True, batch_size=batch_size
    )
    totals = before.totals
    if before.verification_errors or totals.unknown:
        raise KeyRotationError(
            "Preflight inventory contains unverifiable or unknown-key values; no writes performed"
        )
    if not apply:
        fence = await read_master_key_write_fence(session)
        fence_narrowed = _fence_matches_configured_pair(fence, phase="narrowed")
        return {
            "schema": "urn:lians:ops:master-key-rotation-report:v1",
            "mode": "dry_run",
            "status": "verified",
            "current_key_id": get_master_keyring().current.key_id,
            "previous_key_configured": get_master_keyring().previous is not None,
            "safe_to_remove_previous": bool(
                get_master_keyring().previous is not None
                and fence_narrowed
                and totals.legacy == 0
                and totals.previous == 0
                and totals.unknown == 0
                and totals.plaintext == 0
            ),
            "write_fence": fence.as_dict(),
            "inventory": before.as_dict(),
        }

    triggers_disabled = bool(totals.rewrap_required)
    if triggers_disabled:
        await _set_trigger_state(session, enabled=False)
    # Any failure below aborts the surrounding transaction. PostgreSQL then
    # rolls back both rewritten rows and transactional trigger-state changes.
    rewritten = await _rewrap_values(session, batch_size=batch_size)
    if rewritten != totals.rewrap_required:
        raise KeyRotationError(
            "Rewrite count differs from the advisory-locked preflight inventory"
        )
    if triggers_disabled:
        await _set_trigger_state(session, enabled=True)
    after = await inventory_master_key_material(
        session, verify=True, batch_size=batch_size
    )
    run_id = uuid.uuid4()
    await _checkpoint(
        session,
        run_id=run_id,
        inventory=after,
        rewritten=rewritten,
        backup_manifest_sha256=backup_manifest_sha256 or "",
    )
    if prepared_fence is None:
        raise KeyRotationError(
            "Rotation apply reached fence narrowing without a prepared fence"
        )
    narrowed_fence = await _narrow_write_fence(
        session, prepared=prepared_fence
    )

    return {
        "schema": "urn:lians:ops:master-key-rotation-report:v1",
        "mode": "apply",
        "status": "verified",
        "current_key_id": get_master_keyring().current.key_id,
        "run_id": str(run_id),
        "previous_key_configured": get_master_keyring().previous is not None,
        "safe_to_remove_previous": bool(get_master_keyring().previous is not None),
        "write_fence": narrowed_fence.as_dict(),
        "rewritten_values": rewritten,
        "before_inventory_sha256": before.inventory_sha256,
        "after_inventory": after.as_dict(),
        "backup_manifest_sha256": backup_manifest_sha256,
    }
