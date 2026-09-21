"""Exercise portable-agent export and import through a real Chromium page."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket


class DevTools:
    def __init__(self, url: str) -> None:
        self.connection = websocket.create_connection(url, timeout=10, origin="http://localhost")
        self.identifier = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.identifier += 1
        identifier = self.identifier
        self.connection.send(
            json.dumps({"id": identifier, "method": method, "params": params or {}})
        )
        while True:
            value = json.loads(self.connection.recv())
            if value.get("id") != identifier:
                continue
            if "error" in value:
                raise RuntimeError(f"DevTools {method} failed: {value['error']}")
            return value.get("result", {})

    def evaluate(self, expression: str, *, await_promise: bool = False):
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        )
        if "exceptionDetails" in result:
            raise RuntimeError(f"browser expression failed: {result['exceptionDetails']}")
        return result["result"].get("value")

    def close(self) -> None:
        self.connection.close()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _json_url(url: str):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.load(response)


def _wait_for(callable_value, *, timeout: float = 10, message: str):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = callable_value()
            if last:
                return last
        except (OSError, RuntimeError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError(f"{message}; last value was {last!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument(
        "--chrome",
        type=Path,
        default=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    )
    args = parser.parse_args()
    if not args.chrome.is_file():
        raise RuntimeError(f"Chrome was not found: {args.chrome}")
    output = Path(__file__).resolve().parents[1] / "output"
    output.mkdir(exist_ok=True)
    debugging_port = _free_port()
    task = "Carry this governed browser mission to another workspace"

    with tempfile.TemporaryDirectory(prefix="lians-browser-transfer-", dir=output) as temporary:
        temporary_root = Path(temporary)
        download_directory = temporary_root / "downloads"
        download_directory.mkdir()
        command = [
            str(args.chrome),
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*",
            f"--remote-debugging-port={debugging_port}",
            f"--user-data-dir={temporary_root / 'profile'}",
            args.url,
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        tools: DevTools | None = None
        try:
            targets = _wait_for(
                lambda: _json_url(f"http://127.0.0.1:{debugging_port}/json/list"),
                timeout=15,
                message="Chrome did not expose a debugging target",
            )
            page = next(item for item in targets if item.get("type") == "page")
            tools = DevTools(page["webSocketDebuggerUrl"])
            tools.call("Runtime.enable")
            tools.call(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(download_directory),
                    "eventsEnabled": True,
                },
            )
            _wait_for(
                lambda: tools.evaluate(
                    "Boolean(document.querySelector('#repository').value && "
                    "document.querySelector('#verification').value)"
                ),
                message="Lians did not finish local initialization",
            )
            repository = tools.evaluate("document.querySelector('#repository').value")
            state_before = tools.evaluate(
                "Promise.all(['/api/runs','/api/missions'].map(path => fetch(path, {headers: "
                "{'X-Lians-Token': document.querySelector('meta[name=lians-token]').content}})"
                ".then(response => response.json()))).then(([runs, missions]) => "
                "({runs: runs.runs.length, missions: missions.missions.length}))",
                await_promise=True,
            )
            tools.evaluate(
                "(() => { const task = document.querySelector('#task'); "
                f"task.value = {json.dumps(task)}; "
                "task.dispatchEvent(new Event('input', {bubbles: true})); "
                "document.querySelector('#export-agent').click(); return true; })()"
            )
            exported_path = _wait_for(
                lambda: next(
                    (
                        path
                        for path in download_directory.glob("*.lians-agent")
                        if path.is_file() and path.stat().st_size > 0
                    ),
                    None,
                ),
                message="browser export did not produce a .lians-agent file",
            )
            bundle_text = exported_path.read_text(encoding="utf-8")
            if repository in bundle_text:
                raise RuntimeError("browser export disclosed the full local workspace path")
            bundle = json.loads(bundle_text)
            if bundle["agent"]["mission"]["workspace_name"] != args.repo.resolve().name:
                raise RuntimeError("browser export lost the workspace name")

            tools.evaluate(
                "(() => { document.querySelector('#new-run').click(); "
                "document.querySelector('#repository').value = ''; return true; })()"
            )
            root = tools.call("DOM.getDocument", {"depth": 1})["root"]["nodeId"]
            file_node = tools.call(
                "DOM.querySelector", {"nodeId": root, "selector": "#agent-file"}
            )["nodeId"]
            tools.call(
                "DOM.setFileInputFiles",
                {"nodeId": file_node, "files": [str(exported_path.resolve())]},
            )

            def imported_state():
                value = tools.evaluate(
                    "(() => ({task: document.querySelector('#task').value, "
                    "repository: document.querySelector('#repository').value, "
                    "message: document.querySelector('#form-message').textContent}))()"
                )
                if value["task"] == task and "verified and imported" in value["message"]:
                    return value
                return None

            imported = _wait_for(
                imported_state,
                message="browser import did not reach its verified state",
            )
            if imported["repository"]:
                raise RuntimeError("browser import selected a workspace without user action")
            state_after = tools.evaluate(
                "Promise.all(['/api/runs','/api/missions'].map(path => fetch(path, {headers: "
                "{'X-Lians-Token': document.querySelector('meta[name=lians-token]').content}})"
                ".then(response => response.json()))).then(([runs, missions]) => "
                "({runs: runs.runs.length, missions: missions.missions.length}))",
                await_promise=True,
            )
            if state_after != state_before:
                raise RuntimeError(
                    f"browser transfer changed run or mission state: {state_before} -> {state_after}"
                )
            print(
                json.dumps(
                    {
                        "export": "passed",
                        "import": "passed",
                        "workspace_path_exported": False,
                        "workspace_selected_on_import": False,
                        "runs_before_after": [state_before["runs"], state_after["runs"]],
                        "missions_before_after": [
                            state_before["missions"],
                            state_after["missions"],
                        ],
                    },
                    separators=(",", ":"),
                )
            )
        finally:
            if tools is not None:
                try:
                    tools.call("Browser.close")
                except (OSError, RuntimeError, websocket.WebSocketException):
                    pass
                tools.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
