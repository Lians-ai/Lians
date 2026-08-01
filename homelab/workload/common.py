"""Small standard-library helpers shared by the homelab workload scripts."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USER_AGENT = "lians-homelab/1.0"


class HttpFailure(RuntimeError):
    """An HTTP or transport failure with enough context for a useful JSON log."""

    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def emit(event: str, *, level: str = "info", **fields: Any) -> None:
    """Write one concise, machine-readable log record."""

    record = {"ts": utc_now(), "level": level, "event": event, **fields}
    print(json.dumps(record, sort_keys=True, separators=(",", ":"), default=str), flush=True)


def env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    value = default if raw is None else float(raw)
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def endpoint(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def http_request(
    method: str,
    url: str,
    *,
    json_body: Any | None = None,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, str], bytes]:
    """Perform a 2xx-only HTTP request with urllib."""

    if json_body is not None and raw_body is not None:
        raise ValueError("json_body and raw_body are mutually exclusive")
    body = raw_body
    request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    if json_body is not None:
        body = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(url, data=body, method=method.upper(), headers=request_headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as exc:
        response_body = exc.read(4096).decode("utf-8", errors="replace")
        raise HttpFailure(
            f"{method.upper()} {url} returned HTTP {exc.code}",
            status=exc.code,
            body=response_body,
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HttpFailure(f"{method.upper()} {url} failed: {exc}") from exc


def http_json(
    method: str,
    url: str,
    *,
    json_body: Any | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> Any:
    _, _, body = http_request(
        method,
        url,
        json_body=json_body,
        headers=headers,
        timeout=timeout,
    )
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpFailure(f"{method.upper()} {url} returned invalid JSON") from exc


def wait_for_http(url: str, name: str, *, timeout: float = 180.0) -> None:
    """Wait until an endpoint returns any successful response."""

    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    emit("dependency_wait", dependency=name, url=url)
    while time.monotonic() < deadline:
        try:
            http_request("GET", url, timeout=min(5.0, timeout))
            emit("dependency_ready", dependency=name)
            return
        except HttpFailure as exc:
            last_error = str(exc)
            time.sleep(1.0)
    raise TimeoutError(f"timed out waiting for {name}: {last_error}")


def wait_for_file(path: Path, *, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            return
        time.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {path}")


def atomic_write_bytes(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    content = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    atomic_write_bytes(path, content)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    # Match Lians' canonical JSON contract, including the default ASCII escaping.
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_text(canonical)
