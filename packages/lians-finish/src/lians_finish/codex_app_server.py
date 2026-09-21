"""Persistent Codex app-server bridge for one Lians mission.

The app-server protocol is still marked experimental by the Codex CLI.  This
adapter therefore feature-detects it and keeps the older ``codex exec`` runner
available as a compatibility path.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import __version__
from .codex_adapter import CodexRunner
from .policy import StagePlan
from .runtime import RunResult


class CodexAppServerError(RuntimeError):
    """The local Codex app-server could not complete the requested operation."""


def _usage_breakdown(value: Any) -> dict[str, int]:
    """Normalize the app-server's per-turn token counters for Lians receipts."""

    if not isinstance(value, dict):
        return {}
    aliases = {
        "inputTokens": "input_tokens",
        "cachedInputTokens": "cached_input_tokens",
        "outputTokens": "output_tokens",
        "reasoningOutputTokens": "reasoning_tokens",
        "totalTokens": "total_tokens",
        "cacheWriteInputTokens": "cache_write_input_tokens",
    }
    usage: dict[str, int] = {}
    for source, target in aliases.items():
        amount = value.get(source)
        if type(amount) is int and amount >= 0:
            usage[target] = amount
    return usage


@lru_cache(maxsize=8)
def app_server_available(executable: str = "codex") -> bool:
    """Return whether this Codex installation exposes the app-server command."""

    if shutil.which(executable) is None and not Path(executable).is_file():
        return False
    try:
        completed = subprocess.run(
            [executable, "app-server", "--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def preferred_bridge(executable: str = "codex", requested: str = "auto") -> str:
    """Choose the persistent bridge when supported, or the stable exec fallback."""

    override = os.environ.get("LIANS_CODEX_BRIDGE")
    choice = (override or requested).strip().casefold()
    if choice not in {"auto", "app-server", "exec"}:
        raise ValueError("LIANS_CODEX_BRIDGE must be auto, app-server, or exec")
    available = app_server_available(executable)
    if choice == "app-server" and not available:
        raise CodexAppServerError("This Codex installation does not expose app-server.")
    if choice == "exec" or not available:
        return "exec"
    return "app-server"


def make_codex_runner(executable: str = "codex", requested: str = "auto"):
    """Construct the selected Codex runner without changing authentication."""

    if preferred_bridge(executable, requested) == "app-server":
        return CodexAppServerRunner(executable)
    return CodexRunner(executable)


class CodexAppServerRunner:
    """Run all model stages for a mission as turns in one Codex thread."""

    preserves_context = True
    bridge_name = "codex_app_server"

    def __init__(self, executable: str = "codex", timeout_seconds: int = 1_800) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.thread_id: str | None = None
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._pending: deque[dict[str, Any]] = deque()
        self._stderr: deque[str] = deque(maxlen=120)
        self._request_id = 0
        self._write_lock = threading.Lock()

    def _start(self, repo: Path) -> None:
        if self._process is not None:
            return
        scratch = repo / ".lians" / "tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["TEMP"] = str(scratch)
        environment["TMP"] = str(scratch)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            [self.executable, "app-server", "--stdio"],
            cwd=repo,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        threading.Thread(target=self._read_stdout, name="lians-codex-events", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="lians-codex-errors", daemon=True).start()
        self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "lians_mission_control",
                    "title": "Lians",
                    "version": __version__,
                },
                "capabilities": {"experimentalApi": False},
            },
            timeout_seconds=30,
        )
        self._send({"method": "initialized"})

    def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            for line in self._process.stdout:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    self._stderr.append(f"non-JSON app-server output: {line[-500:].rstrip()}")
                    continue
                if isinstance(value, dict):
                    self._messages.put(value)
        finally:
            self._messages.put(None)

    def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        for line in self._process.stderr:
            self._stderr.append(line.rstrip())

    def _send(self, value: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise CodexAppServerError("Codex app-server is not running.")
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n"
        try:
            with self._write_lock:
                self._process.stdin.write(payload)
                self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise CodexAppServerError(self._failure_text("Codex app-server closed.")) from exc

    def _raw_message(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexAppServerError(self._failure_text("Codex app-server timed out."))
        try:
            value = self._messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise CodexAppServerError(self._failure_text("Codex app-server timed out.")) from exc
        if value is None:
            raise CodexAppServerError(self._failure_text("Codex app-server stopped."))
        return value

    def _request(
        self, method: str, params: dict[str, Any], *, timeout_seconds: int | None = None
    ) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + (timeout_seconds or self.timeout_seconds)
        while True:
            value = self._raw_message(deadline)
            if value.get("id") == request_id and "method" not in value:
                if value.get("error") is not None:
                    raise CodexAppServerError(
                        f"Codex {method} failed: {json.dumps(value['error'], ensure_ascii=False)}"
                    )
                result = value.get("result")
                if not isinstance(result, dict):
                    raise CodexAppServerError(f"Codex {method} returned an invalid response.")
                return result
            if "id" in value and isinstance(value.get("method"), str):
                self._decline_server_request(value)
            else:
                self._pending.append(value)

    def _decline_server_request(self, value: dict[str, Any]) -> None:
        """Never grant new authority from a background Lians run."""

        method = str(value.get("method", ""))
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            result: dict[str, Any] = {"decision": "decline"}
        elif method == "item/permissions/requestApproval":
            result = {"permissions": {}}
        elif method == "mcpServer/elicitation/request":
            result = {"action": "decline"}
        elif method == "tool/requestUserInput":
            result = {"answers": {}}
        else:
            self._send(
                {
                    "id": value["id"],
                    "error": {"code": -32601, "message": "Unsupported background request"},
                }
            )
            return
        self._send({"id": value["id"], "result": result})

    def _next_event(self, deadline: float) -> dict[str, Any]:
        if self._pending:
            return self._pending.popleft()
        return self._raw_message(deadline)

    @staticmethod
    def _sandbox(stage: StagePlan, repo: Path) -> dict[str, Any]:
        if stage.sandbox == "read-only":
            return {"type": "readOnly", "networkAccess": False}
        if stage.sandbox == "workspace-write":
            return {
                "type": "workspaceWrite",
                "writableRoots": [str(repo.resolve())],
                "networkAccess": False,
            }
        raise ValueError(f"unsupported Lians sandbox: {stage.sandbox}")

    def _start_thread(self, stage: StagePlan, repo: Path) -> None:
        response = self._request(
            "thread/start",
            {
                "model": stage.model,
                "cwd": str(repo.resolve()),
                "approvalPolicy": "never",
                "sandbox": stage.sandbox,
                "serviceName": "lians_mission_control",
            },
            timeout_seconds=30,
        )
        thread = response.get("thread")
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
            raise CodexAppServerError("Codex thread/start did not return a thread id.")
        self.thread_id = thread["id"]

    def run(self, stage: StagePlan, prompt: str, repo: Path) -> RunResult:
        if not stage.model or not stage.reasoning_effort or not stage.sandbox:
            raise ValueError(f"stage {stage.id!r} is not a model stage")
        self._start(repo)
        if self.thread_id is None:
            self._start_thread(stage, repo)
        response = self._request(
            "turn/start",
            {
                "threadId": self.thread_id,
                "input": [{"type": "text", "text": prompt}],
                "model": stage.model,
                "effort": stage.reasoning_effort,
                "summary": "concise",
                "cwd": str(repo.resolve()),
                "approvalPolicy": "never",
                "sandboxPolicy": self._sandbox(stage, repo),
            },
        )
        turn = response.get("turn")
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
            raise CodexAppServerError("Codex turn/start did not return a turn id.")
        turn_id = turn["id"]
        deadline = time.monotonic() + self.timeout_seconds
        messages: list[str] = []
        usage: dict[str, int] = {}
        completed_turn: dict[str, Any] | None = None

        while completed_turn is None:
            event = self._next_event(deadline)
            if "id" in event and isinstance(event.get("method"), str):
                self._decline_server_request(event)
                continue
            method = event.get("method")
            params = event.get("params")
            if not isinstance(params, dict):
                continue
            if method == "item/completed" and params.get("turnId") == turn_id:
                item = params.get("item")
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        messages.append(text.strip())
            elif method == "thread/tokenUsage/updated" and params.get("turnId") == turn_id:
                token_usage = params.get("tokenUsage")
                if isinstance(token_usage, dict):
                    usage = _usage_breakdown(token_usage.get("last"))
            elif method == "turn/completed":
                candidate = params.get("turn")
                if isinstance(candidate, dict) and candidate.get("id") == turn_id:
                    completed_turn = candidate

        if not messages:
            for item in completed_turn.get("items", []):
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        messages.append(text.strip())
        status = completed_turn.get("status")
        success = status == "completed"
        error = completed_turn.get("error")
        error_text = "" if error is None else json.dumps(error, ensure_ascii=False)
        return RunResult(
            stage_id=stage.id,
            model=stage.model,
            reasoning_effort=stage.reasoning_effort,
            premium=stage.premium,
            success=success,
            message=messages[-1] if messages else "",
            usage=usage,
            returncode=0 if success else 1,
            stderr=error_text[-4_000:],
        )

    def _failure_text(self, message: str) -> str:
        detail = "\n".join(self._stderr)[-4_000:]
        return f"{message}\n{detail}".strip()

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
