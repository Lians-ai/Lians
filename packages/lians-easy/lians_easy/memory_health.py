"""Read-only diagnostics for a Lians memory profile."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import MemoryStore


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _layer(kind: str) -> str:
    if kind in {"preference", "profile"}:
        return "identity"
    if kind in {"decision", "task_contract", "task_state", "control_policy"}:
        return "working"
    if kind == "handoff":
        return "episodic"
    return "knowledge"


class MemoryHealthService:
    """Find fixable memory quality issues without changing or exposing content."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def inspect(self, *, stale_after_days: int = 90) -> dict[str, Any]:
        days = max(7, min(int(stale_after_days), 3650))
        current = self.store.list(state="current", limit=200)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in current:
            normalized = _normalized(str(item.get("content") or ""))
            if normalized:
                grouped[normalized].append(item)

        issues: list[dict[str, Any]] = []
        for matches in grouped.values():
            if len(matches) > 1:
                issues.append(
                    {
                        "type": "duplicate",
                        "severity": "medium",
                        "memory_ids": [item["id"] for item in matches],
                        "detail": "The same active fact is stored more than once.",
                    }
                )

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)  # noqa: UP017
        for item in current:
            kind = str(item.get("kind") or "memory").lower()
            if int(item.get("token_estimate") or 0) > 800:
                issues.append(
                    {
                        "type": "oversized",
                        "severity": "medium",
                        "memory_ids": [item["id"]],
                        "detail": "This memory is over 800 estimated tokens and should be split.",
                    }
                )
            if kind == "decision" and not item.get("memory_key"):
                issues.append(
                    {
                        "type": "unversioned_decision",
                        "severity": "medium",
                        "memory_ids": [item["id"]],
                        "detail": "This decision has no stable key, so newer state cannot supersede it safely.",
                    }
                )
            if kind in {"project", "handoff"} and item.get("scope") == "global":
                issues.append(
                    {
                        "type": "broad_scope",
                        "severity": "high",
                        "memory_ids": [item["id"]],
                        "detail": "Project-specific context is global and can leak into unrelated work.",
                    }
                )
            updated = item.get("updated_at")
            if updated and kind not in {"preference", "profile"}:
                try:
                    rendered_time = str(updated)
                    if rendered_time.endswith("Z"):
                        rendered_time = rendered_time[:-1] + "+00:00"
                    parsed = datetime.fromisoformat(rendered_time)
                except ValueError:
                    parsed = None
                if parsed is not None and parsed.astimezone(timezone.utc) < cutoff:  # noqa: UP017
                    issues.append(
                        {
                            "type": "stale",
                            "severity": "low",
                            "memory_ids": [item["id"]],
                            "detail": f"This memory has not changed in more than {days} days.",
                        }
                    )

        severity_weight = {"low": 2, "medium": 6, "high": 12}
        score = max(0, 100 - sum(severity_weight[item["severity"]] for item in issues))
        layers = Counter(_layer(str(item.get("kind") or "memory").lower()) for item in current)
        recommendations: list[str] = []
        issue_types = {item["type"] for item in issues}
        if "broad_scope" in issue_types:
            recommendations.append("Move project facts and handoffs into project scope.")
        if "duplicate" in issue_types:
            recommendations.append("Keep one current copy of duplicate facts and forget the extras after review.")
        if "unversioned_decision" in issue_types:
            recommendations.append("Use set_current with a stable memory key for decisions that can change.")
        if "oversized" in issue_types:
            recommendations.append("Split long notes into small facts so retrieval stays precise.")
        if "stale" in issue_types:
            recommendations.append("Review stale working memory before trusting it in a new task.")
        if not recommendations:
            recommendations.append("Memory is scoped, current, and compact. No cleanup is suggested.")

        return {
            "schema": "https://lians.ai/schemas/memory-health/v0.1",
            "score": score,
            "status": "healthy" if score >= 90 else "review" if score >= 70 else "needs_attention",
            "active_memories": len(current),
            "hierarchy": dict(sorted(layers.items())),
            "issue_count": len(issues),
            "issues": issues,
            "recommendations": recommendations,
            "mutated": False,
        }
