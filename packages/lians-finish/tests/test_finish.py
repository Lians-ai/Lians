from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from lians_finish.codex_adapter import CodexRunner, _usage_from_events
from lians_finish.policy import PolicyCatalog, RouteError, build_route, load_policy_catalog
from lians_finish.runtime import (
    FinishRuntime,
    RunResult,
    VerificationResult,
    planned_receipt,
    validate_verifier_command,
    verify_receipt,
)


class FakeRunner:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.stages = []
        self.prompts = []

    def run(self, stage, prompt, repo):
        self.stages.append(stage)
        self.prompts.append(prompt)
        failed = stage.id in self.failures
        return RunResult(
            stage_id=stage.id,
            model=stage.model,
            reasoning_effort=stage.reasoning_effort,
            premium=stage.premium,
            success=not failed,
            message=f"{stage.id} result",
            usage={"input_tokens": 100, "output_tokens": 20},
            returncode=1 if failed else 0,
            stderr="stage failed" if failed else "",
        )


class PersistentFakeRunner(FakeRunner):
    preserves_context = True


class FakeVerifier:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self._command = ("python", "-m", "unittest")

    @property
    def command(self):
        return self._command

    def verify(self, repo, attempt):
        success = next(self.outcomes)
        return VerificationResult(
            attempt=attempt,
            success=success,
            command=self._command,
            returncode=0 if success else 1,
            stdout="passed" if success else "failed",
            stderr="",
        )


class FinishTests(unittest.TestCase):
    def test_persistent_runner_does_not_repeat_prior_stage_output(self):
        route = build_route("Add pagination", mode="protect")
        runner = PersistentFakeRunner()
        receipt = FinishRuntime(runner).execute(
            route,
            ROOT,
            FakeVerifier([True]),
            started_at="2026-09-20T15:00:00Z",
        )

        self.assertEqual(receipt["status"], "PASS")
        self.assertNotIn("Prior stage output", runner.prompts[1])
        self.assertNotIn("discover result", runner.prompts[1])

    def test_mission_contract_is_bound_to_receipt_and_stage_prompt(self):
        route = build_route("Add pagination", mode="protect")
        mission = {
            "revision": 2,
            "as_of": "2026-09-20T15:00:00Z",
            "valid_until": None,
            "definition_of_done": "All pagination tests pass",
            "constraints": ["Keep the public API stable"],
            "mission_sha256": "a" * 64,
        }
        runner = FakeRunner()
        receipt = FinishRuntime(runner).execute(
            route,
            ROOT,
            FakeVerifier([True]),
            mission=mission,
            started_at="2026-09-20T15:00:00Z",
        )
        self.assertEqual(receipt["mission_sha256"], "a" * 64)
        self.assertIn("Mission revision: 2", runner.prompts[0])
        self.assertIn("Keep the public API stable", runner.prompts[0])

    def test_cli_modes_prints_exact_deterministic_json_without_artifacts(self):
        expected = (
            '{"modes":[{"name":"balanced","premium_budget":2},'
            '{"name":"maximum","premium_budget":4},'
            '{"name":"protect","premium_budget":1}]}\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            for seed in ("1", "2"):
                completed = subprocess.run(
                    [sys.executable, "-B", "-m", "lians_finish.cli", "modes"],
                    cwd=directory,
                    env={**os.environ, "PYTHONPATH": str(SRC), "PYTHONHASHSEED": seed},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                self.assertEqual(completed.stdout, expected)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cli_modes_rejects_extra_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, "-B", "-m", "lians_finish.cli", "modes", "extra"],
                cwd=directory,
                env={**os.environ, "PYTHONPATH": str(SRC)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_routing_mode_premium_budgets_remain_unchanged(self):
        for mode, budget in (("protect", 1), ("balanced", 2), ("maximum", 4)):
            with self.subTest(mode=mode):
                self.assertEqual(
                    build_route("Add pagination to the project list", mode=mode).max_premium_calls,
                    budget,
                )

    def test_premium_budget_override_is_bounded(self):
        self.assertEqual(
            build_route("Add pagination", mode="protect", premium_budget=0).max_premium_calls,
            0,
        )
        with self.assertRaises(RouteError):
            build_route("Add pagination", mode="protect", premium_budget=-1)
        with self.assertRaises(RouteError):
            build_route("Add pagination", mode="protect", premium_budget=2)

    def test_maximum_budget_zero_returns_block_receipt(self):
        route = build_route("Add pagination", mode="maximum", premium_budget=0)
        receipt = FinishRuntime(FakeRunner()).execute(route, ROOT, FakeVerifier([True]))
        self.assertEqual(receipt["status"], "BLOCK")
        self.assertEqual(receipt["model_calls"], 0)
        self.assertEqual(receipt["premium_attempts"], 0)

    def test_verify_receipt_distinguishes_valid_and_tampered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            receipt = planned_receipt(
                build_route("Add pagination"), created_at="2026-09-20T00:00:00Z"
            )
            path.write_text(json.dumps(receipt), encoding="utf-8")
            valid, digest = verify_receipt(path)
            self.assertTrue(valid)
            self.assertEqual(digest, receipt["receipt_sha256"])
            receipt["status"] = "PASS"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            self.assertFalse(verify_receipt(path)[0])

    def test_policy_catalog_is_frozen_and_changes_only_protect_implementation(self):
        catalog = PolicyCatalog("gpt-5.6-sol", "high")
        with self.assertRaises(AttributeError):
            catalog.implementation_model = "gpt-5.6-luna"
        protect = build_route("Add pagination", policy_catalog=catalog)
        balanced = build_route("Add pagination", mode="balanced", policy_catalog=catalog)
        protect_impl = next(stage for stage in protect.stages if stage.id == "implement")
        balanced_impl = next(stage for stage in balanced.stages if stage.id == "implement")
        self.assertEqual(
            (protect_impl.model, protect_impl.reasoning_effort), ("gpt-5.6-sol", "high")
        )
        self.assertEqual(balanced_impl.model, "gpt-5.6-sol")

    def test_policy_file_rejects_unknown_and_astra(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            for value in (
                {"unknown": {}},
                {"protect": {"unknown": "value"}},
                {"protect": {"implementation_model": "gpt-6-astra"}},
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(RouteError):
                    load_policy_catalog(path)

    def test_protect_simple_uses_luna_without_planned_premium_execution(self):
        route = build_route("Fix a typo in the README", mode="protect")
        implementation = next(stage for stage in route.stages if stage.id == "implement")
        self.assertEqual(route.risk, "simple")
        self.assertEqual(implementation.model, "gpt-5.6-luna")
        self.assertFalse(implementation.premium)

    def test_protect_normal_uses_terra(self):
        route = build_route("Add pagination to the project list", mode="protect")
        implementation = next(stage for stage in route.stages if stage.id == "implement")
        self.assertEqual(implementation.model, "gpt-5.6-terra")

    def test_balanced_uses_sol_for_implementation(self):
        route = build_route("Add pagination to the project list", mode="balanced")
        implementation = next(stage for stage in route.stages if stage.id == "implement")
        self.assertEqual(implementation.model, "gpt-5.6-sol")

    def test_maximum_starts_implementation_with_astra(self):
        route = build_route("Add pagination to the project list", mode="maximum")
        discovery = next(stage for stage in route.stages if stage.id == "discover")
        implementation = next(stage for stage in route.stages if stage.id == "implement")
        self.assertEqual(discovery.model, "gpt-6-astra")
        self.assertTrue(discovery.premium)
        self.assertEqual(implementation.model, "gpt-6-astra")
        self.assertTrue(implementation.premium)

    def test_maximum_normal_task_uses_two_premium_calls_when_verifier_passes(self):
        route = build_route("Add pagination to the project list", mode="maximum")
        receipt = FinishRuntime(FakeRunner()).execute(
            route,
            ROOT,
            FakeVerifier([True]),
            started_at="2026-09-20T15:00:00Z",
        )
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["premium_calls"], 2)

    def test_high_risk_forces_review_even_after_passing_verification(self):
        route = build_route("Fix authentication and password reset", mode="protect")
        runner = FakeRunner()
        receipt = FinishRuntime(runner).execute(
            route,
            ROOT,
            FakeVerifier([True, True]),
            started_at="2026-09-20T15:00:00Z",
        )
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(
            [stage.id for stage in runner.stages], ["discover", "implement", "escalate"]
        )
        self.assertEqual(receipt["premium_calls"], 1)

    def test_successful_normal_task_avoids_premium_call(self):
        route = build_route("Add pagination to the project list", mode="protect")
        runner = FakeRunner()
        receipt = FinishRuntime(runner).execute(
            route,
            ROOT,
            FakeVerifier([True]),
            started_at="2026-09-20T15:00:00Z",
        )
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["premium_calls"], 0)

    def test_failed_verification_escalates_to_astra(self):
        route = build_route("Add pagination to the project list", mode="protect")
        runner = FakeRunner()
        receipt = FinishRuntime(runner).execute(
            route,
            ROOT,
            FakeVerifier([False, True]),
            started_at="2026-09-20T15:00:00Z",
        )
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(runner.stages[-1].model, "gpt-6-astra")
        self.assertEqual(receipt["premium_calls"], 1)

    def test_high_risk_failed_implementation_does_not_double_escalate(self):
        route = build_route("Fix authentication and password reset", mode="protect")
        runner = FakeRunner({"implement"})
        receipt = FinishRuntime(runner).execute(
            route,
            ROOT,
            FakeVerifier([True]),
            started_at="2026-09-20T15:00:00Z",
        )
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual([stage.id for stage in runner.stages].count("escalate"), 1)
        self.assertEqual(receipt["premium_calls"], 1)

    def test_second_failed_verification_blocks(self):
        route = build_route("Add pagination to the project list", mode="protect")
        receipt = FinishRuntime(FakeRunner()).execute(
            route,
            ROOT,
            FakeVerifier([False, False]),
            started_at="2026-09-20T15:00:00Z",
        )
        self.assertEqual(receipt["status"], "BLOCK")

    def test_secret_is_rejected(self):
        with self.assertRaises(RouteError):
            build_route("Use api_key=" + ("x" * 24) + " for this task")

    def test_planned_receipt_makes_no_savings_claim(self):
        receipt = planned_receipt(
            build_route("Add pagination", mode="protect"),
            created_at="2026-09-20T15:00:00Z",
        )
        self.assertEqual(receipt["status"], "PLANNED")
        self.assertIn("No model was invoked", receipt["claim_boundary"])

    def test_usage_parser_uses_largest_snapshot(self):
        events = "\n".join(
            [
                json.dumps({"usage": {"input_tokens": 10, "output_tokens": 2}}),
                json.dumps({"usage": {"input_tokens": 30, "output_tokens": 8}}),
            ]
        )
        self.assertEqual(_usage_from_events(events), {"input_tokens": 30, "output_tokens": 8})

    def test_codex_adapter_applies_planned_model_effort_and_sandbox(self):
        stage = next(
            stage
            for stage in build_route("Add pagination", mode="protect").stages
            if stage.id == "implement"
        )
        captured = []

        def fake_run(command, **kwargs):
            captured.extend(command)
            message_index = command.index("--output-last-message") + 1
            Path(command[message_index]).write_text("implemented", encoding="utf-8")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"usage": {"input_tokens": 11, "output_tokens": 3}}),
                stderr="",
            )

        with patch("lians_finish.codex_adapter.subprocess.run", side_effect=fake_run):
            result = CodexRunner("codex-test").run(stage, "Do the task", ROOT)

        self.assertTrue(result.success)
        self.assertEqual(result.usage, {"input_tokens": 11, "output_tokens": 3})
        self.assertIn("gpt-5.6-terra", captured)
        self.assertIn('model_reasoning_effort="low"', captured)
        self.assertIn("workspace-write", captured)
        self.assertNotIn("--approve-for-me", captured)

    def test_verifier_command_security_and_receipt_binding(self):
        command = ("python", "-m", "unittest")
        self.assertEqual(validate_verifier_command(command), command)
        for unsafe in ((), ("pwsh",), ("python", "x|y")):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                validate_verifier_command(unsafe)
        receipt = FinishRuntime(FakeRunner()).execute(
            build_route("Add pagination"), ROOT, FakeVerifier([True])
        )
        expected = hashlib.sha256(
            json.dumps(list(command), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        self.assertEqual(receipt["verifier_command_sha256"], expected)

    def test_cli_dry_run_writes_route_and_receipt_without_codex(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            env = {"PYTHONPATH": str(SRC)}
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lians_finish.cli",
                    "run",
                    "Add pagination to the project list",
                    "--repo",
                    str(ROOT),
                    "--out",
                    str(output),
                    "--started-at",
                    "2026-09-20T15:00:00Z",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(completed.stdout.startswith("PLANNED "))
            receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "PLANNED")

    def test_cli_json_stdout_matches_written_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lians_finish.cli",
                    "run",
                    "Add pagination",
                    "--repo",
                    str(ROOT),
                    "--out",
                    str(output),
                    "--started-at",
                    "2026-09-20T15:00:00Z",
                    "--json",
                ],
                cwd=ROOT,
                env={"PYTHONPATH": str(SRC)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(completed.stdout),
                json.loads((output / "receipt.json").read_text(encoding="utf-8")),
            )

    def test_failed_repair_blocks_before_unchanged_baseline_can_pass(self):
        route = build_route("Add pagination to the project list", mode="protect")
        runner = FakeRunner({"implement", "escalate"})
        receipt = FinishRuntime(runner).execute(
            route,
            ROOT,
            FakeVerifier([True]),
            started_at="2026-09-20T15:00:00Z",
        )
        self.assertEqual(receipt["status"], "BLOCK")
        self.assertEqual(receipt["verification"], [])
        self.assertEqual(receipt["premium_attempts"], 1)
        self.assertEqual(receipt["premium_calls"], 1)

    def test_cli_refuses_live_execution_without_verifier(self):
        env = {"PYTHONPATH": str(SRC)}
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "lians_finish.cli",
                "run",
                "Add pagination",
                "--repo",
                str(ROOT),
                "--execute",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("--verify is required", completed.stderr)


if __name__ == "__main__":
    unittest.main()
