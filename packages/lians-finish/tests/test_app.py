from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from lians_finish.app import LiansApplication, create_server
from lians_finish.runtime import RunResult, parse_verifier_command


class FakeRunner:
    def run(self, stage, prompt, repo):
        return RunResult(
            stage_id=stage.id,
            model=stage.model,
            reasoning_effort=stage.reasoning_effort,
            premium=stage.premium,
            success=True,
            message=f"{stage.id} complete",
            usage={"input_tokens": 10, "output_tokens": 2},
        )


class ApplicationTests(unittest.TestCase):
    def test_config_detects_stack_without_claiming_unbuilt_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LiansApplication(Path(directory) / "data", default_repository=ROOT)
            with patch(
                "lians_finish.app.shutil.which",
                side_effect=lambda value: (
                    f"C:/tools/{value}.exe" if value in {"codex", "claude"} else None
                ),
            ):
                stack = {agent["id"]: agent for agent in app.config()["agent_stack"]}
            self.assertTrue(stack["codex"]["routable"])
            self.assertEqual(stack["codex"]["status"], "ready")
            self.assertFalse(stack["claude"]["routable"])
            self.assertEqual(stack["claude"]["status"], "detected")
            self.assertEqual(stack["cursor"]["status"], "not_detected")

    def test_plan_is_local_and_returns_the_route(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LiansApplication(
                Path(directory) / "data",
                default_repository=ROOT,
                runner_factory=FakeRunner,
            )
            result = app.plan(
                {
                    "repository": str(ROOT),
                    "task": "Add pagination to the project list",
                    "mode": "protect",
                }
            )
            self.assertEqual(result["receipt"]["status"], "PLANNED")
            self.assertEqual(result["route"]["max_premium_calls"], 1)
            self.assertEqual(result["mission"]["revision"], 1)
            self.assertEqual(
                result["receipt"]["mission_sha256"],
                result["mission"]["mission_sha256"],
            )
            self.assertEqual(app.list_runs(), [])

    def test_plan_versions_changed_constraints_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LiansApplication(Path(directory) / "data", default_repository=ROOT)
            payload = {
                "repository": str(ROOT),
                "task": "Add pagination to the project list",
                "mode": "protect",
                "constraints": "Keep the API stable",
                "definition_of_done": "Tests pass",
            }
            first = app.plan(payload)
            same = app.plan(payload)
            changed = app.plan({**payload, "constraints": "Keep the API stable\nNo new dependency"})
            self.assertEqual(first["mission"]["mission_sha256"], same["mission"]["mission_sha256"])
            self.assertEqual(changed["mission"]["revision"], 2)
            self.assertEqual(
                changed["mission"]["previous_sha256"],
                first["mission"]["mission_sha256"],
            )

    def test_run_persists_verified_receipt_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LiansApplication(
                Path(directory) / "data",
                default_repository=ROOT,
                runner_factory=FakeRunner,
            )
            job = app.start_run(
                {
                    "repository": str(ROOT),
                    "task": "Add pagination to the project list",
                    "mode": "protect",
                    "verification_command": 'python -c "raise SystemExit(0)"',
                }
            )
            finished = app.wait(job["id"], timeout=15)
            self.assertEqual(finished["status"], "PASS", finished)
            self.assertEqual(finished["receipt"]["premium_calls"], 0)
            output = Path(finished["output"])
            self.assertTrue((output / "receipt.json").is_file())
            self.assertTrue((output / "route.json").is_file())
            self.assertTrue((output / "mission.json").is_file())
            self.assertTrue((output / "job.json").is_file())
            self.assertIn(
                "## Mission contract", (output / "receipt.md").read_text(encoding="utf-8")
            )
            event_kinds = [event["kind"] for event in finished["events"]]
            self.assertIn("stage_started", event_kinds)
            self.assertIn("verification_passed", event_kinds)
            self.assertEqual(app.list_runs()[0]["status"], "PASS")
            self.assertEqual(app.usage_summary()["verified_tasks"], 1)

    def test_workspace_inspection_suggests_proof_without_running_it(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repo"
            repository.mkdir()
            (repository / "tests").mkdir()
            (repository / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            app = LiansApplication(Path(directory) / "data", default_repository=repository)
            inspection = app.inspect_workspace({"repository": str(repository)})
            self.assertEqual(
                inspection["proof_suggestions"][0]["command"],
                "python -m unittest discover -s tests -v",
            )
            self.assertEqual(inspection["usage"]["observed_runs"], 0)

    def test_expired_mission_cannot_start(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LiansApplication(Path(directory) / "data", default_repository=ROOT)
            with self.assertRaisesRegex(ValueError, "mission has expired"):
                app.start_run(
                    {
                        "repository": str(ROOT),
                        "task": "Add pagination",
                        "mode": "protect",
                        "valid_until": "2020-01-01T00:00:00Z",
                        "verification_command": 'python -c "raise SystemExit(0)"',
                    }
                )

    def test_http_api_requires_local_token(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LiansApplication(
                Path(directory) / "data",
                default_repository=ROOT,
                runner_factory=FakeRunner,
                token="test-local-token",
            )
            server = create_server(app, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=5
                ) as response:
                    self.assertEqual(json.load(response)["status"], "ok")

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/assets/wordmark.png", timeout=5
                ) as response:
                    wordmark = response.read()
                    self.assertEqual(response.headers.get_content_type(), "image/png")
                    self.assertEqual(
                        hashlib.sha256(wordmark).hexdigest(),
                        "51495b5fc3e9dd339e5d2a5d4f4ae4c82f703c7d2ded21254d087c36b836cd4d",
                    )

                rebound = urllib.request.Request(
                    f"http://127.0.0.1:{port}/",
                    headers={"Host": "attacker.invalid"},
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(rebound, timeout=5)
                self.assertEqual(caught.exception.code, 403)

                body = json.dumps(
                    {
                        "repository": str(ROOT),
                        "task": "Add pagination",
                        "mode": "protect",
                    }
                ).encode("utf-8")
                unauthorized = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/plan",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(unauthorized, timeout=5)
                self.assertEqual(caught.exception.code, 403)

                authorized = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/plan",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Lians-Token": "test-local-token",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(authorized, timeout=5) as response:
                    self.assertEqual(json.load(response)["receipt"]["status"], "PLANNED")

                export_body = json.dumps(
                    {
                        "repository": str(ROOT),
                        "task": "Export this governed agent",
                        "constraints": "Do not store credentials",
                        "definition_of_done": "The test suite passes",
                        "mode": "protect",
                        "verification_command": "python -m unittest discover -s tests -v",
                    }
                ).encode("utf-8")
                export_request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/agent/export",
                    data=export_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Lians-Token": "test-local-token",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(export_request, timeout=5) as response:
                    exported = json.load(response)
                self.assertTrue(exported["filename"].endswith(".lians-agent"))
                import_request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/agent/import",
                    data=json.dumps({"bundle_text": json.dumps(exported["bundle"])}).encode(
                        "utf-8"
                    ),
                    headers={
                        "Content-Type": "application/json",
                        "X-Lians-Token": "test-local-token",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(import_request, timeout=5) as response:
                    imported = json.load(response)
                self.assertEqual(imported["task"], "Export this governed agent")
                self.assertEqual(imported["mode"], "protect")
                self.assertEqual(app.list_runs(), [])

                root_json = json.dumps(str(ROOT))
                for invalid_body, error_fragment in (
                    (
                        f'{{"repository":{root_json},"task":"one","task":"two"}}',
                        "Duplicate request field",
                    ),
                    (
                        f'{{"repository":{root_json},"task":"one","premium_budget":NaN}}',
                        "non-finite",
                    ),
                ):
                    invalid = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/plan",
                        data=invalid_body.encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "X-Lians-Token": "test-local-token",
                        },
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(invalid, timeout=5)
                    self.assertEqual(caught.exception.code, 400)
                    self.assertIn(
                        error_fragment,
                        json.loads(caught.exception.read().decode("utf-8"))["error"],
                    )

                hostile_origin = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/plan",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "https://attacker.invalid",
                        "X-Lians-Token": "test-local-token",
                    },
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(hostile_origin, timeout=5)
                self.assertEqual(caught.exception.code, 403)

                readiness = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/readiness",
                    headers={"X-Lians-Token": "test-local-token"},
                )
                with urllib.request.urlopen(readiness, timeout=5) as response:
                    self.assertIn(json.load(response)["status"], {"ready", "degraded"})

                beta_report = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/beta/report",
                    headers={"X-Lians-Token": "test-local-token"},
                )
                with urllib.request.urlopen(beta_report, timeout=5) as response:
                    exported_report = json.load(response)
                self.assertTrue(exported_report["filename"].startswith("lians-proof-gate-beta-"))
                self.assertEqual(exported_report["report"]["aggregate"]["eligible_runs"], 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)

    def test_verifier_parser_never_invokes_a_shell(self):
        self.assertEqual(
            parse_verifier_command('python -m pytest "tests/unit tests"'),
            ["python", "-m", "pytest", "tests/unit tests"],
        )
        with self.assertRaises(ValueError):
            parse_verifier_command("python -m pytest | more")

    def test_restart_marks_orphaned_active_job_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            run = data / "runs" / "test-run"
            run.mkdir(parents=True)
            (run / "job.json").write_text(
                json.dumps(
                    {
                        "id": "test-run",
                        "status": "RUNNING",
                        "created_at": "2026-09-20T12:00:00Z",
                        "updated_at": "2026-09-20T12:00:00Z",
                        "repository": str(ROOT),
                        "output": str(run),
                        "route": {"task": "Ship the change", "mode": "protect"},
                        "events": [],
                    }
                ),
                encoding="utf-8",
            )
            app = LiansApplication(data, default_repository=ROOT)
            recovered = app.get_job("test-run")
            self.assertEqual(recovered["status"], "INTERRUPTED")
            self.assertEqual(recovered["events"][-1]["kind"], "interrupted")
            self.assertIn("stopped before", recovered["error"])

    def test_queued_job_is_persisted_before_worker_starts(self):
        release = threading.Event()

        class BlockingRunner(FakeRunner):
            def run(self, stage, prompt, repo):
                release.wait(5)
                return super().run(stage, prompt, repo)

        with tempfile.TemporaryDirectory() as directory:
            app = LiansApplication(
                Path(directory) / "data",
                default_repository=ROOT,
                runner_factory=BlockingRunner,
            )
            job = app.start_run(
                {
                    "repository": str(ROOT),
                    "task": "Add pagination",
                    "mode": "protect",
                    "verification_command": 'python -c "raise SystemExit(0)"',
                }
            )
            persisted = Path(job["output"]) / "job.json"
            self.assertTrue(persisted.is_file())
            self.assertIn(
                json.loads(persisted.read_text(encoding="utf-8"))["status"], {"QUEUED", "RUNNING"}
            )
            release.set()
            self.assertEqual(app.wait(job["id"], timeout=15)["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
