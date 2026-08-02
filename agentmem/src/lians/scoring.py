"""Deterministic, explainable memory admission and recall scoring."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from itertools import islice
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
_PRIVILEGED_TRUST_LEVELS = {"system_verified", "trusted_source"}
_SCORING_CONTROL_METADATA_KEYS = {
    "materiality",
    "source_trust",
    "trust_level",
}

SCORING_POLICY_VERSION = "lians-memory-scoring-v2"
_MAX_SCORING_TEXT_CHARS = 8_192
_MAX_SCORING_TOKENS = 1_024
_MAX_METADATA_SCORING_CHARS = 4_096
_MAX_METADATA_ITEMS = 64
_MAX_METADATA_DEPTH = 4
_MAX_METADATA_VALUE_CHARS = 512

# Unicode word runs cover scripts with spaces. Scripts that commonly omit
# spaces are additionally represented as character bigrams, matching the
# dependency-free fallback used by hybrid retrieval.
# Unicode letters/digits while preserving the pre-v2 behavior that treats
# snake_case separators as token boundaries.
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_UNSEGMENTED_SPAN = re.compile(
    "["
    "\u0e00-\u0e7f"  # Thai
    "\u0e80-\u0eff"  # Lao
    "\u1000-\u109f"  # Myanmar
    "\u1780-\u17ff"  # Khmer
    "\u3040-\u30ff"  # Hiragana and Katakana
    "\u3400-\u4dbf"  # CJK Extension A
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\uac00-\ud7af"  # Hangul syllables
    "\uf900-\ufaff"  # CJK Compatibility Ideographs
    "]+"
)
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


def _bounded_scoring_text(value: str) -> str:
    """Keep deterministic head/tail evidence without scanning a whole document."""
    if len(value) <= _MAX_SCORING_TEXT_CHARS:
        return value
    head = (_MAX_SCORING_TEXT_CHARS - 1) // 2
    tail = _MAX_SCORING_TEXT_CHARS - head - 1
    return f"{value[:head]} {value[-tail:]}"


def tokenize_for_scoring(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        text = _bounded_scoring_text(value).casefold()
    elif value is None:
        text = ""
    elif isinstance(value, (bool, int, float)):
        text = str(value).casefold()
    else:
        # Public JSON inputs are strings/scalars. Avoid invoking arbitrary
        # user-defined __str__ methods on internal callers.
        text = type(value).__name__.casefold()

    tokens: list[str] = []
    for word_match in _TOKEN.finditer(text):
        word = word_match.group(0)
        last = 0
        for match in _UNSEGMENTED_SPAN.finditer(word):
            if match.start() > last:
                tokens.append(word[last:match.start()])
            span = match.group(0)
            tokens.extend(
                span[index:index + 2]
                for index in range(max(1, len(span) - 1))
            )
            last = match.end()
            if len(tokens) >= _MAX_SCORING_TOKENS:
                return tuple(tokens[:_MAX_SCORING_TOKENS])
        if last < len(word):
            tokens.append(word[last:])
        if len(tokens) >= _MAX_SCORING_TOKENS:
            break
    return tuple(tokens[:_MAX_SCORING_TOKENS])


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _public_metadata_present(metadata: Mapping[str, Any]) -> bool:
    """Return whether bounded caller-visible evidence exists.

    Server-reserved metadata (``_admission``, ``_learning``, and similar)
    describes engine state. Its presence must not improve a memory's quality
    score merely because the engine attached it.
    """
    for key, _value in islice(metadata.items(), _MAX_METADATA_ITEMS):
        if (
            isinstance(key, str)
            and not key.startswith("_")
            and key not in _SCORING_CONTROL_METADATA_KEYS
        ):
            return True
    return False


def _metadata_text(metadata: Mapping[str, Any]) -> str:
    """Flatten metadata with strict depth, item, value, and output budgets."""
    parts: list[str] = []
    remaining_chars = _MAX_METADATA_SCORING_CHARS
    remaining_items = _MAX_METADATA_ITEMS

    def append(value: str) -> bool:
        nonlocal remaining_chars
        if remaining_chars <= 0:
            return False
        bounded = value[:min(_MAX_METADATA_VALUE_CHARS, remaining_chars)]
        if bounded:
            parts.append(bounded)
            remaining_chars -= len(bounded) + 1
        return remaining_chars > 0

    def walk(value: Any, depth: int) -> None:
        nonlocal remaining_items
        if remaining_items <= 0 or remaining_chars <= 0 or depth > _MAX_METADATA_DEPTH:
            return
        remaining_items -= 1
        if value is None:
            append("null")
        elif isinstance(value, str):
            append(value)
        elif isinstance(value, (bool, int, float)):
            append(str(value))
        elif isinstance(value, Mapping):
            # Work is bounded before sorting. Public JSON metadata has string
            # keys; non-string internal keys are represented by type only.
            entries = list(islice(value.items(), _MAX_METADATA_ITEMS))
            entries.sort(key=lambda item: item[0] if isinstance(item[0], str) else type(item[0]).__name__)
            for key, nested in entries:
                if remaining_items <= 0 or remaining_chars <= 0:
                    break
                key_text = key if isinstance(key, str) else type(key).__name__
                if (
                    key_text.startswith("_")
                    or key_text in _SCORING_CONTROL_METADATA_KEYS
                ):
                    continue
                append(key_text)
                walk(nested, depth + 1)
        elif isinstance(value, (list, tuple)):
            for nested in islice(value, _MAX_METADATA_ITEMS):
                if remaining_items <= 0 or remaining_chars <= 0:
                    break
                walk(nested, depth + 1)
        else:
            append(type(value).__name__)

    walk(metadata, 0)
    return " ".join(parts)[:_MAX_METADATA_SCORING_CHARS]


def _importance(
    content: str,
    metadata: Mapping[str, Any],
    supplied: float,
    *,
    tokens: Optional[tuple[str, ...]] = None,
    metadata_present: Optional[bool] = None,
    durable_signal: Optional[bool] = None,
    date_signal: Optional[bool] = None,
    ephemeral_signal: Optional[bool] = None,
) -> tuple[float, str]:
    bounded_content = _bounded_scoring_text(content)
    resolved_tokens = tokens if tokens is not None else tokenize_for_scoring(bounded_content)
    ephemeral = (
        ephemeral_signal
        if ephemeral_signal is not None
        else bool(_EPHEMERAL.match(bounded_content.strip()))
    )
    durable = (
        durable_signal
        if durable_signal is not None
        else bool(_DURABLE.search(bounded_content))
    )
    has_date = (
        date_signal
        if date_signal is not None
        else bool(_DATE.search(bounded_content))
    )
    has_metadata = (
        metadata_present
        if metadata_present is not None
        else _public_metadata_present(metadata)
    )
    score = 0.15 if len(resolved_tokens) < 3 or ephemeral else 0.4
    if durable:
        score += 0.3
    if has_date:
        score += 0.1
    if has_metadata:
        score += 0.1
    score = max(score, clamp(supplied))
    return clamp(score), "durable fact signals evaluated; caller importance preserved"


def _confidence(
    content: str,
    metadata: Mapping[str, Any],
    event_time: Optional[datetime],
    source: Optional[str],
    *,
    tokens: Optional[tuple[str, ...]] = None,
    metadata_present: Optional[bool] = None,
) -> tuple[float, str]:
    resolved_tokens = tokens if tokens is not None else tokenize_for_scoring(content)
    score = 0.2 + min(len(resolved_tokens), 12) / 30
    score += 0.15 if event_time else 0.0
    score += 0.1 if source else 0.0
    has_metadata = (
        metadata_present
        if metadata_present is not None
        else _public_metadata_present(metadata)
    )
    score += 0.15 if has_metadata else 0.0
    return clamp(score), "structured content, timestamp, source, and metadata evidence evaluated"


def _trust(
    source: Optional[str],
    metadata: Mapping[str, Any],
    verified_trust_level: Optional[str],
) -> tuple[float, str]:
    """Score provenance without trusting caller-controlled attestations.

    ``source`` and memory metadata arrive through the public write API, so they
    can describe provenance but cannot confer privileged trust.  A future
    server-side verifier may pass ``verified_trust_level`` explicitly after it
    has authenticated the source; that value is intentionally not read from
    metadata.
    """
    metadata_claim = metadata.get("trust_level") or metadata.get("source_trust")
    if verified_trust_level is not None:
        level = str(verified_trust_level).strip().lower()
        normalized = level if level in TRUST_LEVELS else "unknown"
        return TRUST_LEVELS[normalized], f"server-verified source trust level {normalized}"

    level = str(source or "unknown").strip().lower()
    reasons: list[str] = []
    if metadata_claim is not None:
        reasons.append("caller metadata trust claim ignored")
    if level in _PRIVILEGED_TRUST_LEVELS:
        reasons.append(f"unverified privileged source claim {level} ignored")
        level = "unknown"
    elif level not in TRUST_LEVELS:
        level = "unknown"
    reasons.append(f"caller-declared source trust level {level}")
    return TRUST_LEVELS[level], "; ".join(reasons)


def _freshness(event_time: Optional[datetime], valid_from: Optional[datetime], valid_to: Optional[datetime], superseded: bool, reference_time: datetime) -> tuple[float, str, bool]:
    reference = _utc(reference_time)
    event = _utc(event_time)
    start = _utc(valid_from) or event
    end = _utc(valid_to)
    if start and start > reference:
        return 0.0, "not yet valid at the recall reference time", False
    if end and reference >= end:
        return 0.0, "outside the validity window at the recall reference time", False
    if event and event > reference:
        return 0.0, "future-dated relative to the recall reference time", False
    if superseded and end is None:
        return 0.1, "marked superseded for present recall", True
    if not event:
        return 0.5, "no event time; neutral freshness", True
    age_days = max(0.0, (reference - event).total_seconds() / 86400)
    return clamp(math.exp(-math.log(2) * age_days / 90.0)), "valid at reference time with deterministic age decay", True


def _relevance(
    content: str,
    metadata: Mapping[str, Any],
    query: Optional[str],
    base_relevance: Optional[float],
    *,
    content_tokens: Optional[tuple[str, ...]] = None,
    query_tokens: Optional[tuple[str, ...]] = None,
    metadata_text: Optional[str] = None,
) -> tuple[float, str]:
    if not query:
        return 0.5, "no recall query; neutral relevance"
    resolved_query_tokens = set(
        query_tokens if query_tokens is not None else tokenize_for_scoring(query)
    )
    resolved_content_tokens = set(
        content_tokens if content_tokens is not None else tokenize_for_scoring(content)
    )
    resolved_content_tokens.update(tokenize_for_scoring(
        metadata_text if metadata_text is not None else _metadata_text(metadata)
    ))
    matches = resolved_query_tokens & resolved_content_tokens
    lexical = len(matches) / max(1, len(resolved_query_tokens))
    if base_relevance is None:
        return clamp(lexical), f"lexical token overlap matched {len(matches)} query tokens"
    combined = 0.7 * clamp(base_relevance) + 0.3 * lexical
    return clamp(combined), f"existing retrieval relevance plus {len(matches)} lexical token matches"


def _stability(
    content: str,
    metadata: Mapping[str, Any],
    event_time: Optional[datetime],
    *,
    tokens: Optional[tuple[str, ...]] = None,
    metadata_present: Optional[bool] = None,
    durable_signal: Optional[bool] = None,
    ephemeral_signal: Optional[bool] = None,
) -> tuple[float, str]:
    bounded_content = _bounded_scoring_text(content)
    resolved_tokens = tokens if tokens is not None else tokenize_for_scoring(bounded_content)
    ephemeral = (
        ephemeral_signal
        if ephemeral_signal is not None
        else bool(_EPHEMERAL.match(bounded_content.strip()))
    )
    score = 0.15 if len(resolved_tokens) < 3 or ephemeral else 0.45
    durable = (
        durable_signal
        if durable_signal is not None
        else bool(_DURABLE.search(bounded_content))
    )
    score += 0.25 if durable else 0.0
    score += 0.1 if event_time else 0.0
    has_metadata = (
        metadata_present
        if metadata_present is not None
        else _public_metadata_present(metadata)
    )
    score += 0.1 if has_metadata else 0.0
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
    verified_trust_level: Optional[str] = None,
    query_tokens: Optional[tuple[str, ...]] = None,
    purpose: str = "recall",
) -> dict[str, Any]:
    """Return bounded component scores, a gated final score, and stable reasons."""
    # Scoring is read-only. Retain the caller's mapping instead of copying an
    # arbitrarily large top-level object before the bounded walkers run.
    meta: Mapping[str, Any] = metadata if metadata is not None else {}
    tags = list(risk_tags or [])
    bounded_content = _bounded_scoring_text(content)
    content_tokens = tokenize_for_scoring(bounded_content)
    metadata_present = _public_metadata_present(meta)
    durable_signal = bool(_DURABLE.search(bounded_content))
    date_signal = bool(_DATE.search(bounded_content))
    ephemeral_signal = bool(_EPHEMERAL.match(bounded_content.strip()))
    metadata_text = _metadata_text(meta) if query else ""
    importance_score, importance_reason = _importance(
        bounded_content,
        meta,
        importance,
        tokens=content_tokens,
        metadata_present=metadata_present,
        durable_signal=durable_signal,
        date_signal=date_signal,
        ephemeral_signal=ephemeral_signal,
    )
    confidence_score, confidence_reason = _confidence(
        bounded_content,
        meta,
        event_time,
        source,
        tokens=content_tokens,
        metadata_present=metadata_present,
    )
    trust_score, trust_reason = _trust(source, meta, verified_trust_level)
    freshness_score, freshness_reason, temporal_eligible = _freshness(
        event_time, valid_from, valid_to, superseded, reference_time
    )
    relevance_score, relevance_reason = _relevance(
        bounded_content,
        meta,
        query,
        base_relevance,
        content_tokens=content_tokens,
        query_tokens=query_tokens,
        metadata_text=metadata_text,
    )
    stability_score, stability_reason = _stability(
        bounded_content,
        meta,
        event_time,
        tokens=content_tokens,
        metadata_present=metadata_present,
        durable_signal=durable_signal,
        ephemeral_signal=ephemeral_signal,
    )
    safety_score, safety_reason, safety_eligible = _safety(safety_status, tags)
    eligible = safety_eligible and temporal_eligible
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
        "safety_eligible": safety_eligible,
        "temporal_eligible": temporal_eligible,
        "purpose": purpose,
        "scoring_policy_version": SCORING_POLICY_VERSION,
        "reference_time": _utc(reference_time).isoformat(),
        "scoring_limits": {
            "text_sample_chars": _MAX_SCORING_TEXT_CHARS,
            "token_cap": _MAX_SCORING_TOKENS,
            "metadata_chars": _MAX_METADATA_SCORING_CHARS,
            "metadata_items": _MAX_METADATA_ITEMS,
            "metadata_depth": _MAX_METADATA_DEPTH,
            "metadata_value_chars": _MAX_METADATA_VALUE_CHARS,
        },
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


def rank_position_score(position: int, total: int, input_score: Any) -> float:
    """Encode an explicit returned order as a bounded, strictly descending score.

    Rerankers such as MMR and graph proximity are order-producing algorithms;
    their raw objectives are not calibrated to the hybrid recall score.  Each
    returned position therefore receives its own score bucket, while half of a
    bucket remains available to preserve the prior score as a deterministic
    secondary signal.  For the public recall limit (200), six-place rounding
    cannot collapse adjacent buckets.
    """
    count = max(1, int(total))
    index = min(max(0, int(position)), count - 1)
    bucket = count - index
    return round((bucket + 0.5 * clamp(input_score)) / (count + 1.0), 6)


def record_ranking_stage(
    memory: Any,
    *,
    stage: str,
    input_score: Any,
    output_score: Any,
    details: Optional[Mapping[str, Any]] = None,
    reason: Optional[str] = None,
) -> float:
    """Synchronize one reranking stage with a memory's public explanation."""
    before = round(clamp(input_score), 6)
    after = round(clamp(output_score), 6)
    prior = getattr(memory, "_score_breakdown", None)
    if not isinstance(prior, dict):
        return after

    breakdown = dict(prior)
    stages = [dict(item) for item in breakdown.get("ranking_stages", [])
              if isinstance(item, Mapping)]
    stage_record = {
        "stage": str(stage),
        "input_score": before,
        "output_score": after,
        **dict(details or {}),
    }
    stages.append(stage_record)
    reasons = list(breakdown.get("reasons") or [])
    if reason:
        reasons.append(reason)
    breakdown.update({
        "final_score": after,
        "ranking_stages": stages,
        "reasons": reasons,
    })
    setattr(memory, "_score_breakdown", breakdown)
    return after
