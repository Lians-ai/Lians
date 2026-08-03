#!/usr/bin/env python3
"""Independent Decision Receipt v0.1 conformance and producer verifier.

This reference deliberately imports no Lians package code. It is suitable for
checking third-party output and for detecting accidental coupling between the
published fixtures and the server implementation.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

CANONICALIZATION_ID = "json-sort-keys-utf8-v1"
HASH_ALGORITHM = "sha-256"
SCRIPT_DIR = Path(__file__).resolve().parent
SPEC_ROOT = SCRIPT_DIR.parent
DEFAULT_MANIFEST = SCRIPT_DIR / "manifest.json"
DEFAULT_SCHEMA = SPEC_ROOT / "schema.json"


class ConformanceError(ValueError):
    """The suite, input document, or referenced material is malformed."""


class DependencyError(ConformanceError):
    """A verification dependency is unavailable."""


def _fail(message: str) -> NoReturn:
    raise ConformanceError(message)


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON member name: {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> NoReturn:
    _fail(f"non-finite JSON number is forbidden: {value}")


def load_json(path: Path) -> Any:
    """Load strict UTF-8 JSON, rejecting duplicate names and non-finite numbers."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConformanceError(f"cannot read {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConformanceError(f"{path} is not UTF-8: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (ConformanceError, json.JSONDecodeError) as exc:
        raise ConformanceError(f"invalid JSON in {path}: {exc}") from exc


def canonical_json_bytes(value: Any) -> bytes:
    """Return the exact json-sort-keys-utf8-v1 representation."""
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConformanceError(f"value cannot be canonicalized: {exc}") from exc
    return rendered.encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise ConformanceError(f"cannot read {path}: {exc}") from exc


def resolve_suite_path(spec_root: Path, relative: str) -> Path:
    """Resolve a manifest path without permitting drive, absolute, or parent escape."""
    logical = PurePosixPath(relative)
    if logical.is_absolute() or not logical.parts or any(part == ".." for part in logical.parts):
        _fail(f"unsafe suite-relative path: {relative!r}")
    if ":" in logical.parts[0]:
        _fail(f"drive-qualified suite path is forbidden: {relative!r}")
    candidate = spec_root.joinpath(*logical.parts).resolve()
    try:
        candidate.relative_to(spec_root.resolve())
    except ValueError:
        _fail(f"suite path escapes the specification root: {relative!r}")
    return candidate


def validate_schema(instance: Any, schema: Mapping[str, Any]) -> list[str]:
    """Return stable, path-qualified Draft 2020-12 validation errors."""
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from jsonschema.exceptions import SchemaError
    except ModuleNotFoundError as exc:
        raise ConformanceError(
            "jsonschema is required; install jsonschema>=4.18"
        ) from exc

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ConformanceError(f"invalid Draft 2020-12 schema: {exc.message}") from exc
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors: list[str] = []
    for error in sorted(
        validator.iter_errors(instance),
        key=lambda item: tuple(str(part) for part in item.path),
    ):
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        errors.append(f"{path or '/'}: {error.message}")
    return errors


def _decode_base64_exact(value: Any, *, length: int, label: str) -> bytes:
    if not isinstance(value, str) or value.strip() != value:
        _fail(f"{label} must be canonical base64 without surrounding whitespace")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConformanceError(f"{label} is not valid base64") from exc
    if len(raw) != length:
        _fail(f"{label} must decode to exactly {length} bytes")
    if base64.b64encode(raw).decode("ascii") != value:
        _fail(f"{label} must use canonical base64 encoding")
    return raw


def load_public_key(path: Path) -> bytes:
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise ConformanceError(f"cannot read public key {path}: {exc}") from exc
    if len(text) == 64:
        try:
            raw = bytes.fromhex(text)
        except ValueError:
            raw = b""
        if len(raw) == 32:
            return raw
    return _decode_base64_exact(text, length=32, label="trusted Ed25519 public key")


def _json_pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        _fail(f"mutation path must be an absolute JSON Pointer: {pointer!r}")
    parts = pointer[1:].split("/")
    decoded: list[str] = []
    for part in parts:
        index = 0
        while index < len(part):
            if part[index] == "~" and (index + 1 >= len(part) or part[index + 1] not in "01"):
                _fail(f"mutation path has an invalid JSON Pointer escape: {pointer!r}")
            index += 2 if part[index] == "~" else 1
        decoded.append(part.replace("~1", "/").replace("~0", "~"))
    if decoded and decoded[0] == "integrity":
        _fail("conformance mutations may change protected payload fields only")
    return decoded


def apply_mutation(receipt: Mapping[str, Any], mutation: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the suite's restricted RFC 6902 replace operations."""
    mutated = copy.deepcopy(dict(receipt))
    operations = mutation.get("operations")
    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
        _fail("mutation.operations must be an array")
    for operation in operations:
        if not isinstance(operation, Mapping) or operation.get("op") != "replace":
            _fail("only RFC 6902 replace mutations are supported")
        pointer = operation.get("path")
        if not isinstance(pointer, str):
            _fail("mutation path must be a string")
        parts = _json_pointer_parts(pointer)
        if not parts:
            _fail("replacing the whole receipt is forbidden")
        target: Any = mutated
        for part in parts[:-1]:
            if isinstance(target, dict) and part in target:
                target = target[part]
            elif isinstance(target, list) and part.isdecimal() and int(part) < len(target):
                target = target[int(part)]
            else:
                _fail(f"mutation path does not exist: {pointer!r}")
        final = parts[-1]
        if isinstance(target, dict) and final in target:
            target[final] = copy.deepcopy(operation.get("value"))
        elif isinstance(target, list) and final.isdecimal() and int(final) < len(target):
            target[int(final)] = copy.deepcopy(operation.get("value"))
        else:
            _fail(f"mutation path does not exist: {pointer!r}")
    return mutated


def semantic_errors(receipt: Mapping[str, Any]) -> list[str]:
    """Check v0.1 cross-field and verified-record invariants not expressible in schema."""
    errors: list[str] = []
    decision = receipt.get("decision")
    if not isinstance(decision, Mapping):
        return ["decision must be an object"]
    decision_id = decision.get("id")
    if (
        not isinstance(decision_id, str)
        or not decision_id
        or receipt.get("receipt_id") != f"urn:lians:decision-receipt:{decision_id}"
    ):
        errors.append("receipt_id must bind exactly to decision.id")
    if receipt.get("issued_at") != decision.get("recorded_at"):
        errors.append("issued_at must match decision.recorded_at")
    record_hash = decision.get("record_hash")
    if (
        not isinstance(record_hash, str)
        or len(record_hash) != 64
        or any(character not in "0123456789abcdef" for character in record_hash)
    ):
        errors.append("decision.record_hash must be a lowercase SHA-256 digest")
    if decision.get("record_hash_version") != 2:
        errors.append("decision.record_hash_version must be 2")
    if decision.get("record_integrity_status") != "verified":
        errors.append("decision.record_integrity_status must be verified")

    actor = receipt.get("actor")
    if not isinstance(actor, Mapping):
        return errors + ["actor must be an object"]
    claimed = actor.get("claimed_agent_id")
    if not isinstance(claimed, str) or not claimed or actor.get("agent_id") != claimed:
        errors.append("actor.claimed_agent_id must match actor.agent_id")
    recorded_by = actor.get("recorded_by")
    principal = actor.get("principal")
    if not isinstance(recorded_by, Mapping):
        errors.append("actor.recorded_by must be an object")
    else:
        principal_ref = recorded_by.get("principal_ref")
        credential_ref = recorded_by.get("credential_ref")
        credential_digest = (
            credential_ref.rsplit(":", 1)[-1] if isinstance(credential_ref, str) else ""
        )
        if (
            not isinstance(principal_ref, str)
            or not principal_ref.startswith("lians:principal:v1:")
            or principal_ref == "lians:principal:v1:legacy-unverified"
        ):
            errors.append("actor.recorded_by requires an authenticated canonical principal")
        if (
            not isinstance(credential_ref, str)
            or not credential_ref.startswith("lians:credential:v1:sha256:")
            or len(credential_digest) != 64
            or any(character not in "0123456789abcdef" for character in credential_digest)
        ):
            errors.append("actor.recorded_by requires a canonical credential reference")
        if (
            not isinstance(principal, Mapping)
            or principal.get("id") != principal_ref
            or principal.get("auth_method") != recorded_by.get("auth_method")
            or principal.get("credential_ref") != credential_ref
        ):
            errors.append("actor.principal must match actor.recorded_by")
    return errors


def verify_receipt(
    receipt: Any,
    *,
    schema: Mapping[str, Any],
    trusted_public_key: bytes | None = None,
    trusted_key_id: str | None = None,
    require_signature: bool = False,
) -> dict[str, Any]:
    """Verify schema, protected digest, optional Ed25519 signature, and trust pin."""
    schema_errors = validate_schema(receipt, schema)
    errors = [f"schema: {error}" for error in schema_errors]
    if not isinstance(receipt, Mapping):
        return {
            "valid": False,
            "schema_valid": False,
            "semantic_valid": False,
            "hash_valid": False,
            "signature_present": False,
            "signature_valid": False,
            "trusted_key": None if trusted_public_key is None else False,
            "declared_receipt_hash": None,
            "computed_protected_sha256": None,
            "canonical_protected_utf8_length": None,
            "errors": errors + ["receipt must be a JSON object"],
        }

    semantics = semantic_errors(receipt)
    errors.extend(f"semantic: {error}" for error in semantics)

    protected = {key: value for key, value in receipt.items() if key != "integrity"}
    canonical = canonical_json_bytes(protected)
    computed_hash = sha256_bytes(canonical)
    integrity = receipt.get("integrity")
    integrity_mapping = isinstance(integrity, Mapping)
    declared_hash = integrity.get("receipt_hash") if integrity_mapping else None
    algorithm_valid = bool(
        integrity_mapping
        and integrity.get("hash_algorithm") == HASH_ALGORITHM
        and integrity.get("canonicalization") == CANONICALIZATION_ID
    )
    if not algorithm_valid:
        errors.append("unsupported integrity hash or canonicalization algorithm")
    hash_valid = algorithm_valid and declared_hash == computed_hash
    if not hash_valid:
        errors.append("receipt_hash does not match the protected payload")

    signature = integrity.get("signature") if integrity_mapping else None
    signature_present = isinstance(signature, Mapping)
    signature_valid = False
    embedded_public_key: bytes | None = None
    embedded_key_id: str | None = None
    if signature is not None and not signature_present:
        errors.append("integrity.signature must be an object or null")
    elif signature_present:
        try:
            if signature.get("algorithm") != "ed25519":
                _fail("signature algorithm must be ed25519")
            issuer = receipt.get("issuer")
            issuer_key_id = issuer.get("key_id") if isinstance(issuer, Mapping) else None
            if not isinstance(signature.get("key_id"), str) or not signature.get("key_id"):
                _fail("signature key_id must be a non-empty string")
            embedded_key_id = signature["key_id"]
            if signature.get("key_id") != issuer_key_id:
                _fail("signature key_id must match issuer.key_id")
            embedded_public_key = _decode_base64_exact(
                signature.get("public_key"),
                length=32,
                label="embedded Ed25519 public key",
            )
            signature_bytes = _decode_base64_exact(
                signature.get("value"),
                length=64,
                label="Ed25519 signature",
            )
            try:
                from cryptography.exceptions import InvalidSignature
                from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                    Ed25519PublicKey,
                )
            except ModuleNotFoundError as exc:
                raise DependencyError(
                    "cryptography is required for signed receipts; install cryptography>=42"
                ) from exc
            try:
                Ed25519PublicKey.from_public_bytes(embedded_public_key).verify(
                    signature_bytes,
                    bytes.fromhex(computed_hash),
                )
            except (InvalidSignature, ValueError) as exc:
                raise ConformanceError("Ed25519 signature verification failed") from exc
            signature_valid = True
        except DependencyError:
            raise
        except ConformanceError as exc:
            errors.append(str(exc))

    if require_signature and not signature_present:
        errors.append("a signature is required but the receipt is unsigned")
    trusted_key: bool | None = None
    if trusted_public_key is not None:
        trusted_key = bool(
            embedded_public_key == trusted_public_key
            and (trusted_key_id is None or embedded_key_id == trusted_key_id)
        )
        if not trusted_key:
            errors.append("signature key identity does not match the trusted key")

    valid = bool(
        not errors
        and not schema_errors
        and hash_valid
        and (not signature_present or signature_valid)
        and (not require_signature or signature_valid)
        and (trusted_public_key is None or trusted_key)
    )
    return {
        "valid": valid,
        "schema_valid": not schema_errors,
        "semantic_valid": not semantics,
        "hash_valid": hash_valid,
        "signature_present": signature_present,
        "signature_valid": signature_valid,
        "trusted_key": trusted_key,
        "declared_receipt_hash": declared_hash,
        "computed_protected_sha256": computed_hash,
        "canonical_protected_utf8_length": len(canonical),
        "errors": errors,
    }


def run_suite(manifest_path: Path) -> dict[str, Any]:
    """Run every language-neutral manifest case through the reference verifier."""
    manifest = load_json(manifest_path)
    if not isinstance(manifest, Mapping):
        _fail("conformance manifest must be a JSON object")
    manifest_schema = load_json(manifest_path.parent / "manifest.schema.json")
    if not isinstance(manifest_schema, Mapping):
        _fail("manifest schema must be a JSON object")
    manifest_errors = validate_schema(manifest, manifest_schema)
    if manifest_errors:
        _fail("manifest validation failed: " + "; ".join(manifest_errors))

    spec_root = manifest_path.resolve().parent.parent
    schema_path = resolve_suite_path(spec_root, str(manifest["receipt_schema"]))
    observed_schema_hash = file_sha256(schema_path)
    if observed_schema_hash != manifest["receipt_schema_sha256"]:
        _fail("receipt schema file hash does not match the conformance manifest")
    receipt_schema = load_json(schema_path)
    if not isinstance(receipt_schema, Mapping):
        _fail("receipt schema must be a JSON object")

    probe = manifest["canonicalization"]["probe"]
    probe_bytes = canonical_json_bytes(probe["input"])
    if (
        probe_bytes.hex() != probe["expected_utf8_hex"]
        or len(probe_bytes) != probe["expected_utf8_length"]
        or sha256_bytes(probe_bytes) != probe["expected_sha256"]
    ):
        _fail("runtime does not reproduce the manifest canonicalization probe")

    trust_material: dict[str, bytes] = {}
    for trust_id, definition in manifest["trust_material"].items():
        key_path = resolve_suite_path(spec_root, definition["path"])
        key = load_public_key(key_path)
        if sha256_bytes(key) != definition["raw_key_sha256"]:
            _fail(f"trust material {trust_id!r} does not match its raw-key digest")
        trust_material[trust_id] = key

    mutation_schema_path = manifest_path.parent / "mutation.schema.json"
    mutation_schema = load_json(mutation_schema_path)
    if not isinstance(mutation_schema, Mapping):
        _fail("mutation schema must be a JSON object")

    case_ids: set[str] = set()
    case_reports: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        case_id = case["id"]
        if case_id in case_ids:
            _fail(f"duplicate conformance case id: {case_id!r}")
        case_ids.add(case_id)

        receipt_path = resolve_suite_path(spec_root, case["receipt_path"])
        if file_sha256(receipt_path) != case["receipt_file_sha256"]:
            _fail(f"fixture bytes changed for case {case_id!r}")
        receipt = load_json(receipt_path)
        if not isinstance(receipt, Mapping):
            _fail(f"receipt fixture for {case_id!r} must be a JSON object")

        mutation_path_value = case["mutation_path"]
        mutation_hash_value = case["mutation_file_sha256"]
        if (mutation_path_value is None) != (mutation_hash_value is None):
            _fail(f"case {case_id!r} must set both mutation fields or neither")
        if mutation_path_value is not None:
            mutation_path = resolve_suite_path(spec_root, mutation_path_value)
            if file_sha256(mutation_path) != mutation_hash_value:
                _fail(f"mutation bytes changed for case {case_id!r}")
            mutation = load_json(mutation_path)
            mutation_errors = validate_schema(mutation, mutation_schema)
            if mutation_errors:
                _fail(
                    f"mutation validation failed for {case_id!r}: "
                    + "; ".join(mutation_errors)
                )
            if not isinstance(mutation, Mapping):
                _fail(f"mutation for {case_id!r} must be a JSON object")
            receipt = apply_mutation(receipt, mutation)

        trust_id = case["trusted_key"]
        if trust_id is not None and trust_id not in trust_material:
            _fail(f"case {case_id!r} references unknown trust material {trust_id!r}")
        report = verify_receipt(
            receipt,
            schema=receipt_schema,
            trusted_public_key=trust_material.get(trust_id),
            trusted_key_id=(
                manifest["trust_material"][trust_id]["key_id"]
                if trust_id is not None
                else None
            ),
            require_signature=case["require_signature"],
        )
        mismatches = {
            key: {"expected": expected, "observed": report.get(key)}
            for key, expected in case["expected"].items()
            if report.get(key) != expected
        }
        case_reports.append(
            {
                "id": case_id,
                "passed": not mismatches,
                "mismatches": mismatches,
                "observed": report,
            }
        )

    return {
        "suite": manifest["suite"],
        "manifest_version": manifest["manifest_version"],
        "passed": all(case["passed"] for case in case_reports),
        "cases": case_reports,
    }


def _print_report(report: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False))
        return
    if "cases" in report:
        for case in report["cases"]:
            status = "PASS" if case["passed"] else "FAIL"
            print(f"{status} {case['id']}")
            for field, mismatch in case["mismatches"].items():
                print(
                    f"  {field}: expected {mismatch['expected']!r}, "
                    f"observed {mismatch['observed']!r}"
                )
        print("PASS suite" if report["passed"] else "FAIL suite")
        return
    print("VALID receipt" if report["valid"] else "INVALID receipt")
    print(f"computed protected SHA-256: {report['computed_protected_sha256']}")
    for error in report["errors"]:
        print(f"  - {error}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--suite",
        action="store_true",
        help="run the published fixture suite (the default when --receipt is absent)",
    )
    mode.add_argument("--receipt", type=Path, help="verify a third-party receipt JSON file")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="suite manifest path",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help="receipt schema path for --receipt mode",
    )
    parser.add_argument("--trusted-public-key-file", type=Path)
    parser.add_argument(
        "--trusted-key-id",
        help="optional independently pinned key ID paired with the trusted public key",
    )
    parser.add_argument("--require-signature", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.receipt is None:
            if (
                args.trusted_public_key_file is not None
                or args.trusted_key_id is not None
                or args.require_signature
            ):
                _fail("trust options apply to --receipt mode; suite cases define their own policy")
            report = run_suite(args.manifest.resolve())
        else:
            receipt = load_json(args.receipt.resolve())
            schema = load_json(args.schema.resolve())
            if not isinstance(schema, Mapping):
                _fail("receipt schema must be a JSON object")
            trusted_key = (
                load_public_key(args.trusted_public_key_file.resolve())
                if args.trusted_public_key_file is not None
                else None
            )
            if args.trusted_key_id is not None and trusted_key is None:
                _fail("--trusted-key-id requires --trusted-public-key-file")
            report = verify_receipt(
                receipt,
                schema=schema,
                trusted_public_key=trusted_key,
                trusted_key_id=args.trusted_key_id,
                require_signature=args.require_signature,
            )
    except ConformanceError as exc:
        error_report = {"passed": False, "error": str(exc)}
        if args.json:
            print(json.dumps(error_report, sort_keys=True, indent=2), file=sys.stderr)
        else:
            print(f"conformance error: {exc}", file=sys.stderr)
        return 2

    _print_report(report, as_json=args.json)
    return 0 if bool(report.get("passed", report.get("valid", False))) else 1


if __name__ == "__main__":
    raise SystemExit(main())
