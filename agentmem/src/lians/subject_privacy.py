"""Tenant-scoped pseudonymous references for data-subject identifiers.

Raw caller identifiers are routing inputs, not evidence identifiers. Before a
subject identifier crosses a persistence boundary Lians converts it into a
keyed, namespace-bound reference. A database-only compromise therefore cannot
perform an offline dictionary attack against low-entropy customer identifiers,
and the same external identifier cannot be correlated across tenants.

The reference key is deliberately independent from the envelope-encryption
keyring: rotating or crypto-shredding content keys must not silently change the
stable reference used by tombstones and erasure evidence.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from typing import Any

from .config import Settings, get_settings

SUBJECT_REFERENCE_PREFIX = "lians:subject:v2:hmac-sha256:"
ERASURE_REQUEST_REFERENCE_PREFIX = "lians:erasure-request:v2:hmac-sha256:"
_HEX_DIGEST_LENGTH = 64
_DEVELOPMENT_KEY = hashlib.sha256(
    b"lians-development-only-subject-reference-key-v1"
).digest()


class SubjectReferenceConfigurationError(ValueError):
    """The subject-reference boundary cannot be initialized safely."""


class SubjectReferenceError(ValueError):
    """A caller supplied a malformed or namespace-incompatible reference."""


class SubjectReferenceNamespaceError(SubjectReferenceError):
    """An opaque reference was replayed into a different namespace."""


def _decode_key(value: str) -> bytes:
    candidate = value.strip()
    if not candidate:
        return b""
    if len(candidate) == 64:
        try:
            decoded = bytes.fromhex(candidate)
        except ValueError:
            decoded = b""
        if len(decoded) == 32:
            return decoded
    try:
        decoded = base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError):
        decoded = b""
    if len(decoded) != 32:
        raise SubjectReferenceConfigurationError(
            "SUBJECT_REFERENCE_KEY must encode exactly 32 bytes as base64 or hex"
        )
    return decoded


def validate_subject_reference_configuration(settings: Settings) -> bool:
    """Validate configuration and return whether an explicit key is present."""
    encoded = settings.subject_reference_key.get_secret_value()
    if not encoded.strip():
        if settings.deployment_environment.strip().lower() in {"prod", "production"}:
            raise SubjectReferenceConfigurationError(
                "SUBJECT_REFERENCE_KEY is required in production"
            )
        return False
    _decode_key(encoded)
    return True


def _reference_key(settings: Settings | None = None) -> bytes:
    configured = settings or get_settings()
    encoded = configured.subject_reference_key.get_secret_value()
    return _decode_key(encoded) if encoded.strip() else _DEVELOPMENT_KEY


def _validated_namespace(namespace: str) -> str:
    tenant = namespace.strip()
    if not tenant:
        raise SubjectReferenceError(
            "namespace is required to derive or validate a subject reference"
        )
    return tenant


def _namespace_scope(key: bytes, namespace: str, domain: bytes) -> str:
    document = (
        b"lians-reference-namespace-scope-v1\0"
        + domain
        + b"\0"
        + namespace.encode()
    )
    return hmac.new(key, document, hashlib.sha256).hexdigest()


def _parse_reference(value: str, prefix: str) -> tuple[str, str] | None:
    if not value.startswith(prefix):
        return None
    components = value.removeprefix(prefix).split(":")
    if len(components) != 2:
        raise SubjectReferenceError("Malformed Lians subject reference")
    if any(
        len(component) != _HEX_DIGEST_LENGTH
        or component != component.lower()
        or any(character not in "0123456789abcdef" for character in component)
        for component in components
    ):
        raise SubjectReferenceError("Malformed Lians subject reference")
    return components[0], components[1]


def is_subject_reference(
    value: str | None,
    *,
    namespace: str | None = None,
    settings: Settings | None = None,
) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = _parse_reference(value, SUBJECT_REFERENCE_PREFIX)
    except SubjectReferenceError:
        return False
    if parsed is None:
        return False
    if namespace is None:
        return True
    tenant = _validated_namespace(namespace)
    expected_scope = _namespace_scope(
        _reference_key(settings), tenant, b"subject-reference"
    )
    return hmac.compare_digest(parsed[0], expected_scope)


def subject_reference(
    namespace: str,
    subject_id: str | None,
    *,
    settings: Settings | None = None,
) -> str | None:
    """Return a stable keyed reference scoped verifiably to one namespace."""
    if subject_id is None:
        return None
    value = subject_id.strip()
    if not value:
        return None
    tenant = _validated_namespace(namespace)
    key = _reference_key(settings)
    parsed = _parse_reference(value, SUBJECT_REFERENCE_PREFIX)
    if parsed is not None:
        expected_scope = _namespace_scope(key, tenant, b"subject-reference")
        if not hmac.compare_digest(parsed[0], expected_scope):
            raise SubjectReferenceNamespaceError(
                "Subject reference belongs to a different namespace"
            )
        return value
    if value.startswith("lians:subject:"):
        raise SubjectReferenceError("Malformed or unsupported Lians subject reference")
    document = b"lians-subject-reference-v1\0" + tenant.encode() + b"\0" + value.encode()
    digest = hmac.new(key, document, hashlib.sha256).hexdigest()
    scope = _namespace_scope(key, tenant, b"subject-reference")
    return f"{SUBJECT_REFERENCE_PREFIX}{scope}:{digest}"


def erasure_request_reference(
    namespace: str,
    request_ref: str | None,
    *,
    settings: Settings | None = None,
) -> str | None:
    """Key an operator request reference before it enters immutable evidence."""
    if request_ref is None:
        return None
    value = request_ref.strip()
    if not value:
        return None
    tenant = _validated_namespace(namespace)
    key = _reference_key(settings)
    parsed = _parse_reference(value, ERASURE_REQUEST_REFERENCE_PREFIX)
    if parsed is not None:
        expected_scope = _namespace_scope(key, tenant, b"erasure-request-reference")
        if not hmac.compare_digest(parsed[0], expected_scope):
            raise SubjectReferenceNamespaceError(
                "Erasure request reference belongs to a different namespace"
            )
        return value
    if value.startswith("lians:erasure-request:"):
        raise SubjectReferenceError(
            "Malformed or unsupported Lians erasure request reference"
        )
    document = b"lians-erasure-request-v1\0" + tenant.encode() + b"\0" + value.encode()
    digest = hmac.new(key, document, hashlib.sha256).hexdigest()
    scope = _namespace_scope(key, tenant, b"erasure-request-reference")
    return f"{ERASURE_REQUEST_REFERENCE_PREFIX}{scope}:{digest}"


def replace_subject_identifier(value: Any, raw_subject_id: str, subject_ref: str) -> Any:
    """Replace exact subject scalars in nested evidence without fuzzy rewriting.

    This intentionally does not claim general-purpose PII discovery. It closes
    the explicit subject-ID path while capture minimization governs arbitrary
    free-form personal data.
    """
    if isinstance(value, dict):
        return {
            key: replace_subject_identifier(item, raw_subject_id, subject_ref)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            replace_subject_identifier(item, raw_subject_id, subject_ref)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            replace_subject_identifier(item, raw_subject_id, subject_ref)
            for item in value
        )
    if isinstance(value, str) and hmac.compare_digest(value, raw_subject_id):
        return subject_ref
    return value


def sanitize_audit_payload(namespace: str, value: Any) -> Any:
    """Pseudonymize explicit subject/request identifiers in immutable payloads."""
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = "".join(character for character in str(key).casefold() if character.isalnum())
            if normalized in {"subjectid", "subjectref"} and isinstance(item, str):
                sanitized[key] = subject_reference(namespace, item)
            elif normalized in {"subjectids", "subjectrefs"} and isinstance(item, list):
                sanitized[key] = [
                    subject_reference(namespace, entry) if isinstance(entry, str) else entry
                    for entry in item
                ]
            elif normalized in {"requestref", "erasurerequestref"} and isinstance(item, str):
                sanitized[key] = erasure_request_reference(namespace, item)
            else:
                sanitized[key] = sanitize_audit_payload(namespace, item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_audit_payload(namespace, item) for item in value]
    return value
