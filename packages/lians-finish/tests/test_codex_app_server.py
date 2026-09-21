from __future__ import annotations

import os
import queue
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from lians_finish.codex_app_server import (
    CodexAppServerError,
    CodexAppServerRunner,
    _usage_breakdown,
    app_server_available,
    make_codex_runner,
    preferred_bridge,
)
from lians_finish.codex_adapter import CodexRunner
from lians_finish.policy import StagePlan


class StubAppServerRunner(CodexAppServerRunner):
    def __init__(self) -> None:
        super().__init__("codex-test", timeout_seconds=2)
        self.sent = []

    def _start(self, repo: Path) -> None:
        return

    def _start_thread(self, stage: StagePlan, repo: Path) -> None:
        self.thread_id = "thread-123"

    def _request(self, method, params, *, timeout_seconds=None):
        self.sent.append((method, params))
        if method == "turn/start":
            return {"turn": {"id": "turn-123", "items": [], "status": "inProgress"}}
        raise AssertionError(method)


class ProtocolRunner(CodexAppServerRunner):
    def __init__(self) -> None:
        super().__init__("codex-test", timeout_seconds=1)
        self.sent = []

    def _send(self, value):
        self.sent.append(value)


class FakeProcess:
    def __init__(self, *, timeouts: int = 0) -> None:
        self.stdin = None
        self._timeouts = timeouts
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        if self._timeouts:
            self._timeouts -= 1
            import subprocess

            raise subprocess.TimeoutExpired("codex", timeout)
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class CodexAppServerTests(unittest.TestCase):
    def tearDown(self):
        app_server_available.cache_clear()

    def test_feature_detection_and_runner_selection_are_explicit(self):
        with patch("lians_finish.codex_app_server.shutil.which", return_value=None):
            self.assertFalse(app_server_available("definitely-missing-codex"))
        app_server_available.cache_clear()
        completed = type("Completed", (), {"returncode": 0})()
        with (
            patch("lians_finish.codex_app_server.shutil.which", return_value="codex"),
            patch("lians_finish.codex_app_server.subprocess.run", return_value=completed),
        ):
            self.assertTrue(app_server_available("codex-test"))
        with patch("lians_finish.codex_app_server.app_server_available", return_value=False):
            self.assertEqual(preferred_bridge("codex-test", "auto"), "exec")
            self.assertIsInstance(make_codex_runner("codex-test", "auto"), CodexRunner)
            with self.assertRaises(CodexAppServerError):
                preferred_bridge("codex-test", "app-server")
        with patch.dict(os.environ, {"LIANS_CODEX_BRIDGE": "invalid"}):
            with self.assertRaises(ValueError):
                preferred_bridge("codex-test")

    def test_auto_prefers_persistent_thread_and_exec_remains_available(self):
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("lians_finish.codex_app_server.app_server_available", return_value=True),
        ):
            os.environ.pop("LIANS_CODEX_BRIDGE", None)
            self.assertEqual(preferred_bridge("codex-test", "auto"), "app-server")
            self.assertEqual(preferred_bridge("codex-test", "exec"), "exec")

    def test_app_server_turn_captures_final_message_and_per_turn_usage(self):
        runner = StubAppServerRunner()
        stage = StagePlan(
            id="discover",
            purpose="Inspect",
            model="gpt-5.6-luna",
            reasoning_effort="low",
            sandbox="read-only",
            run_when="always",
            premium=False,
            reason="bounded discovery",
        )
        runner._pending.extend(
            [
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-123",
                        "turnId": "turn-123",
                        "item": {
                            "id": "item-1",
                            "type": "agentMessage",
                            "text": "Use one persistent thread.",
                        },
                    },
                },
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "threadId": "thread-123",
                        "turnId": "turn-123",
                        "tokenUsage": {
                            "last": {
                                "inputTokens": 80,
                                "cachedInputTokens": 50,
                                "outputTokens": 12,
                                "reasoningOutputTokens": 3,
                                "totalTokens": 95,
                            }
                        },
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-123",
                        "turn": {
                            "id": "turn-123",
                            "items": [],
                            "status": "completed",
                            "error": None,
                        },
                    },
                },
            ]
        )

        result = runner.run(stage, "Inspect the repository", ROOT)

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Use one persistent thread.")
        self.assertEqual(result.usage["input_tokens"], 80)
        self.assertEqual(result.usage["cached_input_tokens"], 50)
        request = runner.sent[0][1]
        self.assertEqual(request["threadId"], "thread-123")
        self.assertEqual(request["sandboxPolicy"]["type"], "readOnly")
        self.assertFalse(request["sandboxPolicy"]["networkAccess"])

    def test_usage_breakdown_ignores_invalid_or_negative_values(self):
        self.assertEqual(
            _usage_breakdown(
                {
                    "inputTokens": 10,
                    "cachedInputTokens": -1,
                    "outputTokens": True,
                    "totalTokens": 14,
                }
            ),
            {"input_tokens": 10, "total_tokens": 14},
        )

    def test_protocol_request_preserves_notifications_and_surfaces_errors(self):
        runner = ProtocolRunner()
        runner._messages.put({"method": "thread/started", "params": {"thread": {}}})
        runner._messages.put({"id": 1, "result": {"ok": True}})
        self.assertEqual(runner._request("test/method", {}), {"ok": True})
        self.assertEqual(runner._next_event(10**12)["method"], "thread/started")
        runner._messages.put({"id": 2, "error": {"code": 7, "message": "failed"}})
        with self.assertRaisesRegex(CodexAppServerError, "test/error failed"):
            runner._request("test/error", {})
        runner._messages.put(None)
        with self.assertRaisesRegex(CodexAppServerError, "stopped"):
            runner._raw_message(10**12)
        runner._messages = queue.Queue()
        with self.assertRaisesRegex(CodexAppServerError, "timed out"):
            runner._raw_message(0)

    def test_background_authority_requests_are_declined(self):
        runner = ProtocolRunner()
        cases = {
            "item/commandExecution/requestApproval": {"decision": "decline"},
            "item/fileChange/requestApproval": {"decision": "decline"},
            "item/permissions/requestApproval": {"permissions": {}},
            "mcpServer/elicitation/request": {"action": "decline"},
            "tool/requestUserInput": {"answers": {}},
        }
        for index, (method, expected) in enumerate(cases.items(), start=1):
            runner._decline_server_request({"id": index, "method": method})
            self.assertEqual(runner.sent[-1], {"id": index, "result": expected})
        runner._decline_server_request({"id": 9, "method": "unknown/request"})
        self.assertEqual(runner.sent[-1]["error"]["code"], -32601)

    def test_sandbox_policy_is_bounded_and_close_stops_only_its_child(self):
        read = StagePlan("read", "read", "model", "low", "read-only", "always", False, "")
        write = StagePlan("write", "write", "model", "low", "workspace-write", "always", False, "")
        invalid = StagePlan("bad", "bad", "model", "low", "danger", "always", False, "")
        self.assertEqual(CodexAppServerRunner._sandbox(read, ROOT)["type"], "readOnly")
        write_policy = CodexAppServerRunner._sandbox(write, ROOT)
        self.assertEqual(write_policy["type"], "workspaceWrite")
        self.assertEqual(write_policy["writableRoots"], [str(ROOT.resolve())])
        with self.assertRaises(ValueError):
            CodexAppServerRunner._sandbox(invalid, ROOT)

        runner = CodexAppServerRunner()
        process = FakeProcess(timeouts=2)
        runner._process = process
        runner.close()
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        runner.close()


if __name__ == "__main__":
    unittest.main()
