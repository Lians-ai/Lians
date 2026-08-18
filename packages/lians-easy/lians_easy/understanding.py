"""Local request understanding for connected AI clients.

The engine is intentionally deterministic. It does not proxy the user's prompt
to another model and it does not persist prompt text. Its job is to identify the
few missing details that would materially change the next action, while using
already-recalled memory so an agent does not ask the user twice.
"""

from __future__ import annotations

import re
from typing import Any

_WORDS = re.compile(r"[a-z0-9][a-z0-9._+-]*")
_VAGUE_REFERENT = re.compile(r"\b(?:this|that|it|thing|stuff|something|whatever)\b", re.IGNORECASE)
_ACTION = re.compile(
    r"\b(?:analy[sz]e|build|compare|create|design|draft|explain|find|fix|implement|"
    r"investigate|learn|make|plan|prepare|research|review|ship|study|summari[sz]e|"
    r"test|understand|write)\b",
    re.IGNORECASE,
)

_INTENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("research", ("research", "analyze", "analysis", "dataset", "posts", "sources", "study")),
    ("build", ("build", "code", "implement", "fix", "app", "api", "repo", "test", "ship")),
    ("write", ("write", "draft", "essay", "article", "email", "copy", "report")),
    ("learn", ("learn", "teach", "explain", "study", "exam", "course")),
    ("plan", ("plan", "roadmap", "schedule", "strategy", "organize", "launch")),
    ("create", ("create", "design", "make", "visual", "presentation", "video")),
)

_SIGNALS: dict[str, tuple[str, ...]] = {
    "audience": ("audience", "reader", "client", "customer", "student", "user", "team", "for "),
    "evidence": ("source", "citation", "data", "dataset", "repo", "file", "posts", "evidence"),
    "format": ("explanation", "report", "table", "list", "code", "app", "essay", "email", "slides", "json", "csv"),
    "platform": ("windows", "mac", "linux", "web", "desktop", "mobile", "claude", "codex", "cursor"),
    "success": ("done", "success", "pass", "ship", "production", "at least", "must", "should"),
    "timeframe": (
        "today", "tomorrow", "week", "month", "deadline", "before", "after", "between", "latest",
        "january", "february", "march", "april", "may", "june", "july", "august", "september",
        "october", "november", "december",
    ),
}

_QUESTIONS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "research": (
        ("evidence", "Which sources or dataset should define the evidence?", "It changes what can be trusted and compared."),
        ("timeframe", "What time period should the research cover?", "It prevents an accurate answer from using the wrong window."),
        ("format", "What should the finished research look like?", "It determines how the evidence should be collected and condensed."),
    ),
    "build": (
        ("format", "What should the finished product let the user do?", "It defines the concrete outcome before implementation begins."),
        ("platform", "Where does this need to run?", "The platform changes the architecture and packaging choices."),
        ("success", "What would make this version ready to ship?", "It gives the agent a definition of done instead of an open-ended build."),
    ),
    "write": (
        ("audience", "Who is this for?", "Audience changes the language, detail, and examples."),
        ("format", "What form should the finished writing take?", "It prevents the agent from choosing the wrong deliverable."),
        ("success", "What must the reader understand or do afterward?", "It anchors the writing to an outcome."),
    ),
    "learn": (
        ("success", "What should you be able to do after learning this?", "It sets the depth and type of explanation."),
        ("format", "Would you rather get an explanation, examples, or a practice exercise?", "It adapts the lesson to how the user wants to learn."),
        ("timeframe", "Is there a deadline or session length to work within?", "It changes the size of the learning path."),
    ),
    "plan": (
        ("success", "What exact result should this plan produce?", "It keeps the plan tied to an outcome."),
        ("timeframe", "What deadline or planning horizon should it use?", "Timing changes priorities and sequencing."),
        ("audience", "Who will carry out or approve the plan?", "Ownership changes what needs to be explicit."),
    ),
    "create": (
        ("format", "What form should the finished work take?", "The medium changes the creative approach."),
        ("audience", "Who should this connect with?", "Audience changes style and emphasis."),
        ("success", "What should someone feel or do after seeing it?", "It gives the creative work a clear purpose."),
    ),
    "general": (
        ("success", "What should be different when this is finished?", "It turns a broad request into a useful outcome."),
        ("format", "What would you like the agent to produce first?", "It identifies the next useful deliverable."),
        ("audience", "Who is the result for?", "It prevents a generic answer."),
    ),
}


def _tokens(value: str) -> list[str]:
    return _WORDS.findall(value.lower())


def _clean(value: str, *, limit: int) -> str:
    rendered = " ".join(str(value).strip().split())
    if len(rendered) <= limit:
        return rendered
    return rendered[: max(1, limit - 1)].rstrip() + "…"


def _memory_layer(memory: dict[str, Any]) -> str:
    kind = str(memory.get("kind") or "memory").lower()
    if kind in {"preference", "profile"}:
        return "identity"
    if kind in {"decision", "task_contract", "task_state", "control_policy"}:
        return "working"
    if kind == "handoff":
        return "episodic"
    return "knowledge"


class UnderstandingService:
    """Turn a request plus bounded memory into an inspectable working brief."""

    @staticmethod
    def classify(request: str) -> str:
        words = set(_tokens(request))
        best = "general"
        best_score = 0
        for intent, vocabulary in _INTENTS:
            score = sum(1 for term in vocabulary if term in words)
            if score > best_score:
                best = intent
                best_score = score
        return best

    @staticmethod
    def analyze(
        request: str,
        *,
        memories: list[dict[str, Any]] | None = None,
        max_questions: int = 3,
    ) -> dict[str, Any]:
        rendered = _clean(request, limit=4000)
        if not rendered:
            raise ValueError("request cannot be blank")
        if len(request) > 20_000:
            raise ValueError("request must be 20,000 characters or fewer")
        bounded_memories = list(memories or [])[:5]
        intent = UnderstandingService.classify(rendered)
        combined = " ".join(
            [rendered, *[str(item.get("content") or "") for item in bounded_memories]]
        ).lower()
        signals = {
            name: any(signal in combined for signal in vocabulary)
            for name, vocabulary in _SIGNALS.items()
        }
        words = _tokens(rendered)
        has_action = bool(_ACTION.search(rendered))
        vague = bool(_VAGUE_REFERENT.search(rendered))
        lacks_goal = not has_action or len(words) < 3
        blocking = lacks_goal or (vague and len(words) < 10 and not bounded_memories)

        questions: list[dict[str, str]] = []
        if blocking:
            questions.append(
                {
                    "dimension": "outcome",
                    "question": "What do you want the finished result to do for you?",
                    "why": "The request does not yet identify a reliable outcome.",
                    "priority": "blocking",
                }
            )
        for dimension, question, why in _QUESTIONS[intent]:
            if len(questions) >= max(0, min(max_questions, 3)):
                break
            if not signals.get(dimension, False):
                questions.append(
                    {
                        "dimension": dimension,
                        "question": question,
                        "why": why,
                        "priority": "helpful",
                    }
                )

        known_context = [
            {
                "memory_id": str(item.get("id") or ""),
                "layer": _memory_layer(item),
                "detail": _clean(str(item.get("content") or ""), limit=180),
            }
            for item in bounded_memories
            if str(item.get("content") or "").strip()
        ]
        coverage = sum(1 for present in signals.values() if present)
        confidence = min(0.95, 0.42 + min(len(words), 16) * 0.02 + coverage * 0.06)
        if blocking:
            confidence = min(confidence, 0.49)

        first_blocking = next(
            (item for item in questions if item["priority"] == "blocking"),
            None,
        )
        needs_clarification = first_blocking is not None
        if needs_clarification:
            guidance = (
                "# Lians understanding\n"
                f"Intent: {intent}.\n"
                f"Ask one question before acting: {first_blocking['question']}\n"
                "Do not ask for details already present in Lians memory."
            )
        else:
            guidance = (
                "# Lians understanding\n"
                f"Intent: {intent}. The request is actionable. Begin with the supplied context; "
                "state a concise assumption only if a missing detail changes the first action."
            )

        return {
            "schema": "https://lians.ai/schemas/understanding-brief/v0.1",
            "intent": intent,
            "outcome": _clean(rendered, limit=500),
            "readiness": "needs_clarification" if needs_clarification else "ready",
            "needs_clarification": needs_clarification,
            "confidence": round(confidence, 2),
            "known_context": known_context,
            "known_dimensions": sorted(name for name, present in signals.items() if present),
            "missing_dimensions": [item["dimension"] for item in questions],
            "questions": questions,
            "guidance": guidance,
            "privacy": {
                "request_persisted": False,
                "external_model_called": False,
                "memory_items_considered": len(known_context),
            },
        }
