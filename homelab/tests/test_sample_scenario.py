"""Fail-closed tests for the customer-run local sample boundary."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "workload"))

from scenario import (
    DEIDENTIFIED_ACK,
    SCHEMA,
    SampleValidationError,
    load_scenario,
    resolve_launch_sample,
)
from verify import CheckFailure, verify_mounted_sample_manifest


class SampleScenarioContract(unittest.TestCase):
    def setUp(self) -> None:
        self.default_path = LAB / "samples/default.json"
        self.default = json.loads(self.default_path.read_text(encoding="utf-8"))

    def write_sample(self, payload: dict) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "sample.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return temporary, path

    def test_default_sample_is_valid_and_report_is_value_free(self):
        loaded = load_scenario(self.default_path)
        self.assertEqual(loaded.manifest["schema"], SCHEMA)
        self.assertEqual(loaded.manifest["classification"], "synthetic")
        self.assertEqual(loaded.manifest["memory_count"], 2)
        rendered = json.dumps(loaded.manifest)
        self.assertNotIn(self.default["query"], rendered)
        self.assertNotIn(self.default["expected_marker"], rendered)
        self.assertNotIn(self.default["subject_id"], rendered)
        self.assertNotIn(self.default["memories"][0]["content"], rendered)

    def test_deidentified_sample_requires_explicit_acknowledgement(self):
        payload = copy.deepcopy(self.default)
        payload["classification"] = "deidentified"
        payload["subject_id"] = "DEIDENTIFIED-SUBJECT-001"
        temporary, path = self.write_sample(payload)
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(SampleValidationError, "explicit local data-policy"):
            load_scenario(path)
        loaded = load_scenario(path, acknowledgement=DEIDENTIFIED_ACK)
        self.assertEqual(loaded.manifest["classification"], "deidentified")

    def test_email_and_secret_shaped_values_are_rejected(self):
        for value in (
            "person@example.com",
            "sk_live_abcdefghijklmnop",
            "212-555-1212",
            "4242 4242 4242 4242",
        ):
            with self.subTest(value=value):
                payload = copy.deepcopy(self.default)
                payload["memories"][0]["content"] = value
                temporary, path = self.write_sample(payload)
                self.addCleanup(temporary.cleanup)
                with self.assertRaises(SampleValidationError):
                    load_scenario(path)

    def test_secret_shaped_identifiers_fail_without_reflection(self):
        secret = "sk_live_abcdefghijklmnop"
        mutations = (
            lambda payload: payload.__setitem__("scenario_id", secret),
            lambda payload: payload.__setitem__("decision_type", secret),
            lambda payload: payload.__setitem__("agent_id", secret),
            lambda payload: payload.__setitem__("subject_id", f"SYNTHETIC-{secret}"),
            lambda payload: payload["reason_codes"].__setitem__(0, secret),
            lambda payload: payload["memories"][0].__setitem__("idempotency_key", secret),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                payload = copy.deepcopy(self.default)
                mutate(payload)
                temporary, path = self.write_sample(payload)
                self.addCleanup(temporary.cleanup)
                with self.assertRaises(SampleValidationError) as raised:
                    load_scenario(path)
                self.assertNotIn(secret, str(raised.exception))

    def test_hostile_field_names_are_not_reflected(self):
        secret = "sk_live_abcdefghijklmnop"
        payload = copy.deepcopy(self.default)
        payload[secret] = True
        temporary, path = self.write_sample(payload)
        self.addCleanup(temporary.cleanup)
        with self.assertRaises(SampleValidationError) as raised:
            load_scenario(path)
        self.assertNotIn(secret, str(raised.exception))

        duplicate = f'{{"{secret}":1,"{secret}":2}}'
        duplicate_path = Path(temporary.name) / "duplicate.json"
        duplicate_path.write_text(duplicate, encoding="utf-8")
        with self.assertRaises(SampleValidationError) as duplicate_raised:
            load_scenario(duplicate_path)
        self.assertNotIn(secret, str(duplicate_raised.exception))

    def test_non_finite_metadata_numbers_are_rejected(self):
        payload = copy.deepcopy(self.default)
        payload["memories"][0]["metadata"]["score"] = "INFINITY_PLACEHOLDER"
        temporary, path = self.write_sample(payload)
        self.addCleanup(temporary.cleanup)
        raw = path.read_text(encoding="utf-8").replace('"INFINITY_PLACEHOLDER"', "1e309")
        path.write_text(raw, encoding="utf-8")
        with self.assertRaisesRegex(SampleValidationError, "finite"):
            load_scenario(path)

    def test_resolved_symlink_target_is_checked_against_repo_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            lab = repo / "homelab"
            samples = lab / "samples"
            samples.mkdir(parents=True)
            (samples / "default.json").write_text(
                json.dumps(self.default), encoding="utf-8"
            )
            unignored = repo / "customer-input.json"
            unignored.write_text(json.dumps(self.default), encoding="utf-8")
            escape = samples / "escape.local.json"
            external = root / "external.json"
            external.write_text(json.dumps(self.default), encoding="utf-8")
            external_link = samples / "external.local.json"

            original_resolve = Path.resolve

            def resolve_target(path: Path, strict: bool = False) -> Path:
                if path == escape:
                    return unignored
                if path == external_link:
                    return external
                return original_resolve(path, strict=strict)

            # Model the final paths returned for two symlinks. This avoids the
            # Windows developer-mode privilege required to create test links.
            with patch.object(Path, "resolve", resolve_target):
                with self.assertRaisesRegex(SampleValidationError, "inside the repository"):
                    resolve_launch_sample(escape, lab)
                self.assertEqual(resolve_launch_sample(external_link, lab), external)

    def test_verifier_recomputes_and_matches_the_mounted_manifest(self):
        loaded = load_scenario(self.default_path)
        verified = verify_mounted_sample_manifest(loaded.manifest, self.default_path)
        self.assertEqual(verified.sample_sha256, loaded.sample_sha256)
        mismatched = copy.deepcopy(loaded.manifest)
        mismatched["sample_sha256"] = "0" * 64
        with self.assertRaisesRegex(CheckFailure, "independently validated"):
            verify_mounted_sample_manifest(mismatched, self.default_path)

    def test_sensitive_metadata_keys_are_rejected(self):
        payload = copy.deepcopy(self.default)
        payload["memories"][0]["metadata"]["borrower_name"] = "Synthetic Person"
        temporary, path = self.write_sample(payload)
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(SampleValidationError, "prohibited sensitive-data field"):
            load_scenario(path)

    def test_unknown_fields_and_filter_mismatches_fail_closed(self):
        for mutation in ("unknown", "filter"):
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(self.default)
                if mutation == "unknown":
                    payload["unreviewed"] = True
                else:
                    payload["memories"][0]["metadata"]["metric"] = "different"
                temporary, path = self.write_sample(payload)
                self.addCleanup(temporary.cleanup)
                with self.assertRaises(SampleValidationError):
                    load_scenario(path)


if __name__ == "__main__":
    unittest.main()
