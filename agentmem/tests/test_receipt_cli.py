"""Public Decision Receipt v0.1 schema and verifier CLI contract tests."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tomllib
from pathlib import Path

import pytest
from lians.decision_receipt import (
    RECEIPT_SCHEMA,
    build_decision_receipt,
    sha256_hex,
    verify_decision_receipt,
)
from lians.receipt_cli import main

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "specs" / "decision-receipt" / "v0.1" / "schema.json"
README_PATH = ROOT / "specs" / "decision-receipt" / "v0.1" / "README.md"
NOW = "2026-08-02T04:00:00+00:00"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _receipt(*, signing_private_key: str = "") -> dict:
    decision = {
        "id": "742be5c2-bb88-4bea-bc4f-a0569435e5d4",
        "namespace": "underwriting",
        "agent_id": "underwriter-1",
        "recorded_by_principal_ref": (
            "lians:principal:v1:api-key:742be5c2-bb88-4bea-bc4f-a0569435e5d4"
        ),
        "recorded_by_auth_method": "api_key",
        "recorded_by_credential_ref": (
            "lians:credential:v1:sha256:" + _hash("credential")
        ),
        "recorded_by_principal_type": "api_key",
        "recorded_by_role": "analyst",
        "recorded_by_scopes": ["read", "write"],
        "decision_type": "credit_application",
        "outcome": "manual_review",
        "reason_codes": ["DTI_NEAR_LIMIT"],
        "regime": "ECOA_REG_B",
        "subject_id": "applicant-42",
        "session_id": "session-42",
        "model_id": "credit-model",
        "model_version": "2026-08-01",
        "policy_version": "credit-policy-17",
        "decided_at": NOW,
        "recorded_at": NOW,
        "knowledge_as_of": NOW,
        "knowledge_recorded_as_of": NOW,
        "input_hash": _hash("normalized application"),
        "output_hash": _hash("manual_review"),
        "record_hash": _hash("decision record"),
        "record_hash_version": 3,
        "record_integrity_status": "verified",
        "human_review_status": "approved",
        "human_reviewer": "risk-reviewer@example.test",
        "human_reviewed_at": NOW,
        "supersedes_id": None,
        "metadata": {
            "model_provider": "example-provider",
            "system_instruction_hash": _hash("system instruction"),
            "configuration_hash": _hash("temperature=0"),
            "principal": {"id": "service:underwriter-1", "type": "service"},
            "authorization": {"decision": "allow", "scopes": ["credit:decide"]},
            "policy_evaluation": {"result": "allow", "rule": "dti-review"},
            "tools": [
                {
                    "name": "income-verification",
                    "definition_hash": _hash("income tool v2"),
                    "result_hash": _hash("verified income: 72000"),
                }
            ],
            "trace_id": "8b6b04692015a61078024e9de5d05e0d",
            "span_id": "70c2e2f65c47917c",
        },
    }
    memory = {
        "id": "05b50d49-72aa-4081-a43c-515ff786cf89",
        "source": "income-provider",
        "metadata": {"source_version": "2026-08-02"},
        "content": "Verified annual income is 72000",
        "content_hash": _hash("Verified annual income is 72000"),
        "valid_from": NOW,
        "valid_to": None,
        "ingestion_time": NOW,
        "erased_at": None,
    }
    return build_decision_receipt(
        decision=decision,
        knowledge_snapshot=[memory],
        cited_evidence=[memory],
        audit_chain={"status": "ok", "rows_checked": 4, "violations": []},
        signing_private_key=signing_private_key,
        signing_key_id="test-deployment-key",
    )


def _write_receipt(tmp_path: Path, receipt: dict) -> Path:
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def _run_json(*args: str) -> tuple[int, dict, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    status = main([*args, "--json"], stdout=stdout, stderr=stderr)
    return status, json.loads(stdout.getvalue()), stderr.getvalue()


def test_public_schema_readme_and_console_entry_point_exist():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == RECEIPT_SCHEMA
    assert "audit_chain" in schema["required"]
    assert README_PATH.read_text(encoding="utf-8").startswith("# Lians Decision Receipt v0.1")
    assert project["project"]["scripts"]["lians-receipt"] == "lians.receipt_cli:main"


def test_cli_verifies_an_unsigned_receipt(tmp_path):
    path = _write_receipt(tmp_path, _receipt())

    status, report, stderr = _run_json("verify", str(path))

    assert status == 0
    assert stderr == ""
    assert report["valid"] is True
    assert report["schema_valid"] is True
    assert report["hash_valid"] is True
    assert report["signature_present"] is False


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("decision", "outcome", "approved"),
        ("audit_chain", "status", "tampered"),
    ],
)
def test_cli_detects_protected_payload_tampering(
    tmp_path, section, field, replacement
):
    receipt = _receipt()
    receipt[section][field] = replacement
    path = _write_receipt(tmp_path, receipt)

    status, report, _ = _run_json("verify", str(path))

    assert status == 1
    assert report["valid"] is False
    assert report["hash_valid"] is False
    assert "receipt_hash does not match" in " ".join(report["errors"])


def test_cli_can_require_and_pin_an_ed25519_signature(tmp_path):
    cryptography = pytest.importorskip("cryptography.hazmat.primitives")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    receipt = _receipt(signing_private_key=base64.b64encode(private_raw).decode("ascii"))
    path = _write_receipt(tmp_path, receipt)

    status, report, _ = _run_json(
        "verify",
        str(path),
        "--require-signature",
        "--trusted-public-key",
        public_raw.hex(),
    )

    assert cryptography is not None
    assert status == 0
    assert report["valid"] is True
    assert report["signature_present"] is True
    assert report["signature_valid"] is True
    assert report["trusted_key"] is True


def test_cli_rejects_an_unsigned_receipt_when_signature_is_required(tmp_path):
    path = _write_receipt(tmp_path, _receipt())

    status, report, _ = _run_json("verify", str(path), "--require-signature")

    assert status == 1
    assert report["valid"] is False
    assert "signature is required" in " ".join(report["errors"])


def test_core_verifier_rejects_rehashed_unsupported_receipt_version():
    receipt = _receipt()
    receipt["receipt_version"] = "9.9"
    protected = {key: value for key, value in receipt.items() if key != "integrity"}
    receipt["integrity"]["receipt_hash"] = sha256_hex(protected)

    report = verify_decision_receipt(receipt)

    assert report["hash_valid"] is True
    assert report["valid"] is False
    assert "receipt_version must be '0.1'" in report["errors"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda receipt: receipt.__setitem__(
                "receipt_id", "urn:lians:decision-receipt:attacker-selected"
            ),
            "receipt_id must bind exactly to decision.id",
        ),
        (
            lambda receipt: receipt.__setitem__("issued_at", "2026-08-03T00:00:00+00:00"),
            "issued_at must match decision.recorded_at",
        ),
    ],
)
def test_core_verifier_rejects_rehashed_semantic_header_tampering(
    mutate,
    message: str,
):
    receipt = _receipt()
    mutate(receipt)
    protected = {key: value for key, value in receipt.items() if key != "integrity"}
    receipt["integrity"]["receipt_hash"] = sha256_hex(protected)

    report = verify_decision_receipt(receipt)

    assert report["hash_valid"] is True
    assert report["valid"] is False
    assert message in report["errors"]


def test_core_verifier_rejects_rehashed_v3_authorization_mismatch():
    receipt = _receipt()
    receipt["actor"]["recorded_by"]["scopes"] = ["admin", "read", "write"]
    receipt["actor"]["principal"]["scopes"] = ["admin", "read", "write"]
    protected = {key: value for key, value in receipt.items() if key != "integrity"}
    receipt["integrity"]["receipt_hash"] = sha256_hex(protected)

    report = verify_decision_receipt(receipt)

    assert report["hash_valid"] is True
    assert report["valid"] is False
    assert "authorization.recording_write" in " ".join(report["errors"])


def test_core_verifier_rejects_unprotected_integrity_metadata_mutation():
    receipt = _receipt()
    receipt["integrity"]["canonicalization"] = "attacker-selected-algorithm"

    report = verify_decision_receipt(receipt)

    assert report["hash_valid"] is True
    assert report["valid"] is False
    assert "integrity.canonicalization" in " ".join(report["errors"])


def test_core_verifier_binds_signature_key_id_to_protected_issuer():
    private_key = pytest.importorskip(
        "cryptography.hazmat.primitives.asymmetric.ed25519"
    ).Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization

    private_raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    receipt = _receipt(signing_private_key=base64.b64encode(private_raw).decode("ascii"))
    receipt["integrity"]["signature"]["key_id"] = "attacker-key-label"

    report = verify_decision_receipt(receipt, require_signature=True)

    assert report["hash_valid"] is True
    assert report["signature_valid"] is True
    assert report["valid"] is False
    assert "must match issuer.key_id" in " ".join(report["errors"])


def test_core_verifier_rejects_non_object_signature():
    receipt = _receipt()
    receipt["integrity"]["signature"] = "not-a-signature"

    report = verify_decision_receipt(receipt)

    assert report["valid"] is False
    assert "must be an object or null" in " ".join(report["errors"])


def test_cli_reports_invalid_json_as_an_input_error(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text("{not-json", encoding="utf-8")

    status, report, stderr = _run_json("verify", str(path))

    assert status == 2
    assert stderr == ""
    assert report["valid"] is False
    assert report["input_error"] is True
