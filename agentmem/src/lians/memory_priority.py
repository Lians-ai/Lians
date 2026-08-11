"""Deterministic memory-priority assessment.

The storage API intentionally accepts raw conversational turns as well as
already-distilled facts.  This module gives every integration the same small,
local classifier so durable preferences are promoted without putting another
model or network call on the write path.  The source text remains authoritative;
the assessment is a reserved, replaceable projection used for ranking and audit.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "lians.memory-priority.v1"
DEFAULT_IMPORTANCE = 0.5

_MAX_ASSESSMENT_CHARS = 8_192
_TRANSIENT = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|thx|ok|okay|k|sure|got it|sounds good|"
    r"perfect|great|cool|nice|lol|bye|goodbye|no problem|you'?re welcome|"
    r"happy to help)[.!?,\s]*$",
    re.IGNORECASE,
)
_EXPLICIT_MEMORY = re.compile(
    r"\b(?:remember (?:that|this)|keep (?:this|that) in mind|note (?:that|this)|"
    r"don'?t forget|for future reference|going forward)\b",
    re.IGNORECASE,
)
_PERSONAL_PREFERENCE = re.compile(
    r"\b(?:I|we)\s+(?:(?:really|generally|usually|typically|always|strongly)\s+)?"
    r"(?:prefer|like|love|enjoy|dislike|hate|avoid|would rather)\b"
    r"|\bmy\s+(?:favorite|favourite|preference|preferred|communication style|"
    r"working style)\b"
    r"|\b(?:user|customer|client|they|he|she)\s+"
    r"(?:prefers?|likes?|loves?|enjoys?|dislikes?|hates?|avoids?)\b"
    r"|\b(?:please\s+)?(?:always|never)\s+"
    r"(?:use|call|address|write|respond|reply|format|include|omit|avoid)\b"
    r"|\b(?:please\s+)?(?:do not|don'?t)\s+"
    r"(?:use|call|address|write|respond|reply|format|include|omit)\b"
    r"|\bI\s+(?:(?:usually|typically|always)\s+)?use\b"
    r"|\bI\s+(?:want|need)\s+(?:my\b|all\b|answers?\b|responses?\b|"
    r"you\s+to\s+(?:always|never)\b)"
    r"|\b(?:call|address)\s+me\s+(?:as\s+)?\b",
    re.IGNORECASE,
)
_DURABLE_PERSONAL_FACT = re.compile(
    r"\bmy\s+(?:name|pronouns?|timezone|time zone|language|locale)\s+(?:is|are)\b"
    r"|\bI(?:'m| am)\s+(?:allergic|vegetarian|vegan|pescatarian|gluten[- ]free|"
    r"lactose[- ]intolerant|based|located)\b"
    r"|\bI\s+(?:live|work|go by)\b"
    r"|\bI\s+(?:can'?t|cannot|don'?t|do not)\s+"
    r"(?:eat|use|read|see|hear|access|attend)\b",
    re.IGNORECASE,
)
_LOW_VALUE_QUESTION = re.compile(r"^.{0,240}\?\s*$", re.DOTALL)

_DURABLE_KIND_FLOORS = {
    "preference": 0.90,
    "policy": 0.90,
    "reflection": 0.78,
    "relationship": 0.75,
    "procedure": 0.72,
}
_SIGNIFICANT_KIND_FLOORS = {
    "outcome": 0.65,
}


@dataclass(frozen=True)
class MemoryPriority:
    kind: str
    tier: str
    importance: float
    durable: bool
    transient: bool
    signals: tuple[str, ...]

    def metadata(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "kind": self.kind,
            "tier": self.tier,
            "importance": self.importance,
            "durable": self.durable,
            "transient": self.transient,
            "signals": list(self.signals),
        }


def _clamp(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return DEFAULT_IMPORTANCE
    if not math.isfinite(numeric):
        return DEFAULT_IMPORTANCE
    return min(1.0, max(0.0, numeric))


def _bounded(value: str) -> str:
    if len(value) <= _MAX_ASSESSMENT_CHARS:
        return value
    half = _MAX_ASSESSMENT_CHARS // 2
    return f"{value[:half]} {value[-half:]}"


def assess_memory_priority(
    content: str,
    metadata: Mapping[str, Any] | None = None,
    supplied_importance: float = DEFAULT_IMPORTANCE,
) -> MemoryPriority:
    """Classify one retained item and resolve its stable salience.

    Durable signals set an importance *floor*.  A caller can still promote any
    ordinary item explicitly.  Exact conversational acknowledgements are
    demoted only when they carry the API's neutral default, preserving an
    intentional non-default caller score.
    """
    from .memory_compiler import classify_memory

    meta = dict(metadata or {})
    meta.pop("_memory_priority", None)
    text = _bounded((content or "").strip())
    kind, _confidence, method = classify_memory(text, meta)
    supplied = _clamp(supplied_importance)
    signals: list[str] = []

    transient = bool(_TRANSIENT.fullmatch(text))
    explicit = bool(_EXPLICIT_MEMORY.search(text)) or bool(meta.get("_explicit_memory"))
    preference_signal = bool(_PERSONAL_PREFERENCE.search(text))
    preference = kind == "preference" or (
        method != "caller" and preference_signal
    )
    personal_fact = bool(_DURABLE_PERSONAL_FACT.search(text))

    if explicit:
        signals.append("explicit-memory-cue")
    if preference_signal:
        signals.append("personal-preference")
    if preference:
        kind = "preference"
    if personal_fact:
        signals.append("durable-personal-fact")
    if transient:
        signals.append("conversational-acknowledgement")

    durable = (
        explicit
        or preference_signal
        or personal_fact
        or kind in _DURABLE_KIND_FLOORS
    )
    if durable:
        tier = "durable"
        floor = max(
            0.88 if explicit or personal_fact or preference_signal else 0.0,
            _DURABLE_KIND_FLOORS.get(kind, 0.0),
        )
        importance = max(supplied, floor)
    elif transient:
        tier = "transient"
        importance = 0.15 if supplied == DEFAULT_IMPORTANCE else supplied
    elif _LOW_VALUE_QUESTION.fullmatch(text):
        tier = "contextual"
        signals.append("short-lived-question")
        importance = 0.30 if supplied == DEFAULT_IMPORTANCE else supplied
    elif kind in _SIGNIFICANT_KIND_FLOORS:
        tier = "significant"
        importance = max(supplied, _SIGNIFICANT_KIND_FLOORS[kind])
    else:
        tier = "standard"
        importance = supplied

    return MemoryPriority(
        kind=kind,
        tier=tier,
        importance=round(_clamp(importance), 4),
        durable=durable,
        transient=transient,
        signals=tuple(signals),
    )


def apply_memory_priority(req: Any) -> MemoryPriority:
    """Replace caller-controlled priority metadata and normalize ``req``."""
    metadata = dict(req.metadata or {})
    metadata.pop("_memory_priority", None)
    priority = assess_memory_priority(req.content, metadata, req.importance)
    metadata["_memory_priority"] = priority.metadata()
    req.metadata = metadata
    req.importance = priority.importance
    return priority
