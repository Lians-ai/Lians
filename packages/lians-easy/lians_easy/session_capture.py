"""Automatic, bounded project-continuity capture for Claude lifecycle hooks.

The transcript is evidence, not memory.  This adapter reads only a bounded tail,
derives a small checkpoint from explicit session language and tool activity, and
stores that checkpoint with the existing encrypted task/memory primitives.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .project import detect_project
from .store import MemoryStore
from .task_contract import TaskContractService, workspace_snapshot

MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024
MAX_TRANSCRIPT_TAIL_BYTES = 2 * 1024 * 1024
MAX_MESSAGES = 24
MAX_ITEM_CHARS = 1_000
MAX_ITEMS_PER_SECTION = 20

_SECTION_ALIASES = {
    "completed": "completed",
    "done": "completed",
    "implemented": "completed",
    "finished": "completed",
    "still open": "unfinished",
    "remaining": "unfinished",
    "unfinished": "unfinished",
    "not done": "unfinished",
    "next": "next_actions",
    "next steps": "next_actions",
    "next action": "next_actions",
    "blocked": "blocked",
    "blockers": "blocked",
    "decisions": "decisions",
    "decision": "decisions",
    "constraints": "constraints",
    "do not": "do_not_repeat",
    "do not redo": "do_not_repeat",
    "avoid": "do_not_repeat",
    "project truth": "project_truth",
    "project facts": "project_truth",
    "changed": "changes",
    "changes": "changes",
}
_HEADING = re.compile(r"^\s{0,3}(?:#{1,6}\s*)?([^:#]{2,40})\s*:?[ \t]*$")
_BULLET = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|\[[ xX]\]\s+)(.+?)\s*$")
_CHANGE = re.compile(
    r"(?i)\b(?:changed|moved|switched|migrated|updated)\b.*?\bfrom\s+(.+?)\s+\bto\s+(.+?)(?:[.;]|$)"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _clean(value: Any, *, maximum: int = MAX_ITEM_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    rendered = " ".join(value.strip().split())
    return rendered[:maximum]


def _unique(values: list[str], *, limit: int = MAX_ITEMS_PER_SECTION) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        rendered = _clean(value)
        key = rendered.casefold().rstrip(".")
        if not rendered or key in seen:
            continue
        seen.add(key)
        result.append(rendered)
        if len(result) >= limit:
            break
    return result


def _bounded_tail(path: Path) -> str:
    candidate = path.expanduser().absolute()
    if candidate.is_symlink():
        raise ValueError("Claude transcript symlinks are not accepted")
    resolved = candidate.resolve(strict=True)
    if resolved.suffix.casefold() != ".jsonl" or not resolved.is_file():
        raise ValueError("Claude transcript must be a regular JSONL file")
    size = resolved.stat().st_size
    if size > MAX_TRANSCRIPT_BYTES:
        raise ValueError("Claude transcript is unexpectedly large")
    with resolved.open("rb") as handle:
        if size > MAX_TRANSCRIPT_TAIL_BYTES:
            handle.seek(size - MAX_TRANSCRIPT_TAIL_BYTES)
            handle.readline()
        encoded = handle.read(MAX_TRANSCRIPT_TAIL_BYTES + 1)
    if len(encoded) > MAX_TRANSCRIPT_TAIL_BYTES:
        encoded = encoded[-MAX_TRANSCRIPT_TAIL_BYTES:]
    return encoded.decode("utf-8", errors="replace")


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _tool_files(content: Any) -> list[str]:
    if not isinstance(content, list):
        return []
    files: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        payload = block.get("input")
        if not isinstance(payload, dict):
            continue
        for key in ("file_path", "path", "notebook_path"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                files.append(_clean(value))
    return files


def read_claude_transcript(path: str | Path) -> dict[str, Any]:
    """Read a safe tail of a Claude JSONL transcript without retaining it."""

    rendered_tail = _bounded_tail(Path(path))
    messages: list[dict[str, str]] = []
    files: list[str] = []
    timestamps: list[str] = []
    for raw_line in rendered_tail.splitlines():
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        timestamp = row.get("timestamp")
        if isinstance(timestamp, str):
            timestamps.append(timestamp)
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = _content_text(message.get("content"))
        if text.strip():
            messages.append({"role": role, "text": text[-12_000:]})
        files.extend(_tool_files(message.get("content")))
    return {
        "messages": messages[-MAX_MESSAGES:],
        "files_touched": _unique(files, limit=50),
        "event_time": timestamps[-1] if timestamps else _now(),
        "tail_sha256": hashlib.sha256(rendered_tail.encode("utf-8")).hexdigest(),
    }


def _extract_sections(text: str) -> dict[str, list[str]]:
    result = {name: [] for name in set(_SECTION_ALIASES.values())}
    active: str | None = None
    for line in text.splitlines():
        bullet = _BULLET.match(line)
        if active and bullet:
            result[active].append(bullet.group(1))
            continue
        heading = _HEADING.match(line)
        if heading:
            normalized = " ".join(heading.group(1).strip().casefold().split())
            active = _SECTION_ALIASES.get(normalized)
            continue
        if line.strip() and not line.startswith((" ", "\t")):
            active = None
    for key, values in result.items():
        result[key] = _unique(values)
    return result


def extract_continuity(transcript: dict[str, Any]) -> dict[str, Any]:
    """Derive only explicit continuity signals from a bounded transcript view."""

    messages = transcript.get("messages") or []
    user_messages = [item["text"] for item in messages if item.get("role") == "user"]
    assistant_messages = [
        item["text"] for item in messages if item.get("role") == "assistant"
    ]
    final = assistant_messages[-1] if assistant_messages else ""
    sections = _extract_sections(final)
    completed = sections["completed"]
    unfinished = sections["unfinished"]
    blocked = sections["blocked"]
    next_actions = sections["next_actions"]
    files = _unique(list(transcript.get("files_touched") or []), limit=50)
    if not unfinished and next_actions:
        unfinished = list(next_actions)

    changes: list[dict[str, str]] = []
    for item in sections["changes"]:
        match = _CHANGE.search(item)
        if match:
            previous = _clean(match.group(1), maximum=500)
            current = _clean(match.group(2), maximum=500)
            changes.append(
                {
                    "description": f"Current value is {current}",
                    "previous": previous,
                    "current": current,
                }
            )
        else:
            changes.append({"description": item, "current": item})

    raw_goal = user_messages[-1] if user_messages else "Continue the current project work"
    first_paragraph = next(
        (part.strip() for part in re.split(r"\n\s*\n", raw_goal) if part.strip()),
        raw_goal,
    )
    goal = _clean(first_paragraph, maximum=500)
    title = goal.split(".", 1)[0][:160] or "Continue project work"
    summary_parts: list[str] = []
    if completed:
        summary_parts.append("Completed: " + "; ".join(completed[:8]))
    if unfinished:
        summary_parts.append("Still open: " + "; ".join(unfinished[:8]))
    if blocked:
        summary_parts.append("Blocked: " + "; ".join(blocked[:5]))
    return {
        "goal": goal,
        "title": title,
        "completed": completed,
        "unfinished": unfinished,
        "blocked": blocked,
        "decisions": sections["decisions"],
        "constraints": sections["constraints"],
        "do_not_repeat": (
            sections["do_not_repeat"]
            if sections["do_not_repeat"]
            else _unique([f"Do not redo: {item}" for item in completed])
        ),
        "project_truth": sections["project_truth"],
        "changes": changes,
        "next_actions": next_actions,
        "files_touched": files,
        "summary": _clean(" ".join(summary_parts), maximum=1_500)
        or "Claude lifecycle checkpoint contained explicit project activity.",
        "event_time": transcript.get("event_time") or _now(),
    }


def _task_id(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return f"claude-{digest}"


def capture_claude_session_end(event: dict[str, Any], *, store: MemoryStore) -> dict[str, Any]:
    """Capture a Claude ``PreCompact`` or ``SessionEnd`` lifecycle event."""

    lifecycle_event = str(event.get("hook_event_name") or "SessionEnd")
    if lifecycle_event not in {"PreCompact", "SessionEnd"}:
        return {"status": "ignored", "reason": "unsupported lifecycle event"}
    session_id = _clean(event.get("session_id"), maximum=160)
    transcript_path = event.get("transcript_path")
    cwd = event.get("cwd")
    if not session_id or not isinstance(transcript_path, str) or not transcript_path.strip():
        return {"status": "ignored", "reason": "missing session_id or transcript_path"}
    if not isinstance(cwd, str) or not cwd.strip():
        return {"status": "ignored", "reason": "missing project directory"}

    project = detect_project(Path(cwd))
    transcript = read_claude_transcript(transcript_path)
    marker_key = (
        f"sessions/claude/{session_id}/captures/{transcript['tail_sha256'][:24]}"
    )
    existing = store.memory_history(
        marker_key,
        scope="project",
        project_id=project.id,
        limit=10,
    )
    if existing:
        return {
            "status": "already_captured",
            "session_id": session_id,
            "project_id": project.id,
            "task_id": _task_id(session_id),
        }

    extracted = extract_continuity(transcript)
    criteria = _unique(
        [*extracted["completed"], *extracted["unfinished"], *extracted["blocked"]]
    )
    if not criteria:
        return {"status": "ignored", "reason": "no explicit work state found"}

    task_id = _task_id(session_id)
    tasks = TaskContractService(store)
    source_ref = f"claude-session:{session_id}:{lifecycle_event.casefold()}"
    constraints = _unique([*extracted["constraints"], *extracted["do_not_repeat"]])
    try:
        status = tasks.status(task_id, project_id=project.id)
    except LookupError:
        status = tasks.start(
            extracted["goal"],
            criteria,
            project_id=project.id,
            title=extracted["title"],
            constraints=constraints,
            task_id=task_id,
            client="claude",
            source_ref=source_ref,
            event_time=extracted["event_time"],
        )
    criterion_ids = {
        item["description"]: item["id"] for item in status["contract"]["success_criteria"]
    }
    evidence = [
        {
            "criterion_id": criterion_ids[item],
            "evidence": f"Claude reported this complete in {source_ref}",
            "trust_class": "agent_attested",
            "source": source_ref,
        }
        for item in extracted["completed"]
        if item in criterion_ids
    ]
    next_action = next(
        iter([*extracted["next_actions"], *extracted["blocked"], *extracted["unfinished"]]),
        None,
    )
    decisions = [
        {"decision": item, "source": source_ref} for item in extracted["decisions"]
    ]
    for change in extracted["changes"]:
        current = change.get("current") or change["description"]
        previous = change.get("previous")
        decisions.append(
            {
                "decision": (
                    f"{current}. Previous value '{previous}' is superseded and stale."
                    if previous
                    else current
                ),
                "source": source_ref,
            }
        )
    status = tasks.checkpoint(
        task_id,
        extracted["summary"],
        project_id=project.id,
        current_action=next_action,
        evidence=evidence,
        blockers=extracted["blocked"],
        artifacts=extracted["files_touched"],
        decisions=decisions,
        client="claude",
        source_ref=source_ref,
        event_time=extracted["event_time"],
        workspace=workspace_snapshot(project.trusted_root),
    )

    common = {
        "scope": "project",
        "project_id": project.id,
        "source": "claude-lifecycle",
        "source_client": "claude",
        "source_ref": source_ref,
        "event_time": extracted["event_time"],
    }
    captured = 0
    for category, values in (
        ("project_truth", extracted["project_truth"]),
        ("decision", extracted["decisions"]),
        ("do_not_repeat", extracted["do_not_repeat"]),
    ):
        for index, value in enumerate(values):
            key = hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()[:16]
            store.set_current(
                f"continuity/{category}/{key}",
                value,
                topic=category.replace("_", " "),
                metadata={
                    "continuity_category": category,
                    "session_id": session_id,
                    "task_id": task_id,
                    "source_index": index,
                },
                kind="handoff" if category == "do_not_repeat" else "decision",
                reason=f"explicit {category} captured at Claude {lifecycle_event}",
                **common,
            )
            captured += 1
    for index, change in enumerate(extracted["changes"]):
        value = change.get("current") or change["description"]
        key_source = change.get("description") or value
        key = hashlib.sha256(key_source.casefold().encode("utf-8")).hexdigest()[:16]
        store.set_current(
            f"continuity/change/{key}",
            value,
            topic="change",
            metadata={
                "continuity_category": "change",
                "session_id": session_id,
                "task_id": task_id,
                "previous": change.get("previous"),
                "source_index": index,
            },
            kind="decision",
            reason=f"current value captured at Claude {lifecycle_event}",
            **common,
        )
        captured += 1

    store.set_current(
        marker_key,
        json.dumps(
            {
                "session_id": session_id,
                "task_id": task_id,
                "captured_items": captured,
                "transcript_retained": False,
                "lifecycle_event": lifecycle_event,
            },
            sort_keys=True,
        ),
        topic="session capture",
        metadata={"lians_type": "session_capture", "task_id": task_id},
        kind="session_capture",
        reason="idempotency marker for automatic Claude session capture",
        **common,
    )
    return {
        "status": "captured",
        "session_id": session_id,
        "project_id": project.id,
        "task_id": task_id,
        "captured_items": captured,
        "transcript_retained": False,
        "task": status,
    }
