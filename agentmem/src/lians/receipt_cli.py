"""Command-line verification for portable Lians Decision Receipts.

The CLI intentionally uses only the Python standard library for argument,
file, and JSON handling. Unsigned receipts can therefore be verified without
extra verifier dependencies. Ed25519 verification uses ``cryptography`` only
when a receipt contains a signature.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from .decision_receipt import (
    RECEIPT_SCHEMA,
    RECEIPT_VERSION,
    verify_decision_receipt,
)

_REQUIRED_FIELDS = {
    "$schema",
    "receipt_version",
    "receipt_id",
    "issued_at",
    "issuer",
    "decision",
    "actor",
    "model",
    "artifacts",
    "tools",
    "sources",
    "policy",
    "authorization",
    "human_review",
    "correlation",
    "reconstruction",
    "audit_chain",
    "completeness",
    "integrity",
}


class ReceiptInputError(ValueError):
    """Raised when a receipt or trusted-key input cannot be read."""


def _read_text(path: str, *, stdin: TextIO) -> str:
    if path == "-":
        return stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReceiptInputError(f"Could not read {path!r}: {exc}") from exc


def _read_receipt(path: str, *, stdin: TextIO) -> dict[str, Any]:
    try:
        value = json.loads(_read_text(path, stdin=stdin))
    except json.JSONDecodeError as exc:
        raise ReceiptInputError(
            f"Receipt is not valid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise ReceiptInputError("Receipt JSON must contain a top-level object")
    return value


def _read_public_key(value: str | None, key_file: str | None) -> str | None:
    if value is not None:
        key = value.strip()
    elif key_file is not None:
        try:
            key = Path(key_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise ReceiptInputError(
                f"Could not read trusted public key {key_file!r}: {exc}"
            ) from exc
    else:
        return None
    if not key:
        raise ReceiptInputError("Trusted public key must not be empty")
    return key


def _contract_errors(receipt: Mapping[str, Any]) -> list[str]:
    """Perform the dependency-free envelope checks mirrored by the JSON Schema."""
    errors: list[str] = []
    missing = sorted(_REQUIRED_FIELDS - set(receipt))
    if missing:
        errors.append("Missing v0.1 fields: " + ", ".join(missing))

    unknown = sorted(set(receipt) - _REQUIRED_FIELDS)
    if unknown:
        errors.append("Unknown v0.1 fields: " + ", ".join(unknown))

    if receipt.get("$schema") != RECEIPT_SCHEMA:
        errors.append(f"$schema must be {RECEIPT_SCHEMA!r}")
    if receipt.get("receipt_version") != RECEIPT_VERSION:
        errors.append(f"receipt_version must be {RECEIPT_VERSION!r}")

    receipt_id = receipt.get("receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id.startswith(
        "urn:lians:decision-receipt:"
    ):
        errors.append("receipt_id must be a Lians Decision Receipt URN")
    if not isinstance(receipt.get("issued_at"), str) or not receipt.get("issued_at"):
        errors.append("issued_at must be a non-empty date-time string")

    for name in (
        "issuer",
        "decision",
        "actor",
        "model",
        "artifacts",
        "policy",
        "human_review",
        "correlation",
        "reconstruction",
        "audit_chain",
        "completeness",
        "integrity",
    ):
        if name in receipt and not isinstance(receipt[name], Mapping):
            errors.append(f"{name} must be an object")
    for name in ("tools", "sources"):
        if name in receipt and not isinstance(receipt[name], list):
            errors.append(f"{name} must be an array")

    audit_chain = receipt.get("audit_chain")
    if isinstance(audit_chain, Mapping) and not isinstance(audit_chain.get("status"), str):
        errors.append("audit_chain.status must be a string")

    integrity = receipt.get("integrity")
    if isinstance(integrity, Mapping):
        if integrity.get("hash_algorithm") != "sha-256":
            errors.append("integrity.hash_algorithm must be 'sha-256'")
        if integrity.get("canonicalization") != "json-sort-keys-utf8-v1":
            errors.append(
                "integrity.canonicalization must be 'json-sort-keys-utf8-v1'"
            )
        signature = integrity.get("signature")
        if signature is not None and not isinstance(signature, Mapping):
            errors.append("integrity.signature must be an object or null")
        elif isinstance(signature, Mapping) and signature.get("algorithm") != "ed25519":
            errors.append("integrity.signature.algorithm must be 'ed25519'")

    return errors


def verify_receipt(
    receipt: Mapping[str, Any],
    *,
    trusted_public_key: str | None = None,
    require_signature: bool = False,
) -> dict[str, Any]:
    """Verify the v0.1 contract plus its digest and optional signature."""
    contract_errors = _contract_errors(receipt)
    result = verify_decision_receipt(
        receipt,
        trusted_public_key=trusted_public_key,
        require_signature=require_signature,
    )
    result["schema_valid"] = not contract_errors
    result["errors"] = list(
        dict.fromkeys(contract_errors + list(result.get("errors") or []))
    )
    result["valid"] = bool(result.get("valid") and not contract_errors)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lians-receipt",
        description="Verify a Lians Decision Receipt v0.1.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    verify = subcommands.add_parser(
        "verify", help="verify the receipt schema contract, hash, and optional signature"
    )
    verify.add_argument("receipt", help="receipt JSON path, or '-' to read standard input")
    verify.add_argument(
        "--require-signature",
        action="store_true",
        help="reject receipts that do not contain a valid Ed25519 signature",
    )
    trusted = verify.add_mutually_exclusive_group()
    trusted.add_argument(
        "--trusted-public-key",
        metavar="KEY",
        help="trusted raw Ed25519 public key encoded as base64 or hexadecimal",
    )
    trusted.add_argument(
        "--trusted-public-key-file",
        metavar="PATH",
        help="file containing a trusted base64 or hexadecimal Ed25519 public key",
    )
    verify.add_argument(
        "--json",
        action="store_true",
        help="write the complete machine-readable verification report",
    )
    return parser


def _write_report(report: Mapping[str, Any], *, as_json: bool, stdout: TextIO) -> None:
    if as_json:
        json.dump(report, stdout, indent=2, sort_keys=True)
        stdout.write("\n")
        return

    label = "VALID" if report.get("valid") else "INVALID"
    stdout.write(f"{label} Lians Decision Receipt v{RECEIPT_VERSION}\n")
    if report.get("receipt_hash"):
        stdout.write(f"receipt_hash: {report['receipt_hash']}\n")
    if report.get("signature_present"):
        signature_label = "valid" if report.get("signature_valid") else "invalid"
    else:
        signature_label = "absent"
    stdout.write(f"signature: {signature_label}\n")
    stdout.writelines(f"error: {error}\n" for error in report.get("errors") or [])


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the receipt verifier and return a process-compatible exit status."""
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    args = _parser().parse_args(argv)

    try:
        receipt = _read_receipt(args.receipt, stdin=input_stream)
        trusted_key = _read_public_key(
            args.trusted_public_key, args.trusted_public_key_file
        )
    except ReceiptInputError as exc:
        if args.json:
            _write_report(
                {"valid": False, "input_error": True, "errors": [str(exc)]},
                as_json=True,
                stdout=output_stream,
            )
        else:
            error_stream.write(f"lians-receipt: {exc}\n")
        return 2

    report = verify_receipt(
        receipt,
        trusted_public_key=trusted_key,
        require_signature=args.require_signature,
    )
    _write_report(report, as_json=args.json, stdout=output_stream)
    return 0 if report["valid"] else 1


if __name__ == "__main__":  # pragma: no cover - exercised by the console script
    raise SystemExit(main())
