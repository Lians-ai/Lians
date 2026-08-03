"""Privacy-safe builders for Lians Universal Recorder envelopes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .types import (
    CaptureMode,
    RecorderActor,
    RecorderCapturePolicy,
    RecorderCorrelation,
    RecorderEnvelope,
)


def _dt(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _base(
    protocol: str,
    payload: Mapping[str, Any],
    *,
    event_type: str | None = None,
    event_id: str | None = None,
    idempotency_key: str | None = None,
    occurred_at: datetime | str | None = None,
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
    subject_id: str | None = None,
    capture_mode: CaptureMode = "hash_only",
    sensitive_fields: Sequence[str] = (),
    extensions: Mapping[str, Any] | None = None,
) -> RecorderEnvelope:
    stable_event = event_id or str(uuid4())
    return RecorderEnvelope(
        protocol=protocol,  # type: ignore[arg-type]
        event_type=event_type,
        event_id=stable_event,
        idempotency_key=idempotency_key or stable_event,
        occurred_at=_dt(occurred_at),
        subject_id=subject_id,
        actor=RecorderActor(
            agent_id=agent_id,
            principal_id=principal_id,
            roles=list(roles),
        ),
        correlation=RecorderCorrelation(
            run_id=run_id,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            session_id=session_id,
            task_id=task_id,
            context_id=context_id,
            message_id=message_id,
            tool_call_id=tool_call_id,
            decision_id=decision_id,  # Pydantic accepts UUID strings.
        ),
        capture=RecorderCapturePolicy(
            mode=capture_mode,
            sensitive_fields=list(sensitive_fields),
        ),
        payload=dict(payload),
        extensions=dict(extensions or {}),
    )


def lians_event(event_type: str, payload: Mapping[str, Any], **options: Any) -> RecorderEnvelope:
    return _base("lians", payload, event_type=event_type, **options)


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
    attrs = {
        key: value
        for key, value in {
            "gen_ai.operation.name": operation,
            "gen_ai.request.model": model,
            "gen_ai.agent.id": agent_id,
            "gen_ai.input.messages": input,
            "gen_ai.output.messages": output,
            **dict(attributes or {}),
        }.items()
        if value is not None
    }
    payload: dict[str, Any] = {
        "name": operation,
        "traceId": trace_id,
        "spanId": span_id,
        "attributes": attrs,
    }
    if parent_span_id:
        payload["parentSpanId"] = parent_span_id
    if ended_at is not None:
        end = _dt(ended_at)
        assert end is not None
        payload["endTimeUnixNano"] = str(int(end.timestamp() * 1_000_000_000))
    return _base(
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
    rpc_id = message.get("id")
    phase = "response" if "result" in message or "error" in message else "request"
    stable = idempotency_key or f"mcp:{run_id}:{rpc_id}:{phase}"
    extensions = dict(options.pop("extensions", {}) or {})
    if tool_name:
        extensions["mcp.tool.name"] = tool_name
    return _base(
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
    task = task_id or _string(event.get("taskId")) or _string(event.get("id"))
    context = context_id or _string(event.get("contextId"))
    message = message_id or _string(event.get("messageId"))
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
        f"message:{message}"
        if message
        else f"artifact:{task}:{artifact_id}"
        if artifact_id
        else f"status:{task}:{kind}:{state}:{timestamp}"
        if state or timestamp
        else f"event:{task or uuid4()}:{kind}"
    )
    stable = idempotency_key or f"a2a:{identity}"
    return _base(
        "a2a",
        event,
        event_type=kind,
        event_id=stable,
        idempotency_key=stable,
        occurred_at=occurred_at,
        agent_id=agent_id,
        run_id=run_id,
        task_id=task,
        context_id=context,
        message_id=message,
        **options,
    )


def _string(value: Any) -> str | None:
    return str(value) if value is not None else None
