from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import threading
import webbrowser
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qs, urlsplit

from lians_easy.agent_experiment import (
    PROVIDER_NAMES,
    PROVIDERS,
    AgentExperimentError,
    provider_preflight,
    run_provider_experiment,
)
from lians_easy.task_runner import run_bounded_task
from lians_easy.work_brief import (
    MAX_INPUT_BYTES,
    WorkBriefError,
    compile_work_brief,
    parse_work_records,
)

Preflight = Callable[[str], Mapping[str, Any]]
ExperimentRunner = Callable[..., dict[str, Any]]
TaskRunner = Callable[..., dict[str, Any]]
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_MAX_REQUEST_BODY = MAX_INPUT_BYTES + (1024 * 1024)
_AUTO_CLOSE_SECONDS = 60 * 60


def _default_preflight(provider: str) -> Mapping[str, Any]:
    return provider_preflight(provider)


def _default_runner(**kwargs: Any) -> dict[str, Any]:
    return run_provider_experiment(**kwargs)


def _default_task_runner(**kwargs: Any) -> dict[str, Any]:
    return run_bounded_task(**kwargs)


def _asset_bytes(name: str) -> bytes:
    tester_root = files("lians_easy").joinpath("tester")
    app_root = files("lians_easy").joinpath("app")
    assets = {
        "index.html": tester_root.joinpath("index.html"),
        "style.css": tester_root.joinpath("style.css"),
        "app.js": tester_root.joinpath("app.js"),
        "wordmark.png": app_root.joinpath("logo-blue.png"),
        "favicon.png": tester_root.joinpath("favicon.png.b64"),
        "sora.woff2": app_root.joinpath("fonts", "sora-latin.woff2"),
    }
    if name == "favicon.png":
        value = assets[name].read_text(encoding="ascii").strip()
        return base64.b64decode(value, validate=True)
    return assets[name].read_bytes()


def _summary(report: Mapping[str, Any]) -> dict[str, Any]:
    results = report["results"]
    full = results["full_replay"]
    bounded = results["lians_bounded"]
    comparison = report["comparison"]
    full_tokens = float(full["average_provider_reported_total_input_tokens"])
    lians_tokens = float(bounded["average_provider_reported_total_input_tokens"])
    all_runs = [*full["runs"], *bounded["runs"]]
    exact_answers = sum(bool(run["quality"]["passed"]) for run in all_runs)
    provider = str(report.get("provider", "claude"))
    measurement = report.get("measurement")
    measurement_label = (
        str(measurement.get("label"))
        if isinstance(measurement, Mapping) and measurement.get("label")
        else f"{PROVIDER_NAMES.get(provider, provider.title())} CLI reported input tokens"
    )
    return {
        "provider": provider,
        "provider_name": PROVIDER_NAMES.get(provider, provider.title()),
        "measurement_label": measurement_label,
        "reduction_percent": comparison["provider_reported_input_token_reduction_percent"],
        "full_input_tokens": full_tokens,
        "lians_input_tokens": lians_tokens,
        "saved_input_tokens": round(full_tokens - lians_tokens, 1),
        "exact_answers": exact_answers,
        "total_answers": len(all_runs),
        "gate_met": bool(report["evidence_gate"]["met"]),
    }


class TesterApplication:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        token: str | None = None,
        preflight: Preflight = _default_preflight,
        experiment_runner: ExperimentRunner = _default_runner,
        task_runner: TaskRunner = _default_task_runner,
        auto_close_seconds: float = _AUTO_CLOSE_SECONDS,
    ) -> None:
        session_token = token or secrets.token_urlsafe(24)
        if not _TOKEN_PATTERN.fullmatch(session_token):
            raise ValueError("token must contain 16 to 128 URL safe characters")
        self.host = host
        self.port = port
        self.token = session_token
        self.preflight = preflight
        self.experiment_runner = experiment_runner
        self.task_runner = task_runner
        self.auto_close_seconds = auto_close_seconds
        self.report: dict[str, Any] | None = None
        self.report_provider: str | None = None
        self.brief: dict[str, Any] | None = None
        self.task_report: dict[str, Any] | None = None
        self.running = False
        self._state_lock = threading.Lock()
        self._server_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._auto_close_timer: threading.Timer | None = None

    @property
    def server(self) -> ThreadingHTTPServer:
        with self._server_lock:
            if self._server is None:
                handler = self._handler()
                self._server = ThreadingHTTPServer((self.host, self.port), handler)
                self._server.daemon_threads = True
            return self._server

    @property
    def base_url(self) -> str:
        bound_port = self.server.server_address[1]
        return f"http://{self.host}:{bound_port}/{self.token}/"

    def serve_forever(self) -> None:
        if self.auto_close_seconds > 0:
            self._auto_close_timer = threading.Timer(
                self.auto_close_seconds,
                self.shutdown,
            )
            self._auto_close_timer.daemon = True
            self._auto_close_timer.start()
        try:
            self.server.serve_forever(poll_interval=0.2)
        finally:
            if self._auto_close_timer is not None:
                self._auto_close_timer.cancel()
            self.server.server_close()

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        application = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "LiansTester"
            sys_version = ""

            def log_message(self, format: str, *args: Any) -> None:
                return

            def end_headers(self) -> None:
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header(
                    "Permissions-Policy",
                    "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
                )
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; connect-src 'self'; img-src 'self'; "
                    "font-src 'self'; style-src 'self'; script-src 'self'; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                )
                super().end_headers()

            def do_GET(self) -> None:
                route = self._route()
                if route is None:
                    self._json_error(HTTPStatus.NOT_FOUND, "Not found")
                    return
                if route in {"", "index.html"}:
                    self._asset("index.html")
                    return
                if route in {
                    "style.css",
                    "app.js",
                    "wordmark.png",
                    "favicon.png",
                    "sora.woff2",
                }:
                    self._asset(route)
                    return
                if route == "api/status":
                    self._status(self._query_provider())
                    return
                if route == "api/report":
                    self._report()
                    return
                if route == "api/brief":
                    self._download(
                        application.brief,
                        filename="lians-context-brief.json",
                        missing="No context brief is ready",
                    )
                    return
                if route == "api/task-report":
                    self._download(
                        application.task_report,
                        filename="lians-task-receipt.json",
                        missing="No task receipt is ready",
                    )
                    return
                self._json_error(HTTPStatus.NOT_FOUND, "Not found")

            def do_POST(self) -> None:
                route = self._route()
                if route not in {"api/compile", "api/ask", "api/run", "api/close"}:
                    self._json_error(HTTPStatus.NOT_FOUND, "Not found")
                    return
                if not self._valid_origin():
                    self._json_error(HTTPStatus.FORBIDDEN, "Request origin was refused")
                    return
                if self.headers.get_content_type() != "application/json":
                    self._json_error(
                        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                        "JSON is required",
                    )
                    return
                raw_length = self.headers.get("Content-Length", "0")
                try:
                    length = int(raw_length)
                except ValueError:
                    length = _MAX_REQUEST_BODY + 1
                if length < 0 or length > _MAX_REQUEST_BODY:
                    self._json_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request is too large")
                    return
                body = self.rfile.read(length)
                try:
                    payload = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    self._json_error(HTTPStatus.BAD_REQUEST, "JSON is not valid")
                    return
                if route == "api/compile":
                    self._compile(payload)
                elif route == "api/ask":
                    self._ask(payload)
                elif route == "api/run":
                    if not isinstance(payload, dict) or set(payload) != {"provider"}:
                        self._json_error(
                            HTTPStatus.BAD_REQUEST,
                            "Request must contain one provider",
                        )
                        return
                    provider = payload.get("provider")
                    if not isinstance(provider, str) or provider not in PROVIDERS:
                        self._json_error(HTTPStatus.BAD_REQUEST, "Provider is not supported")
                        return
                    self._run(provider)
                else:
                    if not isinstance(payload, dict) or payload:
                        self._json_error(
                            HTTPStatus.BAD_REQUEST,
                            "Request must be an empty object",
                        )
                        return
                    self._send_json({"closed": True})
                    threading.Thread(target=application.shutdown, daemon=True).start()

            def _route(self) -> str | None:
                path = urlsplit(self.path).path
                prefix = f"/{application.token}/"
                if not path.startswith(prefix):
                    return None
                route = path[len(prefix) :]
                if "\\" in route or any(part in {".", ".."} for part in route.split("/")):
                    return None
                return route

            def _valid_origin(self) -> bool:
                local = urlsplit(application.base_url)
                expected = f"{local.scheme}://{local.netloc}"
                return self.headers.get("Origin") == expected

            def _query_provider(self) -> str | None:
                try:
                    query = parse_qs(urlsplit(self.path).query, strict_parsing=True)
                except ValueError:
                    return None
                values = query.get("provider")
                if set(query) != {"provider"} or not values or len(values) != 1:
                    return None
                provider = values[0]
                return provider if provider in PROVIDERS else None

            def _status(self, provider: str | None) -> None:
                if provider is None:
                    self._json_error(HTTPStatus.BAD_REQUEST, "Provider is not supported")
                    return
                try:
                    auth = application.preflight(provider)
                except AgentExperimentError as error:
                    self._send_json(
                        {
                            "ready": False,
                            "auth_method": None,
                            "provider": None,
                            "provider_name": PROVIDER_NAMES[provider],
                            "message": str(error),
                        }
                    )
                    return
                except Exception:  # noqa: BLE001
                    # A readiness failure must stay inside the local UI without exposing details.
                    self._send_json(
                        {
                            "ready": False,
                            "auth_method": None,
                            "provider": None,
                            "provider_name": PROVIDER_NAMES[provider],
                            "message": f"{PROVIDER_NAMES[provider]} readiness could not be checked",
                        }
                    )
                    return
                self._send_json(
                    {
                        "ready": True,
                        "auth_method": auth.get("auth_method"),
                        "provider": auth.get("provider"),
                        "provider_name": PROVIDER_NAMES[provider],
                        "message": f"{PROVIDER_NAMES[provider]} account is ready",
                    }
                )

            def _compile(self, payload: Any) -> None:
                if not isinstance(payload, dict) or set(payload) != {
                    "kind",
                    "input",
                    "evidence_limit",
                }:
                    self._json_error(
                        HTTPStatus.BAD_REQUEST,
                        "Request must contain kind, input, and evidence_limit",
                    )
                    return
                kind = payload.get("kind")
                raw = payload.get("input")
                evidence_limit = payload.get("evidence_limit")
                if kind not in {"research", "browser"} or not isinstance(raw, str):
                    self._json_error(HTTPStatus.BAD_REQUEST, "Work export is not valid")
                    return
                if not isinstance(evidence_limit, int) or isinstance(evidence_limit, bool):
                    self._json_error(HTTPStatus.BAD_REQUEST, "Evidence limit is not valid")
                    return
                with application._state_lock:
                    if application.running:
                        self._json_error(HTTPStatus.CONFLICT, "Another task is already running")
                        return
                    application.running = True
                try:
                    records = parse_work_records(raw)
                    brief = compile_work_brief(
                        kind,
                        records,
                        evidence_limit=evidence_limit,
                    )
                except (WorkBriefError, UnicodeError, ValueError, RecursionError) as error:
                    self._json_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                finally:
                    with application._state_lock:
                        application.running = False
                application.brief = brief
                application.task_report = None
                receipt = brief["receipt"]
                raw_tokens = int(receipt["raw_token_estimate"])
                brief_tokens = int(receipt["brief_token_estimate"])
                reduction = round(max(0.0, 1.0 - (brief_tokens / raw_tokens)) * 100.0, 1)
                self._send_json(
                    {
                        "kind": kind,
                        "summary": brief["summary"],
                        "raw_records": receipt["raw_record_count"],
                        "raw_token_estimate": raw_tokens,
                        "brief_token_estimate": brief_tokens,
                        "estimated_reduction_percent": reduction,
                        "evidence_items": len(brief["representative_evidence"]),
                    }
                )

            def _ask(self, payload: Any) -> None:
                if not isinstance(payload, dict) or set(payload) != {"provider", "task"}:
                    self._json_error(
                        HTTPStatus.BAD_REQUEST,
                        "Request must contain one provider and one task",
                    )
                    return
                provider = payload.get("provider")
                task = payload.get("task")
                if not isinstance(provider, str) or provider not in PROVIDERS:
                    self._json_error(HTTPStatus.BAD_REQUEST, "Provider is not supported")
                    return
                if not isinstance(task, str):
                    self._json_error(HTTPStatus.BAD_REQUEST, "Task must be text")
                    return
                if application.brief is None:
                    self._json_error(
                        HTTPStatus.CONFLICT,
                        "Add work and create a context brief first",
                    )
                    return
                with application._state_lock:
                    if application.running:
                        self._json_error(HTTPStatus.CONFLICT, "Another task is already running")
                        return
                    application.running = True
                try:
                    result = application.task_runner(
                        provider=provider,
                        brief=application.brief,
                        task=task,
                    )
                    application.task_report = result
                except (AgentExperimentError, KeyError, TypeError, ValueError) as error:
                    self._json_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                except Exception:  # noqa: BLE001
                    self._json_error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        f"The {PROVIDER_NAMES[provider]} task could not finish",
                    )
                    return
                finally:
                    with application._state_lock:
                        application.running = False
                self._send_json(result)

            def _run(self, provider: str) -> None:
                with application._state_lock:
                    if application.running:
                        self._json_error(HTTPStatus.CONFLICT, "A test is already running")
                        return
                    application.running = True
                try:
                    report = application.experiment_runner(
                        provider=provider,
                        scenario="market-research",
                        repetitions=2,
                    )
                    result = _summary(report)
                    application.report = report
                    application.report_provider = provider
                except (AgentExperimentError, KeyError, TypeError, ValueError) as error:
                    self._json_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                except Exception:  # noqa: BLE001
                    # Provider and process errors are intentionally replaced with safe copy.
                    self._json_error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        f"The {PROVIDER_NAMES[provider]} test could not finish",
                    )
                    return
                finally:
                    with application._state_lock:
                        application.running = False
                self._send_json(result)

            def _report(self) -> None:
                self._download(
                    application.report,
                    filename=(
                        "lians-"
                        f'{application.report_provider or "ai"}-research-report.json'
                    ),
                    missing="No test report is ready",
                )

            def _download(
                self,
                value: Mapping[str, Any] | None,
                *,
                filename: str,
                missing: str,
            ) -> None:
                if value is None:
                    self._json_error(HTTPStatus.NOT_FOUND, missing)
                    return
                payload = json.dumps(value, indent=2).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"',
                )
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _asset(self, name: str) -> None:
                try:
                    payload = _asset_bytes(name)
                except (FileNotFoundError, KeyError):
                    self._json_error(HTTPStatus.NOT_FOUND, "Asset not found")
                    return
                if name == "index.html":
                    content_type = "text/html; charset=utf-8"
                elif name == "style.css":
                    content_type = "text/css; charset=utf-8"
                elif name == "app.js":
                    content_type = "text/javascript; charset=utf-8"
                elif name in {"wordmark.png", "favicon.png"}:
                    content_type = "image/png"
                elif name == "sora.woff2":
                    content_type = "font/woff2"
                else:
                    self._json_error(HTTPStatus.NOT_FOUND, "Asset not found")
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _send_json(
                self,
                payload: Mapping[str, Any],
                status: HTTPStatus = HTTPStatus.OK,
            ) -> None:
                encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _json_error(self, status: HTTPStatus, message: str) -> None:
                self._send_json({"error": message}, status)

        return Handler


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local Lians preview")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token")
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    application = TesterApplication(port=args.port, token=args.token)
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, args=(application.base_url,)).start()
    try:
        application.serve_forever()
    except KeyboardInterrupt:
        application.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
