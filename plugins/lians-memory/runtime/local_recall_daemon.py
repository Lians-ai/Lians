"""Authenticated loopback runtime for low-latency Codex hook recall.

The Codex hook itself is a fresh process for every prompt. Loading a local
embedding runtime in each process dominates retrieval latency, so this module
keeps one ``LocalLiansClient`` warm behind a user-local HTTP endpoint.
The endpoint is never exposed beyond IPv4 loopback and accepts requests only
with the per-user token stored in a user-local runtime directory. POSIX modes are
set owner-only; on Windows, privacy also depends on the directory's inherited
NTFS ACL, so custom runtime directories must be access-controlled by the user.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


LOOPBACK_HOST = "127.0.0.1"
PROTOCOL_VERSION = 1
MAX_QUERY_CHARS = 20_000
MAX_REQUEST_BYTES = 32_768
MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_IDLE_SECONDS = 1_800
DEFAULT_REQUEST_TIMEOUT_MS = 3_000
DEFAULT_START_TIMEOUT_MS = 45_000
_TOKEN_BYTES = 32
_DIRECT_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LocalSettings(Protocol):
    local_db: str
    namespace: str
    agent_id: str
    k: int
    local_runtime_env: tuple[tuple[str, str], ...]
    daemon_runtime_dir: str
    daemon_idle_seconds: int
    daemon_request_timeout_ms: int
    daemon_start_timeout_ms: int


@dataclass(frozen=True)
class DaemonPaths:
    runtime_dir: Path
    token: Path
    state: Path
    lock: Path
    instance_id: str
    fingerprint: str


class LocalRecallRuntime:
    """One initialized SDK client and embedding model, owned by the daemon."""

    def __init__(self, settings: LocalSettings) -> None:
        for key, value in settings.local_runtime_env:
            os.environ.setdefault(key, value)
        from lians import LocalLiansClient

        self._agent_id = settings.agent_id
        self._k = settings.k
        self._client = LocalLiansClient(
            db_path=settings.local_db,
            namespace=settings.namespace,
        )

    def prewarm(self) -> None:
        self._client.recall(
            agent_id=self._agent_id,
            query="__lians_codex_hook_startup_probe__",
            k=1,
        )

    def recall(self, query: str) -> dict[str, Any]:
        return self._client.recall(agent_id=self._agent_id, query=query, k=self._k)

    def close(self) -> None:
        self._client.close()


class _InstanceLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        self._fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(self._path, 0o600)
        if os.fstat(self._fd).st_size == 0:
            os.write(self._fd, b"\0")
        os.lseek(self._fd, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            os.close(self._fd)
            self._fd = None
            return False
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


def _runtime_dir(settings: LocalSettings) -> Path:
    configured = settings.daemon_runtime_dir.strip()
    target = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".lians" / "codex-hook-runtime"
    )
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target, 0o700)
    return target.resolve()


def _identity_document(settings: LocalSettings) -> dict[str, Any]:
    runtime = dict(settings.local_runtime_env)
    database = os.path.normcase(str(Path(settings.local_db).expanduser().resolve()))
    return {
        "protocol": PROTOCOL_VERSION,
        "database": database,
        "namespace": settings.namespace,
        "agent_id": settings.agent_id,
        "k": settings.k,
        "runtime": sorted(runtime.items()),
    }


def daemon_paths(settings: LocalSettings) -> DaemonPaths:
    canonical = json.dumps(
        _identity_document(settings),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    instance_id = fingerprint[:24]
    root = _runtime_dir(settings)
    return DaemonPaths(
        runtime_dir=root,
        token=root / "user.token",
        state=root / f"{instance_id}.json",
        lock=root / f"{instance_id}.lock",
        instance_id=instance_id,
        fingerprint=fingerprint,
    )


def _read_or_create_token(path: Path) -> str:
    for _attempt in range(20):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                token = path.read_text(encoding="ascii").strip()
            except OSError:
                time.sleep(0.01)
                continue
            if len(token) == _TOKEN_BYTES * 2:
                try:
                    bytes.fromhex(token)
                except ValueError:
                    pass
                else:
                    return token
            raise RuntimeError("invalid Codex hook daemon token")
        else:
            token = secrets.token_hex(_TOKEN_BYTES)
            try:
                os.write(fd, token.encode("ascii"))
            finally:
                os.close(fd)
            os.chmod(path, 0o600)
            return token
    raise RuntimeError("Codex hook daemon token is unavailable")


def _read_state(paths: DaemonPaths) -> dict[str, Any]:
    try:
        raw = paths.state.read_bytes()
        if len(raw) > 4_096:
            return {}
        state = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(state, dict):
        return {}
    if state.get("fingerprint") != paths.fingerprint:
        return {}
    if state.get("host") != LOOPBACK_HOST:
        return {}
    port = state.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65_535:
        return {}
    return state


def _write_state(paths: DaemonPaths, state: Mapping[str, Any]) -> None:
    temporary = paths.state.with_name(f".{paths.state.name}.{os.getpid()}.tmp")
    payload = json.dumps(dict(state), sort_keys=True, separators=(",", ":")).encode()
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(temporary, 0o600)
    os.replace(temporary, paths.state)


def _remove_owned_state(paths: DaemonPaths, port: int) -> None:
    state = _read_state(paths)
    if state.get("pid") == os.getpid() and state.get("port") == port:
        try:
            paths.state.unlink()
        except FileNotFoundError:
            pass


class _RecallServer(HTTPServer):
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        *,
        token: str,
        fingerprint: str,
        recall_fn: Callable[[str], dict[str, Any]],
        idle_seconds: int,
    ) -> None:
        self.token = token
        self.fingerprint = fingerprint
        self.recall_fn = recall_fn
        self.idle_seconds = idle_seconds
        self.last_activity = time.monotonic()
        self.stop_requested = False
        super().__init__(address, _RecallHandler, bind_and_activate=True)


class _RecallHandler(BaseHTTPRequestHandler):
    server: _RecallServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _reply(self, status: int, value: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_RESPONSE_BYTES:
            status = 503
            payload = b'{"error":"response_too_large"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Lians-Token", "")
        return hmac.compare_digest(supplied, self.server.token)

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._reply(403, {"error": "forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, {"error": "invalid_request"})
            return
        if not 1 <= length <= MAX_REQUEST_BYTES:
            self._reply(413, {"error": "invalid_request_size"})
            return
        try:
            document = json.loads(self.rfile.read(length))
        except (UnicodeError, json.JSONDecodeError):
            self._reply(400, {"error": "invalid_json"})
            return
        if not isinstance(document, dict) or not hmac.compare_digest(
            str(document.get("fingerprint", "")), self.server.fingerprint
        ):
            self._reply(403, {"error": "wrong_instance"})
            return
        self.server.last_activity = time.monotonic()
        if self.path == "/health":
            self._reply(
                200,
                {
                    "status": "ready",
                    "protocol": PROTOCOL_VERSION,
                    "fingerprint": self.server.fingerprint,
                    "pid": os.getpid(),
                },
            )
            return
        if self.path == "/shutdown":
            self.server.stop_requested = True
            self._reply(200, {"status": "stopping"})
            return
        if self.path != "/v1/recall":
            self._reply(404, {"error": "not_found"})
            return
        query = document.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
            self._reply(400, {"error": "invalid_query"})
            return
        try:
            result = self.server.recall_fn(query)
        except Exception:
            self._reply(503, {"error": "recall_failed"})
            return
        if not isinstance(result, dict):
            self._reply(503, {"error": "invalid_recall_result"})
            return
        self._reply(200, result)


def serve(
    settings: LocalSettings,
    *,
    runtime_factory: Callable[[LocalSettings], Any] = LocalRecallRuntime,
) -> int:
    """Warm one exact local-runtime identity and serve until idle or stopped."""

    paths = daemon_paths(settings)
    lock = _InstanceLock(paths.lock)
    if not lock.acquire():
        return 0
    runtime: Any | None = None
    server: _RecallServer | None = None
    try:
        token = _read_or_create_token(paths.token)
        runtime = runtime_factory(settings)
        runtime.prewarm()
        server = _RecallServer(
            (LOOPBACK_HOST, 0),
            token=token,
            fingerprint=paths.fingerprint,
            recall_fn=runtime.recall,
            idle_seconds=settings.daemon_idle_seconds,
        )
        server.timeout = 0.25
        port = int(server.server_address[1])
        _write_state(
            paths,
            {
                "protocol": PROTOCOL_VERSION,
                "host": LOOPBACK_HOST,
                "port": port,
                "pid": os.getpid(),
                "fingerprint": paths.fingerprint,
                "ready_unix_ms": int(time.time() * 1_000),
            },
        )
        while not server.stop_requested:
            server.handle_request()
            if time.monotonic() - server.last_activity >= server.idle_seconds:
                break
        return 0
    finally:
        if server is not None:
            port = int(server.server_address[1])
            server.server_close()
            _remove_owned_state(paths, port)
        if runtime is not None:
            runtime.close()
        lock.release()


def _post(
    settings: LocalSettings,
    path: str,
    document: Mapping[str, Any],
    *,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    paths = daemon_paths(settings)
    state = _read_state(paths)
    if not state:
        raise RuntimeError("Codex hook recall daemon is not ready")
    token = _read_or_create_token(paths.token)
    payload = json.dumps(dict(document), separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_REQUEST_BYTES:
        raise RuntimeError("Codex hook daemon request is too large")
    request = urllib.request.Request(
        f"http://{LOOPBACK_HOST}:{state['port']}{path}",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Lians-Token": token,
        },
    )
    timeout = (timeout_ms or settings.daemon_request_timeout_ms) / 1_000
    try:
        # Loopback authentication material must never be offered to an HTTP
        # proxy, even when the host has global proxy variables configured.
        with _DIRECT_LOOPBACK_OPENER.open(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("Codex hook recall daemon request failed") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Codex hook recall daemon response is too large")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Codex hook recall daemon returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Codex hook recall daemon returned an invalid response")
    return value


def health(settings: LocalSettings, *, timeout_ms: int = 250) -> dict[str, Any]:
    paths = daemon_paths(settings)
    result = _post(
        settings,
        "/health",
        {"fingerprint": paths.fingerprint},
        timeout_ms=timeout_ms,
    )
    if result.get("status") != "ready" or result.get("fingerprint") != paths.fingerprint:
        raise RuntimeError("Codex hook recall daemon failed its health check")
    return result


def recall(settings: LocalSettings, query: str) -> dict[str, Any]:
    paths = daemon_paths(settings)
    result = _post(
        settings,
        "/v1/recall",
        {"fingerprint": paths.fingerprint, "query": query},
    )
    result["_lians_hook_transport"] = "daemon"
    return result


def stop(settings: LocalSettings) -> bool:
    paths = daemon_paths(settings)
    try:
        result = _post(
            settings,
            "/shutdown",
            {"fingerprint": paths.fingerprint},
            timeout_ms=1_000,
        )
    except RuntimeError:
        return False
    return result.get("status") == "stopping"


def _daemon_environment(settings: LocalSettings) -> dict[str, str]:
    child = dict(os.environ)
    child.update(
        {
            "LIANS_URL": "",
            "LIANS_LOCAL_DB": settings.local_db,
            "LIANS_NAMESPACE": settings.namespace,
            "LIANS_AGENT_ID": settings.agent_id,
            "LIANS_MCP_PROJECT_ROOT": "",
            "LIANS_CODEX_HOOK_K": str(settings.k),
            "LIANS_CODEX_HOOK_DAEMON": "client",
            "LIANS_CODEX_HOOK_DAEMON_RUNTIME_DIR": settings.daemon_runtime_dir,
            "LIANS_CODEX_HOOK_DAEMON_IDLE_SECONDS": str(settings.daemon_idle_seconds),
            "LIANS_CODEX_HOOK_DAEMON_REQUEST_TIMEOUT_MS": str(settings.daemon_request_timeout_ms),
            "LIANS_CODEX_HOOK_DAEMON_START_TIMEOUT_MS": str(settings.daemon_start_timeout_ms),
        }
    )
    child.update(dict(settings.local_runtime_env))
    return child


def spawn(settings: LocalSettings, hook_path: Path) -> None:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": _daemon_environment(settings),
        "cwd": str(Path.cwd()),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(  # noqa: S603
        [sys.executable, str(hook_path.resolve()), "--serve"],
        **kwargs,
    )


def ensure_ready(settings: LocalSettings, hook_path: Path) -> dict[str, Any]:
    try:
        return health(settings)
    except RuntimeError:
        pass
    spawn(settings, hook_path)
    deadline = time.monotonic() + settings.daemon_start_timeout_ms / 1_000
    while time.monotonic() < deadline:
        try:
            return health(settings)
        except RuntimeError:
            time.sleep(0.05)
    raise RuntimeError("Codex hook recall daemon did not become ready")
