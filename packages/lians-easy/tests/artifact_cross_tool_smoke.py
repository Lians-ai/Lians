"""Exercise the cross-tool memory moment through a frozen Bridge artifact."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ALL_CLIENTS = (
    "antigravity",
    "claude",
    "cursor",
    "windsurf",
    "gemini",
    "codex",
    "cline",
    "opencode",
)
AUTOMATIC_HOOK_CLIENTS = ("antigravity", "claude", "codex", "gemini")


def _run(
    argv: list[str],
    *,
    environment: dict[str, str],
    input_text: str | None = None,
    timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        input=input_text,
        timeout=timeout,
    )


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _open(url: str, *, cookie: str | None = None):
    return urlopen(Request(url, headers={"Cookie": cookie} if cookie else {}), timeout=3)


def _post(origin: str, path: str, cookie: str, data: dict):
    return urlopen(
        Request(
            f"{origin}{path}",
            data=json.dumps(data).encode(),
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                "Origin": origin,
            },
            method="POST",
        ),
        timeout=20,
    )


def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"
        subprocess.run(
            [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    else:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=5)


def _wait_for_bridge(origin: str, process: subprocess.Popen[bytes]) -> str:
    deadline = time.monotonic() + 25
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"Frozen Bridge exited with {process.returncode}")
        try:
            with _open(origin) as response:
                response.read()
                return response.headers["Set-Cookie"].split(";", 1)[0]
        except URLError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Frozen Bridge did not become ready in time")
            time.sleep(0.1)


def _context(
    origin: str,
    cookie: str,
    *,
    client: str,
    prompt: str,
    project: Path,
) -> dict:
    with _post(
        origin,
        "/v1/context",
        cookie,
        {
            "prompt": prompt,
            "client": client,
            "cwd": str(project),
            "limit": 3,
            "max_tokens": 118,
        },
    ) as response:
        return json.loads(response.read())


def _assert_receipt(pack: dict, *, content: str, client: str) -> None:
    assert content in pack["context"]
    assert pack["receipt_line"].startswith("1 memories used · Lians ")
    receipt = pack["receipt"]
    assert receipt["client"] == client
    assert receipt["memory_count"] == 1
    assert 0 < receipt["token_estimate"] <= 118
    assert receipt["limits"] == {"max_memories": 3, "max_tokens": 118}
    assert receipt["memories"][0]["reason"]
    assert receipt["memories"][0]["source"] == "Explicit cross-tool smoke instruction"
    assert receipt["memories"][0]["updated_at"]
    assert receipt["signature"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    arguments = parser.parse_args()
    binary = arguments.binary.resolve()
    if not binary.is_file():
        raise SystemExit(f"Frozen Bridge was not found: {binary}")

    with tempfile.TemporaryDirectory(prefix="lians-cross-tool-artifact-") as directory:
        fixture = Path(directory)
        home = fixture / "home"
        roaming = fixture / "roaming"
        local = fixture / "local"
        data_home = fixture / "lians"
        project = fixture / "sample-project"
        for path in (home, roaming, local, project):
            path.mkdir()
        (project / ".git").mkdir()
        environment = os.environ.copy()
        environment.update(
            {
                "USERPROFILE": str(home),
                "HOME": str(home),
                "APPDATA": str(roaming),
                "LOCALAPPDATA": str(local),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "LIANS_EASY_HOME": str(data_home),
            }
        )

        installed = _run(
            [str(binary), "install", "--clients", "all", "--yes", "--json"],
            environment=environment,
        )
        assert installed.returncode == 0, (installed.stdout, installed.stderr)
        setup = json.loads(installed.stdout)
        assert setup["status"] == "installed"
        assert {item["client"] for item in setup["clients"]} == set(ALL_CLIENTS)
        assert len(setup["clients"]) == len(ALL_CLIENTS)
        assert all(item["status"] == "installed" for item in setup["clients"])
        assert all(
            item.get("automatic_recall") is True
            for item in setup["clients"]
            if item["client"] in {"antigravity", "claude", "cursor", "gemini", "codex"}
        )

        data_path = data_home / "memory.sqlite3"
        port = _available_port()
        origin = f"http://127.0.0.1:{port}"
        process = subprocess.Popen(
            [
                str(binary),
                "bridge",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--data",
                str(data_path),
            ],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
        )
        original = "We use FastAPI and never write migrations manually."
        corrected = "We use FastAPI and generate migrations with Alembic."
        prompt = "Set up the FastAPI service and database migrations."
        try:
            cookie = _wait_for_bridge(origin, process)
            with _post(
                origin,
                "/v1/remember",
                cookie,
                {
                    "content": original,
                    "scope": "global",
                    "kind": "preference",
                    "source": "Explicit cross-tool smoke instruction",
                    "client": "cursor",
                    "cwd": str(project),
                },
            ) as response:
                remembered = json.loads(response.read())["memory"]

            cursor_rule = project / ".cursor" / "rules" / "lians-memory.mdc"
            assert original in cursor_rule.read_text(encoding="utf-8")
            for client in ALL_CLIENTS:
                _assert_receipt(
                    _context(
                        origin,
                        cookie,
                        client=client,
                        prompt=prompt,
                        project=project,
                    ),
                    content=original,
                    client=client,
                )

            hook_event = json.dumps({"prompt": prompt, "cwd": str(project), "invocationNum": 0})
            for client in AUTOMATIC_HOOK_CLIENTS:
                hook = _run(
                    [
                        str(binary),
                        "hook",
                        "--client",
                        client,
                        "--data",
                        str(data_path),
                    ],
                    environment=environment,
                    input_text=hook_event,
                )
                assert hook.returncode == 0, (hook.stdout, hook.stderr)
                assert original in hook.stdout

            with _post(
                origin,
                f"/v1/memories/{remembered['id']}/correct",
                cookie,
                {"content": corrected, "cwd": str(project)},
            ) as response:
                replacement = json.loads(response.read())["memory"]
            for client in ALL_CLIENTS:
                pack = _context(
                    origin,
                    cookie,
                    client=client,
                    prompt=prompt,
                    project=project,
                )
                _assert_receipt(pack, content=corrected, client=client)
                assert original not in pack["context"]

            with _post(
                origin,
                f"/v1/memories/{replacement['id']}/forget",
                cookie,
                {"confirmed": True, "cwd": str(project)},
            ) as response:
                assert json.loads(response.read())["status"] == "forgotten"
            for client in ALL_CLIENTS:
                pack = _context(
                    origin,
                    cookie,
                    client=client,
                    prompt=prompt,
                    project=project,
                )
                assert pack["context"] == ""
                assert pack["receipt"]["memory_count"] == 0

            print(
                json.dumps(
                    {
                        "automatic_hook_clients": list(AUTOMATIC_HOOK_CLIENTS),
                        "clients_connected": list(ALL_CLIENTS),
                        "correction_applied_everywhere": True,
                        "forget_applied_everywhere": True,
                        "receipts_bounded_and_signed": True,
                        "remembered_across_tools": True,
                    },
                    sort_keys=True,
                )
            )
        finally:
            _stop_process_tree(process)


if __name__ == "__main__":
    main()
