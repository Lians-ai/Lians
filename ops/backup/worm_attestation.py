#!/usr/bin/env python3
"""Shared primitives for crash-safe local and provider-native WORM attestations."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backup_lib import (
    MAX_MANIFEST_BYTES,
    OperatorError,
    canonical_json,
    ensure_directory,
    fsync_directory,
    read_json_file,
    safe_filename,
    write_new_bytes,
)
from worm_providers import UploadObject, parse_rfc3339, verify_provider_object

ATTESTATION_SCHEMA = "urn:lians:ops:worm-provider-attestation:v1"
ANCHOR_SCHEMA = "urn:lians:ops:worm-provider-attestation-anchor:v1"
SCHEMA_DIRECTORY = Path(__file__).resolve().parent / "schemas"
_BOUNDARY_IDENTITY_FIELDS = (
    "kind",
    "owning_identity",
    "resource_id",
    "location",
    "immutable_capability",
)


def validate_document(document: Any, schema_filename: str, label: str) -> None:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from jsonschema.exceptions import SchemaError, ValidationError
    except ImportError as exc:
        raise OperatorError("WORM attestation validation requires jsonschema[format]") from exc

    schema = read_json_file(SCHEMA_DIRECTORY / schema_filename)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)
    except SchemaError as exc:
        raise OperatorError(f"Bundled {label} JSON Schema is invalid") from exc
    except ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        raise OperatorError(f"{label} does not satisfy its schema at {location}") from exc


def anchor_record_path(core_path: Path) -> Path:
    safe_filename(core_path.name)
    name = safe_filename(core_path.name + ".anchor.json")
    return core_path.with_name(name)


def pending_core_path(core_path: Path) -> Path:
    safe_filename(core_path.name)
    name_digest = hashlib.sha256(core_path.name.encode("utf-8")).hexdigest()
    return core_path.with_name(f"pending-core-{name_digest}.json")


def pending_anchor_path(core_path: Path, core_sha256: str) -> Path:
    safe_filename(core_path.name)
    if len(core_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in core_sha256
    ):
        raise OperatorError("Core attestation SHA-256 is invalid")
    name_digest = hashlib.sha256(core_path.name.encode("utf-8")).hexdigest()
    return core_path.with_name(f"pending-anchor-{name_digest}-{core_sha256}.json")


def provider_anchor_filename(backup_id: str, core_sha256: str) -> str:
    filename = f"lians-provider-attestation-{backup_id}-{core_sha256}.json"
    return safe_filename(filename)


def read_regular_bytes(path: Path, label: str, *, maximum: int = MAX_MANIFEST_BYTES) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise OperatorError(f"{label} must be a regular, non-symlink file") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise OperatorError(f"{label} must be a regular, non-symlink file")
    if before.st_size <= 0 or before.st_size > maximum:
        raise OperatorError(f"{label} has an unsafe size")
    before_fingerprint = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OperatorError(f"Could not safely open {label}") from exc
    try:
        try:
            opened = os.fstat(descriptor)
            opened_fingerprint = (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            if not stat.S_ISREG(opened.st_mode) or opened_fingerprint != before_fingerprint:
                raise OperatorError(f"{label} changed before it was opened")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                payload = handle.read(maximum + 1)
                after = os.fstat(handle.fileno())
        except OSError as exc:
            raise OperatorError(f"Could not safely read {label}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        len(payload) != before.st_size
        or len(payload) > maximum
        or (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        != opened_fingerprint
    ):
        raise OperatorError(f"{label} changed while it was being read")
    return payload


def read_canonical_document(
    path: Path,
    schema_filename: str,
    label: str,
) -> tuple[dict[str, Any], bytes, str]:
    payload = read_regular_bytes(path, label)
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorError(f"{label} is not canonical UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise OperatorError(f"{label} must be a JSON object")
    validate_document(document, schema_filename, label)
    if canonical_json(document) != payload:
        raise OperatorError(f"{label} bytes are not in the required canonical JSON form")
    return document, payload, hashlib.sha256(payload).hexdigest()


def checksum_path(path: Path) -> Path:
    name = safe_filename(path.name + ".sha256")
    return path.with_name(name)


def verify_optional_checksum(path: Path, payload: bytes, label: str) -> None:
    """Verify an existing sidecar while allowing an absent crash-recovery path."""

    sidecar_path = checksum_path(path)
    if not sidecar_path.exists() and not sidecar_path.is_symlink():
        return
    digest = hashlib.sha256(payload).hexdigest()
    expected = f"{digest}  {path.name}\n".encode()
    actual = read_regular_bytes(sidecar_path, f"{label} checksum", maximum=512)
    if actual != expected:
        raise OperatorError(f"{label} checksum sidecar does not match the exact bytes")


def verify_document_pair(
    path: Path,
    schema_filename: str,
    label: str,
) -> tuple[dict[str, Any], bytes, str]:
    document, payload, digest = read_canonical_document(path, schema_filename, label)
    sidecar_path = checksum_path(path)
    expected = f"{digest}  {path.name}\n".encode()
    actual = read_regular_bytes(sidecar_path, f"{label} checksum", maximum=512)
    if actual != expected:
        raise OperatorError(f"{label} checksum sidecar does not match the exact canonical bytes")
    return document, payload, digest


def _assert_existing_bytes(path: Path, expected: bytes, label: str) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    actual = read_regular_bytes(path, label, maximum=max(len(expected), 1))
    if actual != expected:
        raise OperatorError(f"Refusing to replace a different existing {label}")
    return True


def publish_document_pair(path: Path, payload: bytes, label: str) -> tuple[Path, str]:
    """Publish/recover an exact document pair without replacing any existing path."""

    safe_filename(path.name)
    parent = ensure_directory(path.parent)
    digest = hashlib.sha256(payload).hexdigest()
    sidecar_path = checksum_path(path)
    safe_filename(sidecar_path.name)
    sidecar_payload = f"{digest}  {path.name}\n".encode()

    document_exists = _assert_existing_bytes(path, payload, label)
    sidecar_exists = _assert_existing_bytes(sidecar_path, sidecar_payload, f"{label} checksum")
    if document_exists and sidecar_exists:
        return sidecar_path, "reused_verified"

    nonce = secrets.token_hex(12)
    name_digest = hashlib.sha256(path.name.encode("utf-8")).hexdigest()
    temporary_document = parent / f"staging-{nonce}-{name_digest}.json"
    temporary_sidecar = parent / f"staging-{nonce}-{name_digest}.sha256"
    linked: list[Path] = []
    try:
        if not document_exists:
            write_new_bytes(temporary_document, payload)
            os.link(temporary_document, path)
            linked.append(path)
        if not sidecar_exists:
            write_new_bytes(temporary_sidecar, sidecar_payload)
            os.link(temporary_sidecar, sidecar_path)
            linked.append(sidecar_path)
        fsync_directory(parent)
    except BaseException as exc:
        for linked_path in reversed(linked):
            linked_path.unlink(missing_ok=True)
        if isinstance(exc, OSError):
            raise OperatorError(f"Could not atomically publish {label} pair") from None
        raise
    finally:
        temporary_document.unlink(missing_ok=True)
        temporary_sidecar.unlink(missing_ok=True)
    disposition = "recovered_partial_pair" if document_exists or sidecar_exists else "created"
    return sidecar_path, disposition


def provider_boundary_sha256(provider: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(provider)).hexdigest()


def _provider_identity(provider: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(provider.get(field) for field in _BOUNDARY_IDENTITY_FIELDS)


def assert_same_provider_boundary(
    recorded: dict[str, Any],
    observed: dict[str, Any],
    *,
    label: str,
) -> None:
    if _provider_identity(recorded) != _provider_identity(observed):
        raise OperatorError(f"{label} provider ownership boundary changed")


def _retention_not_before(value: Any, minimum: Any, label: str) -> None:
    observed = parse_rfc3339(str(value))
    recorded = parse_rfc3339(str(minimum))
    if observed < recorded:
        raise OperatorError(f"{label} immutable retention was shortened")


def assert_object_reverified(
    recorded: dict[str, Any],
    observed: dict[str, Any],
    *,
    label: str,
) -> None:
    exact_fields = (
        "source_filename",
        "destination",
        "size_bytes",
        "sha256",
        "provider_object_id",
        "provider_etag",
        "provider_checksums",
    )
    for field in exact_fields:
        if recorded.get(field) != observed.get(field):
            raise OperatorError(f"{label} provider claim changed: {field}")
    recorded_content = recorded.get("content_verification", {})
    observed_content = observed.get("content_verification", {})
    if recorded_content.get("sha256") != observed_content.get("sha256"):
        raise OperatorError(f"{label} streamed/provider SHA-256 changed")
    recorded_retention = recorded.get("retention", {})
    observed_retention = observed.get("retention", {})
    for retention, claim_label in (
        (recorded_retention, "recorded"),
        (observed_retention, "observed"),
    ):
        has_hold = retention.get("hold_type") != "none"
        if bool(retention.get("legal_hold")) != has_hold:
            raise OperatorError(f"{label} {claim_label} hold claim is inconsistent")
    for field in ("mode", "policy_scope", "locked"):
        if recorded_retention.get(field) != observed_retention.get(field):
            raise OperatorError(f"{label} immutable retention claim changed: {field}")
    _retention_not_before(
        observed_retention.get("retain_until"),
        recorded_retention.get("retain_until"),
        label,
    )
    if bool(recorded_retention.get("legal_hold")) and not bool(
        observed_retention.get("legal_hold")
    ):
        raise OperatorError(f"{label} required hold is no longer present")
    if recorded_retention.get("hold_type") != "none" and recorded_retention.get(
        "hold_type"
    ) != observed_retention.get("hold_type"):
        raise OperatorError(f"{label} immutable hold type changed")


def _unique_object_index(
    items: Any,
    label: str,
) -> dict[tuple[Any, Any], dict[str, Any]]:
    if not isinstance(items, list):
        raise OperatorError(f"{label} provider inventory is malformed")
    indexed: dict[tuple[Any, Any], dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise OperatorError(f"{label} provider inventory is malformed")
        key = (item.get("destination"), item.get("source_filename"))
        if key in indexed:
            raise OperatorError(f"{label} provider inventory repeats an object")
        indexed[key] = item
    return indexed


def _assert_core_semantics(core: dict[str, Any]) -> dict[tuple[Any, Any], dict[str, Any]]:
    indexed = _unique_object_index(core.get("objects"), "Core attestation")
    destination_prefix = str(core.get("handoff", {}).get("destination_prefix", ""))
    required_retention = core.get("required_retention", {})
    required_until = required_retention.get("retain_until")
    required_hold = bool(required_retention.get("legal_hold"))
    for (destination, source_filename), item in indexed.items():
        filename = safe_filename(str(source_filename))
        if destination != f"{destination_prefix}/{filename}":
            raise OperatorError("Core attestation contains a non-canonical object destination")
        if item.get("content_verification", {}).get("sha256") != item.get("sha256"):
            raise OperatorError("Core attestation contains a conflicting content digest")
        retention = item.get("retention", {})
        has_hold = retention.get("hold_type") != "none"
        if bool(retention.get("legal_hold")) != has_hold:
            raise OperatorError("Core attestation contains an inconsistent hold claim")
        _retention_not_before(
            retention.get("retain_until"),
            required_until,
            f"Core object {filename}",
        )
        if required_hold and not bool(retention.get("legal_hold")):
            raise OperatorError(f"Core object {filename} lacks the required hold")
    return indexed


def assert_core_matches_provider_run(
    core: dict[str, Any],
    handoff: dict[str, Any],
    handoff_sha256: str,
    provider: dict[str, Any],
    provider_objects: list[dict[str, Any]],
) -> None:
    if core.get("backup_id") != handoff.get("backup_id"):
        raise OperatorError("Pending core attestation belongs to a different backup")
    core_handoff = core.get("handoff", {})
    if (
        core_handoff.get("sha256") != handoff_sha256
        or core_handoff.get("source_manifest_sha256") != handoff.get("source_manifest_sha256")
        or core_handoff.get("destination_prefix") != handoff.get("destination_prefix")
    ):
        raise OperatorError("Pending core attestation differs from the sealed handoff")
    if core.get("required_retention") != handoff.get("required_retention"):
        raise OperatorError("Pending core attestation has different required retention")
    assert_same_provider_boundary(core.get("provider", {}), provider, label="Pending core")
    recorded = _assert_core_semantics(core)
    observed = _unique_object_index(provider_objects, "Observed")
    if set(recorded) != set(observed):
        raise OperatorError("Pending core attestation provider inventory changed")
    for key, recorded_item in recorded.items():
        assert_object_reverified(
            recorded_item,
            observed[key],
            label=f"Pending core object {key[1]}",
        )


def verify_anchor_relationship(
    core_path: Path,
    core: dict[str, Any],
    core_payload: bytes,
    core_sha256: str,
    anchor: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> UploadObject:
    _assert_core_semantics(core)
    if anchor.get("backup_id") != core.get("backup_id"):
        raise OperatorError("Anchor record belongs to a different backup")
    core_claim = anchor.get("core_attestation", {})
    if (
        core_claim.get("schema") != ATTESTATION_SCHEMA
        or core_claim.get("local_filename") != core_path.name
        or core_claim.get("size_bytes") != len(core_payload)
        or core_claim.get("sha256") != core_sha256
        or core_claim.get("handoff_sha256") != core.get("handoff", {}).get("sha256")
    ):
        raise OperatorError("Anchor record does not bind the exact local core attestation")
    if anchor.get("provider") != core.get("provider"):
        raise OperatorError("Anchor record does not bind the core provider snapshot")
    cross_binding = anchor.get("cross_binding", {})
    if cross_binding.get("core_attestation_sha256") != core_sha256 or cross_binding.get(
        "provider_boundary_sha256"
    ) != provider_boundary_sha256(core["provider"]):
        raise OperatorError("Anchor cross-binding digest is invalid")
    object_claim = anchor.get("immutable_anchor", {})
    expected_filename = provider_anchor_filename(str(core["backup_id"]), core_sha256)
    expected_destination = f"{core['handoff']['destination_prefix']}/{expected_filename}"
    if (
        object_claim.get("source_filename") != expected_filename
        or object_claim.get("destination") != expected_destination
        or object_claim.get("size_bytes") != len(core_payload)
        or object_claim.get("sha256") != core_sha256
        or object_claim.get("content_verification", {}).get("sha256") != core_sha256
    ):
        raise OperatorError("Immutable provider anchor does not bind the exact core bytes")
    return UploadObject(
        source_filename=expected_filename,
        source_path=source_path or core_path,
        destination=expected_destination,
        size_bytes=len(core_payload),
        sha256=core_sha256,
    )


def verify_anchored_attestation(core_path: Path) -> dict[str, Any]:
    """Validate both local pairs and re-read the exact immutable provider object."""

    if core_path.is_symlink():
        raise OperatorError("Core provider attestation must not be a symlink")
    safe_filename(core_path.name)
    core_parent = ensure_directory(core_path.parent)
    core_path = core_parent / core_path.name
    core, core_payload, core_sha256 = verify_document_pair(
        core_path,
        "worm-provider-attestation-v1.schema.json",
        "WORM provider attestation",
    )
    anchor_path = anchor_record_path(core_path)
    anchor, anchor_payload, anchor_sha256 = verify_document_pair(
        anchor_path,
        "worm-provider-attestation-anchor-v1.schema.json",
        "WORM provider attestation anchor",
    )
    item = verify_anchor_relationship(core_path, core, core_payload, core_sha256, anchor)
    required_retention = core["required_retention"]
    retention_until = parse_rfc3339(str(required_retention["retain_until"]))
    if retention_until <= datetime.now(UTC):
        raise OperatorError("Required WORM retention has expired")
    provider_run = verify_provider_object(
        str(core["handoff"]["destination_prefix"]),
        retention_until,
        bool(required_retention["legal_hold"]),
        item,
        str(anchor["immutable_anchor"]["provider_object_id"]),
    )
    if len(provider_run.objects) != 1:
        raise OperatorError("Provider returned an invalid immutable anchor inventory")
    assert_same_provider_boundary(
        anchor["provider"],
        provider_run.provider,
        label="Immutable anchor",
    )
    assert_object_reverified(
        anchor["immutable_anchor"],
        provider_run.objects[0],
        label="Immutable provider anchor",
    )
    final_core, final_core_payload, final_core_sha256 = verify_document_pair(
        core_path,
        "worm-provider-attestation-v1.schema.json",
        "WORM provider attestation",
    )
    final_anchor, final_anchor_payload, final_anchor_sha256 = verify_document_pair(
        anchor_path,
        "worm-provider-attestation-anchor-v1.schema.json",
        "WORM provider attestation anchor",
    )
    if (
        final_core_payload != core_payload
        or final_core_sha256 != core_sha256
        or final_anchor_payload != anchor_payload
        or final_anchor_sha256 != anchor_sha256
    ):
        raise OperatorError("Local WORM attestation pairs changed during provider verification")
    verify_anchor_relationship(
        core_path,
        final_core,
        final_core_payload,
        final_core_sha256,
        final_anchor,
    )
    return {
        "status": "provider_attestation_anchor_verified",
        "backup_id": core["backup_id"],
        "core_attestation": str(core_path),
        "core_sha256": core_sha256,
        "anchor_record": str(anchor_path),
        "anchor_record_sha256": anchor_sha256,
        "provider": provider_run.provider["kind"],
        "provider_object_id": provider_run.objects[0]["provider_object_id"],
        "verified_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
