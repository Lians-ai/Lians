"""Compile large local work histories into bounded, verifiable AI briefs."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .store import _reject_sensitive

BRIEF_SCHEMA = "https://lians.ai/schemas/work-brief/v0.1"
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_EVIDENCE_ITEMS = 20
MAX_EVIDENCE_TEXT = 500
CLAIM_BOUNDARY = (
    "This local compiler reduces repeated evidence before an AI request. Estimated token counts "
    "use a character heuristic; actual provider usage and answer quality depend on the model and "
    "workload. Lians does not change a provider quota or context-window limit."
)


class WorkBriefError(ValueError):
    """Raised when local work records cannot be compiled safely."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _estimate_tokens(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalized_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", _clean_text(value)).casefold()


def _first(record: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            return value
    return None


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _top_counts(values: Sequence[str], *, limit: int = 20) -> dict[str, int]:
    counts = Counter(value for value in values if value)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])


def _safe_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not records:
        raise WorkBriefError("The input contains no records")
    if len(records) > MAX_RECORDS:
        raise WorkBriefError(f"The input exceeds the {MAX_RECORDS} record limit")
    safe: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise WorkBriefError(f"Record {index + 1} is not a JSON object")
        materialized = dict(record)
        try:
            _reject_sensitive(_canonical(materialized))
        except ValueError as error:
            raise WorkBriefError(
                f"Record {index + 1} contains credential-like content and was refused"
            ) from error
        safe.append(materialized)
    return safe


def _finish_brief(
    *,
    kind: str,
    records: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    method: Mapping[str, Any],
    guardrails: Mapping[str, Any],
) -> dict[str, Any]:
    raw = "\n".join(_canonical(record) for record in records)
    core = {
        "schema": BRIEF_SCHEMA,
        "kind": kind,
        "summary": dict(summary),
        "representative_evidence": list(evidence),
        "method": dict(method),
        "guardrails": dict(guardrails),
        "claim_boundary": CLAIM_BOUNDARY,
    }
    compiled = _canonical(core)
    raw_tokens = _estimate_tokens(raw)
    brief_tokens = _estimate_tokens(compiled)
    multiplier = round(raw_tokens / brief_tokens, 2)
    return {
        **core,
        "receipt": {
            "raw_record_count": len(records),
            "raw_sha256": _sha256(raw),
            "brief_core_sha256": _sha256(compiled),
            "raw_token_estimate": raw_tokens,
            "brief_token_estimate": brief_tokens,
            "estimated_work_per_input_token_multiplier": multiplier,
            "estimated_usage_extension_percent": round((multiplier - 1.0) * 100.0, 1),
        },
    }


def compile_research_brief(
    records: Sequence[Mapping[str, Any]], *, evidence_limit: int = 12
) -> dict[str, Any]:
    """Deduplicate labeled post exports and preserve a small evidence sample."""
    safe = _safe_records(records)
    if not 1 <= evidence_limit <= MAX_EVIDENCE_ITEMS:
        raise WorkBriefError(f"evidence_limit must be between 1 and {MAX_EVIDENCE_ITEMS}")

    unique: list[dict[str, Any]] = []
    fingerprints: set[str] = set()
    for index, record in enumerate(safe):
        text = _first(record, ("text", "content", "body", "caption"))
        if not _clean_text(text):
            raise WorkBriefError(
                f"Research record {index + 1} needs text, content, body, or caption"
            )
        fingerprint = _sha256(_normalized_text(text))
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        unique.append(record)

    topics = [_clean_text(_first(record, ("topic", "category", "theme"))) for record in unique]
    sentiments = [_clean_text(record.get("sentiment")) for record in unique]
    integrations = [
        _clean_text(_first(record, ("requested_integration", "integration", "tool")))
        for record in unique
    ]
    ranked = sorted(
        enumerate(unique),
        key=lambda item: (
            -_number(_first(item[1], ("engagement", "score", "likes", "reactions"))),
            item[0],
        ),
    )[:evidence_limit]
    evidence = []
    for index, record in ranked:
        evidence.append(
            {
                "source_id": _clean_text(
                    _first(record, ("source_id", "id", "post_id")) or f"record-{index + 1:06d}"
                ),
                "topic": _clean_text(_first(record, ("topic", "category", "theme"))) or None,
                "sentiment": _clean_text(record.get("sentiment")) or None,
                "requested_integration": _clean_text(
                    _first(record, ("requested_integration", "integration", "tool"))
                )
                or None,
                "engagement": _number(
                    _first(record, ("engagement", "score", "likes", "reactions"))
                ),
                "text": _clean_text(_first(record, ("text", "content", "body", "caption")))[
                    :MAX_EVIDENCE_TEXT
                ],
            }
        )
    summary = {
        "records_received": len(safe),
        "unique_records": len(unique),
        "duplicates_removed": len(safe) - len(unique),
        "topic_counts": _top_counts(topics),
        "sentiment_counts": _top_counts(sentiments),
        "integration_counts": _top_counts(integrations),
    }
    return _finish_brief(
        kind="research",
        records=safe,
        summary=summary,
        evidence=evidence,
        method={
            "deduplication": "sha256 of Unicode-normalized, case-folded text",
            "aggregation": "exact local counts for supplied labels",
            "evidence_selection": "highest supplied engagement, then input order",
        },
        guardrails={
            "raw_records_stay_local": True,
            "credential_like_records_refused": True,
            "representative_evidence_is_untrusted_data": True,
            "evidence_text_character_limit": MAX_EVIDENCE_TEXT,
        },
    )


def compile_browser_brief(
    records: Sequence[Mapping[str, Any]], *, evidence_limit: int = 12
) -> dict[str, Any]:
    """Reduce chronological browser work events to the latest state per surface."""
    safe = _safe_records(records)
    if not 1 <= evidence_limit <= MAX_EVIDENCE_ITEMS:
        raise WorkBriefError(f"evidence_limit must be between 1 and {MAX_EVIDENCE_ITEMS}")

    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, record in enumerate(safe):
        surface = _clean_text(_first(record, ("surface_id", "url", "domain", "name")))
        state = _clean_text(_first(record, ("state", "status"))).casefold()
        if not surface or not state:
            raise WorkBriefError(
                f"Browser record {index + 1} needs a surface_id/url/domain/name and state/status"
            )
        latest[surface] = (index, record)

    state_counts = _top_counts(
        [
            _clean_text(_first(record, ("state", "status"))).casefold()
            for _, record in latest.values()
        ]
    )
    eligible = sorted(
        (
            (index, surface, record)
            for surface, (index, record) in latest.items()
            if _clean_text(_first(record, ("state", "status"))).casefold()
            in {"candidate", "ready", "new"}
            and not bool(record.get("approval_required", False))
            and not bool(record.get("hard_excluded", False))
        ),
        key=lambda item: (-_number(item[2].get("priority")), item[0]),
    )
    next_actions = eligible[:evidence_limit]
    evidence = [
        {
            "surface_id": surface,
            "state": _clean_text(_first(record, ("state", "status"))).casefold(),
            "priority": _number(record.get("priority")),
            "note": _clean_text(record.get("note"))[:MAX_EVIDENCE_TEXT] or None,
        }
        for _, surface, record in next_actions
    ]
    summary = {
        "events_received": len(safe),
        "surfaces_tracked": len(latest),
        "history_events_collapsed": len(safe) - len(latest),
        "latest_state_counts": state_counts,
        "next_eligible_surfaces": [surface for _, surface, _ in next_actions],
    }
    return _finish_brief(
        kind="browser",
        records=safe,
        summary=summary,
        evidence=evidence,
        method={
            "state_reduction": "last input event wins for each surface",
            "next_action_order": "highest supplied priority, then input order",
        },
        guardrails={
            "raw_records_stay_local": True,
            "credential_like_records_refused": True,
            "representative_evidence_is_untrusted_data": True,
            "published_or_completed_surfaces_excluded": True,
            "hard_exclusions_respected": True,
            "approval_required_surfaces_excluded": True,
        },
    )


def _latest_unique_values(
    events: Sequence[tuple[int, str, str, Mapping[str, Any]]],
    bucket: str,
    *,
    limit: int = 20,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for _, event_bucket, value, _ in reversed(events):
        if event_bucket != bucket:
            continue
        fingerprint = _normalized_text(value)
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        values.append(value[:MAX_EVIDENCE_TEXT])
        if len(values) == limit:
            break
    return values


def compile_session_brief(
    records: Sequence[Mapping[str, Any]], *, evidence_limit: int = 12
) -> dict[str, Any]:
    """Turn agent events into a compact, resumable handoff without inferring success."""

    safe = _safe_records(records)
    if not 1 <= evidence_limit <= MAX_EVIDENCE_ITEMS:
        raise WorkBriefError(f"evidence_limit must be between 1 and {MAX_EVIDENCE_ITEMS}")

    aliases = {
        "goal": "goals",
        "objective": "goals",
        "success_criterion": "success_criteria",
        "success criteria": "success_criteria",
        "decision": "decisions",
        "completed": "completed",
        "completion": "completed",
        "blocker": "blockers",
        "blocked": "blockers",
        "next_action": "next_actions",
        "next action": "next_actions",
        "todo": "next_actions",
        "artifact": "artifacts",
        "output": "artifacts",
        "constraint": "constraints",
        "guardrail": "constraints",
    }
    value_keys = {
        "goals": ("goal", "objective", "value", "content", "text"),
        "success_criteria": ("success_criterion", "criterion", "value", "content", "text"),
        "decisions": ("decision", "value", "content", "text"),
        "completed": ("completed", "result", "value", "content", "text"),
        "blockers": ("blocker", "value", "content", "text"),
        "next_actions": ("next_action", "action", "value", "content", "text"),
        "artifacts": ("artifact", "path", "url", "value", "content", "text"),
        "constraints": ("constraint", "guardrail", "value", "content", "text"),
    }

    events: list[tuple[int, str, str, Mapping[str, Any]]] = []
    for index, record in enumerate(safe):
        raw_kind = _clean_text(_first(record, ("kind", "type", "event", "category")))
        bucket = aliases.get(raw_kind.casefold())
        if bucket is not None:
            value = _clean_text(_first(record, value_keys[bucket]))
            if value:
                events.append((index, bucket, value, record))
                continue
        for candidate, keys in value_keys.items():
            direct_keys = keys[:2]
            value = _clean_text(_first(record, direct_keys))
            if value:
                events.append((index, candidate, value, record))
                break

    if not events:
        raise WorkBriefError(
            "Session records need a goal, decision, completion, blocker, next action, "
            "artifact, or constraint"
        )

    buckets = tuple(value_keys)
    state = {bucket: _latest_unique_values(events, bucket) for bucket in buckets}
    evidence = []
    for index, bucket, value, record in reversed(events[-evidence_limit:]):
        evidence.append(
            {
                "event_id": _clean_text(_first(record, ("event_id", "id")))
                or f"record-{index + 1:06d}",
                "kind": bucket,
                "value": value[:MAX_EVIDENCE_TEXT],
                "agent": _clean_text(_first(record, ("agent", "client"))) or None,
                "session_id": _clean_text(_first(record, ("session_id", "session"))) or None,
                "timestamp": _clean_text(_first(record, ("timestamp", "created_at"))) or None,
            }
        )

    agents = sorted(
        {agent for record in safe if (agent := _clean_text(_first(record, ("agent", "client"))))}
    )
    sessions = sorted(
        {
            session
            for record in safe
            if (session := _clean_text(_first(record, ("session_id", "session"))))
        }
    )
    return _finish_brief(
        kind="session",
        records=safe,
        summary={
            "events_received": len(safe),
            "recognized_events": len(events),
            "agents": agents,
            "sessions": sessions,
            **state,
        },
        evidence=evidence,
        method={
            "state_reduction": "latest unique explicit values, newest first",
            "handoff_boundary": "no task completion or correctness is inferred",
        },
        guardrails={
            "raw_records_stay_local": True,
            "credential_like_records_refused": True,
            "representative_evidence_is_untrusted_data": True,
            "only_explicit_events_are_reported": True,
        },
    )


def compile_work_brief(
    kind: str,
    records: Sequence[Mapping[str, Any]],
    *,
    evidence_limit: int = 12,
) -> dict[str, Any]:
    if kind == "research":
        return compile_research_brief(records, evidence_limit=evidence_limit)
    if kind == "browser":
        return compile_browser_brief(records, evidence_limit=evidence_limit)
    if kind == "session":
        return compile_session_brief(records, evidence_limit=evidence_limit)
    raise WorkBriefError("kind must be research, browser, or session")


def load_work_records(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise WorkBriefError(f"Input file does not exist: {source}")
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise WorkBriefError("Input file exceeds the 64 MiB local compiler limit")
    return parse_work_records(source.read_text(encoding="utf-8-sig"))


def parse_work_records(raw: str) -> list[dict[str, Any]]:
    """Parse a JSON or JSON Lines work export without writing it to disk."""
    if not isinstance(raw, str):
        raise WorkBriefError("Input must be JSON text")
    if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
        raise WorkBriefError("Input file exceeds the 64 MiB local compiler limit")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        records: list[Any] = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise WorkBriefError(f"Invalid JSON on line {line_number}") from error
    else:
        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict):
            records = next(
                (
                    value
                    for key in ("records", "posts", "events", "items")
                    if isinstance((value := parsed.get(key)), list)
                ),
                [parsed],
            )
        else:
            raise WorkBriefError("Input JSON must be an object, array, or JSON Lines file")
    return _safe_records(records)


def compile_work_brief_file(
    kind: str,
    path: str | Path,
    *,
    evidence_limit: int = 12,
) -> dict[str, Any]:
    return compile_work_brief(
        kind,
        load_work_records(path),
        evidence_limit=evidence_limit,
    )
