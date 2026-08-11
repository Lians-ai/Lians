"""Deterministic, provenance-preserving memory compilation.

The raw memory remains the source of truth. This module adds a compact typed
projection to metadata so retrieval, policy, and evaluation can distinguish
facts, preferences, procedures, episodes, outcomes, relationships, policies,
and reviewed reflections without rewriting or summarizing the source text.

Compilation is deliberately local and deterministic:

* no model or network call is required;
* caller-supplied ``memory_type`` is authoritative when valid;
* every projection records its compiler and schema version;
* the source content hash and event time remain attached to the projection;
* recompilation is safe because the reserved metadata block is replaced.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

SCHEMA_VERSION = "lians.memory-artifact.v1"
COMPILER_VERSION = "deterministic-v1"
METADATA_KEY = "_lians_compiled"

MEMORY_KINDS = frozenset({
    "fact",
    "preference",
    "procedure",
    "episode",
    "outcome",
    "relationship",
    "policy",
    "reflection",
})

_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "reflection",
        re.compile(r"\b(lesson learned|next time|in retrospect|we learned|reflection)\b", re.I),
        0.93,
    ),
    (
        "policy",
        re.compile(
            r"\b(policy|must not|must always|shall not|shall always|required to|"
            r"prohibited|not permitted|compliance requires)\b",
            re.I,
        ),
        0.94,
    ),
    (
        "procedure",
        re.compile(
            r"\b(first,\s|first\s|next,\s|then\s|after that|steps? (?:are|to)|"
            r"workflow|runbook|how to|procedure|click\s|open\s.+\sthen\s)\b",
            re.I,
        ),
        0.90,
    ),
    (
        "outcome",
        re.compile(
            r"\b(outcome|result(?:ed)?|succeeded|failed|resolved|completed|"
            r"approved|rejected|reduced|increased|achieved)\b",
            re.I,
        ),
        0.86,
    ),
    (
        "preference",
        re.compile(
            r"\b(?:I|we)\s+(?:(?:really|generally|usually|typically|always|strongly)\s+)?"
            r"(?:prefer|like|love|enjoy|dislike|hate|avoid|would rather)\b"
            r"|\bmy\s+(?:favorite|favourite|preference|preferred|communication style|"
            r"working style)\b"
            r"|\b(?:user|customer|client|they|he|she)\s+"
            r"(?:prefers?|likes?|loves?|enjoys?|dislikes?|hates?|avoids?)\b"
            r"|\b\w+\s+(?:prefers|preferred|dislikes|hates)\b"
            r"|\b(?:please\s+)?(?:always|never)\s+"
            r"(?:use|call|address|write|respond|reply|format|include|omit|avoid)\b"
            r"|\b(?:call|address)\s+me\s+(?:as\s+)?\b",
            re.I,
        ),
        0.92,
    ),
    (
        "relationship",
        re.compile(
            r"\b(reports? to|manager|managed by|works? with|teammate|colleague|"
            r"partner|parent|sibling|spouse|friend|customer of|vendor for)\b",
            re.I,
        ),
        0.86,
    ),
    (
        "episode",
        re.compile(
            r"\b(yesterday|last (?:week|month|year)|earlier today|"
            r"on \d{4}-\d{2}-\d{2}|during the|when (?:I|we|they|he|she))\b",
            re.I,
        ),
        0.80,
    ),
)

_ENTITY = re.compile(
    r"\b(?:[A-Z][a-zA-Z0-9&'.-]+(?:\s+[A-Z][a-zA-Z0-9&'.-]+){0,3}|"
    r"[A-Z]{2,8}(?:[-/][A-Z0-9]{1,8})?)\b"
)
_TEMPORAL = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|today|yesterday|tomorrow|"
    r"last (?:week|month|year)|next (?:week|month|year)|"
    r"currently|formerly|previously|now)\b",
    re.I,
)
_ENTITY_STOP = frozenset({
    "A", "An", "And", "But", "For", "From", "I", "In", "It", "Next",
    "On", "The", "Then", "This", "Today", "Tomorrow", "We", "When", "Yesterday",
})


def _kind_from_metadata(metadata: dict[str, Any]) -> tuple[str | None, str, float]:
    requested = str(metadata.get("memory_type") or metadata.get("kind") or "").lower().strip()
    if requested in MEMORY_KINDS:
        return requested, "caller", 1.0
    if metadata.get("_derived") == "reflection" or metadata.get("reflection_proposal_id"):
        return "reflection", "reviewed-derivation", 1.0
    if metadata.get("outcome") is not None or metadata.get("reward") is not None:
        return "outcome", "structured-metadata", 0.98
    return None, "rules", 0.0


def classify_memory(content: str, metadata: dict[str, Any] | None = None) -> tuple[str, float, str]:
    """Return ``(kind, confidence, method)`` for a retained item."""
    metadata = dict(metadata or {})
    explicit, method, confidence = _kind_from_metadata(metadata)
    if explicit is not None:
        return explicit, confidence, method
    for kind, pattern, score in _PATTERNS:
        if pattern.search(content or ""):
            return kind, score, "rules"
    return "fact", 0.72, "rules"


def extract_entities(content: str, metadata: dict[str, Any] | None = None) -> list[str]:
    """Return a bounded, stable entity list from structured keys and text."""
    metadata = dict(metadata or {})
    values: list[str] = []
    for key in ("ticker", "entity", "entity_id", "subject", "company", "person", "tool"):
        value = metadata.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            values.append(str(value).strip())
    values.extend(match.group(0).strip() for match in _ENTITY.finditer(content or ""))

    seen: set[str] = set()
    entities: list[str] = []
    for value in values:
        if value in _ENTITY_STOP:
            continue
        normalized = " ".join(value.split())
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        entities.append(normalized)
        if len(entities) >= 12:
            break
    return entities


def compile_memory_metadata(
    content: str,
    metadata: dict[str, Any] | None,
    *,
    event_time: datetime,
    source: str | None,
) -> dict[str, Any]:
    """Return metadata enriched with a versioned memory-artifact projection."""
    compiled = dict(metadata or {})
    kind, confidence, method = classify_memory(content, compiled)
    temporal_hints = list(dict.fromkeys(
        match.group(0).lower() for match in _TEMPORAL.finditer(content or "")
    ))[:8]
    compiled[METADATA_KEY] = {
        "schema": SCHEMA_VERSION,
        "compiler": COMPILER_VERSION,
        "method": method,
        "kind": kind,
        "confidence": round(confidence, 4),
        "entities": extract_entities(content, compiled),
        "temporal_hints": temporal_hints,
        "source": {
            "content_sha256": hashlib.sha256((content or "").encode()).hexdigest(),
            "event_time": event_time.isoformat(),
            "source": source,
        },
    }
    return compiled


def compiled_kind(metadata: dict[str, Any] | None) -> str:
    """Read a compiled kind safely, falling back to ``fact``."""
    block = dict(metadata or {}).get(METADATA_KEY)
    if isinstance(block, dict) and block.get("kind") in MEMORY_KINDS:
        return str(block["kind"])
    return "fact"
