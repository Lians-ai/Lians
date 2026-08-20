"""Thin Claude-to-Codex project continuity experiment built on Lians Bridge."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "packages" / "lians-easy"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from lians_easy.mcp import default_data_path
from lians_easy.project import Project, detect_project
from lians_easy.store import MemoryStore
from lians_easy.task_contract import TaskContractService

SCHEMA = "https://lians.ai/schemas/cross-agent-session/v0.1"
HANDOFF_SCHEMA = "https://lians.ai/schemas/cross-agent-handoff/v0.1"
MAX_INPUT_BYTES = 1_000_000
MAX_ITEMS = 100
MAX_ITEM_CHARS = 2_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _token_estimate(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def _clean_text(value: Any, *, field: str, maximum: int = MAX_ITEM_CHARS) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    rendered = value.strip()
    if not rendered:
        raise ValueError(f"{field} cannot be blank")
    if len(rendered) > maximum:
        raise ValueError(f"{field} must be {maximum} characters or fewer")
    return rendered


def _slug(value: str) -> str:
    rendered = "".join(character if character.isalnum() else "-" for character in value.lower())
    rendered = "-".join(part for part in rendered.split("-") if part)
    if not rendered:
        raise ValueError("item id must contain a letter or number")
    return rendered[:80]


def _records(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    values = payload.get(field) or []
    if not isinstance(values, list):
        raise TypeError(f"{field} must be a list")
    if len(values) > MAX_ITEMS:
        raise ValueError(f"{field} must contain at most {MAX_ITEMS} items")
    result: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if isinstance(value, str):
            result.append({"id": _slug(value), "description": _clean_text(value, field=field)})
            continue
        if not isinstance(value, dict):
            raise TypeError(f"{field}[{index}] must be text or an object")
        description = value.get("description", value.get("content", value.get("decision")))
        clean = dict(value)
        clean["description"] = _clean_text(description, field=f"{field}[{index}].description")
        clean["id"] = _slug(str(value.get("id") or value.get("key") or clean["description"]))
        result.append(clean)
    return result


def _read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("session input is unexpectedly large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("session input must be a JSON object")
    return value


def _project(root: Path) -> Project:
    return detect_project(root.resolve())


def _memory_key(prefix: str, item: dict[str, Any]) -> str:
    supplied = str(item.get("key") or "").strip().lower()
    if supplied:
        return supplied
    return f"{prefix}/{item['id']}"


def _event_time(value: Any, *, fallback: str) -> str:
    return _clean_text(value, field="event_time", maximum=100) if value else fallback


def _parse_time(value: str) -> datetime:
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc  # noqa: UP017 - Python 3.10 compatibility
        )
    return datetime.fromisoformat(value)


class ContinuityExperiment:
    """Map session-end state into existing Lians primitives and derive a handoff."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.tasks = TaskContractService(store)

    def capture(
        self,
        payload: dict[str, Any],
        *,
        project: Project,
        client: str = "claude",
    ) -> dict[str, Any]:
        if payload.get("schema") not in {None, SCHEMA}:
            raise ValueError(f"session schema must be {SCHEMA}")
        session_id = _clean_text(payload.get("session_id"), field="session_id", maximum=160)
        task = payload.get("task")
        if not isinstance(task, dict):
            raise TypeError("task must be an object")
        task_id = _slug(_clean_text(task.get("id"), field="task.id", maximum=100))
        goal = _clean_text(task.get("goal"), field="task.goal")
        title = _clean_text(task.get("title", goal), field="task.title", maximum=160)
        timestamp = _event_time(payload.get("event_time"), fallback=_now())
        source_ref = str(payload.get("source_reference") or f"session:{session_id}")[:1_000]

        completed = _records(payload, "completed")
        in_progress = _records(payload, "in_progress")
        blocked = _records(payload, "blocked")
        not_started = _records(payload, "not_started")
        criteria = [*completed, *in_progress, *blocked, *not_started]
        if not criteria:
            raise ValueError("session must include completed, in_progress, blocked, or not_started")
        constraints = _records(payload, "constraints")
        decisions = _records(payload, "decisions")
        truths = _records(payload, "project_truth")
        changes = _records(payload, "changes")
        do_not_repeat = _records(payload, "do_not_repeat")
        next_actions = _records(payload, "next_actions")

        try:
            status = self.tasks.status(task_id, project_id=project.id)
        except LookupError:
            status = self.tasks.start(
                goal,
                [item["description"] for item in criteria],
                title=title,
                constraints=[item["description"] for item in constraints],
                project_id=project.id,
                task_id=task_id,
                client=client,
                source_ref=source_ref,
                event_time=timestamp,
            )
        else:
            existing = [item["description"] for item in status["contract"]["success_criteria"]]
            proposed = [item["description"] for item in criteria]
            if status["contract"]["goal"] != goal or existing != proposed:
                raise ValueError(
                    f"task {task_id} already exists with a different goal or definition of done"
                )

        criterion_ids = {
            item["description"]: item["id"] for item in status["contract"]["success_criteria"]
        }
        evidence = []
        for item in completed:
            evidence.append(
                {
                    "criterion_id": criterion_ids[item["description"]],
                    "evidence": _clean_text(
                        item.get("evidence") or f"Recorded complete in {source_ref}",
                        field="completed.evidence",
                        maximum=4_000,
                    ),
                    "trust_class": "agent_attested",
                    "source": source_ref,
                }
            )

        current_action = next(
            (
                item["description"]
                for item in [*next_actions, *in_progress, *blocked, *not_started]
            ),
            None,
        )
        blocker_text = [
            _clean_text(item.get("blocker") or item["description"], field="blocked.blocker")
            for item in blocked
        ]
        files_touched = [
            _clean_text(value, field="files_touched", maximum=1_000)
            for value in payload.get("files_touched") or []
        ]
        commit = str(payload.get("commit") or "").strip()
        artifacts = [*files_touched, *([f"git:{commit}"] if commit else [])]
        existing_state = status.get("state") or {}
        checkpoint_decisions = list(existing_state.get("decisions") or [])
        for item in decisions:
            key = _memory_key("decisions", item)
            checkpoint_decisions = [
                value
                for value in checkpoint_decisions
                if not str(value.get("source") or "").endswith(f"#{key}")
            ]
            checkpoint_decisions.append(
                {
                    "decision": item["description"],
                    "reason": str(item.get("reason") or "").strip(),
                    "source": f"{str(item.get('source') or source_ref).strip()}#{key}",
                }
            )
        for item in changes:
            key = _memory_key("changes", item)
            current = str(item.get("current") or item["description"]).strip()
            previous = str(item.get("previous") or "").strip()
            checkpoint_decisions = [
                value
                for value in checkpoint_decisions
                if not str(value.get("source") or "").endswith(f"#{key}")
            ]
            checkpoint_decisions.append(
                {
                    "decision": (
                        f"{current} Previous value '{previous}' is superseded and stale."
                        if previous
                        else current
                    ),
                    "reason": str(item.get("reason") or "session changed current project state"),
                    "source": f"{source_ref}#{key}",
                }
            )
        summary = _clean_text(
            payload.get("summary") or "Captured the current cross-agent work state.",
            field="summary",
            maximum=4_000,
        )
        open_questions = [
            _clean_text(value, field="open_questions", maximum=1_000)
            for value in payload.get("open_questions") or []
        ]
        proposed_evidence = {
            item["criterion_id"]: {
                "evidence": item["evidence"],
                "trust_class": item["trust_class"],
                "source": item["source"],
                "declared_trust_class": None,
                "trust_provenance": None,
            }
            for item in evidence
        }
        same_checkpoint = bool(existing_state) and all(
            (
                existing_state.get("summary") == summary,
                existing_state.get("current_action") == current_action,
                (existing_state.get("evidence") or {}) == proposed_evidence,
                list(existing_state.get("blockers") or []) == blocker_text,
                list(existing_state.get("artifacts") or []) == artifacts,
                list(existing_state.get("decisions") or []) == checkpoint_decisions,
                list(existing_state.get("open_questions") or []) == open_questions,
                existing_state.get("client") == client,
            )
        )
        if not same_checkpoint:
            status = self.tasks.checkpoint(
                task_id,
                summary,
                project_id=project.id,
                current_action=current_action,
                evidence=evidence,
                blockers=blocker_text,
                artifacts=artifacts,
                decisions=checkpoint_decisions,
                open_questions=open_questions,
                client=client,
                source_ref=source_ref,
                event_time=timestamp,
            )
        if list((status.get("state") or {}).get("decisions") or []) != checkpoint_decisions:
            normalized_state = dict(status["state"])
            normalized_state["decisions"] = checkpoint_decisions
            normalized_state["updated_at"] = _now()
            self.store.set_current(
                f"tasks/{task_id}/state",
                json.dumps(normalized_state, ensure_ascii=False, sort_keys=True),
                source="continuity-extractor",
                topic=f"task:{task_id}",
                metadata={"lians_type": "task_state", "task_id": task_id},
                kind="task_state",
                scope="project",
                project_id=project.id,
                source_client=client,
                source_ref=source_ref,
                event_time=timestamp,
                reason="superseded session decisions removed from current work state",
                expected_current_id=status["lineage"]["state_memory_id"],
            )
            status = self.tasks.status(task_id, project_id=project.id)

        captured: list[dict[str, Any]] = []
        common = {
            "scope": "project",
            "project_id": project.id,
            "source": "continuity-extractor",
            "source_client": client,
            "source_ref": source_ref,
            "event_time": timestamp,
        }
        for category, prefix, kind, records in (
            ("project_truth", "project", "project", truths),
            ("constraint", "constraints", "decision", constraints),
            ("decision", "decisions", "decision", decisions),
            ("do_not_repeat", f"tasks/{task_id}/do-not-repeat", "handoff", do_not_repeat),
        ):
            for item in records:
                captured.append(
                    self.store.set_current(
                        _memory_key(prefix, item),
                        item["description"],
                        topic=category.replace("_", " "),
                        metadata={
                            "continuity_category": category,
                            "session_id": session_id,
                            "task_id": task_id,
                        },
                        kind=kind,
                        reason=str(item.get("reason") or f"{category} refreshed by session end")[:500],
                        **common,
                    )
                )

        for item in changes:
            key = _memory_key("changes", item)
            previous = str(item.get("previous") or "").strip()
            current = str(item.get("current") or item["description"]).strip()
            previous_time = str(item.get("previous_event_time") or "").strip()
            if previous and not self.store.memory_history(key, project_id=project.id):
                if not previous_time:
                    parsed = _parse_time(timestamp)
                    previous_time = (parsed - timedelta(seconds=1)).isoformat()
                captured.append(
                    self.store.set_current(
                        key,
                        previous,
                        topic="change",
                        metadata={
                            "continuity_category": "change",
                            "session_id": session_id,
                            "task_id": task_id,
                        },
                        kind="decision",
                        reason="initial state captured before session change",
                        event_time=previous_time,
                        **{name: value for name, value in common.items() if name != "event_time"},
                    )
                )
            captured.append(
                self.store.set_current(
                    key,
                    _clean_text(current, field="changes.current"),
                    topic="change",
                    metadata={
                        "continuity_category": "change",
                        "session_id": session_id,
                        "task_id": task_id,
                    },
                    kind="decision",
                    reason=str(item.get("reason") or "session changed current project state")[:500],
                    **common,
                )
            )

        return {
            "schema": SCHEMA,
            "session_id": session_id,
            "project": project.public(),
            "task": status,
            "memories_captured_or_confirmed": len(captured),
            "memory_ids": [item["id"] for item in captured],
            "source_reference": source_ref,
        }

    def handoff(
        self,
        *,
        project: Project,
        task_id: str | None = None,
        client: str = "codex",
        max_tokens: int = 1_200,
        max_items: int = 20,
    ) -> dict[str, Any]:
        if not 300 <= int(max_tokens) <= 1_200:
            raise ValueError("max_tokens must be between 300 and 1200")
        if not 8 <= int(max_items) <= 20:
            raise ValueError("max_items must be between 8 and 20")
        continuation = self.tasks.continue_work(
            project_id=project.id,
            task_id=task_id,
            client=client,
            max_tokens=min(max_tokens, 768),
        )
        if continuation["status"] != "ready":
            return {
                "schema": HANDOFF_SCHEMA,
                "status": continuation["status"],
                "context": "",
                "tasks": continuation.get("tasks") or [],
                "message": continuation.get("message"),
            }

        status = continuation
        contract = status["contract"]
        state = status.get("state") or {}
        assessment = status["assessment"]
        completed = [item["description"] for item in assessment["criteria"] if item["satisfied"]]
        reported_completed = [
            item["description"]
            for item in assessment["criteria"]
            if item.get("evidence") and not item["satisfied"]
        ]
        unfinished = [
            item["description"]
            for item in assessment["criteria"]
            if not item["satisfied"] and not item.get("evidence")
        ]
        current_memories = self.store.list(
            state="current",
            project_id=project.id,
            limit=500,
        )
        signals = [
            item
            for item in current_memories
            if (item.get("metadata") or {}).get("task_id") in {None, status["task_id"]}
            and (item.get("metadata") or {}).get("continuity_category")
        ]
        decisions = [
            item for item in signals if item["metadata"]["continuity_category"] == "decision"
        ]
        project_truth = [
            item
            for item in signals
            if item["metadata"]["continuity_category"] == "project_truth"
        ]
        constraints = [
            item for item in signals if item["metadata"]["continuity_category"] == "constraint"
        ]
        changes = [
            item for item in signals if item["metadata"]["continuity_category"] == "change"
        ]
        do_not_repeat = [
            item
            for item in signals
            if item["metadata"]["continuity_category"] == "do_not_repeat"
        ]

        change_views: list[dict[str, Any]] = []
        stale_ids: list[str] = []
        for item in changes:
            history = self.store.memory_history(item["memory_key"], project_id=project.id)
            previous = history[-2] if len(history) > 1 else None
            if previous:
                stale_ids.append(previous["id"])
            change_views.append(
                {
                    "key": item["memory_key"],
                    "current": item["content"],
                    "previous": previous["content"] if previous else None,
                    "reason": item.get("supersession_reason"),
                    "current_memory_id": item["id"],
                    "stale_memory_id": previous["id"] if previous else None,
                }
            )

        sections: list[tuple[str, list[str]]] = [
            ("Verified complete", completed),
            ("Reported complete; verify", reported_completed),
            ("Still open", unfinished),
            ("Blocked", list(assessment["blockers"])),
            ("Project truth", [item["content"] for item in project_truth]),
            ("Decisions", [item["content"] for item in decisions]),
            (
                "Changed",
                [
                    f"{item['current']} (current; replaces {item['previous']}, now stale"
                    + (f"; why: {item['reason']}" if item.get("reason") else "")
                    + ")"
                    if item.get("previous")
                    else f"{item['current']} (current)"
                    for item in change_views
                ],
            ),
            (
                "Do NOT",
                [
                    *[f"redo completed work: {item}" for item in completed],
                    *[item["content"] for item in do_not_repeat],
                    *[item["content"] for item in constraints],
                ],
            ),
        ]
        next_action = state.get("current_action")
        lines = [
            "# Lians project continuity",
            f"Project: {project.name}",
            f"Task: {contract['title']}",
            f"Status: {assessment['status']}",
        ]
        selected_items = 0
        for heading, values in sections:
            if not values or selected_items >= max_items:
                continue
            lines.append(f"{heading}:")
            for value in values:
                candidate = f"- {value}"
                proposed = "\n".join([*lines, candidate])
                if selected_items >= max_items or _token_estimate(proposed) > max_tokens:
                    break
                lines.append(candidate)
                selected_items += 1
        if next_action:
            candidate = f"Next recommended action: {next_action}"
            if _token_estimate("\n".join([*lines, candidate])) <= max_tokens:
                lines.append(candidate)
        boundary = (
            "Boundary: use only this project-scoped current state; "
            "superseded values are history."
        )
        if _token_estimate("\n".join([*lines, boundary])) <= max_tokens:
            lines.append(boundary)
        context = "\n".join(lines)
        if _token_estimate(context) > max_tokens:
            raise RuntimeError("continuity renderer exceeded its token budget")

        receipt = {
            "schema": HANDOFF_SCHEMA,
            "created_at": _now(),
            "client": client,
            "project_id": project.id,
            "task_id": status["task_id"],
            "context_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest(),
            "token_estimate": _token_estimate(context),
            "selected_item_count": selected_items,
            "source_memory_ids": [
                memory_id
                for memory_id in (
                    status["lineage"]["contract_memory_id"],
                    status["lineage"]["state_memory_id"],
                    *[item["id"] for item in signals],
                )
                if memory_id is not None
            ],
            "stale_memory_ids_excluded_from_current": stale_ids,
            "limits": {"max_tokens": max_tokens, "max_items": max_items},
        }
        receipt["signature"] = self.store.cipher.sign(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        return {
            "schema": HANDOFF_SCHEMA,
            "status": "ready",
            "context": context,
            "receipt": receipt,
            "state": {
                "completed": completed,
                "reported_completed": reported_completed,
                "unfinished": unfinished,
                "blocked": list(assessment["blockers"]),
                "project_truth": [item["content"] for item in project_truth],
                "decisions": [item["content"] for item in decisions],
                "constraints": [item["content"] for item in constraints],
                "changes": change_views,
                "do_not_repeat": [item["content"] for item in do_not_repeat],
                "next_action": next_action,
            },
            "sources": [
                {
                    "memory_id": item["id"],
                    "memory_key": item["memory_key"],
                    "category": item["metadata"]["continuity_category"],
                    "source_client": item["source_client"],
                    "source_reference": item["source_ref"],
                    "event_time": item["event_time"],
                }
                for item in signals
            ],
            "metrics": {
                "continuity_context_tokens": receipt["token_estimate"],
                "continuity_items_selected": selected_items,
                "active_project_memories_available": len(current_memories),
                "stale_facts_excluded_from_current": len(stale_ids),
                "re_explanation_facts_avoided": selected_items,
            },
        }


def evaluate(handoff: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """Evaluate structured continuity. Agent behavior remains a separate live test."""

    state = handoff.get("state") or {}
    checks: list[dict[str, Any]] = []

    def compare(field: str, actual: list[str]) -> None:
        for value in expected.get(field) or []:
            rendered = _clean_text(value, field=f"expected.{field}")
            passed = any(rendered.casefold() in item.casefold() for item in actual)
            checks.append({"field": field, "expected": rendered, "passed": passed})

    compare("completed", list(state.get("completed") or []))
    compare("reported_completed", list(state.get("reported_completed") or []))
    compare("unfinished", list(state.get("unfinished") or []))
    compare("project_truth", list(state.get("project_truth") or []))
    compare("decisions", list(state.get("decisions") or []))
    compare("constraints", list(state.get("constraints") or []))
    compare("do_not_repeat", list(state.get("do_not_repeat") or []))
    next_action = str(state.get("next_action") or "")
    for value in expected.get("next_actions") or []:
        rendered = _clean_text(value, field="expected.next_actions")
        checks.append(
            {
                "field": "next_actions",
                "expected": rendered,
                "passed": rendered.casefold() in next_action.casefold(),
            }
        )
    active_change_values = [str(item.get("current") or "") for item in state.get("changes") or []]
    compare("current_facts", active_change_values)
    stale = [str(value) for value in expected.get("stale_facts") or []]
    stale_errors = sum(
        any(value.casefold() == current.casefold() for current in active_change_values)
        for value in stale
    )
    passed = sum(bool(item["passed"]) for item in checks)
    total = len(checks)
    return {
        "schema": "https://lians.ai/schemas/cross-agent-evaluation/v0.1",
        "continuity_accuracy": round(passed / total, 4) if total else 1.0,
        "correct_facts": passed,
        "expected_facts": total,
        "stale_fact_error_rate": round(stale_errors / len(stale), 4) if stale else 0.0,
        "stale_fact_errors": stale_errors,
        "redundant_work_rate": "requires live second-agent observation",
        "context_tokens": (handoff.get("metrics") or {}).get("continuity_context_tokens", 0),
        "checks": checks,
        "passed": total > 0 and passed / total >= 0.8 and stale_errors == 0,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--data", type=Path, default=default_data_path())
    result.add_argument("--project-root", type=Path, default=Path.cwd())
    commands = result.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Map one session-end extraction into Lians")
    capture.add_argument("--session", type=Path, required=True)
    capture.add_argument("--client", default="claude")
    show = commands.add_parser("show", help="Render the current bounded handoff")
    show.add_argument("--task-id")
    show.add_argument("--client", default="codex")
    show.add_argument("--max-tokens", type=int, default=1_200)
    show.add_argument("--max-items", type=int, default=20)
    show.add_argument("--json", action="store_true")
    check = commands.add_parser("evaluate", help="Evaluate the current handoff")
    check.add_argument("--expected", type=Path, required=True)
    check.add_argument("--task-id")
    check.add_argument("--json", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = MemoryStore(args.data)
    project = _project(args.project_root)
    service = ContinuityExperiment(store)
    if args.command == "capture":
        result = service.capture(_read_json(args.session), project=project, client=args.client)
        print(
            json.dumps(
                {
                    "memories_captured_or_confirmed": len(result.get("memory_ids") or []),
                    "status": "captured",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    handoff = service.handoff(
        project=project,
        task_id=args.task_id,
        client="codex",
        max_tokens=getattr(args, "max_tokens", 1_200),
        max_items=getattr(args, "max_items", 20),
    )
    if args.command == "evaluate":
        result = evaluate(handoff, _read_json(args.expected))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1
    if args.json:
        print(json.dumps(handoff, indent=2, sort_keys=True))
    else:
        print(handoff.get("context") or handoff.get("message") or "No continuity available.")
    return 0 if handoff.get("status") == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
