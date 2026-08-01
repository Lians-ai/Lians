"""Deterministic, explainable memory admission and recall scoring."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


RECALL_WEIGHTS = {
    "relevance_score": 0.35,
    "confidence_score": 0.15,
    "importance_score": 0.15,
    "trust_score": 0.10,
    "freshness_score": 0.10,
    "stability_score": 0.10,
    "safety_score": 0.05,
}
ADMISSION_WEIGHTS = {
    "importance_score": 0.25,
    "stability_score": 0.20,
    "confidence_score": 0.20,
    "trust_score": 0.15,
    "safety_score": 0.15,
    "freshness_score": 0.05,
}
TRUST_LEVELS = {
    "system_verified": 1.0,
    "trusted_source": 0.9,
    "user_provided": 0.75,
    "chat": 0.65,
    "imported": 0.6,
    "unknown": 0.5,
    "untrusted": 0.25,
}

_TOKEN = re.compile(r"[a-z0-9]+")
_EPHEMERAL = re.compile(r"^(?:hi|hello|hey|thanks|thank you|ok|okay|lol|bye)[.! ]*$", re.I)
_DURABLE = re.compile(
    r"\b(decided|decision|deadline|must|shall|required|constraint|policy|"
    r"revenue|guidance|budget|price|contract|prefers?|allergic|diagnos|legal|"
    r"regulat|compliance|effective|expires?)\b|[$€£]\s?\d|\b\d+(?:\.\d+)?%\b",
    re.I,
)
_DATE = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2}-\d{2})?\b")


def clamp(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(numeric):
        return 0.0
    return min(1.0, max(0.0, numeric))


def tokenize_for_scoring(value: Any) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(str(value or "").lower()))


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _metadata_text(metadata: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key, value in sorted(metadata.items()):
        if key.startswith("_"):
            continue
        parts.extend((str(key), str(value)))
    return " ".join(parts)


def _importance(content: str, metadata: Mapping[str, Any], supplied: float) -> tuple[float, str]:
    tokens = tokenize_for_scoring(content)
    score = 0.15 if len(tokens) < 3 or _EPHEMERAL.match(content.strip()) else 0.4
    if _DURABLE.search(content):
        score += 0.3
    if _DATE.search(content):
        score += 0.1
    if metadata:
        score += 0.1
    score = max(score, clamp(supplied))
    return clamp(score), "durable fact signals evaluated; caller importance preserved"


def _confidence(content: str, metadata: Mapping[str, Any], event_time: Optional[datetime], source: Optional[str]) -> tuple[float, str]:
    tokens = tokenize_for_scoring(content)
    score = 0.2 + min(len(tokens), 12) / 30
    score += 0.15 if event_time else 0.0
    score += 0.1 if source else 0.0
    score += 0.15 if metadata else 0.0
    return clamp(score), "structured content, timestamp, source, and metadata evidence evaluated"


def _trust(source: Optional[str], metadata: Mapping[str, Any]) -> tuple[float, str]:
    raw = metadata.get("trust_level") or metadata.get("source_trust") or source or "unknown"
    level = str(raw).strip().lower()
    score = TRUST_LEVELS.get(level, TRUST_LEVELS["unknown"])
    return score, f"source trust level {level if level in TRUST_LEVELS else 'unknown'}"


def _freshness(event_time: Optional[datetime], valid_from: Optional[datetime], valid_to: Optional[datetime], superseded: bool, reference_time: datetime) -> tuple[float, str]:
    reference = _utc(reference_time)
    event = _utc(event_time)
    start = _utc(valid_from) or event
    end = _utc(valid_to)
    if start and start > reference:
        return 0.0, "not yet valid at the recall reference time"
    if end and reference >= end:
        return 0.0, "outside the validity window at the recall reference time"
    if event and event > reference:
        return 0.0, "future-dated relative to the recall reference time"
    if superseded and end is None:
        return 0.1, "marked superseded for present recall"
    if not event:
        return 0.5, "no event time; neutral freshness"
    age_days = max(0.0, (reference - event).total_seconds() / 86400)
    return clamp(math.exp(-math.log(2) * age_days / 90.0)), "valid at reference time with deterministic age decay"


def _relevance(content: str, metadata: Mapping[str, Any], query: Optional[str], base_relevance: Optional[float]) -> tuple[float, str]:
    if not query:
        return 0.5, "no recall query; neutral relevance"
    query_tokens = set(tokenize_for_scoring(query))
    content_tokens = set(tokenize_for_scoring(f"{content} {_metadata_text(metadata)}"))
    lexical = len(query_tokens & content_tokens) / max(1, len(query_tokens))
    if base_relevance is None:
        return clamp(lexical), f"lexical token overlap matched {len(query_tokens & content_tokens)} query tokens"
    combined = 0.7 * clamp(base_relevance) + 0.3 * lexical
    return clamp(combined), f"existing retrieval relevance plus {len(query_tokens & content_tokens)} lexical token matches"


def _stability(content: str, metadata: Mapping[str, Any], event_time: Optional[datetime]) -> tuple[float, str]:
    tokens = tokenize_for_scoring(content)
    score = 0.15 if len(tokens) < 3 or _EPHEMERAL.match(content.strip()) else 0.45
    score += 0.25 if _DURABLE.search(content) else 0.0
    score += 0.1 if event_time else 0.0
    score += 0.1 if metadata else 0.0
    return clamp(score), "durability, specificity, timestamp, and evidence evaluated"


def _safety(status: str, risk_tags: list[str]) -> tuple[float, str, bool]:
    normalized = status.strip().lower()
    unsafe = normalized in {"quarantined", "unsafe", "rejected"} or any(
        tag in {"injection", "source:blocked"} for tag in risk_tags
    )
    if unsafe:
        return 0.0, "unsafe or quarantined content is gated from normal recall", False
    if normalized in {"review", "review_needed", "held_for_review", "pending"}:
        return 0.5, "human review required before normal recall", False
    return 1.0, "not quarantined", True


def score_memory(
    *, content: str, reference_time: datetime, metadata: Optional[Mapping[str, Any]] = None,
    importance: float = 0.5, source: Optional[str] = None,
    event_time: Optional[datetime] = None, valid_from: Optional[datetime] = None,
    valid_to: Optional[datetime] = None, superseded: bool = False,
    query: Optional[str] = None, base_relevance: Optional[float] = None,
    safety_status: str = "safe", risk_tags: Optional[list[str]] = None,
    purpose: str = "recall",
) -> dict[str, Any]:
    """Return bounded component scores, a gated final score, and stable reasons."""
    meta = dict(metadata or {})
    tags = list(risk_tags or [])
    importance_score, importance_reason = _importance(content, meta, importance)
    confidence_score, confidence_reason = _confidence(content, meta, event_time, source)
    trust_score, trust_reason = _trust(source, meta)
    freshness_score, freshness_reason = _freshness(event_time, valid_from, valid_to, superseded, reference_time)
    relevance_score, relevance_reason = _relevance(content, meta, query, base_relevance)
    stability_score, stability_reason = _stability(content, meta, event_time)
    safety_score, safety_reason, eligible = _safety(safety_status, tags)
    components = {
        "importance_score": importance_score,
        "confidence_score": confidence_score,
        "trust_score": trust_score,
        "freshness_score": freshness_score,
        "relevance_score": relevance_score,
        "stability_score": stability_score,
        "safety_score": safety_score,
    }
    weights = ADMISSION_WEIGHTS if purpose == "admission" else RECALL_WEIGHTS
    weighted = sum(components[key] * weight for key, weight in weights.items())
    final = clamp(weighted) if eligible else 0.0
    return {
        **{key: round(value, 6) for key, value in components.items()},
        "final_score": round(final, 6),
        "eligible": eligible,
        "purpose": purpose,
        "weights": dict(weights),
        "reasons": [importance_reason, confidence_reason, trust_reason, freshness_reason,
                    relevance_reason, stability_reason, safety_reason],
    }


def stable_score_key(memory_id: Any, event_time: Optional[datetime], created_at: Optional[datetime], breakdown: Mapping[str, Any]) -> tuple[Any, ...]:
    """Ascending sort key implementing final/event/created descending, id ascending."""
    event = _utc(event_time)
    created = _utc(created_at)
    return (-clamp(breakdown.get("final_score")),
            -(event.timestamp() if event else float("-inf")),
            -(created.timestamp() if created else float("-inf")), str(memory_id))
