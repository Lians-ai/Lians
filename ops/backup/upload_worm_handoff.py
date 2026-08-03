#!/usr/bin/env python3
"""Upload a sealed backup handoff and emit a verified provider attestation.

Credentials are intentionally absent from this CLI.  Authentication is resolved
only through the selected provider's default workload-identity credential chain.
Every destination operation is create-only; an already-present object is reused
only after content, version/generation, retention, and hold verification succeeds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backup_lib import (
    HANDOFF_SCHEMA,
    MAX_MANIFEST_BYTES,
    OperatorError,
    canonical_json,
    ensure_directory,
    fsync_directory,
    safe_filename,
    utc_now,
    verify_bundle,
    write_new_bytes,
)
from worm_attestation import (
    ANCHOR_SCHEMA,
    ATTESTATION_SCHEMA,
    anchor_record_path,
    assert_core_matches_provider_run,
    assert_object_reverified,
    assert_same_provider_boundary,
    checksum_path,
    pending_anchor_path,
    pending_core_path,
    provider_anchor_filename,
    provider_boundary_sha256,
    publish_document_pair,
    read_canonical_document,
    read_regular_bytes,
    validate_document,
    verify_anchor_relationship,
    verify_anchored_attestation,
    verify_document_pair,
    verify_optional_checksum,
)
from worm_providers import (
    UploadObject,
    dependency_version,
    ensure_unique_provider_object_ids,
    parse_rfc3339,
    run_provider,
    validate_provider_destination_budget,
    verifier_workload_identity,
    verify_provider_object,
)

MAX_HANDOFF_OBJECTS = 1000


def read_verified_handoff(handoff_path: Path) -> tuple[dict[str, Any], str]:
    """Parse and hash the same no-follow byte snapshot bound by the sidecar."""

    payload = read_regular_bytes(handoff_path, "WORM handoff")
    digest = hashlib.sha256(payload).hexdigest()
    sidecar_path = handoff_path.with_name(safe_filename(handoff_path.name + ".sha256"))
    sidecar = read_regular_bytes(sidecar_path, "WORM handoff checksum", maximum=512)
    expected = f"{digest}  {handoff_path.name}\n".encode()
    if sidecar != expected:
        raise OperatorError("WORM handoff checksum does not match its sealed request")
    try:
        handoff = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorError("WORM handoff is not valid UTF-8 JSON") from exc
    if not isinstance(handoff, dict):
        raise OperatorError("WORM handoff must be a JSON object")
    validate_document(handoff, "worm-handoff-v1.schema.json", "WORM handoff")
    if canonical_json(handoff) != payload:
        raise OperatorError("WORM handoff bytes are not in canonical JSON form")
    return handoff, digest


def prepare_objects(
    handoff: dict[str, Any],
    bundle: Path,
    manifest: dict[str, Any],
    verification: dict[str, Any],
) -> list[UploadObject]:
    if handoff.get("backup_id") != manifest.get("backup_id"):
        raise OperatorError("WORM handoff backup_id differs from the verified bundle")
    if handoff.get("source_manifest_sha256") != verification.get("manifest_sha256"):
        raise OperatorError("WORM handoff manifest digest differs from the verified bundle")
    raw_objects = handoff.get("objects")
    if not isinstance(raw_objects, list) or not 3 <= len(raw_objects) <= MAX_HANDOFF_OBJECTS:
        raise OperatorError("WORM handoff has an unsafe object count")
    destination_prefix = str(handoff.get("destination_prefix", ""))
    expected: dict[str, dict[str, Any]] = {
        str(item["filename"]): {
            "size_bytes": int(item["size_bytes"]),
            "sha256": str(item["sha256"]),
        }
        for item in verification["artifacts"]
    }
    expected["manifest.json"] = {
        "size_bytes": (bundle / "manifest.json").stat().st_size,
        "sha256": verification["manifest_sha256"],
    }
    expected["SHA256SUMS"] = {
        "size_bytes": (bundle / "SHA256SUMS").stat().st_size,
        "sha256": verification["checksums_sha256"],
    }

    prepared: list[UploadObject] = []
    seen: set[str] = set()
    for raw in raw_objects:
        if not isinstance(raw, dict):
            raise OperatorError("WORM handoff contains a malformed object entry")
        filename = safe_filename(str(raw.get("source_filename", "")))
        if filename in seen:
            raise OperatorError(f"WORM handoff repeats object: {filename}")
        seen.add(filename)
        expected_item = expected.get(filename)
        if expected_item is None:
            raise OperatorError(f"WORM handoff names an unsealed object: {filename}")
        source_path = bundle / filename
        if source_path.is_symlink() or not source_path.is_file():
            raise OperatorError(f"WORM source object is missing or is a symlink: {filename}")
        destination = str(raw.get("destination", ""))
        if destination != f"{destination_prefix}/{filename}":
            raise OperatorError(f"WORM destination is not canonical for {filename}")
        if (
            raw.get("size_bytes") != expected_item["size_bytes"]
            or raw.get("sha256") != expected_item["sha256"]
        ):
            raise OperatorError(f"WORM handoff inventory differs from bundle: {filename}")
        prepared.append(
            UploadObject(
                source_filename=filename,
                source_path=source_path,
                destination=destination,
                size_bytes=expected_item["size_bytes"],
                sha256=expected_item["sha256"],
            )
        )
    if seen != set(expected):
        raise OperatorError("WORM handoff does not cover the complete sealed bundle")
    return prepared


def verify_provider_coverage(
    requested: list[UploadObject],
    attested: list[dict[str, Any]],
) -> None:
    expected = {
        (item.destination, item.source_filename): (item.size_bytes, item.sha256)
        for item in requested
    }
    observed: dict[tuple[str, str], tuple[Any, Any]] = {}
    for item in attested:
        key = (str(item.get("destination", "")), str(item.get("source_filename", "")))
        if key in observed:
            raise OperatorError("Provider attestation repeats a requested destination")
        observed[key] = (item.get("size_bytes"), item.get("sha256"))
    if observed != expected:
        raise OperatorError("Provider attestation inventory differs from the sealed handoff")


def verify_core_input_binding(
    core: dict[str, Any],
    handoff: dict[str, Any],
    handoff_sha256: str,
    objects: list[UploadObject],
) -> None:
    """Prove that a reusable core belongs to the supplied sealed handoff and bundle."""

    if core.get("backup_id") != handoff.get("backup_id"):
        raise OperatorError("Existing core attestation belongs to a different backup")
    core_handoff = core.get("handoff", {})
    if (
        core_handoff.get("schema") != HANDOFF_SCHEMA
        or core_handoff.get("sha256") != handoff_sha256
        or core_handoff.get("source_manifest_sha256") != handoff.get("source_manifest_sha256")
        or core_handoff.get("destination_prefix") != handoff.get("destination_prefix")
        or core.get("required_retention") != handoff.get("required_retention")
    ):
        raise OperatorError("Existing core attestation differs from the sealed handoff")
    verify_provider_coverage(objects, list(core.get("objects", [])))


def ensure_regular_or_absent(path: Path, label: str) -> bool:
    """Return whether a path exists while rejecting symlinks and non-files."""

    if path.is_symlink():
        raise OperatorError(f"{label} must not be a symlink")
    if not path.exists():
        return False
    if not path.is_file():
        raise OperatorError(f"{label} must be a regular file")
    return True


def stage_document(
    path: Path,
    payload: bytes,
    schema_filename: str,
    label: str,
) -> tuple[dict[str, Any], bytes, str]:
    """Create, sync, and reread a canonical crash-recovery document."""

    if not 0 < len(payload) <= MAX_MANIFEST_BYTES:
        raise OperatorError(f"{label} has an unsafe size")
    try:
        write_new_bytes(path, payload)
    except OSError as exc:
        raise OperatorError(f"Could not create {label} without replacement") from exc
    fsync_directory(path.parent)
    return read_canonical_document(path, schema_filename, label)


def remove_verified_pending(
    path: Path,
    expected_payload: bytes,
    schema_filename: str,
    label: str,
) -> None:
    """Remove only the exact private staging file after all four outputs verify."""

    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        raise OperatorError(f"Refusing to remove non-regular {label}")
    _, current, _ = read_canonical_document(path, schema_filename, label)
    if current != expected_payload:
        raise OperatorError(f"Refusing to remove a different {label}")
    try:
        path.unlink()
    except OSError as exc:
        raise OperatorError(f"Could not remove verified {label}") from exc
    fsync_directory(path.parent)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handoff", type=Path, help="sealed pending handoff JSON")
    parser.add_argument("--bundle", type=Path, required=True, help="verified local backup bundle")
    parser.add_argument("--output", type=Path, required=True, help="new provider attestation JSON")
    args = parser.parse_args()

    if args.handoff.is_symlink():
        raise OperatorError("WORM handoff must be a regular, non-symlink file")
    safe_filename(args.handoff.name)
    handoff_parent = ensure_directory(args.handoff.parent)
    handoff_path = handoff_parent / args.handoff.name
    handoff, handoff_sha256 = read_verified_handoff(handoff_path)
    if handoff.get("schema") != HANDOFF_SCHEMA:
        raise OperatorError("Unsupported WORM handoff schema")

    bundle = ensure_directory(args.bundle)
    if args.output.is_symlink():
        raise OperatorError("Provider attestation output must not be a symlink")
    safe_filename(args.output.name)
    output_parent = ensure_directory(args.output.parent)
    output = output_parent / args.output.name
    if output_parent == bundle or bundle in output_parent.parents:
        raise OperatorError("Provider attestation must be outside the sealed backup bundle")

    core_checksum = checksum_path(output)
    anchor_path = anchor_record_path(output)
    anchor_checksum = checksum_path(anchor_path)
    pending_core = pending_core_path(output)
    protected_inputs = {
        handoff_path,
        handoff_path.with_name(handoff_path.name + ".sha256"),
    }
    if {output, core_checksum, anchor_path, anchor_checksum, pending_core} & protected_inputs:
        raise OperatorError("Provider attestation paths cannot replace the sealed handoff")

    core_exists = ensure_regular_or_absent(output, "Core provider attestation")
    core_checksum_exists = ensure_regular_or_absent(
        core_checksum,
        "Core provider attestation checksum",
    )
    anchor_exists = ensure_regular_or_absent(anchor_path, "Provider anchor record")
    anchor_checksum_exists = ensure_regular_or_absent(
        anchor_checksum,
        "Provider anchor record checksum",
    )
    pending_core_exists = ensure_regular_or_absent(
        pending_core,
        "Pending core provider attestation",
    )
    if (
        not core_exists
        and not pending_core_exists
        and (core_checksum_exists or anchor_exists or anchor_checksum_exists)
    ):
        raise OperatorError("Provider attestation output set is orphaned and cannot be recovered")

    manifest, verification = verify_bundle(bundle)
    objects = prepare_objects(handoff, bundle, manifest, verification)
    destination_prefix = str(handoff["destination_prefix"])
    for item in objects:
        validate_provider_destination_budget(destination_prefix, item.source_filename)
    preflight_anchor_filename = provider_anchor_filename(
        str(handoff["backup_id"]),
        "0" * 64,
    )
    validate_provider_destination_budget(destination_prefix, preflight_anchor_filename)
    required_retention = handoff["required_retention"]
    retention_until = parse_rfc3339(str(required_retention["retain_until"]))
    if retention_until <= datetime.now(UTC):
        raise OperatorError("Required WORM retention has expired before upload")
    legal_hold = bool(required_retention["legal_hold"])

    if core_exists and core_checksum_exists and anchor_exists and anchor_checksum_exists:
        core, core_payload, core_sha256 = verify_document_pair(
            output,
            "worm-provider-attestation-v1.schema.json",
            "WORM provider attestation",
        )
        verify_core_input_binding(core, handoff, handoff_sha256, objects)
        anchor, anchor_payload, _ = verify_document_pair(
            anchor_path,
            "worm-provider-attestation-anchor-v1.schema.json",
            "WORM provider attestation anchor",
        )
        verify_anchor_relationship(output, core, core_payload, core_sha256, anchor)
        result = verify_anchored_attestation(output)
        _, final_handoff_sha256 = read_verified_handoff(handoff_path)
        if final_handoff_sha256 != handoff_sha256:
            raise OperatorError("Sealed WORM handoff changed during verification")
        completed_pending_anchor = pending_anchor_path(output, core_sha256)
        if completed_pending_anchor in protected_inputs:
            raise OperatorError("Pending provider anchor path collides with the sealed handoff")
        remove_verified_pending(
            pending_core,
            core_payload,
            "worm-provider-attestation-v1.schema.json",
            "pending core provider attestation",
        )
        remove_verified_pending(
            completed_pending_anchor,
            anchor_payload,
            "worm-provider-attestation-anchor-v1.schema.json",
            "pending provider anchor record",
        )
        result.update(
            {
                "disposition": "reused_verified",
                "core_checksum": str(core_checksum),
                "anchor_checksum": str(anchor_checksum),
            }
        )
        print(json.dumps(result, sort_keys=True))
        return 0

    provider_run = run_provider(
        destination_prefix,
        retention_until,
        legal_hold,
        objects,
    )
    ensure_unique_provider_object_ids(provider_run.objects)
    if len(provider_run.objects) != len(objects):
        raise OperatorError("Provider attestation does not cover every handoff object")
    verify_provider_coverage(objects, provider_run.objects)

    if core_exists:
        core, core_payload, core_sha256 = read_canonical_document(
            output,
            "worm-provider-attestation-v1.schema.json",
            "WORM provider attestation",
        )
        if pending_core_exists:
            _, staged_payload, _ = read_canonical_document(
                pending_core,
                "worm-provider-attestation-v1.schema.json",
                "Pending WORM provider attestation",
            )
            if staged_payload != core_payload:
                raise OperatorError("Published and pending core attestations differ")
        core_source = output
    elif pending_core_exists:
        core, core_payload, core_sha256 = read_canonical_document(
            pending_core,
            "worm-provider-attestation-v1.schema.json",
            "Pending WORM provider attestation",
        )
        core_source = pending_core
    else:
        verified_at = utc_now()
        core = {
            "schema": ATTESTATION_SCHEMA,
            "created_at": verified_at,
            "status": "provider_verified_immutable",
            "backup_id": handoff["backup_id"],
            "handoff": {
                "schema": HANDOFF_SCHEMA,
                "sha256": handoff_sha256,
                "source_manifest_sha256": handoff["source_manifest_sha256"],
                "destination_prefix": handoff["destination_prefix"],
            },
            "required_retention": required_retention,
            "provider": provider_run.provider,
            "verifier": {
                "workload_identity": verifier_workload_identity(),
                "provider_principal": provider_run.verifier_principal,
                "verified_at": verified_at,
                "software": sorted(
                    {
                        "lians-worm-uploader/2",
                        dependency_version("jsonschema"),
                        *provider_run.software,
                    }
                ),
            },
            "objects": provider_run.objects,
        }
        validate_document(
            core,
            "worm-provider-attestation-v1.schema.json",
            "WORM provider attestation",
        )
        core_payload = canonical_json(core)
        core_sha256 = hashlib.sha256(core_payload).hexdigest()
        core, core_payload, core_sha256 = stage_document(
            pending_core,
            core_payload,
            "worm-provider-attestation-v1.schema.json",
            "Pending WORM provider attestation",
        )
        core_source = pending_core

    verify_optional_checksum(output, core_payload, "WORM provider attestation")
    assert_core_matches_provider_run(
        core,
        handoff,
        handoff_sha256,
        provider_run.provider,
        provider_run.objects,
    )
    core_sha256 = hashlib.sha256(core_payload).hexdigest()
    anchor_staging = pending_anchor_path(output, core_sha256)
    if anchor_staging in protected_inputs:
        raise OperatorError("Pending provider anchor path cannot replace the sealed handoff")
    anchor_staging_exists = ensure_regular_or_absent(
        anchor_staging,
        "Pending provider anchor record",
    )
    anchor_filename = provider_anchor_filename(str(core["backup_id"]), core_sha256)
    anchor_item = UploadObject(
        source_filename=anchor_filename,
        source_path=core_source,
        destination=f"{handoff['destination_prefix']}/{anchor_filename}",
        size_bytes=len(core_payload),
        sha256=core_sha256,
    )

    if anchor_exists:
        anchor, anchor_payload, _ = read_canonical_document(
            anchor_path,
            "worm-provider-attestation-anchor-v1.schema.json",
            "WORM provider attestation anchor",
        )
        if anchor_staging_exists:
            _, staged_anchor_payload, _ = read_canonical_document(
                anchor_staging,
                "worm-provider-attestation-anchor-v1.schema.json",
                "Pending WORM provider attestation anchor",
            )
            if staged_anchor_payload != anchor_payload:
                raise OperatorError("Published and pending provider anchor records differ")
    elif anchor_staging_exists:
        anchor, anchor_payload, _ = read_canonical_document(
            anchor_staging,
            "worm-provider-attestation-anchor-v1.schema.json",
            "Pending WORM provider attestation anchor",
        )
    else:
        anchor = None
        anchor_payload = b""

    if anchor is not None:
        verify_optional_checksum(
            anchor_path,
            anchor_payload,
            "WORM provider attestation anchor",
        )
        anchor_item = verify_anchor_relationship(
            output,
            core,
            core_payload,
            core_sha256,
            anchor,
            source_path=core_source,
        )
        anchor_run = verify_provider_object(
            destination_prefix,
            retention_until,
            legal_hold,
            anchor_item,
            str(anchor["immutable_anchor"]["provider_object_id"]),
        )
        if len(anchor_run.objects) != 1:
            raise OperatorError("Provider returned an invalid immutable anchor inventory")
        assert_same_provider_boundary(
            anchor["provider"],
            anchor_run.provider,
            label="Immutable anchor",
        )
        assert_object_reverified(
            anchor["immutable_anchor"],
            anchor_run.objects[0],
            label="Immutable provider anchor",
        )
    else:
        anchor_run = run_provider(
            destination_prefix,
            retention_until,
            legal_hold,
            [anchor_item],
        )
        ensure_unique_provider_object_ids(anchor_run.objects)
        if len(anchor_run.objects) != 1:
            raise OperatorError("Provider did not attest the immutable core anchor")
        verify_provider_coverage([anchor_item], anchor_run.objects)
        assert_same_provider_boundary(
            core["provider"],
            anchor_run.provider,
            label="Immutable anchor",
        )
        anchor_verified_at = utc_now()
        anchor = {
            "schema": ANCHOR_SCHEMA,
            "created_at": anchor_verified_at,
            "status": "provider_attestation_anchored_immutable",
            "backup_id": core["backup_id"],
            "core_attestation": {
                "schema": ATTESTATION_SCHEMA,
                "local_filename": output.name,
                "size_bytes": len(core_payload),
                "sha256": core_sha256,
                "handoff_sha256": handoff_sha256,
            },
            "provider": core["provider"],
            "immutable_anchor": anchor_run.objects[0],
            "cross_binding": {
                "algorithm": "sha256",
                "anchored_payload": "canonical_core_attestation_bytes",
                "core_attestation_sha256": core_sha256,
                "provider_boundary_sha256": provider_boundary_sha256(core["provider"]),
                "construction": (
                    "core_digest_and_provider_boundary_only_no_anchor_record_self_hash"
                ),
            },
            "verifier": {
                "workload_identity": verifier_workload_identity(),
                "provider_principal": anchor_run.verifier_principal,
                "verified_at": anchor_verified_at,
                "software": sorted(
                    {
                        "lians-worm-uploader/2",
                        dependency_version("jsonschema"),
                        *anchor_run.software,
                    }
                ),
            },
        }
        validate_document(
            anchor,
            "worm-provider-attestation-anchor-v1.schema.json",
            "WORM provider attestation anchor",
        )
        anchor_payload = canonical_json(anchor)
        anchor, anchor_payload, _ = stage_document(
            anchor_staging,
            anchor_payload,
            "worm-provider-attestation-anchor-v1.schema.json",
            "Pending WORM provider attestation anchor",
        )

    verify_anchor_relationship(
        output,
        core,
        core_payload,
        core_sha256,
        anchor,
        source_path=core_source,
    )
    _, final_handoff_sha256 = read_verified_handoff(handoff_path)
    if final_handoff_sha256 != handoff_sha256:
        raise OperatorError("Sealed WORM handoff changed during provider verification")
    core_checksum, core_disposition = publish_document_pair(
        output,
        core_payload,
        "WORM provider attestation",
    )
    anchor_checksum, anchor_disposition = publish_document_pair(
        anchor_path,
        anchor_payload,
        "WORM provider attestation anchor",
    )
    result = verify_anchored_attestation(output)
    remove_verified_pending(
        pending_core,
        core_payload,
        "worm-provider-attestation-v1.schema.json",
        "pending core provider attestation",
    )
    remove_verified_pending(
        anchor_staging,
        anchor_payload,
        "worm-provider-attestation-anchor-v1.schema.json",
        "pending provider anchor record",
    )
    result.update(
        {
            "core_checksum": str(core_checksum),
            "core_disposition": core_disposition,
            "anchor_checksum": str(anchor_checksum),
            "anchor_disposition": anchor_disposition,
            "provider_anchor_destination": anchor_item.destination,
        }
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
