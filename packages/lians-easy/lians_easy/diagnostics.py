"""Privacy-safe crash diagnostics for the Lians desktop runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import traceback as traceback_module
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

from . import __version__

_SCHEMA = "lians-crash/v1"
_MAX_LOG_BYTES = 256 * 1024
_MAX_FRAMES = 20
_LOCK = threading.Lock()
_INSTALLED = False


def _data_dir() -> Path:
    override = os.environ.get("LIANS_EASY_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Lians"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Lians"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "lians"


def _log_path() -> Path:
    return _data_dir() / "diagnostics" / "crashes.jsonl"


def _safe_label(value: object, *, fallback: str, limit: int = 80) -> str:
    text = str(value or "")[:limit]
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "-", text).strip("-.")
    return cleaned or fallback


def _safe_frames(traceback: TracebackType | None) -> list[dict[str, Any]]:
    if traceback is None:
        return []
    frames = traceback_module.extract_tb(traceback)[-_MAX_FRAMES:]
    return [
        {
            "file": _safe_label(Path(frame.filename).name, fallback="unknown.py"),
            "line": int(frame.lineno),
            "function": _safe_label(frame.name, fallback="unknown"),
        }
        for frame in frames
    ]


def _crash_fingerprint(
    exc_type: type[BaseException], frames: list[dict[str, Any]]
) -> str:
    """Group equivalent call sites without hashing private exception messages."""

    signature = {
        "type": _safe_label(exc_type.__name__, fallback="Exception"),
        "frames": frames,
    }
    encoded = json.dumps(signature, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _rotate_if_needed(path: Path) -> None:
    try:
        if not path.is_file() or path.stat().st_size < _MAX_LOG_BYTES:
            return
        previous = path.with_name("crashes.previous.jsonl")
        previous.unlink(missing_ok=True)
        path.replace(previous)
    except OSError:
        return


def record_exception(
    exc_type: type[BaseException],
    exc_value: BaseException | None,
    traceback: TracebackType | None,
    *,
    component: str = "runtime",
) -> None:
    """Record only structural crash metadata, never exception text or local data."""

    if not isinstance(exc_type, type) or not issubclass(exc_type, BaseException):
        return
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        return
    frames = _safe_frames(traceback)
    event = {
        "schema": _SCHEMA,
        # datetime.UTC is unavailable on the supported Python 3.10 runtime.
        "timestamp": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "lians_version": __version__,
        "component": _safe_label(component, fallback="runtime"),
        "exception_type": _safe_label(exc_type.__name__, fallback="Exception"),
        "crash_fingerprint": _crash_fingerprint(exc_type, frames),
        "frames": frames,
    }
    try:
        with _LOCK:
            path = _log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed(path)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(event, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                path.chmod(0o600)
    except OSError:
        return


def recent_crash_summaries(*, limit: int = 5) -> list[dict[str, Any]]:
    """Return a bounded, schema-filtered set of already-redacted crash events."""

    if limit <= 0:
        return []
    events: list[dict[str, Any]] = []
    paths = [_log_path().with_name("crashes.previous.jsonl"), _log_path()]
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(value, dict) or value.get("schema") != _SCHEMA:
                continue
            raw_frames = value.get("frames", [])
            frames = []
            if isinstance(raw_frames, list):
                for frame in raw_frames[-_MAX_FRAMES:]:
                    if not isinstance(frame, dict):
                        continue
                    try:
                        line_number = max(0, min(int(frame.get("line", 0)), 10_000_000))
                    except (TypeError, ValueError):
                        line_number = 0
                    frames.append(
                        {
                            "file": _safe_label(
                                Path(str(frame.get("file", ""))).name,
                                fallback="unknown.py",
                            ),
                            "line": line_number,
                            "function": _safe_label(
                                frame.get("function"), fallback="unknown"
                            ),
                        }
                    )
            fingerprint = str(value.get("crash_fingerprint", ""))
            if not re.fullmatch(r"[0-9a-f]{16}", fingerprint):
                fingerprint = "unknown"
            events.append(
                {
                    "timestamp": str(value.get("timestamp", ""))[:64],
                    "lians_version": _safe_label(
                        value.get("lians_version"), fallback="unknown", limit=32
                    ),
                    "component": _safe_label(
                        value.get("component"), fallback="runtime"
                    ),
                    "exception_type": _safe_label(
                        value.get("exception_type"), fallback="Exception"
                    ),
                    "crash_fingerprint": fingerprint,
                    "frames": frames,
                }
            )
    return events[-limit:]


def install_crash_logging(*, component: str) -> None:
    """Install best-effort handlers for unhandled main and worker-thread errors."""

    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        _INSTALLED = True

        previous_sys_hook = sys.excepthook

        def system_hook(
            exc_type: type[BaseException],
            exc_value: BaseException,
            traceback: TracebackType | None,
        ) -> None:
            record_exception(
                exc_type,
                exc_value,
                traceback,
                component=component,
            )
            previous_sys_hook(exc_type, exc_value, traceback)

        sys.excepthook = system_hook

        if hasattr(threading, "excepthook"):
            previous_thread_hook = threading.excepthook

            def thread_hook(args: threading.ExceptHookArgs) -> None:
                record_exception(
                    args.exc_type,
                    args.exc_value,
                    args.exc_traceback,
                    component=component,
                )
                previous_thread_hook(args)

            threading.excepthook = thread_hook
