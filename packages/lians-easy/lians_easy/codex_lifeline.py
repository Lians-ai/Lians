"""Read-only lifeline metrics from the installed Lians Codex plugin."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .lifeline import format_activity_time, format_count

_MAX_RECEIPT_BYTES = 16 * 1024 * 1024
_MAX_RECEIPT_LINES = 20_000
_PROJECT_SUFFIX = re.compile(r"-[0-9a-f]{12}$", re.IGNORECASE)


def _codex_memory_home() -> Path:
    override = os.environ.get("LIANS_CODEX_MEMORY_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return local / "Lians" / "CodexMemory"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Lians" / "CodexMemory"
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data / "lians" / "CodexMemory"


def _latest_project(home: Path) -> Path | None:
    projects = home / "projects"
    try:
        candidates = [
            (path.stat().st_mtime_ns, path.parent)
            for path in projects.glob("*/hook-receipts.jsonl")
            if path.is_file()
        ]
    except OSError:
        return None
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def _bounded_receipts(path: Path) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
        with path.open("rb") as source:
            if size > _MAX_RECEIPT_BYTES:
                source.seek(size - _MAX_RECEIPT_BYTES)
                source.readline()
            lines = source.readlines()
    except OSError:
        return []

    receipts: list[dict[str, Any]] = []
    for raw in lines[-_MAX_RECEIPT_LINES:]:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        if value.get("status") != "injected" or value.get("injected") is not True:
            continue
        receipts.append(value)
    return receipts


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _active_memory_count(database: Path) -> int:
    if not database.is_file():
        return 0
    try:
        connection = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=0.2,
        )
        try:
            connection.execute("PRAGMA query_only=ON")
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memories)")}
            predicates = []
            if "erased_at" in columns:
                predicates.append("erased_at IS NULL")
            if "system_valid_to" in columns:
                predicates.append("system_valid_to IS NULL")
            elif "valid_to" in columns:
                predicates.append("valid_to IS NULL")
            where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
            row = connection.execute(f"SELECT COUNT(*) FROM memories{where}").fetchone()
            return _integer(row[0] if row else 0)
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return 0


def _project_label(project: Path) -> str:
    stem = _PROJECT_SUFFIX.sub("", project.name)
    return re.sub(r"[\-_]+", " ", stem).strip().title() or "Codex project"


def _activity(receipt: dict[str, Any], project_name: str) -> dict[str, Any]:
    memories = _integer(receipt.get("memory_count"))
    tokens = _integer(receipt.get("token_estimate"))
    memory_text = f"{memories} memor{'y' if memories == 1 else 'ies'} reused"
    detail = f"{memory_text} · about {format_count(tokens)} context tokens delivered"
    return {
        "title": f"Codex · {project_name}",
        "time": format_activity_time(receipt.get("created_at")),
        "detail": detail,
        "client": "Codex",
        "project": project_name,
        "memories_reused": memories,
        "context_tokens_sent_estimate": tokens,
        "repeated_tokens_avoided_estimate": 0,
    }


def codex_lifeline_snapshot(*, home: Path | None = None, limit: int = 4) -> dict[str, Any] | None:
    """Return measured Codex hook activity without decrypting memory content."""

    project = _latest_project(home or _codex_memory_home())
    if project is None:
        return None
    receipts = _bounded_receipts(project / "hook-receipts.jsonl")
    if not receipts:
        return None

    context_events = len(receipts)
    memories_reused = sum(_integer(item.get("memory_count")) for item in receipts)
    context_tokens = sum(_integer(item.get("token_estimate")) for item in receipts)
    project_name = _project_label(project)
    recent = list(reversed(receipts[-max(1, min(limit, 20)) :]))
    return {
        "saved_memories": _active_memory_count(project / "memory.sqlite3"),
        "context_events": context_events,
        "memories_reused": memories_reused,
        "context_tokens_sent_estimate": context_tokens,
        "repeated_tokens_avoided_estimate": 0,
        "reduction_percent_estimate": 0,
        "clients_used": 1,
        "activity": [_activity(item, project_name) for item in recent],
        "token_metric": {
            "label": "Context reused",
            "value": context_tokens,
            "detail": "Tokens delivered from memory",
            "approximate": True,
        },
        "measurement_basis": (
            "Installed Codex hook receipts. Context delivered is measured; "
            "quota savings are not inferred."
        ),
    }
