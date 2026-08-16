"""Human-readable lifetime and recent-activity views for the desktop companion."""

from __future__ import annotations

from datetime import datetime
from typing import Any

CLIENT_NAMES = {
    "claude": "Claude",
    "claude-code": "Claude",
    "codex": "Codex",
    "cursor": "Cursor",
}


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def format_count(value: Any) -> str:
    """Format a non-negative counter for the compact desktop UI."""

    return f"{_integer(value):,}"


def format_activity_time(value: Any, *, now: datetime | None = None) -> str:
    """Render an ISO receipt time in the computer's local timezone."""

    if not isinstance(value, str) or not value:
        return "Recent"
    try:
        normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        occurred = datetime.fromisoformat(normalized)
        if occurred.tzinfo is not None:
            occurred = occurred.astimezone()
        current = now or datetime.now().astimezone()
        if current.tzinfo is not None and occurred.tzinfo is None:
            current = current.replace(tzinfo=None)
        if occurred.date() == current.date():
            return f"Today at {occurred.strftime('%I:%M %p').lstrip('0')}"
        if (current.date() - occurred.date()).days == 1:
            return f"Yesterday at {occurred.strftime('%I:%M %p').lstrip('0')}"
        return occurred.strftime("%b %d at %I:%M %p").replace(" 0", " ")
    except (TypeError, ValueError):
        return "Recent"


def _activity(receipt: dict[str, Any]) -> dict[str, Any]:
    efficiency = receipt.get("efficiency")
    efficiency = efficiency if isinstance(efficiency, dict) else {}
    project = receipt.get("project")
    project = project if isinstance(project, dict) else {}

    client_key = str(receipt.get("client") or "AI app").strip()
    client = CLIENT_NAMES.get(client_key.lower(), client_key.replace("-", " ").title())
    project_name = str(project.get("name") or "Global").strip()
    memory_count = _integer(receipt.get("memory_count"))
    sent = _integer(receipt.get("token_estimate"))
    avoided = _integer(efficiency.get("repeated_memory_tokens_avoided_estimate"))
    if memory_count:
        memory_text = f"{memory_count} memor{'y' if memory_count == 1 else 'ies'} reused"
        detail = f"{memory_text} · about {format_count(avoided)} repeated tokens avoided"
    else:
        detail = "No saved context matched this task"
    if sent:
        detail += f" · about {format_count(sent)} context tokens sent"
    return {
        "title": f"{client} · {project_name}",
        "time": format_activity_time(receipt.get("created_at")),
        "detail": detail,
        "client": client,
        "project": project_name,
        "memories_reused": memory_count,
        "context_tokens_sent_estimate": sent,
        "repeated_tokens_avoided_estimate": avoided,
    }


def lifeline_snapshot(store: Any, *, limit: int = 5) -> dict[str, Any]:
    """Build the honest, local-only dashboard model from signed context receipts."""

    stats = store.stats()
    efficiency = stats.get("efficiency")
    efficiency = efficiency if isinstance(efficiency, dict) else {}
    receipts = store.receipts(limit=max(1, limit))
    available = _integer(efficiency.get("available_memory_tokens_estimate"))
    avoided = _integer(efficiency.get("repeated_memory_tokens_avoided_estimate"))
    reduction = round((avoided / available) * 100) if available else 0
    return {
        "saved_memories": _integer(stats.get("current")),
        "context_events": _integer(efficiency.get("context_events")),
        "memories_reused": _integer(efficiency.get("memories_reused")),
        "context_tokens_sent_estimate": _integer(
            efficiency.get("context_tokens_sent_estimate")
        ),
        "repeated_tokens_avoided_estimate": avoided,
        "reduction_percent_estimate": max(0, min(100, reduction)),
        "clients_used": _integer(efficiency.get("clients_used")),
        "activity": [_activity(item) for item in receipts[:limit]],
    }
