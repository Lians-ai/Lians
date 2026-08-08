"""Ergonomic, privacy-safe builders for Universal Recorder envelopes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .platform_types import CaptureMode, RecorderEnvelope, RecorderOperational


def _time(value: datetime | str | None) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


def _clean(value: Any) -> Any:
    """Drop ``None`` recursively without inspecting or logging values."""
    if isinstance(value, Mapping):
        return {str(key): _clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _envelope(
    protocol: str,
    payload: Mapping[str, Any],
    *,
    event_type: str | None = None,
    event_id: str | None = None,
    idempotency_key: str | None = None,
    occurred_at: datetime | str | None = None,
    subject_id: str | None = None,
    agent_id: str | None = None,
    principal_id: str | None = None,
    roles: Sequence[str] = (),
    run_id: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    context_id: str | None = None,
    message_id: str | None = None,
    tool_call_id: str | None = None,
    decision_id: str | None = None,
    capture_mode: CaptureMode = "hash_only",
    sensitive_fields: Sequence[str] = (),
    operational: RecorderOperational | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> RecorderEnvelope:
    stable_event_id = event_id or str(uuid4())
    return _clean(
        {
            "schema_version": "0.2",
            "protocol": protocol,
            "event_type": event_type,
            "event_id": stable_event_id,
            # A built envelope can be retried safely. Callers may supply a stable
            # business key when they need deduplication across process restarts.
            "idempotency_key": idempotency_key or stable_event_id,
            "occurred_at": _time(occurred_at),
            "subject_id": subject_id,
            "actor": {
                "agent_id": agent_id,
                "principal_id": principal_id,
                "roles": list(roles),
            },
            "correlation": {
                "run_id": run_id,
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "session_id": session_id,
                "task_id": task_id,
                "context_id": context_id,
                "message_id": message_id,
                "tool_call_id": tool_call_id,
                "decision_id": decision_id,
            },
            "capture": {
                "mode": capture_mode,
                "sensitive_fields": list(sensitive_fields),
            },
            "operational": operational,
            "payload": dict(payload),
            "extensions": dict(extensions or {}),
        }
    )  # type: ignore[return-value]


def lians_event(
    event_type: str,
    payload: Mapping[str, Any],
    **options: Any,
) -> RecorderEnvelope:
    """Build a native Lians event; persisted content is hash-only by default."""
    return _envelope("lians", payload, event_type=event_type, **options)


def otlp_genai_span(
    *,
    trace_id: str,
    span_id: str,
    operation: str,
    model: str,
    input: Any = None,
    output: Any = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    parent_span_id: str | None = None,
    occurred_at: datetime | str | None = None,
    ended_at: datetime | str | None = None,
    attributes: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
    **options: Any,
) -> RecorderEnvelope:
    """Build an OTLP GenAI semantic-convention span in Recorder JSON form."""
    attrs = {
        "gen_ai.operation.name": operation,
        "gen_ai.request.model": model,
        "gen_ai.agent.id": agent_id,
        "gen_ai.input.messages": input,
        "gen_ai.output.messages": output,
        **dict(attributes or {}),
    }
    payload: dict[str, Any] = {
        "name": operation,
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_span_id,
        "attributes": _clean(attrs),
    }
    if ended_at is not None:
        end = ended_at
        if isinstance(end, str):
            end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        payload["endTimeUnixNano"] = str(int(end.timestamp() * 1_000_000_000))
    return _envelope(
        "otlp.genai",
        payload,
        event_type=f"genai.{operation}",
        event_id=f"{trace_id}:{span_id}",
        idempotency_key=idempotency_key or f"otlp:{trace_id}:{span_id}",
        occurred_at=occurred_at,
        agent_id=agent_id,
        run_id=run_id,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        **options,
    )


def mcp_jsonrpc_event(
    message: Mapping[str, Any],
    *,
    run_id: str,
    tool_name: str | None = None,
    agent_id: str | None = None,
    occurred_at: datetime | str | None = None,
    idempotency_key: str | None = None,
    **options: Any,
) -> RecorderEnvelope:
    """Build an MCP JSON-RPC request or response with call/run correlation."""
    rpc_id = message.get("id")
    phase = "response" if "result" in message or "error" in message else "request"
    stable = idempotency_key or f"mcp:{run_id}:{rpc_id}:{phase}"
    extensions = dict(options.pop("extensions", {}) or {})
    if tool_name:
        extensions["mcp.tool.name"] = tool_name
    return _envelope(
        "mcp",
        message,
        event_id=stable,
        idempotency_key=stable,
        occurred_at=occurred_at,
        agent_id=agent_id,
        run_id=run_id,
        tool_call_id=str(rpc_id) if rpc_id is not None else None,
        extensions=extensions,
        **options,
    )


def a2a_event(
    event: Mapping[str, Any],
    *,
    task_id: str | None = None,
    context_id: str | None = None,
    message_id: str | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    occurred_at: datetime | str | None = None,
    idempotency_key: str | None = None,
    **options: Any,
) -> RecorderEnvelope:
    """Build an A2A task/message/artifact event using its native JSON shape."""
    resolved_task = task_id or _string(event.get("taskId")) or _string(event.get("id"))
    resolved_context = context_id or _string(event.get("contextId"))
    resolved_message = message_id or _string(event.get("messageId"))
    kind = _string(event.get("kind")) or "event"
    status = event.get("status")
    state = _string(status.get("state")) if isinstance(status, Mapping) else _string(status)
    timestamp = _string(status.get("timestamp")) if isinstance(status, Mapping) else None
    artifact = event.get("artifact")
    if not isinstance(artifact, Mapping):
        artifacts = event.get("artifacts")
        artifact = artifacts[0] if isinstance(artifacts, list) and artifacts else None
    artifact_id = (
        _string(artifact.get("artifactId") or artifact.get("artifact_id"))
        if isinstance(artifact, Mapping)
        else None
    )
    identity = (
        f"message:{resolved_message}"
        if resolved_message
        else f"artifact:{resolved_task}:{artifact_id}"
        if artifact_id
        else f"status:{resolved_task}:{kind}:{state}:{timestamp}"
        if state or timestamp
        else f"event:{resolved_task or uuid4()}:{kind}"
    )
    stable = idempotency_key or f"a2a:{identity}"
    return _envelope(
        "a2a",
        event,
        event_type=kind,
        event_id=stable,
        idempotency_key=stable,
        occurred_at=occurred_at,
        agent_id=agent_id,
        run_id=run_id,
        task_id=resolved_task,
        context_id=resolved_context,
        message_id=resolved_message,
        **options,
    )


def _string(value: Any) -> str | None:
    return str(value) if value is not None else None
