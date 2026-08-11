"""Customer-owned, judge-free memory evaluation for local and CI workflows."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

DEFAULT_DATASET = Path(__file__).with_name("data") / "sample_memory_eval.json"
REPORT_SCHEMA = "lians.memory-eval.v1"


def load_dataset(path: str | Path | None = None) -> dict[str, Any]:
    dataset_path = Path(path).expanduser() if path else DEFAULT_DATASET
    return json.loads(dataset_path.read_text(encoding="utf-8"))


def _event_time(value: str) -> datetime:
    normalized = value if "T" in value else f"{value}T12:00:00"
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def run_evaluation(
    client: Any,
    dataset: dict[str, Any],
    *,
    k: int = 5,
    mode: str = "fast",
    min_recall: float = 0.8,
    max_stale_leak_rate: float = 0.0,
    max_p95_latency_ms: float = 500.0,
) -> dict[str, Any]:
    """Ingest the supplied sessions and measure the memory layer deterministically."""

    total = 0
    passed = 0
    stale_cases = 0
    stale_leaks = 0
    latency_values: list[float] = []
    token_values: list[int] = []
    deadline_misses = 0
    by_category: dict[str, list[int]] = {}
    detail: list[dict[str, Any]] = []

    for sample_index, sample in enumerate(dataset.get("samples", [])):
        agent_id = str(sample.get("agent_id") or f"eval-{sample_index}")
        for session in sample.get("sessions", []):
            event_time = _event_time(str(session["date"]))
            items = [
                {
                    "content": f"{turn['speaker']}: {turn['text']}",
                    "event_time": event_time,
                    "metadata": turn.get("metadata") or {"role": turn["speaker"]},
                }
                for turn in session.get("turns", [])
            ]
            if items:
                client.add_batch(agent_id=agent_id, items=items)

        for question in sample.get("questions", []):
            result = client.recall(
                agent_id=agent_id,
                query=str(question["question"]),
                k=k,
                mode=mode,
                strategy="standard" if mode == "fast" else "adaptive",
            )
            memories = result.get("memories", [])
            answer = str(question["answer"]).casefold()
            answer_found = any(answer in str(memory.get("content") or "").casefold()
                               for memory in memories)

            stale_excluded = True
            if question.get("stale") is not None:
                stale_cases += 1
                stale = str(question["stale"]).casefold()
                stale_excluded = not any(
                    stale in str(memory.get("content") or "").casefold()
                    for memory in memories
                )
                stale_leaks += int(not stale_excluded)

            latency_ms = float(result.get("latency_ms") or 0.0)
            token_estimate = int(result.get("token_estimate") or 0)
            deadline_exceeded = bool(result.get("deadline_exceeded", False))
            latency_values.append(latency_ms)
            token_values.append(token_estimate)
            deadline_misses += int(deadline_exceeded)

            ok = answer_found and stale_excluded
            category = str(question.get("category") or "general")
            by_category.setdefault(category, [0, 0])
            by_category[category][0] += int(ok)
            by_category[category][1] += 1
            total += 1
            passed += int(ok)
            detail.append({
                "question": question["question"],
                "category": category,
                "ok": ok,
                "answer_found": answer_found,
                "stale_excluded": stale_excluded,
                "latency_ms": latency_ms,
                "deadline_exceeded": deadline_exceeded,
                "token_estimate": token_estimate,
                "returned_memory_ids": [memory.get("id") for memory in memories],
                "receipt_sha256": result.get("receipt_sha256") or "",
            })

    recall_rate = passed / total if total else 0.0
    stale_leak_rate = stale_leaks / stale_cases if stale_cases else 0.0
    p95_latency = _percentile(latency_values, 0.95)
    threshold_checks = {
        "answer_recall_at_k": recall_rate >= min_recall,
        "stale_leak_rate": stale_leak_rate <= max_stale_leak_rate,
        "p95_latency_ms": p95_latency <= max_p95_latency_ms,
    }
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset.get("name", "unnamed-dataset"),
        "mode": mode,
        "k": k,
        "total_questions": total,
        "passed_questions": passed,
        "answer_recall_at_k": round(recall_rate, 6),
        "stale_cases": stale_cases,
        "stale_leaks": stale_leaks,
        "stale_leak_rate": round(stale_leak_rate, 6),
        "latency_ms": {
            "p50": round(_percentile(latency_values, 0.5), 3),
            "p95": round(p95_latency, 3),
            "max": round(max(latency_values, default=0.0), 3),
        },
        "deadline_miss_rate": round(deadline_misses / total, 6) if total else 0.0,
        "average_token_estimate": round(mean(token_values), 2) if token_values else 0.0,
        "by_category": {
            category: round(score / count, 6)
            for category, (score, count) in sorted(by_category.items())
        },
        "thresholds": {
            "min_recall": min_recall,
            "max_stale_leak_rate": max_stale_leak_rate,
            "max_p95_latency_ms": max_p95_latency_ms,
        },
        "threshold_checks": threshold_checks,
        "evaluation_passed": all(threshold_checks.values()),
        "detail": detail,
    }


def render_summary(report: dict[str, Any]) -> str:
    status = "PASS" if report["evaluation_passed"] else "FAIL"
    lines = [
        f"Lians memory evaluation: {status}",
        f"  dataset              {report['dataset']}",
        f"  answer recall@{report['k']:<3}   {report['answer_recall_at_k']:.1%}",
        f"  stale leak rate      {report['stale_leak_rate']:.1%}",
        f"  recall latency p95   {report['latency_ms']['p95']:.1f} ms",
        f"  deadline miss rate   {report['deadline_miss_rate']:.1%}",
        f"  average prompt size  {report['average_token_estimate']:.0f} tokens",
    ]
    for category, score in report["by_category"].items():
        lines.append(f"  {category[:20]:20} {score:.1%}")
    failed = [name for name, passed in report["threshold_checks"].items() if not passed]
    if failed:
        lines.append(f"  failed thresholds    {', '.join(failed)}")
    return "\n".join(lines)
