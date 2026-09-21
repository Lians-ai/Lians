from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from lians_finish.beta import (
    BETA_REPORT_SCHEMA,
    build_beta_report,
    create_beta_context,
    load_or_create_beta_identity,
)


class BetaReportTests(unittest.TestCase):
    @staticmethod
    def receipt(mode: str, *, status: str = "PASS", premium_calls: int = 0):
        return {
            "status": status,
            "started_at": "2026-09-21T12:00:00Z",
            "finished_at": "2026-09-21T12:01:00Z",
            "model_calls": 2,
            "premium_calls": premium_calls,
            "premium_attempts": premium_calls,
            "max_premium_calls": 1 if mode == "protect" else 4,
            "usage_observed": {"input_tokens": 100, "output_tokens": 20},
            "stages": [
                {
                    "model": "gpt-5.6-terra" if mode == "protect" else "gpt-6-astra",
                    "message": "PRIVATE MODEL OUTPUT",
                    "stderr": "PRIVATE STDERR",
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                }
            ],
            "verification": [{"stdout": "PRIVATE TEST OUTPUT"}],
            "verifier_command_sha256": "a" * 64,
            "receipt_sha256": ("b" if mode == "protect" else "c") * 64,
        }

    def test_identity_is_random_local_and_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            first = load_or_create_beta_identity(data)
            second = load_or_create_beta_identity(data)
            self.assertEqual(first, second)
            self.assertRegex(first["participant_id"], r"^beta-[0-9a-f]{12}$")
            self.assertEqual(len(first["pair_key"]), 64)

    def test_report_pairs_modes_without_exporting_private_content(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            repository = Path(directory) / "PRIVATE-REPOSITORY-NAME"
            repository.mkdir()
            snapshot = {
                "git_head": "1" * 40,
                "git_state_sha256": "2" * 64,
                "git_clean": True,
            }
            with patch("lians_finish.beta.repository_snapshot", return_value=snapshot):
                context = create_beta_context(
                    data,
                    repository,
                    task="PRIVATE TASK TEXT",
                    constraints=("PRIVATE CONSTRAINT",),
                    definition_of_done="PRIVATE DEFINITION",
                    verifier_command=("python", "-m", "unittest"),
                    product_version="0.11.0",
                )
                repeated = create_beta_context(
                    data,
                    repository,
                    task="PRIVATE   TASK TEXT",
                    constraints=("PRIVATE   CONSTRAINT",),
                    definition_of_done="PRIVATE DEFINITION",
                    verifier_command=("python", "-m", "unittest"),
                    product_version="0.11.0",
                )
                changed_constraint = create_beta_context(
                    data,
                    repository,
                    task="PRIVATE TASK TEXT",
                    constraints=("CHANGED PRIVATE CONSTRAINT",),
                    definition_of_done="PRIVATE DEFINITION",
                    verifier_command=("python", "-m", "unittest"),
                    product_version="0.11.0",
                )
            self.assertEqual(context["task_pair_sha256"], repeated["task_pair_sha256"])
            self.assertNotEqual(context["task_pair_sha256"], changed_constraint["task_pair_sha256"])

            jobs = []
            for index, (mode, premium) in enumerate((("protect", 1), ("maximum", 4))):
                jobs.append(
                    {
                        "id": f"private-job-{index}",
                        "duration_seconds": 60.0,
                        "repository": str(repository),
                        "route": {"mode": mode, "risk": "normal", "task": "PRIVATE TASK TEXT"},
                        "mission": {"constraints": ["PRIVATE CONSTRAINT"]},
                        "beta_context": context,
                        "execution_bridge": "codex_app_server",
                        "receipt": self.receipt(mode, premium_calls=premium),
                    }
                )

            report = build_beta_report(
                data,
                jobs,
                product_version="0.11.0",
                generated_at="2026-09-21T13:00:00Z",
            )
            self.assertEqual(report["schema"], BETA_REPORT_SCHEMA)
            self.assertEqual(report["aggregate"]["matched_pairs"], 1)
            self.assertEqual(report["aggregate"]["verified_matched_pairs"], 1)
            self.assertEqual(report["aggregate"]["premium_call_reduction_percent"], 75.0)
            self.assertEqual(report["runs"][0]["execution_bridge"], "codex_app_server")
            self.assertTrue(report["runs"][0]["usage_complete"])
            serialized = json.dumps(report)
            for private in (
                "PRIVATE TASK TEXT",
                "PRIVATE DEFINITION",
                "PRIVATE CONSTRAINT",
                "PRIVATE-REPOSITORY-NAME",
                "PRIVATE MODEL OUTPUT",
                "PRIVATE STDERR",
                "PRIVATE TEST OUTPUT",
                "private-job",
            ):
                self.assertNotIn(private, serialized)
            self.assertNotIn(load_or_create_beta_identity(data)["pair_key"], serialized)

    def test_legacy_and_incomplete_jobs_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            report = build_beta_report(
                Path(directory),
                [{"id": "old", "status": "PASS", "receipt": {"status": "PASS"}}],
                product_version="0.11.0",
            )
            self.assertEqual(report["runs"], [])
            self.assertEqual(report["excluded_legacy_or_incomplete_runs"], 1)


if __name__ == "__main__":
    unittest.main()
