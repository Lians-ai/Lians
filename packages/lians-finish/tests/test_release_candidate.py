from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from lians_finish.app import LiansApplication, create_server
from lians_finish.governance import MissionInput, MissionLedger
from lians_finish.policy import MODE_PREMIUM_BUDGETS, build_route
from lians_finish.runtime import (
    FinishRuntime,
    RunResult,
    VerificationResult,
    planned_receipt,
    validate_verifier_command,
    verify_receipt,
)


class RecordingRunner:
    def __init__(self, failed: set[str] | None = None) -> None:
        self.failed = failed or set()

    def run(self, stage, prompt, repo):
        failed = stage.id in self.failed
        return RunResult(
            stage_id=stage.id,
            model=stage.model,
            reasoning_effort=stage.reasoning_effort,
            premium=stage.premium,
            success=not failed,
            message="simulated release-candidate stage",
            usage={"input_tokens": 2, "output_tokens": 1},
            returncode=1 if failed else 0,
            stderr="simulated failure" if failed else "",
        )


class SequenceVerifier:
    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = iter(outcomes)
        self._command = ("python", "-m", "unittest")

    @property
    def command(self):
        return self._command

    def verify(self, repo, attempt):
        success = next(self.outcomes, False)
        return VerificationResult(
            attempt=attempt,
            success=success,
            command=self._command,
            returncode=0 if success else 1,
            stdout="passed" if success else "failed",
            stderr="",
        )


class ReleaseCandidateStressTests(unittest.TestCase):
    def test_3000_deterministic_routes_preserve_policy_invariants(self):
        stems = [
            "Fix a typo in the README",
            "Add pagination to the project list",
            "Investigate a flaky concurrency performance regression",
            "Repair authentication billing and production database migration",
        ]
        rng = random.Random(20260920)
        for index in range(1000):
            task = f"{rng.choice(stems)} case {index}"
            for mode in ("protect", "balanced", "maximum"):
                route = build_route(task, mode=mode)
                again = build_route(task, mode=mode)
                self.assertEqual(route.to_dict(), again.to_dict())
                self.assertEqual(route.max_premium_calls, MODE_PREMIUM_BUDGETS[mode])
                self.assertEqual(
                    [stage.id for stage in route.stages],
                    ["discover", "implement", "verify", "escalate"],
                )
                self.assertLessEqual(
                    sum(stage.premium and stage.run_when == "always" for stage in route.stages),
                    route.max_premium_calls,
                )
                if mode == "protect":
                    implementation = next(
                        stage for stage in route.stages if stage.id == "implement"
                    )
                    self.assertFalse(implementation.premium)
                if route.risk == "high":
                    review = next(stage for stage in route.stages if stage.id == "escalate")
                    self.assertEqual(review.run_when, "always")

    def test_540_runtime_paths_never_exceed_premium_ceiling(self):
        tasks = [
            "Add pagination",
            "Fix authentication and payment permissions",
            "Fix a typo in documentation",
        ]
        outcome_sets = ([True], [False, True], [False, False])
        count = 0
        for repeat in range(20):
            for task in tasks:
                for mode in ("protect", "balanced", "maximum"):
                    for outcomes in outcome_sets:
                        route = build_route(f"{task} {repeat}", mode=mode)
                        receipt = FinishRuntime(RecordingRunner()).execute(
                            route,
                            ROOT,
                            SequenceVerifier(list(outcomes)),
                            started_at="2026-09-20T12:00:00Z",
                        )
                        self.assertIn(receipt["status"], {"PASS", "BLOCK"})
                        self.assertLessEqual(receipt["premium_attempts"], route.max_premium_calls)
                        self.assertLessEqual(receipt["premium_calls"], route.max_premium_calls)
                        count += 1
        self.assertEqual(count, 540)

    def test_250_concurrent_mission_revisions_form_one_valid_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = MissionLedger(Path(directory) / "missions")

            def record(index: int):
                return ledger.record(
                    MissionInput(
                        goal="Operate the production release",
                        repository=ROOT,
                        constraints=(f"constraint-{index}",),
                    )
                )

            with ThreadPoolExecutor(max_workers=24) as pool:
                records = list(pool.map(record, range(250)))
            self.assertEqual({record["revision"] for record in records}, set(range(1, 251)))
            latest = ledger.list_latest()
            self.assertEqual(latest[0]["revision"], 250)
            path = next((Path(directory) / "missions").glob("*.jsonl"))
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 250)

    def test_200_tampered_receipts_are_all_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            for index in range(200):
                receipt = planned_receipt(
                    build_route(f"Add pagination case {index}"),
                    created_at="2026-09-20T12:00:00Z",
                )
                tampered = deepcopy(receipt)
                tampered["max_premium_calls"] += 1
                path.write_text(json.dumps(tampered), encoding="utf-8")
                self.assertFalse(verify_receipt(path)[0])

    def test_verifier_injection_corpus_is_rejected(self):
        unsafe = [
            ("python", f"tests{character}whoami")
            for character in (";", "&", "|", "<", ">", "\r", "\n")
        ]
        unsafe.extend([("powershell", "-Command", "Get-ChildItem"), ("cmd", "/c", "dir")])
        for command in unsafe:
            with self.subTest(command=command), self.assertRaises(ValueError):
                validate_verifier_command(command)

    def test_120_concurrent_http_inspections_remain_authorized_and_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            application = LiansApplication(
                Path(directory) / "data",
                default_repository=ROOT,
                runner_factory=RecordingRunner,
                token="release-candidate-token",
            )
            server = create_server(application, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            body = json.dumps({"repository": str(ROOT)}).encode("utf-8")

            def inspect(_: int):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/inspect",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Lians-Token": "release-candidate-token",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, json.load(response)

            try:
                with ThreadPoolExecutor(max_workers=24) as pool:
                    results = list(pool.map(inspect, range(120)))
                self.assertTrue(all(status == 200 for status, _ in results))
                self.assertTrue(all(value["repository"] == str(ROOT) for _, value in results))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)

    def test_40_simultaneous_starts_allow_exactly_one_active_run(self):
        release = threading.Event()

        class BlockingRunner(RecordingRunner):
            def run(self, stage, prompt, repo):
                release.wait(10)
                return super().run(stage, prompt, repo)

        with tempfile.TemporaryDirectory() as directory:
            application = LiansApplication(
                Path(directory) / "data",
                default_repository=ROOT,
                runner_factory=BlockingRunner,
            )

            def start(index: int):
                try:
                    return application.start_run(
                        {
                            "repository": str(ROOT),
                            "task": f"Concurrent release task {index}",
                            "mode": "protect",
                            "verification_command": 'python -c "raise SystemExit(0)"',
                        }
                    )
                except ValueError as exc:
                    return str(exc)

            accepted: list[dict] = []
            try:
                with ThreadPoolExecutor(max_workers=40) as pool:
                    results = list(pool.map(start, range(40)))
                accepted = [value for value in results if isinstance(value, dict)]
                rejected = [value for value in results if isinstance(value, str)]
                self.assertEqual(len(accepted), 1)
                self.assertEqual(len(rejected), 39)
                self.assertTrue(all("already active" in value for value in rejected))
            finally:
                release.set()
                if accepted:
                    application.wait(accepted[0]["id"], timeout=15)

    def test_web_assets_are_packaged_and_contain_no_inline_script_dependency(self):
        web = SRC / "lians_finish" / "web"
        for name in ("index.html", "styles.css", "app.js", "wordmark.png.b64"):
            self.assertGreater((web / name).stat().st_size, 100)
        index = (web / "index.html").read_text(encoding="utf-8")
        wordmark = base64.b64decode(
            (web / "wordmark.png.b64").read_text(encoding="ascii").strip(), validate=True
        )
        self.assertIn("/assets/app.js", index)
        self.assertEqual(index.count("/assets/wordmark.png"), 3)
        self.assertEqual(
            hashlib.sha256(wordmark).hexdigest(),
            "51495b5fc3e9dd339e5d2a5d4f4ae4c82f703c7d2ded21254d087c36b836cd4d",
        )
        self.assertNotIn("<script>", index)

    def test_working_surface_keeps_one_composer_and_one_context_rail(self):
        index = (SRC / "lians_finish" / "web" / "index.html").read_text(encoding="utf-8")
        styles = (SRC / "lians_finish" / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertEqual(index.count('class="composer"'), 1)
        self.assertEqual(index.count('id="activity"'), 1)
        self.assertIn('class="inspector hidden"', index)
        self.assertEqual(index.count("<h1>"), 1)
        for element_id in (
            "task",
            "mode-grid",
            "toggle-sidebar",
            "toggle-controls",
            "preview",
            "run",
            "mission-controls",
            "route-panel",
            "run-panel",
            "ledger-summary",
            "proof-summary",
            "usage-summary",
        ):
            self.assertEqual(index.count(f'id="{element_id}"'), 1)
        self.assertNotIn("Start mission", index)
        self.assertIn("grid-template-rows: 52px minmax(0, 1fr)", styles)
        font_sizes = [
            int(size)
            for match in re.findall(r"font-size:\s*(\d+)px|font:\s*(?:\d+\s+)?(\d+)px", styles)
            for size in match
            if size
        ]
        self.assertGreaterEqual(min(font_sizes), 12)

    def test_distribution_surface_has_no_founder_identity_or_false_ready_state(self):
        web = SRC / "lians_finish" / "web"
        index = (web / "index.html").read_text(encoding="utf-8")
        script = (web / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("Ethan", index)
        self.assertNotIn("Local engine</span><em>Ready", index)
        self.assertEqual(index.count('id="engine-state"'), 1)
        self.assertEqual(index.count('id="engine-status"'), 1)
        self.assertIn('id="run" class="primary" type="button" disabled', index)
        self.assertIn("button.disabled = busyState || !runtimeRoutable", script)
        self.assertIn('"Setup needed"', script)
        self.assertIn("Preview and portable agents still work", script)


if __name__ == "__main__":
    unittest.main()
