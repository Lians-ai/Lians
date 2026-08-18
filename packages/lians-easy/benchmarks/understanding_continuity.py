"""Deterministic Lians understanding and context-budget comparison.

This compares product strategies, not vendor quality. It does not call a model.
The checked fixture makes the token and recall tradeoff reproducible before any
provider-backed evaluation is attempted.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from lians_easy.store import MemoryStore
from lians_easy.understanding import UnderstandingService

SCENARIOS = (
    {
        "name": "social-research",
        "request": "Research this",
        "memories": (
            ("The research covers 10,000 public posts from August.", "project", "evidence"),
            ("The finished deliverable is a client report with a CSV appendix.", "handoff", "format"),
            ("The client cares about emerging purchase objections.", "profile", "audience"),
            ("Unrelated project uses a purple navigation bar.", "project", None),
        ),
        "essential": {"evidence", "format"},
        "useful_questions": {"outcome"},
    },
    {
        "name": "desktop-build",
        "request": "Build the Windows desktop app and make all tests pass before shipping",
        "memories": (
            ("The interface is designed for college students.", "profile", "audience"),
            ("The app must not require an AI provider API key.", "decision", "constraint"),
            ("The last handoff says the native launcher already works.", "handoff", "handoff"),
            ("The old website used a pink border.", "project", None),
        ),
        "essential": {"constraint", "handoff"},
        "useful_questions": set(),
    },
    {
        "name": "student-writing",
        "request": "Write an explanation of vector databases for first-year students",
        "memories": (
            ("Prefer short examples before formal definitions.", "preference", "style"),
            ("The output should fit on one study sheet.", "project", "format"),
            ("Use Python examples when code helps.", "preference", "style"),
            ("A different course uses Java.", "project", None),
        ),
        "essential": {"style", "format"},
        "useful_questions": {"success"},
    },
)


def _token_estimate(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def run_benchmark(directory: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    totals = {
        strategy: {"tokens": 0, "recalled": 0, "essential": 0, "questions": 0, "useful": 0}
        for strategy in ("prompt_only", "full_replay", "lians")
    }
    for index, scenario in enumerate(SCENARIOS):
        store = MemoryStore(directory / f"scenario-{index}.sqlite3")
        tags_by_id: dict[str, str | None] = {}
        replay_history = list(scenario["memories"])
        replay_history.extend(
            (
                (
                    f"Archived session {position}. "
                    + "This older working note is retained for audit history but does not "
                    "change the current request. " * 3,
                    "memory",
                    None,
                )
            )
            for position in range(12)
        )
        for content, kind, tag in replay_history:
            item = store.remember(content, kind=kind, scope="global")
            tags_by_id[item["id"]] = tag
        pack = store.context_pack(
            scenario["request"],
            project=None,
            client="understanding-benchmark",
            limit=3,
            max_tokens=512,
            excluded_kinds={"control_policy", "task_contract", "task_state"},
        )
        brief = UnderstandingService.analyze(
            scenario["request"],
            memories=pack["memories"],
            max_questions=3,
        )
        selected_tags = {
            tags_by_id.get(item["id"])
            for item in pack["memories"]
            if tags_by_id.get(item["id"])
        }
        question_dimensions = {item["dimension"] for item in brief["questions"]}
        full_tokens = sum(_token_estimate(content) for content, _, _ in replay_history)
        lians_tokens = int(pack["receipt"]["token_estimate"]) + _token_estimate(
            brief["guidance"]
        )
        essential = set(scenario["essential"])
        useful_questions = set(scenario["useful_questions"])
        strategies = {
            "prompt_only": {"tokens": 0, "recalled": set(), "questions": set()},
            "full_replay": {
                "tokens": full_tokens,
                "recalled": {tag for _, _, tag in replay_history if tag},
                "questions": set(),
            },
            "lians": {
                "tokens": lians_tokens,
                "recalled": selected_tags,
                "questions": question_dimensions,
            },
        }
        rendered: dict[str, Any] = {}
        for strategy, result in strategies.items():
            recalled = len(essential & result["recalled"])
            useful = len(useful_questions & result["questions"])
            rendered[strategy] = {
                "context_tokens_estimate": result["tokens"],
                "essential_facts_recalled": recalled,
                "essential_facts_total": len(essential),
                "question_dimensions": sorted(result["questions"]),
                "useful_question_dimensions": useful,
            }
            totals[strategy]["tokens"] += result["tokens"]
            totals[strategy]["recalled"] += recalled
            totals[strategy]["essential"] += len(essential)
            totals[strategy]["questions"] += len(result["questions"])
            totals[strategy]["useful"] += useful
        rows.append(
            {
                "scenario": scenario["name"],
                "intent": brief["intent"],
                "readiness": brief["readiness"],
                "lians_selected_fact_tags": sorted(selected_tags),
                "lians_selected_memory_count": len(pack["memories"]),
                "strategies": rendered,
            }
        )

    aggregate: dict[str, Any] = {}
    full_tokens = max(1, totals["full_replay"]["tokens"])
    for strategy, result in totals.items():
        aggregate[strategy] = {
            "context_tokens_estimate": result["tokens"],
            "essential_fact_recall_percent": round(
                result["recalled"] / max(1, result["essential"]) * 100,
                1,
            ),
            "useful_question_precision_percent": round(
                result["useful"] / max(1, result["questions"]) * 100,
                1,
            ) if result["questions"] else None,
            "context_vs_full_replay_percent": round(result["tokens"] / full_tokens * 100, 1),
        }
    return {
        "schema": "https://lians.ai/schemas/understanding-benchmark/v0.1",
        "method": "deterministic fixture; no model or vendor API called",
        "claim_boundary": (
            "Compares prompt-only, replay-all, and Lians strategies on checked fixtures. "
            "It is not a benchmark of Mem0, Letta, Supermemory, Graphiti, Graphify, or Pieces."
        ),
        "scenarios": rows,
        "aggregate": aggregate,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="lians-understanding-") as temporary:
        report = run_benchmark(Path(temporary))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
