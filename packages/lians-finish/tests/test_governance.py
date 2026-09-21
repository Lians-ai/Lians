from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from lians_finish.governance import (
    MissionInput,
    MissionLedger,
    discover_proof,
    mission_status,
    summarize_usage,
)


class GovernanceTests(unittest.TestCase):
    def test_mission_ledger_reuses_identical_revision_and_links_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = MissionLedger(Path(directory) / "missions")
            first_input = MissionInput(
                goal="Ship the feature",
                repository=ROOT,
                constraints=("No cloud storage",),
                definition_of_done="Tests pass",
            )
            first = ledger.record(first_input, recorded_at="2026-09-20T12:00:00Z")
            same = ledger.record(first_input, recorded_at="2026-09-20T12:01:00Z")
            changed = ledger.record(
                MissionInput(
                    goal="Ship the feature",
                    repository=ROOT,
                    constraints=("No cloud storage", "Keep the public API stable"),
                    definition_of_done="Tests pass",
                ),
                recorded_at="2026-09-20T12:02:00Z",
            )
            self.assertEqual(first["mission_sha256"], same["mission_sha256"])
            self.assertEqual(first["revision"], 1)
            self.assertEqual(changed["revision"], 2)
            self.assertEqual(changed["previous_sha256"], first["mission_sha256"])
            self.assertEqual(len(ledger.list_latest()), 1)

    def test_mission_expiry_is_computed_from_valid_until(self):
        mission = {"valid_until": "2026-09-20T12:00:00Z"}
        self.assertEqual(
            mission_status(mission, now=datetime(2026, 9, 20, 12, 0, 1, tzinfo=UTC)),
            "expired",
        )

    def test_proof_discovery_finds_python_and_package_test_suites(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            (repository / "tests").mkdir()
            (repository / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            python_suggestions = discover_proof(repository)
            self.assertEqual(
                python_suggestions[0].command,
                "python -m unittest discover -s tests -v",
            )

            (repository / "package.json").write_text(
                json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8"
            )
            (repository / "pnpm-lock.yaml").write_text("lockfileVersion: 9", encoding="utf-8")
            commands = [suggestion.command for suggestion in discover_proof(repository)]
            self.assertIn("pnpm test", commands)

    def test_usage_summary_aggregates_receipts_without_making_quota_claims(self):
        summary = summarize_usage(
            [
                {
                    "id": "one",
                    "receipt": {
                        "status": "PASS",
                        "model_calls": 2,
                        "premium_calls": 1,
                        "usage_observed": {"input_tokens": 100, "output_tokens": 20},
                    },
                },
                {
                    "id": "two",
                    "receipt": {
                        "status": "BLOCK",
                        "model_calls": 1,
                        "premium_calls": 0,
                        "usage_observed": {"input_tokens": 40},
                    },
                },
            ]
        )
        self.assertEqual(summary["observed_runs"], 2)
        self.assertEqual(summary["verified_tasks"], 1)
        self.assertEqual(summary["blocked_tasks"], 1)
        self.assertEqual(summary["usage_observed"]["input_tokens"], 140)
        self.assertIn("not provider quota", summary["claim_boundary"])

    def test_mission_contract_rejects_secrets_in_every_persisted_text_field(self):
        secret = "api_key=" + ("x" * 24)
        for payload in (
            {"task": "Ship safely", "constraints": secret},
            {"task": "Ship safely", "definition_of_done": secret},
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "secret"):
                MissionInput.from_payload(payload, ROOT)

    def test_mission_ledger_detects_tampering_before_appending(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = MissionLedger(Path(directory) / "missions")
            first = MissionInput(goal="Ship safely", repository=ROOT)
            ledger.record(first, recorded_at="2026-09-20T12:00:00Z")
            path = next((Path(directory) / "missions").glob("*.jsonl"))
            record = json.loads(path.read_text(encoding="utf-8"))
            record["goal"] = "Tampered goal"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity"):
                ledger.record(first, recorded_at="2026-09-20T12:01:00Z")


if __name__ == "__main__":
    unittest.main()
