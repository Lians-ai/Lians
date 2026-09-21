from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lians_finish.hosted_connector import (
    CONFIG_SCHEMA,
    HostedAPI,
    HostedConnector,
    HostedConnectorError,
    load_config,
    pair_connector,
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


if __name__ == "__main__":
    unittest.main()
