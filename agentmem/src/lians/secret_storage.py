"""Purpose-separated, versioned envelope encryption for sensitive strings."""
from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .kms import get_master_keyring, validate_master_key_id

_V1_PREFIX = "lians-sealed:v1:"
_V2_PREFIX = "lians-sealed:v2:"
_SEALED_FAMILY_PREFIX = "lians-sealed:"
PENDING_CONTENT_PURPOSE = "pending-admission-content"
WEBHOOK_SIGNING_PURPOSE = "webhook-hmac-secret"
CONTROL_CLOSURE_STATEMENT_PURPOSE = "control-closure-attestation-statement"
SUBJECT_ERASURE_LOCATOR_PURPOSE = "subject-erasure-legacy-locator"


class SealedValueFormatError(ValueError):
    """A value claims to be a Lians envelope but violates its wire contract."""


class UnknownSealedKeyVersion(ValueError):
    """A v2 envelope names a key outside the bounded current/previous keyring."""


def _purpose_key(master_key: bytes, purpose: str, *, version: int) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"lians/{purpose}/v{version}".encode(),
    ).derive(master_key)


def _decode_payload(value: str, prefix: str) -> bytes:
    encoded = value[len(prefix):]
    try:
        packed = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SealedValueFormatError("Sealed value payload is not canonical base64url") from exc
    if len(packed) < 12 + 16:
        raise SealedValueFormatError("Sealed value payload is truncated")
    return packed


def sealed_text_version(value: str) -> tuple[int, str | None]:
    """Classify plaintext (0), legacy v1, or self-identifying v2 values."""
    if value.startswith(_V1_PREFIX):
        return 1, None
    if value.startswith(_V2_PREFIX):
        remainder = value[len(_V2_PREFIX):]
        key_id, separator, payload = remainder.partition(":")
        if not separator or not payload:
            raise SealedValueFormatError("v2 sealed value is missing its key id or payload")
        try:
            key_id = validate_master_key_id(key_id, field="sealed value key id")
        except ValueError as exc:
            raise SealedValueFormatError(str(exc)) from exc
        return 2, key_id
    if value.startswith(_SEALED_FAMILY_PREFIX):
        raise SealedValueFormatError("Unsupported Lians sealed-value version")
    return 0, None


def is_sealed_text(value: str) -> bool:
    return sealed_text_version(value)[0] in {1, 2}


def seal_text(plaintext: str, *, purpose: str, context: str) -> str:
    """Encrypt a string with the current key in a context-bound v2 envelope."""
    current = get_master_keyring().current
    prefix = f"{_V2_PREFIX}{current.key_id}:"
    nonce = os.urandom(12)
    aad = f"{prefix}\0{context}".encode()
    ciphertext = AESGCM(_purpose_key(current.material, purpose, version=2)).encrypt(
        nonce,
        plaintext.encode("utf-8"),
        aad,
    )
    token = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
    return prefix + token


def unseal_text(value: str, *, purpose: str, context: str) -> str:
    """Decrypt v2 or bounded-current/previous v1; retain plaintext compatibility."""
    version, key_id = sealed_text_version(value)
    if version == 0:
        return value

    ring = get_master_keyring()
    if version == 2:
        if key_id is None:
            raise SealedValueFormatError(
                "v2 sealed value classification omitted its key id"
            )
        candidate = ring.by_id(key_id)
        if candidate is None:
            raise UnknownSealedKeyVersion(
                f"Sealed value references unavailable key id {key_id!r}"
            )
        prefix = f"{_V2_PREFIX}{key_id}:"
        packed = _decode_payload(value, prefix)
        nonce, ciphertext = packed[:12], packed[12:]
        aad = f"{prefix}\0{context}".encode()
        plaintext = AESGCM(
            _purpose_key(candidate.material, purpose, version=2)
        ).decrypt(nonce, ciphertext, aad)
        return plaintext.decode("utf-8")

    packed = _decode_payload(value, _V1_PREFIX)
    nonce, ciphertext = packed[:12], packed[12:]
    last_error: InvalidTag | None = None
    for candidate in ring.candidates:
        try:
            plaintext = AESGCM(
                _purpose_key(candidate.material, purpose, version=1)
            ).decrypt(nonce, ciphertext, context.encode("utf-8"))
            return plaintext.decode("utf-8")
        except InvalidTag as exc:
            last_error = exc
    if last_error is None:
        raise RuntimeError(
            "Legacy sealed-value unwrap had no configured key candidates"
        )
    raise last_error


def rewrap_sealed_text(value: str, *, purpose: str, context: str) -> str:
    """Authenticate a sealed value and return a verified current-key v2 token."""
    if sealed_text_version(value)[0] == 0:
        raise SealedValueFormatError("Refusing to treat plaintext as an encrypted value")
    plaintext = unseal_text(value, purpose=purpose, context=context)
    rewritten = seal_text(plaintext, purpose=purpose, context=context)
    if unseal_text(rewritten, purpose=purpose, context=context) != plaintext:
        raise RuntimeError("Sealed value rewrap verification failed")
    return rewritten


async def protect_legacy_sensitive_rows(
    db: AsyncSession,
    *,
    batch_size: int = 500,
) -> int:
    """Seal legacy plaintext rows in restart-safe, memory-bounded pages.

    This compatibility pass runs before a server replica accepts traffic.  A
    large tenant must not turn startup into a whole-table ORM materialization,
    and concurrent replica starts must not encrypt the same page while holding
    each other indefinitely.  PostgreSQL therefore claims a bounded page with
    ``SKIP LOCKED`` and commits each page independently; a final database-side
    invariant makes a replica fail closed if another transaction left plaintext
    rows behind.
    """
    from .models import PendingAdmission, WebhookEndpoint

    if not 1 <= batch_size <= 5_000:
        raise ValueError("batch_size must be between 1 and 5000")

    dialect = db.get_bind().dialect.name
    changed = 0
    targets = (
        (PendingAdmission, PendingAdmission.content, PENDING_CONTENT_PURPOSE),
        (WebhookEndpoint, WebhookEndpoint.secret, WEBHOOK_SIGNING_PURPOSE),
    )
    for model, value_column, purpose in targets:
        plaintext_predicate = (
            func.substr(value_column, 1, len(_SEALED_FAMILY_PREFIX))
            != _SEALED_FAMILY_PREFIX
        )
        while True:
            statement = (
                select(model)
                .where(plaintext_predicate)
                .order_by(model.id)
                .limit(batch_size)
            )
            if dialect == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            rows = list((await db.execute(statement)).scalars().all())
            if not rows:
                break
            for row in rows:
                value = row.content if model is PendingAdmission else row.secret
                if is_sealed_text(value):
                    continue
                sealed = seal_text(value, purpose=purpose, context=row.namespace)
                if model is PendingAdmission:
                    row.content = sealed
                else:
                    row.secret = sealed
                changed += 1
            await db.commit()

    remaining = 0
    for model, value_column, _purpose in targets:
        plaintext_predicate = (
            func.substr(value_column, 1, len(_SEALED_FAMILY_PREFIX))
            != _SEALED_FAMILY_PREFIX
        )
        remaining += int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(model)
                    .where(plaintext_predicate)
                )
            ).scalar_one()
        )
    if remaining:
        raise RuntimeError(
            "Legacy sensitive-row protection is incomplete; "
            f"{remaining} plaintext rows remain"
        )
    return changed
