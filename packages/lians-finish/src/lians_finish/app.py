"""Loopback-only web application for Lians Finish."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import __version__
from .bundle import (
    AgentBundleError,
    bundle_filename,
    create_agent_bundle,
    parse_agent_bundle,
    verify_agent_bundle,
)
from .beta import beta_report_filename, build_beta_report, create_beta_context
from .codex_app_server import app_server_available, make_codex_runner, preferred_bridge
from .governance import MissionInput, MissionLedger, discover_proof, summarize_usage
from .policy import MODE_PREMIUM_BUDGETS, RouteError, build_route
from .runtime import (
    AgentRunner,
    FinishRuntime,
    SubprocessVerifier,
    parse_verifier_command,
    planned_receipt,
    write_artifacts,
)

MAX_BODY_BYTES = 64 * 1024
ACTIVE_STATUSES = {"QUEUED", "RUNNING"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _default_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "Lians Finish"
    return Path.home() / ".lians-finish"


def _safe_job_id(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 64
        and all(character.isalnum() or character in "-_" for character in value)
    )


def _request_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ApplicationError(f"Duplicate request field: {key}")
        value[key] = item
    return value


def _reject_request_constant(value: str) -> Any:
    raise ApplicationError(f"Request contains a non-finite number: {value}")


class ApplicationError(ValueError):
    """A safe error that can be returned to the local user interface."""


class LiansApplication:
    """Application service shared by the HTTP layer and tests."""

    def __init__(
        self,
        data_dir: Path,
        *,
        default_repository: Path | None = None,
        runner_factory: Callable[[], AgentRunner] | None = None,
        token: str | None = None,
    ) -> None:
        self.data_dir = data_dir.resolve()
        self.runs_dir = self.data_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.mission_ledger = MissionLedger(self.data_dir / "missions")
        self.default_repository = (default_repository or Path.cwd()).resolve()
        self.runner_factory = runner_factory or make_codex_runner
        self.token = token or secrets.token_urlsafe(32)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._picker_lock = threading.Lock()
        self._recover_interrupted_jobs()

    def config(self) -> dict[str, Any]:
        return {
            "product": "Lians Mission Control",
            "default_repository": str(self.default_repository),
            "agent_stack": self._detect_agent_stack(),
            "codex_bridge": self._codex_bridge(),
            "usage": self.usage_summary(),
            "modes": [
                {
                    "name": name,
                    "premium_budget": MODE_PREMIUM_BUDGETS[name],
                    "summary": {
                        "protect": "Save premium usage",
                        "balanced": "Reliable everyday default",
                        "maximum": "Use the strongest route",
                    }[name],
                }
                for name in ("protect", "balanced", "maximum")
            ],
        }

    def readiness(self) -> dict[str, Any]:
        """Report whether the local runtime has everything required to execute."""

        agents = self._detect_agent_stack()
        codex = next(agent for agent in agents if agent["id"] == "codex")
        checks = {
            "data_directory_writable": os.access(self.data_dir, os.W_OK),
            "repository_available": self.default_repository.is_dir(),
            "codex_routable": codex["routable"],
            "active_runs": sum(job["status"] in ACTIVE_STATUSES for job in self._all_jobs()),
        }
        ready = all(
            checks[name]
            for name in ("data_directory_writable", "repository_available", "codex_routable")
        )
        return {
            "status": "ready" if ready else "degraded",
            "checks": checks,
            "claim_boundary": (
                "Readiness checks local execution prerequisites only; it does not certify "
                "a model provider, an external integration, or market readiness."
            ),
        }

    @staticmethod
    def _detect_agent_stack() -> list[dict[str, Any]]:
        agents = (
            ("codex", "Codex", "codex", True),
            ("claude", "Claude Code", "claude", False),
            ("cursor", "Cursor Agent", "cursor-agent", False),
            ("gemini", "Gemini CLI", "gemini", False),
        )
        values = []
        for agent_id, name, executable, routable_now in agents:
            installed = shutil.which(executable) is not None
            values.append(
                {
                    "id": agent_id,
                    "name": name,
                    "installed": installed,
                    "routable": installed and routable_now,
                    "status": (
                        "ready"
                        if installed and routable_now
                        else "detected"
                        if installed
                        else "not_detected"
                    ),
                }
            )
        return values

    @staticmethod
    def _codex_bridge() -> dict[str, Any]:
        installed = shutil.which("codex") is not None
        available = installed and app_server_available("codex")
        try:
            selected = preferred_bridge("codex") if installed else "unavailable"
        except (ValueError, RuntimeError):
            selected = "unavailable"
        return {
            "selected": selected,
            "persistent_thread": selected == "app-server",
            "app_server_available": available,
            "status": "beta" if selected == "app-server" else "compatibility",
            "claim_boundary": (
                "Codex app-server keeps one thread across a mission but is experimental. "
                "Older Codex installations use one-shot codex exec."
            ),
        }

    @staticmethod
    def _repository(payload: dict[str, Any]) -> Path:
        raw = payload.get("repository")
        if not isinstance(raw, str) or not raw.strip():
            raise ApplicationError("Choose a repository first.")
        repository = Path(raw).expanduser().resolve()
        if not repository.is_dir():
            raise ApplicationError(f"Repository folder does not exist: {repository}")
        return repository

    def _mission(self, payload: dict[str, Any], repository: Path) -> dict[str, Any]:
        try:
            value = MissionInput.from_payload(payload, repository)
            return self.mission_ledger.record(value)
        except (TypeError, ValueError) as exc:
            raise ApplicationError(str(exc)) from exc

    def inspect_workspace(self, payload: dict[str, Any]) -> dict[str, Any]:
        repository = self._repository(payload)
        return {
            "repository": str(repository),
            "proof_suggestions": [
                suggestion.to_dict() for suggestion in discover_proof(repository)
            ],
            "agent_stack": self._detect_agent_stack(),
            "usage": self.usage_summary(),
        }

    @staticmethod
    def _route(payload: dict[str, Any]):
        task = payload.get("task")
        mode = payload.get("mode", "protect")
        premium_budget = payload.get("premium_budget")
        if not isinstance(task, str) or not task.strip():
            raise ApplicationError("Describe the outcome you want Lians to finish.")
        if not isinstance(mode, str):
            raise ApplicationError("Choose a valid mode.")
        if premium_budget is not None and type(premium_budget) is not int:
            raise ApplicationError("Premium budget must be a whole number.")
        try:
            return build_route(task.strip(), mode=mode, premium_budget=premium_budget)
        except RouteError as exc:
            raise ApplicationError(str(exc)) from exc

    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        repository = self._repository(payload)
        route = self._route(payload)
        mission = self._mission(payload, repository)
        return {
            "repository": str(repository),
            "route": route.to_dict(),
            "mission": mission,
            "receipt": planned_receipt(route, mission_sha256=mission["mission_sha256"]),
        }

    def export_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a portable agent without recording or executing its mission."""

        repository = self._repository(payload)
        route = self._route(payload)
        command = payload.get("verification_command")
        if not isinstance(command, str):
            raise ApplicationError("Add a proof command before exporting the agent.")
        try:
            mission = MissionInput.from_payload(payload, repository)
            bundle = create_agent_bundle(
                mission,
                route,
                command,
                product_version=__version__,
            )
        except (AgentBundleError, TypeError, ValueError) as exc:
            raise ApplicationError(str(exc)) from exc
        return {
            "filename": bundle_filename(repository, route.mode),
            "bundle": bundle,
            "claim_boundary": (
                "The export contains mission, policy, and proof configuration only. "
                "It contains no provider credentials and does not execute anything."
            ),
        }

    @staticmethod
    def import_agent(payload: dict[str, Any]) -> dict[str, Any]:
        """Verify a portable agent and return inert form values."""

        if set(payload) != {"bundle_text"} or not isinstance(payload["bundle_text"], str):
            raise ApplicationError("Import requires exactly one agent bundle file.")
        try:
            return verify_agent_bundle(parse_agent_bundle(payload["bundle_text"]))
        except (AgentBundleError, TypeError, ValueError) as exc:
            raise ApplicationError(str(exc)) from exc

    @staticmethod
    def _verifier(payload: dict[str, Any]) -> tuple[SubprocessVerifier, list[str]]:
        raw = payload.get("verification_command")
        if not isinstance(raw, str) or not raw.strip():
            raise ApplicationError(
                "Add a verification command. Lians will not claim completion unchecked."
            )
        allowed = payload.get("allowed_verifiers", [])
        if not isinstance(allowed, list) or any(not isinstance(path, str) for path in allowed):
            raise ApplicationError("Allowed verifier paths must be a list of paths.")
        try:
            verifier = SubprocessVerifier(parse_verifier_command(raw), allowed_paths=allowed)
        except ValueError as exc:
            raise ApplicationError(str(exc)) from exc
        return verifier, allowed

    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        repository = self._repository(payload)
        route = self._route(payload)
        mission = self._mission(payload, repository)
        if mission["status"] == "expired":
            raise ApplicationError(
                "This mission has expired. Update its valid-until time before starting."
            )
        verifier, allowed_paths = self._verifier(payload)
        try:
            beta_context = create_beta_context(
                self.data_dir,
                repository,
                task=mission["goal"],
                constraints=mission["constraints"],
                definition_of_done=mission["definition_of_done"],
                verifier_command=verifier.command,
                product_version=__version__,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ApplicationError(f"Could not prepare the local beta receipt: {exc}") from exc
        with self._lock:
            if any(job["status"] in ACTIVE_STATUSES for job in self._jobs.values()):
                raise ApplicationError(
                    "A run is already active. Let it finish before starting another."
                )
            job_id = f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
            output = self.runs_dir / job_id
            job = {
                "id": job_id,
                "status": "QUEUED",
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "repository": str(repository),
                "output": str(output),
                "route": route.to_dict(),
                "mission": mission,
                "beta_context": beta_context,
                "events": [
                    {
                        "at": _now_iso(),
                        "kind": "queued",
                        "message": "Run queued locally.",
                    }
                ],
            }
            self._jobs[job_id] = job
            thread = threading.Thread(
                target=self._execute,
                args=(
                    job_id,
                    route,
                    mission,
                    repository,
                    verifier,
                    allowed_paths,
                    output,
                ),
                name=f"lians-{job_id}",
                daemon=True,
            )
            self._threads[job_id] = thread
            self._persist_job(job_id)
            thread.start()
            return self._copy_job(job)

    def _event(self, job_id: str, kind: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["updated_at"] = _now_iso()
            job["events"].append({"at": job["updated_at"], "kind": kind, "message": message})

    def _execute(
        self,
        job_id: str,
        route: Any,
        mission: dict[str, Any],
        repository: Path,
        verifier: SubprocessVerifier,
        allowed_paths: list[str],
        output: Path,
    ) -> None:
        started = time.monotonic()
        try:
            with self._lock:
                self._jobs[job_id]["status"] = "RUNNING"
            self._event(
                job_id,
                "running",
                f"Running the {route.mode.title()} route with a premium budget of "
                f"{route.max_premium_calls}.",
            )
            self._persist_job(job_id)
            runner = self.runner_factory()
            receipt = FinishRuntime(
                runner,
                event_sink=lambda kind, message: self._event(job_id, kind, message),
            ).execute(
                route,
                repository,
                verifier,
                mission=mission,
                allowed_paths=allowed_paths,
            )
            write_artifacts(output, route, receipt, mission=mission)
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = receipt["status"]
                job["receipt"] = receipt
                job["execution_bridge"] = getattr(runner, "bridge_name", "codex_exec")
                thread_id = getattr(runner, "thread_id", None)
                if isinstance(thread_id, str):
                    job["codex_thread_id"] = thread_id
                job["duration_seconds"] = round(time.monotonic() - started, 2)
            self._event(
                job_id,
                receipt["status"].lower(),
                (
                    "Verification passed. The receipt is ready."
                    if receipt["status"] == "PASS"
                    else "The verifier did not pass. Lians did not claim completion."
                ),
            )
        except Exception as exc:  # noqa: BLE001 - preserve any failed background job
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "ERROR"
                job["error"] = str(exc)
                job["duration_seconds"] = round(time.monotonic() - started, 2)
            self._event(job_id, "error", "The run stopped before it earned a receipt.")
        finally:
            close = getattr(locals().get("runner"), "close", None)
            try:
                if callable(close):
                    close()
            finally:
                self._persist_job(job_id)

    def _persist_job(self, job_id: str) -> None:
        with self._lock:
            job = self._copy_job(self._jobs[job_id])
        output = Path(job["output"])
        output.mkdir(parents=True, exist_ok=True)
        self._atomic_json(output / "job.json", job)

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        """Durably replace JSON so readers never observe a partial job record."""

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(6):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.01 * (attempt + 1))
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _recover_interrupted_jobs(self) -> None:
        """Turn orphaned active records into truthful terminal records after restart."""

        for path in self.runs_dir.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(job, dict) or job.get("status") not in ACTIVE_STATUSES:
                    continue
                recovered_at = _now_iso()
                events = job.setdefault("events", [])
                if not isinstance(events, list):
                    events = []
                    job["events"] = events
                job["status"] = "INTERRUPTED"
                job["updated_at"] = recovered_at
                job["error"] = (
                    "The previous Lians process stopped before this run earned a receipt."
                )
                events.append(
                    {
                        "at": recovered_at,
                        "kind": "interrupted",
                        "message": "Run recovered after the local process stopped.",
                    }
                )
                self._atomic_json(path, job)
            except (OSError, ValueError, TypeError):
                continue

    @staticmethod
    def _copy_job(job: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(job, ensure_ascii=False))

    def get_job(self, job_id: str) -> dict[str, Any]:
        if not _safe_job_id(job_id):
            raise ApplicationError("Run not found.")
        with self._lock:
            if job_id in self._jobs:
                return self._copy_job(self._jobs[job_id])
        job_path = self.runs_dir / job_id / "job.json"
        if not job_path.is_file():
            raise ApplicationError("Run not found.")
        return json.loads(job_path.read_text(encoding="utf-8"))

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        seen: set[str] = set()
        with self._lock:
            current = sorted(self._jobs.values(), key=lambda job: job["created_at"], reverse=True)
            for job in current:
                runs.append(self._run_summary(job))
                seen.add(job["id"])
        paths = sorted(
            self.runs_dir.glob("*/job.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in paths:
            if len(runs) >= limit:
                break
            job_id = path.parent.name
            if job_id in seen or not _safe_job_id(job_id):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                runs.append(self._run_summary(job))
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return runs[:limit]

    def _all_jobs(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        seen: set[str] = set()
        with self._lock:
            for job in self._jobs.values():
                copied = self._copy_job(job)
                jobs.append(copied)
                seen.add(copied["id"])
        for path in self.runs_dir.glob("*/job.json"):
            if path.parent.name in seen:
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    jobs.append(value)
            except (OSError, ValueError, TypeError):
                continue
        return jobs

    def usage_summary(self) -> dict[str, Any]:
        return summarize_usage(self._all_jobs())

    def beta_report(self) -> dict[str, Any]:
        """Return a privacy-bounded external-test report and suggested filename."""

        try:
            report = build_beta_report(
                self.data_dir,
                self._all_jobs(),
                product_version=__version__,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ApplicationError(f"Could not build the beta report: {exc}") from exc
        return {
            "filename": beta_report_filename(report["participant_id"]),
            "report": report,
        }

    @staticmethod
    def _run_summary(job: dict[str, Any]) -> dict[str, Any]:
        route = job.get("route", {})
        receipt = job.get("receipt", {})
        return {
            "id": job["id"],
            "status": job["status"],
            "created_at": job["created_at"],
            "task": route.get("task", "Untitled run"),
            "mode": route.get("mode"),
            "premium_calls": receipt.get("premium_calls"),
            "model_calls": receipt.get("model_calls"),
            "duration_seconds": job.get("duration_seconds"),
        }

    def wait(self, job_id: str, timeout: float = 10) -> dict[str, Any]:
        with self._lock:
            thread = self._threads.get(job_id)
        if thread:
            thread.join(timeout)
        return self.get_job(job_id)

    def pick_directory(self) -> str:
        with self._picker_lock:
            try:
                import tkinter as tk
                from tkinter import filedialog
            except ImportError as exc:
                raise ApplicationError(
                    "The folder picker is unavailable. Paste the repository path instead."
                ) from exc
            try:
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                try:
                    selected = filedialog.askdirectory(
                        title="Choose the repository Lians should work in",
                        initialdir=str(self.default_repository),
                        mustexist=True,
                    )
                finally:
                    root.destroy()
            except (RuntimeError, tk.TclError) as exc:
                raise ApplicationError(
                    "The folder picker is unavailable. Paste the repository path instead."
                ) from exc
        return selected


class LiansHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], application: LiansApplication):
        super().__init__(address, LiansRequestHandler)
        self.application = application


class LiansRequestHandler(BaseHTTPRequestHandler):
    server: LiansHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, status: HTTPStatus, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _asset(self, name: str, content_type: str) -> None:
        try:
            asset_name = "wordmark.png.b64" if name == "wordmark.png" else name
            data = resources.files("lians_finish.web").joinpath(asset_name).read_bytes()
        except (FileNotFoundError, ModuleNotFoundError):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if name == "wordmark.png":
            data = base64.b64decode(data.strip(), validate=True)
        if name == "index.html":
            text = data.decode("utf-8").replace(
                "__LIANS_TOKEN__", json.dumps(self.server.application.token)
            )
            data = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _security_headers(self) -> None:
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.port == self.server.server_address[1]
        )

    def _local_host(self) -> bool:
        raw = self.headers.get("Host", "")
        try:
            parsed = urlparse(f"//{raw}")
            return (
                parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                and parsed.port == self.server.server_address[1]
                and parsed.username is None
                and parsed.password is None
            )
        except ValueError:
            return False

    def _authorized(self) -> bool:
        token = self.headers.get("X-Lians-Token", "")
        return secrets.compare_digest(token, self.server.application.token)

    def _require_authorized(self) -> bool:
        if self._authorized():
            return True
        self._json(HTTPStatus.FORBIDDEN, {"error": "Local app token is missing."})
        return False

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type != "application/json":
            raise ApplicationError("Content-Type must be application/json.")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApplicationError("Request body is required.")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApplicationError("Invalid request length.") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApplicationError("Request is too large.")
        try:
            value = json.loads(
                self.rfile.read(length).decode("utf-8"),
                object_pairs_hook=_request_object,
                parse_constant=_reject_request_constant,
            )
        except ApplicationError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApplicationError("Request must be valid JSON.") from exc
        if not isinstance(value, dict):
            raise ApplicationError("Request must be a JSON object.")
        return value

    def do_GET(self) -> None:
        if not self._local_host():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local host."})
            return
        path = urlparse(self.path).path
        if path == "/":
            self._asset("index.html", "text/html; charset=utf-8")
            return
        if path == "/assets/styles.css":
            self._asset("styles.css", "text/css; charset=utf-8")
            return
        if path == "/assets/app.js":
            self._asset("app.js", "text/javascript; charset=utf-8")
            return
        if path == "/assets/wordmark.png":
            self._asset("wordmark.png", "image/png")
            return
        if path == "/api/health":
            self._json(HTTPStatus.OK, {"status": "ok", "scope": "loopback"})
            return
        if path.startswith("/api/") and not self._require_authorized():
            return
        if path == "/api/config":
            self._json(HTTPStatus.OK, self.server.application.config())
            return
        if path == "/api/readiness":
            self._json(HTTPStatus.OK, self.server.application.readiness())
            return
        if path == "/api/runs":
            self._json(HTTPStatus.OK, {"runs": self.server.application.list_runs()})
            return
        if path == "/api/beta/report":
            try:
                self._json(HTTPStatus.OK, self.server.application.beta_report())
            except ApplicationError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path == "/api/missions":
            self._json(
                HTTPStatus.OK,
                {"missions": self.server.application.mission_ledger.list_latest()},
            )
            return
        if path.startswith("/api/jobs/"):
            job_id = path.removeprefix("/api/jobs/")
            try:
                self._json(HTTPStatus.OK, self.server.application.get_job(job_id))
            except ApplicationError as exc:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._local_host() or not self._authorized() or not self._same_origin():
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                length = 0
            if 0 < length <= MAX_BODY_BYTES:
                self.rfile.read(length)
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": "Local app token is missing or the request origin is invalid."},
            )
            return
        try:
            if path == "/api/plan":
                self._json(HTTPStatus.OK, self.server.application.plan(self._read_json()))
                return
            if path == "/api/run":
                self._json(
                    HTTPStatus.ACCEPTED,
                    self.server.application.start_run(self._read_json()),
                )
                return
            if path == "/api/inspect":
                self._json(
                    HTTPStatus.OK,
                    self.server.application.inspect_workspace(self._read_json()),
                )
                return
            if path == "/api/agent/export":
                self._json(
                    HTTPStatus.OK,
                    self.server.application.export_agent(self._read_json()),
                )
                return
            if path == "/api/agent/import":
                self._json(
                    HTTPStatus.OK,
                    self.server.application.import_agent(self._read_json()),
                )
                return
            if path == "/api/pick-directory":
                self._json(
                    HTTPStatus.OK,
                    {"repository": self.server.application.pick_directory()},
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except ApplicationError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def create_server(
    application: LiansApplication, host: str = "127.0.0.1", port: int = 4318
) -> LiansHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Lians Finish only binds to the local computer")
    return LiansHTTPServer((host, port), application)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lians-finish-app", description="Open the local Lians Finish application."
    )
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument("--data-dir", type=Path, default=_default_data_dir())
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--no-open", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    application = LiansApplication(args.data_dir, default_repository=args.repo)
    try:
        server = create_server(application, port=args.port)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"Lians Finish is ready at {url}")
    print("Press Ctrl+C to stop it. Runs and receipts stay on this computer.")
    if not args.no_open:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLians Finish stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
