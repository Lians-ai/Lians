"""Daemon-free checks for homelab signing key bootstrap and offline verification."""

from __future__ import annotations

import base64
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(LAB / "workload"))

from common import sha256_json
from env_bootstrap import (
    DEFAULT_EVIDENCE_SIGNING_KEY_ID,
    EnvironmentBootstrapError,
    bootstrap_environment,
)
from verify import CheckFailure, verify_evidence_pack_signature


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name] = value
    return values


def signed_pack(key_id: str = DEFAULT_EVIDENCE_SIGNING_KEY_ID) -> dict:
    manifest = {
        "schema": "https://lians.ai/schemas/evidence-pack/v2",
        "decision": {"id": "decision-1"},
    }
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = {
        "status": "signed",
        "algorithm": "Ed25519",
        "key_id": key_id,
        "public_key": base64.b64encode(public_key).decode("ascii"),
        "value": base64.b64encode(private_key.sign(canonical)).decode("ascii"),
    }
    pack_without_hash = {
        **manifest,
        "manifest_hash": sha256_json(manifest),
        "signature": signature,
    }
    return {**pack_without_hash, "pack_hash": sha256_json(pack_without_hash)}


class EnvironmentBootstrapContract(unittest.TestCase):
    def test_fresh_environment_gets_random_32_byte_signing_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / ".env"
            status = bootstrap_environment(LAB / ".env.example", target)
            values = parse_env(target)
            self.assertEqual(status, "created")
            self.assertEqual(
                len(base64.b64decode(values["LIANS_EVIDENCE_SIGNING_PRIVATE_KEY"])), 32
            )
            self.assertEqual(
                values["LIANS_EVIDENCE_SIGNING_KEY_ID"], DEFAULT_EVIDENCE_SIGNING_KEY_ID
            )
            self.assertEqual(len(bytes.fromhex(values["LIANS_ADMIN_SECRET"])), 32)
            self.assertEqual(len(base64.b64decode(values["LIANS_MASTER_ENCRYPTION_KEY"])), 32)

    def test_legacy_environment_is_upgraded_once_without_changing_existing_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / ".env"
            legacy = (
                "# preserve this file\n"
                "LIANS_ADMIN_SECRET=customer-admin\n"
                "LIANS_MASTER_ENCRYPTION_KEY=customer-master\n"
                "GRAFANA_ADMIN_PASSWORD=customer-grafana\n"
            )
            target.write_text(legacy, encoding="utf-8")
            self.assertEqual(bootstrap_environment(LAB / ".env.example", target), "upgraded")
            upgraded = target.read_text(encoding="utf-8")
            values = parse_env(target)
            self.assertTrue(upgraded.startswith(legacy))
            self.assertEqual(values["LIANS_ADMIN_SECRET"], "customer-admin")
            self.assertEqual(values["LIANS_MASTER_ENCRYPTION_KEY"], "customer-master")
            self.assertEqual(values["GRAFANA_ADMIN_PASSWORD"], "customer-grafana")
            self.assertEqual(
                len(base64.b64decode(values["LIANS_EVIDENCE_SIGNING_PRIVATE_KEY"])), 32
            )
            self.assertEqual(bootstrap_environment(LAB / ".env.example", target), "unchanged")
            self.assertEqual(target.read_text(encoding="utf-8"), upgraded)

    def test_existing_signing_identity_is_preserved_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / ".env"
            private_key = Ed25519PrivateKey.generate().private_bytes(
                Encoding.Raw, PrivateFormat.Raw, NoEncryption()
            )
            content = (
                f"LIANS_EVIDENCE_SIGNING_PRIVATE_KEY="
                f"{base64.b64encode(private_key).decode('ascii')}\n"
                "LIANS_EVIDENCE_SIGNING_KEY_ID=customer-homelab-key\n"
            )
            target.write_text(content, encoding="utf-8")
            self.assertEqual(bootstrap_environment(LAB / ".env.example", target), "unchanged")
            self.assertEqual(target.read_text(encoding="utf-8"), content)

    def test_invalid_existing_private_key_fails_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / ".env"
            content = (
                "LIANS_EVIDENCE_SIGNING_PRIVATE_KEY=not-a-private-key\n"
                "LIANS_EVIDENCE_SIGNING_KEY_ID=customer-homelab-key\n"
            )
            target.write_text(content, encoding="utf-8")
            with self.assertRaises(EnvironmentBootstrapError):
                bootstrap_environment(LAB / ".env.example", target)
            self.assertEqual(target.read_text(encoding="utf-8"), content)


class OfflineEvidenceVerificationContract(unittest.TestCase):
    def test_signed_pack_verifies_offline(self):
        detail = verify_evidence_pack_signature(
            signed_pack(), expected_key_id=DEFAULT_EVIDENCE_SIGNING_KEY_ID
        )
        self.assertTrue(detail["signature_valid"])
        self.assertEqual(detail["signature_algorithm"], "Ed25519")
        self.assertEqual(len(detail["signer_public_key_sha256"]), 64)

    def test_unsigned_pack_fails_closed(self):
        pack = signed_pack()
        pack["signature"] = {"status": "unsigned"}
        with self.assertRaisesRegex(CheckFailure, "not signed"):
            verify_evidence_pack_signature(
                pack, expected_key_id=DEFAULT_EVIDENCE_SIGNING_KEY_ID
            )

    def test_tampered_manifest_fails_signature_verification(self):
        pack = signed_pack()
        tampered = copy.deepcopy(pack)
        tampered["decision"]["id"] = "tampered"
        with self.assertRaisesRegex(CheckFailure, "signature is invalid"):
            verify_evidence_pack_signature(
                tampered, expected_key_id=DEFAULT_EVIDENCE_SIGNING_KEY_ID
            )

    def test_wrong_key_id_fails_closed(self):
        with self.assertRaisesRegex(CheckFailure, "key ID"):
            verify_evidence_pack_signature(signed_pack(), expected_key_id="other-key")


if __name__ == "__main__":
    unittest.main()
