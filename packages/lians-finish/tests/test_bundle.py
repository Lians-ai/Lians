from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from lians_finish.app import LiansApplication
from lians_finish.bundle import AgentBundleError, parse_agent_bundle, verify_agent_bundle


class AgentBundleTests(unittest.TestCase):
    def payload(self, repository: Path) -> dict:
        return {
            "repository": str(repository),
            "task": "Keep the release notes synchronized with verified behavior",
            "constraints": "Keep the public API stable\nDo not store credentials",
            "definition_of_done": "The complete test suite passes",
            "valid_until": "2030-01-01T00:00:00Z",
            "mode": "protect",
            "verification_command": "python -m unittest discover -s tests -v",
        }

    def test_export_and_import_round_trip_without_recording_or_running(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            app = LiansApplication(data, default_repository=ROOT)
            exported = app.export_agent(self.payload(ROOT))
            self.assertEqual(exported["filename"], "lians-finish-protect.lians-agent")
            self.assertEqual(app.mission_ledger.list_latest(), [])
            self.assertEqual(app.list_runs(), [])

            imported = app.import_agent({"bundle_text": json.dumps(exported["bundle"])})
            self.assertEqual(imported["task"], self.payload(ROOT)["task"])
            self.assertEqual(imported["mode"], "protect")
            self.assertEqual(imported["workspace_name"], ROOT.name)
            self.assertEqual(imported["repository"], "")
            self.assertFalse(imported["route_stale"])
            self.assertEqual(imported["route"]["max_premium_calls"], 1)
            self.assertEqual(app.mission_ledger.list_latest(), [])
            self.assertEqual(app.list_runs(), [])

    def test_tampered_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LiansApplication(Path(directory) / "data", default_repository=ROOT)
            bundle = app.export_agent(self.payload(ROOT))["bundle"]
            tampered = deepcopy(bundle)
            tampered["agent"]["policy"]["premium_budget"] = 4
            with self.assertRaisesRegex(AgentBundleError, "checksum does not match"):
                verify_agent_bundle(tampered)

    def test_import_never_transfers_shell_or_custom_executable_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LiansApplication(Path(directory) / "data", default_repository=ROOT)
            hostile = self.payload(ROOT)
            hostile["verification_command"] = "python -m unittest & whoami"
            with self.assertRaisesRegex(ValueError, "portable agents require"):
                app.export_agent(hostile)

            custom = self.payload(ROOT)
            custom["verification_command"] = str(ROOT / "tools" / "checker.exe")
            with self.assertRaisesRegex(ValueError, "custom executable approvals"):
                app.export_agent(custom)

    def test_export_never_discloses_the_original_workspace_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "workspace"
            repository.mkdir()
            app = LiansApplication(root / "data", default_repository=repository)
            bundle = app.export_agent(self.payload(repository))["bundle"]
            serialized = json.dumps(bundle)
            self.assertNotIn(str(repository.resolve()), serialized)

            imported = app.import_agent({"bundle_text": serialized})
            self.assertEqual(imported["repository"], "")
            self.assertEqual(imported["workspace_name"], "workspace")

    def test_duplicate_or_unknown_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LiansApplication(Path(directory) / "data", default_repository=ROOT)
            bundle = app.export_agent(self.payload(ROOT))["bundle"]
            serialized = json.loads(json.dumps(bundle))
            serialized["unexpected"] = True
            with self.assertRaisesRegex(AgentBundleError, "unknown fields"):
                verify_agent_bundle(serialized)
            with self.assertRaisesRegex(AgentBundleError, "duplicate field"):
                parse_agent_bundle('{"schema":"one","schema":"two"}')


if __name__ == "__main__":
    unittest.main()
