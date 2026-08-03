#!/usr/bin/env python3
"""Create a provider-neutral, still-pending WORM handoff request.

The output is not an attestation.  A storage control plane must return object
versions/generations and locked retention metadata before an operator can mark a
copy immutable.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from backup_lib import (
    HANDOFF_SCHEMA,
    OperatorError,
    ensure_new_file,
    ensure_directory,
    safe_filename,
    sha256_file,
    utc_now,
    verify_bundle,
    write_new_bytes,
    write_new_json,
)


def parse_future(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OperatorError("--retention-until must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise OperatorError("--retention-until must include a timezone")
    if parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise OperatorError("--retention-until must be in the future")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--destination", required=True, help="s3://, gs://, or azure:// immutable prefix")
    parser.add_argument("--retention-until", required=True)
    parser.add_argument("--legal-hold", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    destination = urlparse(args.destination)
    if (
        destination.scheme not in {"s3", "gs", "azure"}
        or not destination.netloc
        or destination.username
        or destination.password
        or destination.query
        or destination.fragment
    ):
        raise OperatorError("Destination must be an s3://, gs://, or azure:// URI")
    if any(segment in {".", ".."} for segment in destination.path.split("/")):
        raise OperatorError("Destination prefix contains an unsafe dot segment")
    safe_filename(args.output.name)
    ensure_directory(args.output.parent)
    retention_until = parse_future(args.retention_until)
    manifest, verification = verify_bundle(args.bundle)
    bundle = args.bundle.resolve()
    output_resolved = args.output.resolve(strict=False)
    if output_resolved.parent == bundle or bundle in output_resolved.parents:
        raise OperatorError("WORM handoff output must be outside the sealed backup bundle")
    checksum_path = args.output.with_name(args.output.name + ".sha256")
    ensure_new_file(args.output)
    ensure_new_file(checksum_path)
    objects = []
    for name in [item["filename"] for item in verification["artifacts"]] + [
        "manifest.json",
        "SHA256SUMS",
    ]:
        path = bundle / name
        objects.append(
            {
                "source_filename": name,
                "destination": args.destination.rstrip("/") + "/" + manifest["backup_id"] + "/" + name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    handoff = {
        "schema": HANDOFF_SCHEMA,
        "created_at": utc_now(),
        "backup_id": manifest["backup_id"],
        "source_manifest_sha256": verification["manifest_sha256"],
        "destination_prefix": args.destination.rstrip("/") + "/" + manifest["backup_id"],
        "required_retention": {
            "mode": "compliance_or_provider_equivalent_locked_policy",
            "retain_until": retention_until,
            "legal_hold": args.legal_hold,
        },
        "status": "pending_provider_attestation",
        "objects": objects,
        "required_provider_attestation": [
            "provider and account/project/tenant identity",
            "bucket/container immutable-policy revision",
            "object version ID or generation for every object",
            "provider-reported checksum for every object",
            "effective retain-until timestamp for every object",
            "effective retention mode and legal-hold state",
            "verification timestamp and verifier workload identity",
        ],
    }
    write_new_json(args.output, handoff)
    write_new_bytes(checksum_path, f"{sha256_file(args.output)}  {args.output.name}\n".encode("utf-8"))
    print(
        json.dumps(
            {
                "status": "pending_provider_attestation",
                "handoff": str(args.output.resolve()),
                "checksum": str(checksum_path.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
