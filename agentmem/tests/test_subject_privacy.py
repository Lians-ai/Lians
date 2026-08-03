"""Keyed subject-reference and immutable-payload privacy invariants."""

import pytest
from pydantic import SecretStr
from lians.config import Settings
from lians.subject_privacy import (
    ERASURE_REQUEST_REFERENCE_PREFIX,
    SUBJECT_REFERENCE_PREFIX,
    SubjectReferenceConfigurationError,
    SubjectReferenceNamespaceError,
    erasure_request_reference,
    replace_subject_identifier,
    sanitize_audit_payload,
    subject_reference,
    validate_subject_reference_configuration,
)

_KEY = SecretStr("11" * 32)


def _settings(**overrides) -> Settings:
    values = {
        "deployment_environment": "development",
        "subject_reference_key": _KEY,
    }
    values.update(overrides)
    return Settings(**values)


def test_subject_reference_is_stable_tenant_scoped_and_idempotent():
    settings = _settings()
    first = subject_reference("tenant-a", "customer-42", settings=settings)
    repeated = subject_reference("tenant-a", "customer-42", settings=settings)
    other_tenant = subject_reference("tenant-b", "customer-42", settings=settings)

    assert first == repeated
    assert first != other_tenant
    assert first.startswith(SUBJECT_REFERENCE_PREFIX)
    assert subject_reference("tenant-a", first, settings=settings) == first
    assert "customer-42" not in first
    with pytest.raises(SubjectReferenceNamespaceError):
        subject_reference("tenant-b", first, settings=settings)


def test_production_requires_exact_32_byte_reference_key():
    with pytest.raises(SubjectReferenceConfigurationError):
        validate_subject_reference_configuration(
            _settings(
                deployment_environment="production",
                subject_reference_key=SecretStr(""),
            )
        )
    with pytest.raises(SubjectReferenceConfigurationError):
        validate_subject_reference_configuration(
            _settings(subject_reference_key=SecretStr("too-short"))
        )


def test_audit_payload_references_subject_and_request_without_raw_values():
    settings = _settings()
    # Exercise the configured-key path without relying on the cached global
    # Settings object for the direct replacement assertion.
    subject_ref = subject_reference("tenant-a", "customer-42", settings=settings)
    assert subject_ref is not None
    nested = replace_subject_identifier(
        {"actor": {"id": "customer-42"}, "safe": "other"},
        "customer-42",
        subject_ref,
    )
    assert nested["actor"]["id"] == subject_ref
    assert nested["safe"] == "other"


def test_erasure_request_reference_is_keyed_and_idempotent():
    settings = _settings()
    ref = erasure_request_reference("tenant-a", "DSR-2026-42", settings=settings)

    assert ref is not None
    assert ref.startswith(ERASURE_REQUEST_REFERENCE_PREFIX)
    assert "DSR-2026-42" not in ref
    assert erasure_request_reference("tenant-a", ref, settings=settings) == ref
    with pytest.raises(SubjectReferenceNamespaceError):
        erasure_request_reference("tenant-b", ref, settings=settings)


def test_audit_payload_sanitizer_covers_explicit_identifier_keys(monkeypatch):
    from lians import subject_privacy

    monkeypatch.setattr(subject_privacy, "get_settings", lambda: _settings())
    result = sanitize_audit_payload(
        "tenant-a",
        {
            "subject_id": "customer-42",
            "nested": {"request_ref": "DSR-2026-42"},
        },
    )

    assert result["subject_id"].startswith(SUBJECT_REFERENCE_PREFIX)
    assert result["nested"]["request_ref"].startswith(
        ERASURE_REQUEST_REFERENCE_PREFIX
    )
