from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from lians_finish.hosted_connector import (
    CONFIG_SCHEMA,
    HostedAPI,
    HostedConnector,
    HostedConnectorError,
    LocalMissionExecutor,
    default_config_path,
    load_config,
    pair_connector,
    run_agent_once,
    save_config,
)


class PairingAPI:
    server = "https://www.lians.ai"

    def __init__(self) -> None:
        self.claimed = None

    def claim(self, code: str, name: str, label: str):
        self.claimed = {"code": code, "name": name, "label": label}
        return {
            "token": "secret-connector-token-" + ("x" * 32),
            "connector": {
                "id": "connector-1",
                "projects": [{"id": "project-1", "label": label}],
            },
        }


class FakeMissionAPI:
    def __init__(self, mission=None) -> None:
        self.mission = mission
        self.events = []
        self.completions = []

    def next_mission(self):
        value = self.mission
        self.mission = None
        return value

    def event(self, mission_id, lease, kind):
        self.events.append((mission_id, lease, kind))

    def complete(self, mission_id, lease, receipt):
        self.completions.append((mission_id, lease, receipt))


def config(repository: Path) -> dict:
    return {
        "schema": CONFIG_SCHEMA,
        "server": "https://www.lians.ai",
        "connector_id": "connector-1",
        "token": "secret-connector-token-" + ("x" * 32),
        "name": "Ethan laptop",
        "projects": [
            {
                "id": "project-1",
                "label": "Lians",
                "repository": str(repository),
                "verification_command": 'python -c "raise SystemExit(0)"',
                "allowed_verifiers": [],
            }
        ],
        "codex_bin": "codex",
        "codex_bridge": "auto",
    }


class HostedConnectorTests(unittest.TestCase):
    def test_nonlocal_servers_require_https_and_origins_reject_credentials(self):
        with self.assertRaisesRegex(HostedConnectorError, "require HTTPS"):
            HostedAPI("http://lians.ai")
        with self.assertRaisesRegex(HostedConnectorError, "without a path or credentials"):
            HostedAPI("https://user:secret@lians.ai/private")
        self.assertEqual(HostedAPI("http://127.0.0.1:8000/").server, "http://127.0.0.1:8000")

    def test_pairing_sends_only_public_labels_and_keeps_execution_details_local(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "private-repository"
            repository.mkdir()
            api = PairingAPI()
            value = pair_connector(
                api,
                code="AAAA-BBBB-CCCC-DDDD",
                name="Ethan laptop",
                repository=repository,
                label="Lians",
                verification_command='python -c "raise SystemExit(0)"',
                allowed_verifiers=[],
                codex_bin="codex",
                codex_bridge="auto",
            )
            self.assertEqual(api.claimed, {
                "code": "AAAA-BBBB-CCCC-DDDD",
                "name": "Ethan laptop",
                "label": "Lians",
            })
            self.assertEqual(value["projects"][0]["repository"], str(repository.resolve()))
            self.assertIn("verification_command", value["projects"][0])

    def test_configuration_is_written_atomically_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "connector.json"
            expected = config(Path(directory))
            save_config(path, expected)
            self.assertEqual(load_config(path), expected)
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_one_shot_connector_reports_progress_and_bounded_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            mission = {
                "id": "mission-1",
                "lease": "lease-1",
                "projectId": "project-1",
                "goal": "Finish the hosted app",
                "definitionOfDone": "Tests pass",
                "constraints": ["No paid dependency"],
                "mode": "balanced",
            }
            api = FakeMissionAPI(mission)

            def execute(received, project, notify):
                self.assertEqual(received["goal"], mission["goal"])
                self.assertEqual(project["repository"], str(repository))
                notify("planning")
                notify("implementing")
                notify("verifying")
                return {
                    "status": "PASS",
                    "modelCalls": 2,
                    "premiumCalls": 1,
                    "executionBridge": "codex_exec",
                    "receiptSha256": "a" * 64,
                    "verification": {
                        "success": True,
                        "exitCode": 0,
                        "commandSha256": "b" * 64,
                        "durationSeconds": None,
                    },
                }

            result = HostedConnector(api, config(repository), execute).run_once()
            self.assertEqual(result, {"mission_id": "mission-1", "status": "PASS"})
            self.assertEqual([event[2] for event in api.events], ["planning", "implementing", "verifying"])
            self.assertEqual(api.completions[0][2]["receiptSha256"], "a" * 64)
            self.assertNotIn("goal", json.dumps(api.completions[0][2]))

    def test_local_failure_is_reported_without_error_text_or_terminal_output(self):
        with tempfile.TemporaryDirectory() as directory:
            mission = {
                "id": "mission-1",
                "lease": "lease-1",
                "projectId": "project-1",
                "goal": "Private goal",
                "definitionOfDone": "Tests pass",
                "constraints": [],
                "mode": "protect",
            }
            api = FakeMissionAPI(mission)

            def fail(_mission, _project, _notify):
                raise RuntimeError("PRIVATE TERMINAL OUTPUT")

            with self.assertRaisesRegex(HostedConnectorError, "Mission stopped locally"):
                HostedConnector(api, config(Path(directory)), fail).run_once()
            receipt = api.completions[0][2]
            self.assertEqual(receipt["status"], "ERROR")
            self.assertNotIn("PRIVATE", json.dumps(receipt))

    def test_http_pairing_request_has_no_local_path_or_proof_command(self):
        captured = {}

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read(_limit):
                return json.dumps({
                    "token": "t" * 48,
                    "connector": {"id": "connector-1", "projects": [{"id": "project-1"}]},
                }).encode()

        def fake_open(request, timeout):
            captured["body"] = json.loads(request.data.decode())
            captured["timeout"] = timeout
            return Response()

        with patch("lians_finish.hosted_connector.urlopen", fake_open):
            HostedAPI("https://www.lians.ai").claim("PAIR-CODE", "Laptop", "Lians")
        self.assertEqual(captured["body"], {
            "code": "PAIR-CODE",
            "name": "Laptop",
            "projects": [{"label": "Lians", "verification": "configured"}],
        })

    def test_default_config_path_uses_windows_locations_then_a_safe_fallback(self):
        with patch.dict("os.environ", {"LOCALAPPDATA": "C:/LocalData"}, clear=True):
            self.assertEqual(default_config_path(), Path("C:/LocalData") / "Lians Finish" / "hosted-connector.json")
        with patch.dict("os.environ", {"USERPROFILE": "C:/Users/tester"}, clear=True):
            self.assertEqual(default_config_path(), Path("C:/Users/tester").resolve() / ".lians-finish" / "hosted-connector.json")
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(default_config_path(), Path.cwd() / ".lians-finish" / "hosted-connector.json")

    def test_load_config_reports_missing_malformed_and_unsupported_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "connector.json"
            with self.assertRaisesRegex(HostedConnectorError, "Run `lians-finish connect`"):
                load_config(path)
            path.write_text("not json", encoding="utf-8")
            with self.assertRaisesRegex(HostedConnectorError, "Could not read"):
                load_config(path)
            path.write_text(json.dumps({"schema": "old"}), encoding="utf-8")
            with self.assertRaisesRegex(HostedConnectorError, "unsupported format"):
                load_config(path)
            path.write_text(json.dumps({"schema": CONFIG_SCHEMA, "token": "short", "projects": [{}]}), encoding="utf-8")
            with self.assertRaisesRegex(HostedConnectorError, "missing its local token"):
                load_config(path)
            path.write_text(json.dumps({"schema": CONFIG_SCHEMA, "token": "t" * 40, "projects": []}), encoding="utf-8")
            with self.assertRaisesRegex(HostedConnectorError, "no local projects"):
                load_config(path)

    def test_http_client_bounds_and_validates_hosted_responses(self):
        class Response:
            def __init__(self, status=200, body=b""):
                self.status = status
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return self.body

        api = HostedAPI("https://www.lians.ai", "t" * 40)
        with patch("lians_finish.hosted_connector.urlopen", return_value=Response(status=204)):
            self.assertIsNone(api._request("GET", "/empty"))
        with patch("lians_finish.hosted_connector.urlopen", return_value=Response(body=b"[]")):
            with self.assertRaisesRegex(HostedConnectorError, "not a JSON object"):
                api._request("GET", "/list")
        with patch("lians_finish.hosted_connector.urlopen", return_value=Response(body=b"x" * (256 * 1024 + 1))):
            with self.assertRaisesRegex(HostedConnectorError, "exceeded"):
                api._request("GET", "/large")
        with patch("lians_finish.hosted_connector.urlopen", return_value=Response(body=b"not-json")):
            with self.assertRaisesRegex(HostedConnectorError, "not valid JSON"):
                api._request("GET", "/invalid")

        hosted_error = HTTPError("https://www.lians.ai/bad", 400, "bad", {}, BytesIO(b'{"error":"Safe public error"}'))
        with patch("lians_finish.hosted_connector.urlopen", side_effect=hosted_error):
            with self.assertRaisesRegex(HostedConnectorError, "Safe public error"):
                api._request("GET", "/bad")
        fallback_error = HTTPError("https://www.lians.ai/bad", 503, "bad", {}, BytesIO(b"not-json"))
        with patch("lians_finish.hosted_connector.urlopen", side_effect=fallback_error):
            with self.assertRaisesRegex(HostedConnectorError, r"503"):
                api._request("GET", "/bad")
        with patch("lians_finish.hosted_connector.urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(HostedConnectorError, "offline"):
                api._request("GET", "/offline")
        with patch("lians_finish.hosted_connector.urlopen", side_effect=TimeoutError("slow")):
            with self.assertRaisesRegex(HostedConnectorError, "slow"):
                api._request("GET", "/slow")

    def test_hosted_api_convenience_methods_reject_incomplete_payloads(self):
        api = HostedAPI("https://www.lians.ai")
        with patch.object(api, "_request", return_value={}):
            with self.assertRaisesRegex(HostedConnectorError, "connector token"):
                api.claim("code", "Laptop", "Lians")
            with self.assertRaisesRegex(HostedConnectorError, "invalid mission"):
                api.next_mission()
        with patch.object(api, "_request", return_value=None):
            self.assertIsNone(api.next_mission())
        with patch.object(api, "_request") as request:
            api.event("mission", "lease", "planning")
            api.complete("mission", "lease", {"status": "PASS"})
        self.assertEqual(request.call_count, 2)

    def test_pairing_rejects_missing_local_inputs_and_malformed_hosted_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with self.assertRaisesRegex(HostedConnectorError, "does not exist"):
                pair_connector(PairingAPI(), code="code", name="Laptop", repository=missing, label="Lians", verification_command="python -m unittest", allowed_verifiers=[], codex_bin="codex", codex_bridge="auto")
            with self.assertRaisesRegex(HostedConnectorError, "not allowed"):
                pair_connector(PairingAPI(), code="code", name="Laptop", repository=Path(directory), label="Lians", verification_command="python ; bad", allowed_verifiers=[], codex_bin="codex", codex_bridge="auto")

            api = PairingAPI()
            api.claim = lambda *_args: {"token": "t" * 40, "connector": {"id": "connector", "projects": []}}
            with self.assertRaisesRegex(HostedConnectorError, "did not return the local project"):
                pair_connector(api, code="code", name="Laptop", repository=Path(directory), label="Lians", verification_command="python -m unittest", allowed_verifiers=[], codex_bin="codex", codex_bridge="auto")

    def test_bounded_receipt_maps_local_terminal_states_without_private_output(self):
        project = {"verification_command": "python -m unittest"}
        mission = {"id": "mission-1"}
        passed = LocalMissionExecutor._bounded_receipt({
            "status": "PASS",
            "execution_bridge": "codex_app_server",
            "receipt": {
                "model_calls": 3,
                "premium_calls": 1,
                "receipt_sha256": "a" * 64,
                "verifier_command_sha256": "b" * 64,
                "verification": [{"returncode": 0, "stdout": "private"}],
            },
        }, mission, project)
        self.assertEqual(passed["status"], "PASS")
        self.assertEqual(passed["verification"]["exitCode"], 0)
        self.assertNotIn("stdout", json.dumps(passed))
        interrupted = LocalMissionExecutor._bounded_receipt({"status": "INTERRUPTED"}, mission, project)
        self.assertEqual(interrupted["status"], "ERROR")
        blocked = LocalMissionExecutor._bounded_receipt({"status": "BLOCK"}, mission, project)
        self.assertEqual(blocked["status"], "FAIL")

    def test_one_shot_connector_rejects_unusable_claims_and_run_helper_wires_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            no_work = HostedConnector(FakeMissionAPI(), config(Path(directory)), lambda *_args: {})
            self.assertIsNone(no_work.run_once())
            missing_lease = FakeMissionAPI({"id": "mission", "projectId": "project-1"})
            with self.assertRaisesRegex(HostedConnectorError, "missing its lease"):
                HostedConnector(missing_lease, config(Path(directory)), lambda *_args: {}).run_once()
            wrong_project = FakeMissionAPI({"id": "mission", "lease": "lease", "projectId": "other"})
            with self.assertRaisesRegex(HostedConnectorError, "unknown local project"):
                HostedConnector(wrong_project, config(Path(directory)), lambda *_args: {}).run_once()

            path = Path(directory) / "connector.json"
            save_config(path, config(Path(directory)))
            with patch("lians_finish.hosted_connector.HostedAPI") as api_class, patch("lians_finish.hosted_connector.HostedConnector.run_once", return_value=None) as run_once:
                api_class.return_value = object()
                self.assertIsNone(run_agent_once(path))
                run_once.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
