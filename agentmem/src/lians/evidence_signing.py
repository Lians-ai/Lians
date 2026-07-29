"""Portable Ed25519 signing and offline verification for Evidence Pack v2."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from .config import get_settings
from .decision_evidence import canonical_json, canonical_sha256

PACK_CONTROL_FIELDS = frozenset({"manifest_hash", "signature", "pack_hash"})


def _decode_raw_key(value: str, *, expected_length: int, label: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError(f"{label} must be valid base64") from exc
    if len(raw) != expected_length:
        raise ValueError(f"{label} must decode to {expected_length} bytes")
    return raw


def evidence_manifest(pack: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical signed portion of a pack."""
    return {key: value for key, value in pack.items() if key not in PACK_CONTROL_FIELDS}


def sign_evidence_manifest(
    manifest: dict[str, Any],
    *,
    private_key_b64: str | None = None,
    key_id: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    configured_key = (
        settings.evidence_signing_private_key
        if private_key_b64 is None
        else private_key_b64
    )
    manifest_bytes = canonical_json(manifest).encode()
    manifest_hash = canonical_sha256(manifest)
    if not configured_key:
        signature = {
            "status": "unsigned",
            "algorithm": None,
            "key_id": None,
            "public_key": None,
            "value": None,
            "identity_note": (
                "No evidence signing key is configured. Hash integrity can be "
                "checked, but signer identity cannot be verified."
            ),
        }
    else:
        raw_private = _decode_raw_key(
            configured_key,
            expected_length=32,
            label="Evidence signing private key",
        )
        private_key = Ed25519PrivateKey.from_private_bytes(raw_private)
        public_key = private_key.public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        signature = {
            "status": "signed",
            "algorithm": "Ed25519",
            "key_id": key_id or settings.evidence_signing_key_id,
            "public_key": base64.b64encode(public_key).decode(),
            "value": base64.b64encode(private_key.sign(manifest_bytes)).decode(),
            "identity_note": (
                "Trust signer identity only when public_key matches an independently "
                "configured trusted key. key_id is a routing label, not identity proof."
            ),
        }
    pack_without_hash = {
        **manifest,
        "manifest_hash": manifest_hash,
        "signature": signature,
    }
    return {
        **pack_without_hash,
        "pack_hash": canonical_sha256(pack_without_hash),
    }


def verify_evidence_pack(
    pack: dict[str, Any],
    *,
    trusted_public_key_b64: str | None = None,
    expected_key_id: str | None = None,
    allow_unsigned: bool = False,
) -> dict[str, Any]:
    manifest = evidence_manifest(pack)
    expected_manifest_hash = canonical_sha256(manifest)
    hash_valid = pack.get("manifest_hash") == expected_manifest_hash
    signature = dict(pack.get("signature") or {})
    status = signature.get("status")
    signature_valid = False
    identity_verified = False
    key_id_matched: bool | None = None
    errors: list[str] = []
    if not hash_valid:
        errors.append("manifest_hash does not match the canonical manifest")
    expected_pack_hash = canonical_sha256(
        {
            **manifest,
            "manifest_hash": pack.get("manifest_hash"),
            "signature": signature,
        }
    )
    pack_hash_valid = pack.get("pack_hash") == expected_pack_hash
    if not pack_hash_valid:
        errors.append("pack_hash does not match the pack contents")
    if status == "signed":
        if signature.get("algorithm") != "Ed25519":
            errors.append("unsupported signature algorithm")
        else:
            try:
                embedded_public = _decode_raw_key(
                    str(signature.get("public_key") or ""),
                    expected_length=32,
                    label="Embedded public key",
                )
                signature_bytes = base64.b64decode(
                    str(signature.get("value") or ""),
                    validate=True,
                )
                Ed25519PublicKey.from_public_bytes(embedded_public).verify(
                    signature_bytes,
                    canonical_json(manifest).encode(),
                )
                signature_valid = True
            except (ValueError, InvalidSignature):
                errors.append("Ed25519 signature is invalid")
        if expected_key_id is not None:
            key_id_matched = signature.get("key_id") == expected_key_id
            if not key_id_matched:
                errors.append("signature key_id does not match the expected key_id")
        if trusted_public_key_b64 is not None:
            try:
                trusted_public = _decode_raw_key(
                    trusted_public_key_b64,
                    expected_length=32,
                    label="Trusted public key",
                )
                identity_verified = (
                    signature_valid
                    and base64.b64encode(trusted_public).decode()
                    == signature.get("public_key")
                )
                if not identity_verified:
                    errors.append("embedded key does not match the trusted public key")
            except ValueError as exc:
                errors.append(str(exc))
    elif status == "unsigned":
        if not allow_unsigned:
            errors.append("evidence pack is unsigned")
    else:
        errors.append("signature status is missing or invalid")
    accepted = (
        hash_valid
        and pack_hash_valid
        and (
            signature_valid
            if status == "signed"
            else bool(allow_unsigned and status == "unsigned")
        )
        and (identity_verified if trusted_public_key_b64 is not None else True)
        and (key_id_matched if expected_key_id is not None else True)
    )
    return {
        "accepted": accepted,
        "manifest_hash_valid": hash_valid,
        "pack_hash_valid": pack_hash_valid,
        "signature_status": status,
        "signature_valid": signature_valid,
        "identity_verified": identity_verified,
        "key_id_matched": key_id_matched,
        "key_id": signature.get("key_id"),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a Lians Evidence Pack v2 without contacting Lians."
    )
    parser.add_argument("pack", type=Path)
    parser.add_argument("--trusted-public-key", type=Path)
    parser.add_argument("--expected-key-id")
    parser.add_argument("--allow-unsigned", action="store_true")
    args = parser.parse_args()
    pack = json.loads(args.pack.read_text(encoding="utf-8"))
    trusted = (
        args.trusted_public_key.read_text(encoding="utf-8").strip()
        if args.trusted_public_key
        else None
    )
    result = verify_evidence_pack(
        pack,
        trusted_public_key_b64=trusted,
        expected_key_id=args.expected_key_id,
        allow_unsigned=args.allow_unsigned,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["accepted"] else 1)


if __name__ == "__main__":
    main()
