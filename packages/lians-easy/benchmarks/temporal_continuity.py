"""Reproducible adversarial benchmark for evolving cross-agent state.

This intentionally compares capabilities, not vendor marketing. The baseline is
an append-only retriever that keeps every note and returns the latest recorded
match. That is a common failure mode for chat history, vector stores, and simple
project indexes when facts change or arrive out of order.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lians_easy.project import Project
from lians_easy.store import MemoryStore


@dataclass
class BaselineRecord:
    memory_key: str
    content: str
    event_time: str
    client: str


class AppendOnlyBaseline:
    def __init__(self) -> None:
        self.records: list[BaselineRecord] = []

    def add(self, memory_key: str, content: str, event_time: str, client: str) -> None:
        self.records.append(BaselineRecord(memory_key, content, event_time, client))

    def current(self, memory_key: str) -> BaselineRecord | None:
        matches = [item for item in self.records if item.memory_key == memory_key]
        return matches[-1] if matches else None

    def recall(self, memory_key: str) -> list[BaselineRecord]:
        return [item for item in reversed(self.records) if item.memory_key == memory_key]

    def at(self, memory_key: str, _valid_at: str, _known_at: str) -> BaselineRecord | None:
        return self.current(memory_key)


def _case(name: str, passed: bool, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"case": name, "passed": passed, "evidence": evidence}


def run() -> dict[str, Any]:
    key = "architecture/database"
    old_content = "Use SQLite for the local prototype."
    current_content = "Use PostgreSQL for the shared production service."
    stale_content = "Use an in-memory database."
    old_time = "2026-08-01T12:00:00Z"
    current_time = "2026-08-10T12:00:00Z"
    stale_time = "2026-07-15T12:00:00Z"
    valid_before_change = "2026-08-05T12:00:00Z"
    valid_after_change = "2026-08-12T12:00:00Z"

    with tempfile.TemporaryDirectory(prefix="lians-temporal-benchmark-") as directory:
        root = Path(directory)
        project = Project(
            id="project-benchmark",
            name="Benchmark project",
            root=str(root),
            origin="example.com/benchmark/project",
        )
        cursor = MemoryStore(root / "memory.sqlite3")
        first = cursor.set_current(
            key,
            old_content,
            project_id=project.id,
            source_client="cursor",
            event_time=old_time,
        )
        current = cursor.set_current(
            key,
            current_content,
            project_id=project.id,
            source_client="claude",
            event_time=current_time,
            reason="production concurrency requirement",
        )
        stale_rejected = False
        try:
            cursor.set_current(
                key,
                stale_content,
                project_id=project.id,
                source_client="offline-agent",
                event_time=stale_time,
            )
        except ValueError:
            stale_rejected = True

        codex = MemoryStore(root / "memory.sqlite3")
        history = codex.memory_history(key, project_id=project.id)
        factual_past = codex.memory_at(
            key,
            project_id=project.id,
            valid_at=valid_before_change,
            known_at=current["recorded_at"],
        )
        knowledge_past = codex.memory_at(
            key,
            project_id=project.id,
            valid_at=valid_after_change,
            known_at=first["recorded_at"],
        )
        pack = codex.context_pack(
            "Which database should this architecture use?",
            project=project,
            client="codex",
        )

        baseline = AppendOnlyBaseline()
        baseline.add(key, old_content, old_time, "cursor")
        baseline.add(key, current_content, current_time, "claude")
        baseline_before_stale = baseline.current(key)
        baseline.add(key, stale_content, stale_time, "offline-agent")
        baseline_current = baseline.current(key)
        baseline_factual = baseline.at(key, valid_before_change, current_time)
        baseline_known = baseline.at(key, valid_after_change, old_time)
        baseline_recall = baseline.recall(key)

        lians_cases = [
            _case(
                "newer_update_becomes_current",
                history[-1]["content"] == current_content,
                {"current_id": history[-1]["id"]},
            ),
            _case(
                "late_stale_update_is_rejected",
                stale_rejected and history[-1]["content"] == current_content,
                {"stale_rejected": stale_rejected},
            ),
            _case(
                "factual_point_in_time_is_reconstructed",
                factual_past is not None and factual_past["content"] == old_content,
                {"selected_id": factual_past["id"] if factual_past else None},
            ),
            _case(
                "prior_agent_knowledge_is_reconstructed",
                knowledge_past is not None and knowledge_past["content"] == old_content,
                {"selected_id": knowledge_past["id"] if knowledge_past else None},
            ),
            _case(
                "current_recall_excludes_superseded_state",
                current_content in pack["context"] and old_content not in pack["context"],
                {"memory_ids": [item["id"] for item in pack["memories"]]},
            ),
            _case(
                "state_crosses_agent_clients",
                bool(pack["memories"])
                and pack["memories"][0]["source_client"] == "claude"
                and pack["receipt"]["client"] == "codex",
                {
                    "source_client": pack["memories"][0]["source_client"],
                    "receiving_client": pack["receipt"]["client"],
                },
            ),
        ]
        baseline_cases = [
            _case(
                "newer_update_becomes_current",
                baseline_before_stale is not None
                and baseline_before_stale.content == current_content,
                {"selected": baseline_before_stale.content if baseline_before_stale else None},
            ),
            _case(
                "late_stale_update_is_rejected",
                baseline_current is not None and baseline_current.content == current_content,
                {"selected": baseline_current.content if baseline_current else None},
            ),
            _case(
                "factual_point_in_time_is_reconstructed",
                baseline_factual is not None and baseline_factual.content == old_content,
                {"selected": baseline_factual.content if baseline_factual else None},
            ),
            _case(
                "prior_agent_knowledge_is_reconstructed",
                baseline_known is not None and baseline_known.content == old_content,
                {"selected": baseline_known.content if baseline_known else None},
            ),
            _case(
                "current_recall_excludes_superseded_state",
                [item.content for item in baseline_recall] == [current_content],
                {"selected_count": len(baseline_recall)},
            ),
            _case(
                "state_crosses_agent_clients",
                baseline_before_stale is not None and baseline_before_stale.client == "claude",
                {
                    "source_client": (
                        baseline_before_stale.client if baseline_before_stale else None
                    ),
                    "receiving_client": "codex",
                },
            ),
        ]

    def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
        passed = sum(1 for case in cases if case["passed"])
        return {
            "passed": passed,
            "total": len(cases),
            "score_percent": round(100 * passed / len(cases), 1),
            "cases": cases,
        }

    return {
        "schema": "https://lians.ai/schemas/temporal-continuity-benchmark/v0.1",
        "claim_boundary": (
            "Deterministic capability test against the included append-only baseline; "
            "not an independent vendor benchmark or universal quality claim."
        ),
        "systems": {
            "lians_temporal_continuity": summarize(lians_cases),
            "append_only_latest_match": summarize(baseline_cases),
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
