"""Shared, dependency-free helpers for native Recorder adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any, Callable, Literal
from uuid import UUID

from .platform_types import RecorderEnvelope
from .recorder import lians_event
from .recorder_sink import RecorderAttribution, recorder_content_hash


def adapter_event(
    *,
    framework: str,
    kind: str,
    phase: str,
    source_identity: Sequence[Any],
    run_id: str,
    attribution: RecorderAttribution,
    occurred_at: datetime | str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    task_id: str | None = None,
    tool_call_id: str | None = None,
    name: str | None = None,
    model_id: str | None = None,
    status: str | None = None,
    observed_input: Any = None,
    observed_output: Any = None,
    metadata: Mapping[str, Any] | None = None,
    content_hasher: Callable[[Any], str] = recorder_content_hash,
    commitment_scheme: Literal["sha256", "hmac-sha256"] = "sha256",
) -> RecorderEnvelope:
    """Build a locally minimized native event for a framework callback.

    Native adapters intentionally support ``hash_only`` and ``metadata_only``.
    They never transport raw callback content, even when the framework exposes
    it.  This also means they cannot provide full-content capture.
    """

    if attribution.capture_mode == "full":
        raise ValueError(
            "native Recorder adapters do not transmit raw callback content; "
            "use hash_only or metadata_only, or submit an explicit full envelope"
        )
    # Hash a canonical sequence instead of delimiter-joining strings: caller
    # identifiers may themselves contain any delimiter, and two distinct source
    # tuples must not accidentally collapse to the same retry identity.
    digest = recorder_content_hash(list(source_identity))
    stable = f"lians:{framework}:v1:{digest}"
    supplemental = {
        key: value
        for key, value in (metadata or {}).items()
        if isinstance(key, str)
        and key
        not in {
            "framework",
            "name",
            "phase",
            "status",
            "model_id",
            "input_hash",
            "output_hash",
        }
        and value is not None
    }
    payload: dict[str, Any] = {
        **supplemental,
        "framework": framework,
        "name": _bounded_text(name, kind),
        "phase": phase,
        "status": status,
        "model_id": _bounded_text(model_id, None),
        "input_hash": (
            content_hasher(observed_input)
            if attribution.capture_mode == "hash_only" and observed_input is not None
            else None
        ),
        "output_hash": (
            content_hasher(observed_output)
            if attribution.capture_mode == "hash_only" and observed_output is not None
            else None
        ),
    }
    return lians_event(
        f"{framework}.{kind}.{phase}",
        payload,
        event_id=stable,
        idempotency_key=stable,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        subject_id=attribution.subject_id,
        agent_id=attribution.claimed_agent_id,
        principal_id=attribution.claimed_principal_id,
        roles=attribution.claimed_roles,
        run_id=run_id,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        session_id=attribution.session_id,
        task_id=task_id,
        tool_call_id=tool_call_id,
        decision_id=attribution.decision_id,
        capture_mode=attribution.capture_mode,
        extensions={
            "lians.adapter.framework": framework,
            "lians.adapter.capture_boundary": "sdk_local_hash"
            if attribution.capture_mode == "hash_only"
            else "metadata_only",
            "lians.adapter.actor_attribution": "claimed_unverified",
            "lians.adapter.content_commitment": (
                commitment_scheme
                if attribution.capture_mode == "hash_only"
                else "not_applicable"
            ),
            "lians.adapter.identity_commitment": "sha256",
        },
    )


def _bounded_text(value: str | None, fallback: str | None) -> str | None:
    if value is None:
        return fallback
    text = value.strip()
    return text[:512] if text else fallback


def public_mapping(value: Any) -> Mapping[str, Any]:
    """Obtain a documented public export when available, without raw logging."""

    if isinstance(value, Mapping):
        return _checked_public_mapping(value)
    export = getattr(value, "export", None)
    if callable(export):
        exported = export()
        if isinstance(exported, Mapping):
            return _checked_public_mapping(exported)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="python")
        except TypeError:
            dumped = model_dump()
        if isinstance(dumped, Mapping):
            return _checked_public_mapping(dumped)
    return {}


def public_name(serialized: Any, fallback: str) -> str:
    """Extract only a public component name from a framework descriptor."""

    mapping = public_mapping(serialized)
    raw = mapping.get("name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:512]
    identifiers = mapping.get("id")
    if isinstance(identifiers, (list, tuple)) and identifiers:
        candidate = identifiers[-1]
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:512]
        if isinstance(candidate, UUID):
            return str(candidate)
    return fallback


def text_attr(value: Any, *names: str) -> str | None:
    for name in names:
        item = getattr(value, name, None)
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, UUID):
            text = str(item)
        elif isinstance(item, datetime):
            text = item.isoformat()
        elif isinstance(item, date):
            text = item.isoformat()
        elif isinstance(item, int) and not isinstance(item, bool):
            text = str(item)
        else:
            continue
        if text:
            return text[:512]
    return None


def _checked_public_mapping(value: Mapping[Any, Any]) -> Mapping[str, Any]:
    if len(value) > 1_000:
        raise ValueError("framework public export exceeds 1,000 top-level fields")
    if any(not isinstance(key, str) for key in value):
        raise TypeError("framework public exports must use string keys")
    return value
