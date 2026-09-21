"""A thin local execution connector for the hosted Lians workspace.

The hosted service receives mission text and bounded status/receipt metadata.
Repository paths, verifier commands, source code, terminal output, and Codex
credentials remain in this local configuration and process.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .app import LiansApplication
from .codex_app_server import make_codex_runner
from .runtime import parse_verifier_command

CONFIG_SCHEMA = "lians.hosted-connector.v1"
TERMINAL_STATUSES = {"PASS", "BLOCK", "ERROR", "INTERRUPTED"}


class HostedConnectorError(RuntimeError):
    """A safe connector error suitable for the local terminal."""


def default_config_path() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if root:
        return Path(root) / "Lians Finish" / "hosted-connector.json"
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    base = Path(home).resolve() if home else Path.cwd()
    return base / ".lians-finish" / "hosted-connector.json"


def _server_origin(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not parsed.hostname
        or parsed.scheme not in {"http", "https"}
    ):
        raise HostedConnectorError("--server must be an HTTP origin without a path or credentials")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HostedConnectorError("Hosted connectors require HTTPS outside local development")
    return raw


def _json_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HostedConnectorError(
            f"No hosted connector is configured at {path}. Run `lians-finish connect` first."
        ) from exc
    except (OSError, ValueError) as exc:
        raise HostedConnectorError(f"Could not read connector configuration: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != CONFIG_SCHEMA:
        raise HostedConnectorError("Connector configuration has an unsupported format")
    if not isinstance(value.get("token"), str) or len(value["token"]) < 32:
        raise HostedConnectorError("Connector configuration is missing its local token")
    projects = value.get("projects")
    if not isinstance(projects, list) or not projects:
        raise HostedConnectorError("Connector configuration has no local projects")
    return value


def save_config(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class HostedAPI:
    """Small standard-library client for the hosted mission endpoints."""

    def __init__(self, server: str, token: str | None = None, timeout: int = 30) -> None:
        self.server = _server_origin(server)
        self.token = token
        self.timeout = timeout

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        headers = {
            "Accept": "application/json",
            "User-Agent": "lians-finish-hosted-connector/0.11",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(f"{self.server}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - validated origin
                raw = response.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    raise HostedConnectorError("Hosted response exceeded the connector limit")
                if response.status == 204 or not raw:
                    return None
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise HostedConnectorError("Hosted response was not a JSON object")
                return value
        except HTTPError as exc:
            try:
                raw = exc.read(64 * 1024)
                value = json.loads(raw.decode("utf-8"))
                message = value.get("error") if isinstance(value, dict) else None
            except (OSError, UnicodeDecodeError, ValueError):
                message = None
            raise HostedConnectorError(message or f"Hosted request failed ({exc.code})") from exc
        except (URLError, TimeoutError) as exc:
            detail = getattr(exc, "reason", exc)
            raise HostedConnectorError(f"Could not reach {self.server}: {detail}") from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise HostedConnectorError("Hosted response was not valid JSON") from exc

    def claim(self, code: str, name: str, label: str) -> dict[str, Any]:
        value = self._request(
            "POST",
            "/api/connectors/claim",
            {
                "code": code,
                "name": name,
                "projects": [{"label": label, "verification": "configured"}],
            },
        )
        if not value or not isinstance(value.get("token"), str):
            raise HostedConnectorError("Hosted pairing response did not include a connector token")
        return value

    def next_mission(self) -> dict[str, Any] | None:
        value = self._request("GET", "/api/connector/missions/next")
        if value is None:
            return None
        mission = value.get("mission")
        if not isinstance(mission, dict):
            raise HostedConnectorError("Hosted queue returned an invalid mission")
        return mission

    def event(self, mission_id: str, lease: str, kind: str) -> None:
        self._request(
            "POST",
            f"/api/connector/missions/{mission_id}/event",
            {"lease": lease, "kind": kind},
        )

    def complete(self, mission_id: str, lease: str, receipt: dict[str, Any]) -> None:
        self._request(
            "POST",
            f"/api/connector/missions/{mission_id}/complete",
            {"lease": lease, "receipt": receipt},
        )


def pair_connector(
    api: HostedAPI,
    *,
    code: str,
    name: str,
    repository: Path,
    label: str,
    verification_command: str,
    allowed_verifiers: list[str],
    codex_bin: str,
    codex_bridge: str,
) -> dict[str, Any]:
    repository = repository.resolve()
    if not repository.is_dir():
        raise HostedConnectorError(f"Project folder does not exist: {repository}")
    try:
        parse_verifier_command(verification_command)
    except ValueError as exc:
        raise HostedConnectorError(f"Proof command is not allowed: {exc}") from exc
    claimed = api.claim(code, name, label)
    connector = claimed.get("connector")
    projects = connector.get("projects") if isinstance(connector, dict) else None
    if not isinstance(projects, list) or len(projects) != 1:
        raise HostedConnectorError("Hosted pairing response did not return the local project")
    project = projects[0]
    if not isinstance(project, dict) or not isinstance(project.get("id"), str):
        raise HostedConnectorError("Hosted pairing response returned an invalid project")
    return {
        "schema": CONFIG_SCHEMA,
        "server": api.server,
        "connector_id": connector["id"],
        "token": claimed["token"],
        "name": name,
        "projects": [
            {
                "id": project["id"],
                "label": label,
                "repository": str(repository),
                "verification_command": verification_command,
                "allowed_verifiers": list(allowed_verifiers),
            }
        ],
        "codex_bin": codex_bin,
        "codex_bridge": codex_bridge,
    }


class LocalMissionExecutor:
    def __init__(self, config_path: Path, config: dict[str, Any]) -> None:
        self.config_path = config_path
        self.config = config

    def __call__(
        self,
        mission: dict[str, Any],
        project: dict[str, Any],
        notify: Callable[[str], None],
    ) -> dict[str, Any]:
        repository = Path(project["repository"]).resolve()
        if not repository.is_dir():
            raise HostedConnectorError(f"Local project is unavailable: {project['label']}")
        notify("planning")
        runner_factory = lambda: make_codex_runner(  # noqa: E731 - injected factory
            self.config.get("codex_bin", "codex"),
            self.config.get("codex_bridge", "auto"),
        )
        app = LiansApplication(
            self.config_path.parent / "hosted-runs",
            default_repository=repository,
            runner_factory=runner_factory,
        )
        job = app.start_run(
            {
                "repository": str(repository),
                "task": mission["goal"],
                "mode": mission["mode"],
                "constraints": mission.get("constraints", []),
                "definition_of_done": mission["definitionOfDone"],
                "verification_command": project["verification_command"],
                "allowed_verifiers": project.get("allowed_verifiers", []),
            }
        )
        notify("implementing")
        job_id = job["id"]
        verification_reported = False
        while True:
            current = app.wait(job_id, timeout=1)
            if not verification_reported and any(
                event.get("kind") == "verification_started"
                for event in current.get("events", [])
                if isinstance(event, dict)
            ):
                notify("verifying")
                verification_reported = True
            if current.get("status") in TERMINAL_STATUSES:
                break
        return self._bounded_receipt(current, mission, project)

    @staticmethod
    def _bounded_receipt(
        job: dict[str, Any], mission: dict[str, Any], project: dict[str, Any]
    ) -> dict[str, Any]:
        receipt = job.get("receipt") if isinstance(job.get("receipt"), dict) else {}
        status = (
            "PASS"
            if job.get("status") == "PASS"
            else ("ERROR" if job.get("status") in {"ERROR", "INTERRUPTED"} else "FAIL")
        )
        verifications = receipt.get("verification")
        last = verifications[-1] if isinstance(verifications, list) and verifications else {}
        command = parse_verifier_command(project["verification_command"])
        command_digest = receipt.get("verifier_command_sha256") or _json_digest(list(command))
        receipt_digest = receipt.get("receipt_sha256") or _json_digest(
            {"mission": mission.get("id"), "status": status, "command": command_digest}
        )
        return {
            "status": status,
            "modelCalls": int(receipt.get("model_calls", 0)),
            "premiumCalls": int(receipt.get("premium_calls", 0)),
            "executionBridge": job.get("execution_bridge", "unknown"),
            "receiptSha256": receipt_digest,
            "verification": {
                "success": status == "PASS",
                "exitCode": last.get("returncode") if isinstance(last, dict) else None,
                "commandSha256": command_digest,
                "durationSeconds": None,
            },
        }


class HostedConnector:
    """Claim at most one mission and execute it with the configured local project."""

    def __init__(
        self,
        api: HostedAPI,
        config: dict[str, Any],
        executor: Callable[[dict[str, Any], dict[str, Any], Callable[[str], None]], dict[str, Any]],
    ) -> None:
        self.api = api
        self.config = config
        self.executor = executor

    def run_once(self) -> dict[str, Any] | None:
        mission = self.api.next_mission()
        if mission is None:
            return None
        mission_id = mission.get("id")
        lease = mission.get("lease")
        if not isinstance(mission_id, str) or not isinstance(lease, str):
            raise HostedConnectorError("Claimed mission is missing its lease")
        project = next(
            (
                item
                for item in self.config["projects"]
                if item.get("id") == mission.get("projectId")
            ),
            None,
        )
        if project is None:
            raise HostedConnectorError("Claimed mission targets an unknown local project")

        def notify(kind: str) -> None:
            self.api.event(mission_id, lease, kind)

        try:
            receipt = self.executor(mission, project, notify)
        except Exception as exc:  # noqa: BLE001 - report bounded failure before surfacing it locally
            command = parse_verifier_command(project["verification_command"])
            command_digest = _json_digest(list(command))
            receipt = {
                "status": "ERROR",
                "modelCalls": 0,
                "premiumCalls": 0,
                "executionBridge": "unknown",
                "receiptSha256": _json_digest(
                    {"mission": mission_id, "status": "ERROR", "command": command_digest}
                ),
                "verification": {
                    "success": False,
                    "exitCode": None,
                    "commandSha256": command_digest,
                    "durationSeconds": None,
                },
            }
            self.api.complete(mission_id, lease, receipt)
            raise HostedConnectorError(f"Mission stopped locally: {exc}") from exc
        self.api.complete(mission_id, lease, receipt)
        return {"mission_id": mission_id, "status": receipt["status"]}


def connect_defaults() -> tuple[str, str]:
    return platform.node() or "This computer", "https://www.lians.ai"


def run_agent_once(config_path: Path) -> dict[str, Any] | None:
    config = load_config(config_path)
    api = HostedAPI(config["server"], config["token"])
    connector = HostedConnector(api, config, LocalMissionExecutor(config_path, config))
    return connector.run_once()
