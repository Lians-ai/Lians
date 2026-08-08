"""Normalization, deduplication, correlation, and readiness for the Recorder."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Text, and_, cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import chain_log
from .capture_privacy import capture_sha256 as _hash
from .capture_privacy import sanitize_capture as _sanitize
from .config import get_settings
from .evidence_models import DecisionEvidenceKindCoverage, EvidenceArtifact
from .evidence_service import (
    ArtifactSpec,
    artifact_identity_hash,
    ensure_artifacts_bulk,
    ensure_links_bulk,
)
from .governance_service import estimate_ingest_bytes, reserve_namespace_usage
from .models import DecisionRecord, EventLog
from .pii import assert_subject_not_erased
from .recorder_models import RecorderEvent, RecorderEvidenceIndexJob, RecorderRun
from .recorder_schemas import (
    FirstReceiptReadinessSummary,
    RecorderEnvelope,
    RecorderEventOut,
    RecorderIngestResult,
    RecorderOperational,
    RecorderRunReadiness,
)
from .subject_privacy import replace_subject_identifier

_READINESS_WEIGHTS = {
    "agent_identity": 10,
    "temporal_boundary": 10,
    "correlation_id": 10,
    "model_identity": 10,
    "input_capture": 10,
    "output_capture": 10,
    "outcome_status": 10,
    "policy_context": 10,
    "principal_context": 10,
    "evidence_trace": 10,
}
_READY_REQUIRED = {
    "agent_identity",
    "temporal_boundary",
    "correlation_id",
    "output_capture",
    "outcome_status",
}
_LEGACY_RECORDER_PRINCIPAL_REF = "lians:principal:v1:legacy-unverified"
_LEGACY_RECORDER_AUTH_METHOD = "legacy_unverified"
_DECISION_RECORDER_INDEX_LIMIT = 500
_RECORDER_EVENT_PAGE_BIND_BATCH = 400
_RECORDER_EVENT_JSON_MATERIALIZATION_MULTIPLIER = 8
_RECORDER_EVENT_ROW_OVERHEAD_BYTES = 16 * 1024
_RECORDER_EVIDENCE_BULK_PAGE_SIZE = 500
_RECORDER_COVERAGE_KINDS = ("model", "policy", "tool", "input", "output")
_RECORDER_COVERAGE_INDEXER_VERSION = "decision-recorder-normalizer:v1"
_RECORDER_COVERAGE_SCOPE = "decision_record_and_recorder_events"
_DECISION_RECORDER_FENCE_HASH_SEED = 1_106_713_909
_EVIDENCE_REGISTRATION_FENCE_HASH_SEED = 1_279_873_363


class RecorderNormalizationError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class RecorderIntegrityError(RuntimeError):
    """Stored Recorder evidence no longer matches its immutable commitments."""


class RecorderEventPageCapacityExceeded(RuntimeError):
    """A complete requested Recorder page cannot fit its materialization budget."""

    def __init__(self, *, estimated_bytes: int, byte_limit: int) -> None:
        super().__init__("Recorder event page exceeds its materialization byte budget")
        self.estimated_bytes = estimated_bytes
        self.byte_limit = byte_limit


@dataclass(slots=True)
class RecorderEventPage:
    events: list[RecorderEventOut]
    total: int
    has_more: bool


@dataclass
class NormalizedRecorderEvent:
    schema_version: str
    protocol: str
    event_kind: str
    event_name: str | None
    phase: str
    status: str | None
    source_event_id: str | None
    occurred_at: datetime
    occurred_at_supplied: bool
    agent_id: str | None
    principal_id: str | None
    subject_id: str | None
    session_id: str | None
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    task_id: str | None
    context_id: str | None
    message_id: str | None
    tool_call_id: str | None
    decision_id: UUID | None
    model_id: str | None
    model_version: str | None
    policy_version: str | None
    input_hash: str | None
    output_hash: str | None
    has_evidence: bool
    correlation_type: str
    correlation_value: str
    correlation_hash: str
    boundary_kind: str
    dedup_key: str
    idempotency_key_hash: str | None
    source_payload_hash: str
    capture_mode: str
    normalized_payload: dict[str, Any]
    extensions: dict[str, Any]
    capture_gaps: list[str] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _datetime_from_nanos(value: Any) -> datetime | None:
    try:
        nanos = int(str(value))
    except (TypeError, ValueError):
        return None
    if nanos <= 0:
        return None
    return datetime.fromtimestamp(nanos / 1_000_000_000, tz=timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _first(*values: Any) -> Any:
    return next((value for value in values if value is not None and value != ""), None)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _uuid_or_none(value: Any) -> UUID | None:
    if value is None or value == "":
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _any_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in (
        "stringValue",
        "boolValue",
        "intValue",
        "doubleValue",
        "bytesValue",
        "arrayValue",
        "kvlistValue",
    ):
        if key not in value:
            continue
        item = value[key]
        if key == "arrayValue":
            return [_any_value(entry) for entry in _dict(item).get("values", [])]
        if key == "kvlistValue":
            return _attributes(_dict(item).get("values", []))
        return item
    return value


def _attributes(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, list):
        return {}
    return {
        str(item["key"]): _any_value(item.get("value"))
        for item in value
        if isinstance(item, dict) and item.get("key") is not None
    }


def _semantic_base(envelope: RecorderEnvelope) -> dict[str, Any]:
    payload = envelope.payload
    correlation = envelope.correlation
    actor = envelope.actor
    return {
        "event_kind": envelope.event_type,
        "event_name": _first(payload.get("name"), payload.get("event_name")),
        "phase": payload.get("phase"),
        "status": payload.get("status") if isinstance(payload.get("status"), str) else None,
        "agent_id": _first(actor.agent_id, payload.get("agent_id"), payload.get("agentId")),
        "principal_id": _first(actor.principal_id, payload.get("principal_id")),
        "session_id": _first(correlation.session_id, payload.get("session_id")),
        "trace_id": _first(correlation.trace_id, payload.get("trace_id")),
        "span_id": _first(correlation.span_id, payload.get("span_id")),
        "parent_span_id": _first(correlation.parent_span_id, payload.get("parent_span_id")),
        "task_id": _first(correlation.task_id, payload.get("task_id")),
        "context_id": _first(correlation.context_id, payload.get("context_id")),
        "message_id": _first(correlation.message_id, payload.get("message_id")),
        "tool_call_id": _first(correlation.tool_call_id, payload.get("tool_call_id")),
        "decision_id": _first(correlation.decision_id, _uuid_or_none(payload.get("decision_id"))),
        "model_id": _first(
            payload.get("model_id"),
            payload.get("model"),
            envelope.extensions.get("gen_ai.model.id"),
        ),
        "model_version": payload.get("model_version"),
        "policy_version": _first(
            payload.get("policy_version"), envelope.extensions.get("lians.policy.version")
        ),
        "provider": _first(payload.get("provider"), envelope.extensions.get("gen_ai.system")),
        "runtime_framework": _first(
            payload.get("runtime_framework"), envelope.extensions.get("lians.runtime.framework")
        ),
        "operation": _first(payload.get("operation"), payload.get("operation_name")),
        "input": _first(payload.get("input"), payload.get("prompt"), payload.get("arguments")),
        "output": _first(payload.get("output"), payload.get("result"), payload.get("completion")),
        "input_hash": payload.get("input_hash"),
        "output_hash": payload.get("output_hash"),
        "has_evidence": bool(
            payload.get("evidence") or payload.get("evidence_memory_ids") or payload.get("sources")
        ),
        "occurred_at": envelope.occurred_at,
    }


def _normalize_lians(envelope: RecorderEnvelope) -> dict[str, Any]:
    data = _semantic_base(envelope)
    payload = envelope.payload
    data["event_kind"] = _first(
        envelope.event_type,
        payload.get("event_type"),
        payload.get("type"),
        "agent.event",
    )
    data["event_name"] = _first(data["event_name"], data["event_kind"])
    data["phase"] = _first(data["phase"], "completed" if data["output"] is not None else "event")
    if data["status"] is None and data["phase"] == "completed":
        data["status"] = "ok"
    return data


def _normalize_otlp(envelope: RecorderEnvelope) -> dict[str, Any]:
    data = _semantic_base(envelope)
    payload = envelope.payload
    attrs = _attributes(payload.get("attributes"))
    resource = _attributes(
        _first(payload.get("resource_attributes"), payload.get("resourceAttributes"))
    )
    operation = _first(
        attrs.get("gen_ai.operation.name"),
        attrs.get("gen_ai.operation_name"),
        payload.get("operation_name"),
    )
    end_timestamp = _datetime_from_nanos(
        _first(payload.get("end_time_unix_nano"), payload.get("endTimeUnixNano"))
    )
    data.update(
        {
            "event_kind": _first(
                envelope.event_type,
                f"genai.{operation}" if operation else None,
                "genai.span",
            ),
            "event_name": _first(payload.get("name"), operation, "unnamed-genai-span"),
            "provider": _first(
                attrs.get("gen_ai.provider.name"),
                attrs.get("gen_ai.system"),
                data.get("provider"),
            ),
            "runtime_framework": _first(
                attrs.get("lians.runtime.framework"),
                resource.get("telemetry.sdk.name"),
                data.get("runtime_framework"),
            ),
            "operation": _first(operation, data.get("operation")),
            "phase": "completed" if end_timestamp is not None else "started",
            "agent_id": _first(
                data["agent_id"],
                attrs.get("gen_ai.agent.name"),
                attrs.get("gen_ai.agent.id"),
                resource.get("service.name"),
            ),
            "principal_id": _first(
                data["principal_id"], attrs.get("enduser.id"), attrs.get("user.id")
            ),
            "session_id": _first(
                data["session_id"],
                attrs.get("gen_ai.conversation.id"),
                attrs.get("session.id"),
            ),
            "trace_id": _first(data["trace_id"], payload.get("trace_id"), payload.get("traceId")),
            "span_id": _first(data["span_id"], payload.get("span_id"), payload.get("spanId")),
            "parent_span_id": _first(
                data["parent_span_id"], payload.get("parent_span_id"), payload.get("parentSpanId")
            ),
            "model_id": _first(
                attrs.get("gen_ai.response.model"),
                attrs.get("gen_ai.request.model"),
                attrs.get("gen_ai.system"),
                data["model_id"],
            ),
            "model_version": _first(
                attrs.get("gen_ai.response.model_version"), data["model_version"]
            ),
            "policy_version": _first(attrs.get("lians.policy.version"), data["policy_version"]),
            "decision_id": _first(
                data["decision_id"], _uuid_or_none(attrs.get("lians.decision.id"))
            ),
            "input": _first(
                attrs.get("gen_ai.input.messages"),
                attrs.get("gen_ai.prompt"),
                attrs.get("gen_ai.request.input"),
                data["input"],
            ),
            "output": _first(
                attrs.get("gen_ai.output.messages"),
                attrs.get("gen_ai.completion"),
                attrs.get("gen_ai.response.output"),
                data["output"],
            ),
            "has_evidence": bool(
                data["has_evidence"]
                or attrs.get("lians.evidence.ids")
                or attrs.get("gen_ai.tool.call.id")
            ),
            "occurred_at": _first(
                envelope.occurred_at,
                _datetime_from_nanos(
                    _first(payload.get("start_time_unix_nano"), payload.get("startTimeUnixNano"))
                ),
            ),
        }
    )
    raw_status = payload.get("status")
    status_code = _first(
        _dict(raw_status).get("code"), payload.get("status_code"), payload.get("statusCode")
    )
    data["status"] = "error" if str(status_code) in {"2", "ERROR", "error"} else "ok"
    data["attributes"] = attrs
    data["resource_attributes"] = resource
    data["start_timestamp"] = _datetime_from_nanos(
        _first(payload.get("start_time_unix_nano"), payload.get("startTimeUnixNano"))
    )
    data["end_timestamp"] = end_timestamp
    return data


def _nonnegative_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0 or parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _observed_measurement(value: Any, provenance: str) -> dict[str, Any] | None:
    parsed = _nonnegative_number(value)
    if parsed is None:
        return None
    return {"value": parsed, "provenance": provenance}


def _finish_reason(value: Any) -> str | None:
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, str) and item), None)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:128] if value else None


def _operational_fields(
    envelope: RecorderEnvelope,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Merge explicit v0.2 values with conservative protocol observations."""

    explicit = envelope.operational.model_dump(mode="json", exclude_none=True)
    attrs = _dict(data.get("attributes"))
    tokens = dict(explicit.get("tokens") or {})
    tokens.setdefault(
        "input",
        _observed_measurement(
            _first(
                attrs.get("gen_ai.usage.input_tokens"),
                attrs.get("gen_ai.usage.prompt_tokens"),
            ),
            "provider-reported",
        ),
    )
    tokens.setdefault(
        "output",
        _observed_measurement(
            _first(
                attrs.get("gen_ai.usage.output_tokens"),
                attrs.get("gen_ai.usage.completion_tokens"),
            ),
            "provider-reported",
        ),
    )
    tokens.setdefault(
        "cached",
        _observed_measurement(
            _first(
                attrs.get("gen_ai.usage.input_tokens.cached"),
                attrs.get("gen_ai.usage.cached_tokens"),
            ),
            "provider-reported",
        ),
    )
    explicit["tokens"] = {key: value for key, value in tokens.items() if value is not None}

    explicit.setdefault("provider", data.get("provider"))
    explicit.setdefault("runtime_framework", data.get("runtime_framework"))
    explicit.setdefault("operation", data.get("operation"))
    for field_name, extension_name in (
        ("prompt_hash", "lians.prompt.hash"),
        ("toolset_hash", "lians.toolset.hash"),
        ("request_configuration_hash", "lians.request.configuration.hash"),
        ("agent_version_id", "lians.agent.version.id"),
        ("release_reference", "lians.release.ref"),
        ("outcome_correlation", "lians.outcome.correlation"),
    ):
        explicit.setdefault(field_name, envelope.extensions.get(extension_name))

    start = data.get("start_timestamp")
    end = data.get("end_timestamp")
    if "latency_ms" not in explicit and start is not None and end is not None and end >= start:
        explicit["latency_ms"] = {
            "value": (end - start).total_seconds() * 1000,
            "provenance": "client-measured",
        }
    explicit.setdefault(
        "finish_reason",
        _finish_reason(
            _first(
                attrs.get("gen_ai.response.finish_reasons"),
                attrs.get("gen_ai.response.finish_reason"),
            )
        ),
    )
    explicit.setdefault(
        "error_code",
        _first(attrs.get("error.type"), attrs.get("error.code")),
    )
    if "cost" not in explicit:
        amount = _observed_measurement(
            _first(attrs.get("gen_ai.usage.cost"), attrs.get("lians.cost.amount")),
            "provider-reported",
        )
        currency = _first(attrs.get("gen_ai.usage.cost.currency"), attrs.get("lians.cost.currency"))
        if amount is not None and isinstance(currency, str):
            explicit["cost"] = {"amount": amount, "currency": currency.upper()}

    # setdefault can retain None; validation/output should expose only observed fields.
    return {key: value for key, value in explicit.items() if value is not None}


def _normalize_mcp(envelope: RecorderEnvelope) -> dict[str, Any]:
    data = _semantic_base(envelope)
    payload = envelope.payload
    method = payload.get("method")
    params = _dict(payload.get("params"))
    rpc_id = payload.get("id")
    is_error = payload.get("error") is not None
    is_response = method is None and ("result" in payload or is_error)
    phase = "response" if is_response else "request" if rpc_id is not None else "event"
    tool_name = _first(params.get("name"), envelope.extensions.get("mcp.tool.name"))
    if method == "tools/call":
        event_kind = "mcp.tool.call"
    elif is_response and tool_name:
        event_kind = "mcp.tool.result"
    else:
        event_kind = _first(
            envelope.event_type,
            f"mcp.{method}" if method else None,
            "mcp.response",
        )
    data.update(
        {
            "event_kind": event_kind,
            "event_name": _first(tool_name, method, data["event_name"], event_kind),
            "phase": phase,
            "status": "error" if is_error else "ok" if is_response else "pending",
            "tool_call_id": _first(
                data["tool_call_id"],
                str(rpc_id) if rpc_id is not None else None,
            ),
            "input": _first(params.get("arguments"), params if method else None, data["input"]),
            "output": _first(payload.get("result"), payload.get("error"), data["output"]),
            "model_id": _first(envelope.extensions.get("gen_ai.model.id"), data["model_id"]),
            "policy_version": _first(
                envelope.extensions.get("lians.policy.version"),
                data["policy_version"],
            ),
            "has_evidence": True,
        }
    )
    return data


def _normalize_a2a(envelope: RecorderEnvelope) -> dict[str, Any]:
    data = _semantic_base(envelope)
    payload = envelope.payload
    kind = str(_first(payload.get("kind"), envelope.event_type, "event"))
    status_value = payload.get("status")
    state = _first(
        _dict(status_value).get("state"),
        status_value if isinstance(status_value, str) else None,
    )
    role = str(payload.get("role") or "").casefold().removeprefix("role_")
    task_id = _first(data["task_id"], payload.get("taskId"), payload.get("id"))
    context_id = _first(data["context_id"], payload.get("contextId"))
    message_id = _first(data["message_id"], payload.get("messageId"))
    state_token = str(state or "").casefold().removeprefix("task_state_")
    if state_token == "cancelled":
        state_token = "canceled"
    terminal = state_token in {"completed", "failed", "canceled", "rejected"}
    event_kind = f"a2a.{kind}"
    if state_token:
        event_kind = f"{event_kind}.{state_token}"
    parts = payload.get("parts")
    artifacts = payload.get("artifacts") or payload.get("artifact")
    data.update(
        {
            "event_kind": event_kind,
            "event_name": _first(payload.get("name"), event_kind),
            "phase": "completed" if terminal or (role == "agent" and not task_id) else "event",
            "status": state_token or "observed",
            "task_id": str(task_id) if task_id is not None else None,
            "context_id": str(context_id) if context_id is not None else None,
            "message_id": str(message_id) if message_id is not None else None,
            "input": _first(parts if role == "user" else None, data["input"]),
            "output": _first(artifacts, parts if role == "agent" else None, data["output"]),
            "model_id": _first(envelope.extensions.get("gen_ai.model.id"), data["model_id"]),
            "policy_version": _first(
                envelope.extensions.get("lians.policy.version"),
                data["policy_version"],
            ),
            "has_evidence": bool(artifacts or data["has_evidence"]),
            "occurred_at": _first(
                envelope.occurred_at,
                _parse_datetime(_dict(status_value).get("timestamp")),
                data["occurred_at"],
            ),
        }
    )
    return data


_NORMALIZERS = {
    "lians": _normalize_lians,
    "otlp.genai": _normalize_otlp,
    "mcp": _normalize_mcp,
    "a2a": _normalize_a2a,
}


def _correlation(
    data: dict[str, Any],
    envelope: RecorderEnvelope,
    source_hash: str,
) -> tuple[str, str]:
    candidates = (
        ("decision", str(data["decision_id"]) if data.get("decision_id") else None),
        ("run", envelope.correlation.run_id),
        ("trace", data.get("trace_id")),
        ("task", data.get("task_id")),
        ("session", data.get("session_id")),
        ("context", data.get("context_id")),
        ("tool_call", data.get("tool_call_id")),
        ("event", envelope.event_id),
    )
    for correlation_type, value in candidates:
        if value:
            return correlation_type, str(value)
    return "event", source_hash


def _dedup_material(
    envelope: RecorderEnvelope,
    data: dict[str, Any],
    correlation_type: str,
    correlation_value: str,
    source_hash: str,
) -> tuple[str, str | None]:
    if envelope.idempotency_key:
        return _hash(f"idempotency:{envelope.idempotency_key}"), _hash(envelope.idempotency_key)
    if envelope.event_id:
        return _hash(f"{envelope.protocol}:event:{envelope.event_id}"), None
    if envelope.protocol == "otlp.genai" and data.get("trace_id") and data.get("span_id"):
        return _hash(f"otlp:{data['trace_id']}:{data['span_id']}"), None
    if envelope.protocol == "mcp" and data.get("tool_call_id"):
        return _hash(
            f"mcp:{correlation_type}:{correlation_value}:{data['tool_call_id']}:{data['phase']}"
        ), None
    if envelope.protocol == "a2a" and (data.get("message_id") or data.get("task_id")):
        artifact = _dict(envelope.payload.get("artifact"))
        artifact_id = _first(artifact.get("artifactId"), artifact.get("artifact_id"))
        status_timestamp = _dict(envelope.payload.get("status")).get("timestamp")
        if data.get("message_id"):
            a2a_identity = f"message:{data['message_id']}"
        elif artifact_id:
            a2a_identity = f"artifact:{data.get('task_id')}:{artifact_id}:{source_hash}"
        elif status_timestamp:
            a2a_identity = (
                f"status:{data.get('task_id')}:{data['event_kind']}:"
                f"{data.get('status')}:{status_timestamp}"
            )
        else:
            a2a_identity = ":".join(
                str(value)
                for value in (
                    data.get("task_id"),
                    data["event_kind"],
                    data.get("status"),
                )
            )
        return _hash(f"a2a:{a2a_identity}"), None
    return _hash(f"fallback:{envelope.protocol}:{source_hash}"), None


def normalize_recorder_envelope(
    envelope: RecorderEnvelope,
    *,
    received_at: datetime | None = None,
) -> NormalizedRecorderEvent:
    """Map an envelope to the stable Recorder event contract without I/O."""
    if envelope.capture.mode == "full" and not get_settings().recorder_allow_full_capture:
        raise RecorderNormalizationError(
            "full_capture_disabled",
            "Full Recorder capture is disabled by deployment policy",
        )
    now = _utc(received_at or datetime.now(timezone.utc))
    source_document = envelope.model_dump(mode="json", exclude_none=True)
    sensitive_fields = set(envelope.capture.sensitive_fields)
    try:
        sanitized_source = _sanitize(
            source_document,
            mode=envelope.capture.mode,
            sensitive_fields=sensitive_fields,
        )
        source_hash = _hash(sanitized_source)
    except (TypeError, ValueError) as exc:
        raise RecorderNormalizationError(
            "non_canonical_payload",
            "Recorder payload must contain finite, canonically serializable values",
        ) from exc
    data = _NORMALIZERS[envelope.protocol](envelope)

    occurred_supplied = data.get("occurred_at") is not None
    occurred_at = _utc(data.get("occurred_at") or now)
    may_derive_content_hash = envelope.capture.mode != "metadata_only"
    input_hash = data.get("input_hash") or (
        _hash(
            _sanitize(
                data["input"],
                mode="full",
                sensitive_fields=sensitive_fields,
            )
        )
        if may_derive_content_hash and data.get("input") is not None
        else None
    )
    output_hash = data.get("output_hash") or (
        _hash(
            _sanitize(
                data["output"],
                mode="full",
                sensitive_fields=sensitive_fields,
            )
        )
        if may_derive_content_hash and data.get("output") is not None
        else None
    )
    for name, value in (("input_hash", input_hash), ("output_hash", output_hash)):
        invalid = value is not None and (
            len(str(value)) != 64 or any(c not in "0123456789abcdefABCDEF" for c in str(value))
        )
        if invalid:
            raise RecorderNormalizationError("invalid_hash", f"{name} must be a SHA-256 hex digest")

    correlation_type, correlation_value = _correlation(data, envelope, source_hash)
    correlation_hash = _hash(f"{correlation_type}:{correlation_value}")
    dedup_key, idempotency_hash = _dedup_material(
        envelope, data, correlation_type, correlation_value, source_hash
    )
    event_kind = str(data.get("event_kind") or "agent.event")[:128]
    phase = str(data.get("phase") or "event")[:32]
    boundary_kind = (
        "decision" if data.get("decision_id") or "decision" in event_kind.casefold() else "run"
    )

    capture_gaps: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    observed = {
        # Envelope identities are claims. Authenticated producer identity is
        # added only at the persistence boundary.
        "agent_identity": False,
        "temporal_boundary": occurred_supplied,
        "correlation_id": correlation_type != "event" or envelope.event_id is not None,
        "model_identity": bool(data.get("model_id")),
        "input_capture": bool(input_hash),
        "output_capture": bool(output_hash),
        "outcome_status": bool(
            data.get("status")
            and (phase in {"completed", "response"} or "decision" in event_kind.casefold())
        ),
        "policy_context": bool(data.get("policy_version")),
        "principal_context": False,
        "evidence_trace": bool(
            data.get("has_evidence") or data.get("tool_call_id") or data.get("trace_id")
        ),
    }
    for gap, present in observed.items():
        if not present:
            capture_gaps.append(gap)
    if not occurred_supplied:
        diagnostics.append(
            {
                "code": "occurred_at_defaulted",
                "severity": "warning",
                "message": (
                    "No source timestamp was captured; receipt time defaults to ingestion time."
                ),
            }
        )
    if correlation_type == "event":
        diagnostics.append(
            {
                "code": "weak_correlation",
                "severity": "warning",
                "message": "No run, trace, task, session, or decision identifier was captured.",
            }
        )
    if envelope.protocol == "otlp.genai" and not data.get("attributes"):
        diagnostics.append(
            {
                "code": "missing_genai_attributes",
                "severity": "warning",
                "message": "The OTLP span has no GenAI semantic-convention attributes.",
            }
        )
    if data.get("agent_id") or data.get("principal_id") or envelope.actor.roles:
        diagnostics.append(
            {
                "code": "unverified_actor_claim",
                "severity": "info",
                "message": (
                    "Envelope actor fields are caller-reported labels and are not "
                    "the authenticated ingestion principal."
                ),
            }
        )

    normalized_payload = {
        "source": sanitized_source.get("payload", {}),
        "semantic": {
            "input_hash": input_hash,
            "output_hash": output_hash,
            "source_payload_hash": source_hash,
        },
        "actor": {
            "attribution": "claimed_unverified",
            "claimed_principal_id": data.get("principal_id"),
            "claimed_roles": list(envelope.actor.roles),
            "claimed_authentication_context": _sanitize(
                envelope.actor.authentication_context,
                mode=envelope.capture.mode,
                sensitive_fields=sensitive_fields,
            ),
        },
        "operational": _operational_fields(envelope, data),
    }
    raw_extensions = {
        **envelope.extensions,
        **{f"actor.{key}": value for key, value in envelope.actor.extensions.items()},
        **{f"correlation.{key}": value for key, value in envelope.correlation.extensions.items()},
    }
    extensions = _sanitize(
        raw_extensions,
        mode=envelope.capture.mode,
        sensitive_fields=sensitive_fields,
    )
    return NormalizedRecorderEvent(
        schema_version=envelope.schema_version,
        protocol=envelope.protocol,
        event_kind=event_kind,
        event_name=str(data["event_name"])[:512] if data.get("event_name") else None,
        phase=phase,
        status=str(data["status"])[:64] if data.get("status") is not None else None,
        source_event_id=envelope.event_id,
        occurred_at=occurred_at,
        occurred_at_supplied=occurred_supplied,
        agent_id=str(data["agent_id"])[:255] if data.get("agent_id") else None,
        principal_id=str(data["principal_id"])[:512] if data.get("principal_id") else None,
        subject_id=envelope.subject_id,
        session_id=str(data["session_id"])[:512] if data.get("session_id") else None,
        trace_id=str(data["trace_id"])[:64] if data.get("trace_id") else None,
        span_id=str(data["span_id"])[:64] if data.get("span_id") else None,
        parent_span_id=(str(data["parent_span_id"])[:64] if data.get("parent_span_id") else None),
        task_id=str(data["task_id"])[:512] if data.get("task_id") else None,
        context_id=str(data["context_id"])[:512] if data.get("context_id") else None,
        message_id=str(data["message_id"])[:512] if data.get("message_id") else None,
        tool_call_id=(str(data["tool_call_id"])[:512] if data.get("tool_call_id") else None),
        decision_id=data.get("decision_id"),
        model_id=str(data["model_id"])[:512] if data.get("model_id") else None,
        model_version=(str(data["model_version"])[:512] if data.get("model_version") else None),
        policy_version=(str(data["policy_version"])[:512] if data.get("policy_version") else None),
        input_hash=str(input_hash).lower() if input_hash else None,
        output_hash=str(output_hash).lower() if output_hash else None,
        has_evidence=bool(data.get("has_evidence")),
        correlation_type=correlation_type,
        correlation_value=correlation_value[:512],
        correlation_hash=correlation_hash,
        boundary_kind=boundary_kind,
        dedup_key=dedup_key,
        idempotency_key_hash=idempotency_hash,
        source_payload_hash=source_hash,
        capture_mode=envelope.capture.mode,
        normalized_payload=normalized_payload,
        extensions=extensions,
        capture_gaps=capture_gaps,
        diagnostics=diagnostics,
    )


def _barrier_scope(barrier_group: str | None) -> str:
    # Prefix and hash scoped groups so no user-chosen barrier name can collide
    # with the unbarriered sentinel or become an indexed disclosure surface.
    return "unbarriered" if barrier_group is None else f"barrier:{_hash(barrier_group)}"


def _barrier_filter(column, barrier_group: str | None):
    if barrier_group is None:
        return None
    return or_(column.is_(None), column == barrier_group)


def _capture_state(event: NormalizedRecorderEvent) -> dict[str, bool]:
    return {name: name not in event.capture_gaps for name in _READINESS_WEIGHTS}


def _readiness(state: dict[str, Any]) -> tuple[int, bool, list[str]]:
    score = sum(weight for name, weight in _READINESS_WEIGHTS.items() if state.get(name))
    missing = [name for name in _READINESS_WEIGHTS if not state.get(name)]
    ready = score >= 70 and all(state.get(name) for name in _READY_REQUIRED)
    return score, ready, missing


def _merge_diagnostics(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_code = {
        str(item.get("code", _hash(item))): item for item in [*(existing or []), *(incoming or [])]
    }
    return list(by_code.values())[:100]


async def _get_or_create_run(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    event: NormalizedRecorderEvent,
    recorded_at: datetime,
) -> RecorderRun:
    scope = _barrier_scope(barrier_group)
    filters = (
        RecorderRun.namespace == namespace,
        RecorderRun.barrier_scope == scope,
        RecorderRun.correlation_hash == event.correlation_hash,
    )
    existing = (await db.execute(select(RecorderRun).where(*filters))).scalar_one_or_none()
    if existing is not None:
        return existing

    run = RecorderRun(
        id=uuid.uuid4(),
        namespace=namespace,
        barrier_group=barrier_group,
        barrier_scope=scope,
        correlation_type=event.correlation_type,
        correlation_value=event.correlation_value,
        correlation_hash=event.correlation_hash,
        boundary_kind=event.boundary_kind,
        agent_id=event.agent_id,
        subject_id=event.subject_id,
        session_id=event.session_id,
        trace_id=event.trace_id,
        task_id=event.task_id,
        decision_id=event.decision_id,
        status="open",
        first_occurred_at=event.occurred_at,
        last_occurred_at=event.occurred_at,
        first_recorded_at=recorded_at,
        last_recorded_at=recorded_at,
        event_count=0,
        protocols=[],
        capture_state={},
        completeness_gaps=list(_READINESS_WEIGHTS),
        diagnostics=[],
        extension_attributes={},
        created_at=recorded_at,
        updated_at=recorded_at,
    )
    try:
        async with db.begin_nested():
            db.add(run)
            await db.flush()
        return run
    except IntegrityError:
        found = (await db.execute(select(RecorderRun).where(*filters))).scalar_one_or_none()
        if found is None:
            raise
        return found


def _recorder_event_hash_document(row: RecorderEvent) -> dict[str, Any]:
    version = int(row.event_hash_version or 1)
    legacy = {
        "schema_version": row.schema_version,
        "namespace": row.namespace,
        "run_id": str(row.run_id),
        "protocol": row.protocol,
        "event_kind": row.event_kind,
        "phase": row.phase,
        "occurred_at": _utc(row.occurred_at).isoformat(),
        "source_payload_hash": row.source_payload_hash,
        "normalized_payload": row.normalized_payload,
        "extensions": row.extension_attributes,
    }
    if version == 1:
        return legacy
    if version != 2:
        raise RecorderIntegrityError(f"Recorder event {row.id} uses unknown hash version {version}")
    return {
        "event_hash_version": 2,
        "id": str(row.id),
        "namespace": row.namespace,
        "run_id": str(row.run_id),
        "barrier_group": row.barrier_group,
        "barrier_scope": row.barrier_scope,
        "schema_version": row.schema_version,
        "protocol": row.protocol,
        "event_kind": row.event_kind,
        "event_name": row.event_name,
        "phase": row.phase,
        "status": row.status,
        "source_event_id": row.source_event_id,
        "dedup_key": row.dedup_key,
        "idempotency_key_hash": row.idempotency_key_hash,
        "source_payload_hash": row.source_payload_hash,
        "occurred_at": _utc(row.occurred_at).isoformat(),
        "recorded_at": _utc(row.recorded_at).isoformat(),
        "ingested_by_principal_ref": row.ingested_by_principal_ref,
        "ingested_by_auth_method": row.ingested_by_auth_method,
        "ingested_by_credential_id": row.ingested_by_credential_id,
        "actor_attribution": row.actor_attribution,
        "agent_id": row.agent_id,
        "subject_id": row.subject_id,
        "session_id": row.session_id,
        "trace_id": row.trace_id,
        "span_id": row.span_id,
        "parent_span_id": row.parent_span_id,
        "task_id": row.task_id,
        "context_id": row.context_id,
        "message_id": row.message_id,
        "tool_call_id": row.tool_call_id,
        "decision_id": str(row.decision_id) if row.decision_id else None,
        "model_id": row.model_id,
        "model_version": row.model_version,
        "policy_version": row.policy_version,
        "input_hash": row.input_hash,
        "output_hash": row.output_hash,
        "capture_mode": row.capture_mode,
        "normalized_payload": row.normalized_payload,
        "extension_attributes": row.extension_attributes,
        "capture_gaps": row.capture_gaps,
        "diagnostics": row.diagnostics,
    }


def compute_recorder_event_hash(row: RecorderEvent) -> str:
    return _hash(_recorder_event_hash_document(row))


def assert_recorder_event_hash(row: RecorderEvent) -> None:
    expected = compute_recorder_event_hash(row)
    if row.event_hash != expected:
        raise RecorderIntegrityError(
            f"Recorder event {row.id} does not match its stored event_hash"
        )


async def assert_recorder_event_integrity(
    db: AsyncSession,
    row: RecorderEvent,
) -> None:
    """Verify the event hash and its original core-audit commitment."""
    assert_recorder_event_hash(row)
    event_id = str(row.id)
    run_id = str(row.run_id)
    version = int(row.event_hash_version or 1)
    if version == 1 and (
        row.ingested_by_principal_ref != _LEGACY_RECORDER_PRINCIPAL_REF
        or row.ingested_by_auth_method != _LEGACY_RECORDER_AUTH_METHOD
        or row.ingested_by_credential_id is not None
        or row.actor_attribution != "claimed_unverified"
    ):
        raise RecorderIntegrityError(
            f"Recorder event {row.id} has invalid legacy provenance markers"
        )
    if version == 2 and (
        not row.ingested_by_principal_ref.startswith("lians:principal:v1:")
        or row.ingested_by_principal_ref == _LEGACY_RECORDER_PRINCIPAL_REF
        or row.ingested_by_auth_method not in {"api_key", "oidc_bearer"}
        or row.actor_attribution not in {"claimed_unverified", "not_supplied"}
        or (
            row.ingested_by_credential_id is not None
            and not 1 <= len(row.ingested_by_credential_id) <= 128
        )
    ):
        raise RecorderIntegrityError(
            f"Recorder event {row.id} has invalid authenticated provenance"
        )
    filters = [
        EventLog.namespace == row.namespace,
        EventLog.op == "recorder_ingest",
        EventLog.content_hash == row.event_hash,
    ]
    if version == 2:
        filters.append(EventLog.agent_id == row.ingested_by_principal_ref)

    query = select(EventLog).where(*filters)
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        # PostgreSQL can apply the exact JSON identity predicates using the
        # event-log payload index. Other supported dialects take the bounded
        # namespace/op/content-hash result set and filter it portably below.
        binding_identity: dict[str, Any] = {
            "recorder_event_id": event_id,
            "recorder_run_id": run_id,
        }
        if version == 2:
            binding_identity.update(
                {
                    "protocol": row.protocol,
                    "event_kind": row.event_kind,
                    "event_hash_version": 2,
                }
            )
        query = query.where(cast(EventLog.payload, JSONB).contains(binding_identity))
    elif dialect == "sqlite":
        # JSON1 predicates make the corruption check cardinality-bounded without
        # hydrating every audit row that happens to share the same content hash.
        query = query.where(
            func.json_extract(EventLog.payload, "$.recorder_event_id") == event_id,
            func.json_extract(EventLog.payload, "$.recorder_run_id") == run_id,
        )
        if version == 2:
            query = query.where(
                func.json_extract(EventLog.payload, "$.protocol") == row.protocol,
                func.json_extract(EventLog.payload, "$.event_kind") == row.event_kind,
                func.json_extract(EventLog.payload, "$.event_hash_version") == 2,
            )
    candidates = list(
        (
            await db.execute(
                query.order_by(EventLog.chain_position, EventLog.id)
                .limit(2)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    bindings = [
        event
        for event in candidates
        if isinstance(event.payload, dict)
        and event.payload.get("recorder_event_id") == event_id
        and event.payload.get("recorder_run_id") == run_id
        and (
            version == 1
            or (
                event.agent_id == row.ingested_by_principal_ref
                and event.payload.get("protocol") == row.protocol
                and event.payload.get("event_kind") == row.event_kind
                and event.payload.get("event_hash_version") == 2
            )
        )
    ]
    if len(bindings) != 1:
        raise RecorderIntegrityError(
            f"Recorder event {row.id} lacks exactly one original audit binding"
        )


async def assert_recorder_events_integrity(
    db: AsyncSession,
    rows: list[RecorderEvent],
) -> None:
    """Verify one bounded page and all audit bindings with a single query."""

    if not rows:
        return
    if len(rows) > _DECISION_RECORDER_INDEX_LIMIT:
        raise RecorderIntegrityError("Recorder integrity page exceeds its hard bound")
    namespaces = {row.namespace for row in rows}
    if len(namespaces) != 1:
        raise RecorderIntegrityError("Recorder integrity page crosses namespaces")
    for row in rows:
        assert_recorder_event_hash(row)
        version = int(row.event_hash_version or 1)
        if version == 1 and (
            row.ingested_by_principal_ref != _LEGACY_RECORDER_PRINCIPAL_REF
            or row.ingested_by_auth_method != _LEGACY_RECORDER_AUTH_METHOD
            or row.ingested_by_credential_id is not None
            or row.actor_attribution != "claimed_unverified"
        ):
            raise RecorderIntegrityError("Recorder event has invalid legacy provenance")
        if version == 2 and (
            not row.ingested_by_principal_ref.startswith("lians:principal:v1:")
            or row.ingested_by_principal_ref == _LEGACY_RECORDER_PRINCIPAL_REF
            or row.ingested_by_auth_method not in {"api_key", "oidc_bearer"}
            or row.actor_attribution not in {"claimed_unverified", "not_supplied"}
            or (
                row.ingested_by_credential_id is not None
                and not 1 <= len(row.ingested_by_credential_id) <= 128
            )
        ):
            raise RecorderIntegrityError("Recorder event has invalid authenticated provenance")
    event_ids = [str(row.id) for row in rows]
    query = select(EventLog).where(
        EventLog.namespace == rows[0].namespace,
        EventLog.op == "recorder_ingest",
        EventLog.content_hash.in_({row.event_hash for row in rows}),
    )
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        payload = cast(EventLog.payload, JSONB)
        query = query.where(
            func.jsonb_extract_path_text(payload, "recorder_event_id").in_(event_ids)
        )
    elif dialect == "sqlite":
        query = query.where(
            func.json_extract(EventLog.payload, "$.recorder_event_id").in_(event_ids)
        )
    candidates = list(
        (
            await db.execute(
                query.order_by(EventLog.chain_position, EventLog.id).limit(len(rows) * 2 + 1)
            )
        ).scalars()
    )
    by_event_id: dict[str, list[EventLog]] = {event_id: [] for event_id in event_ids}
    for event in candidates:
        if isinstance(event.payload, dict):
            event_id = str(event.payload.get("recorder_event_id") or "")
            if event_id in by_event_id:
                by_event_id[event_id].append(event)
    for row in rows:
        version = int(row.event_hash_version or 1)
        bindings = [
            event
            for event in by_event_id[str(row.id)]
            if event.content_hash == row.event_hash
            and event.payload.get("recorder_run_id") == str(row.run_id)
            and (
                version == 1
                or (
                    event.agent_id == row.ingested_by_principal_ref
                    and event.payload.get("protocol") == row.protocol
                    and event.payload.get("event_kind") == row.event_kind
                    and event.payload.get("event_hash_version") == 2
                )
            )
        ]
        if len(bindings) != 1:
            raise RecorderIntegrityError("Recorder evidence page contains an invalid audit binding")


def _event_out(row: RecorderEvent) -> RecorderEventOut:
    assert_recorder_event_hash(row)
    return RecorderEventOut(
        id=row.id,
        run_id=row.run_id,
        protocol=row.protocol,
        event_kind=row.event_kind,
        event_name=row.event_name,
        phase=row.phase,
        status=row.status,
        occurred_at=row.occurred_at,
        recorded_at=row.recorded_at,
        agent_id=row.agent_id,
        actor_attribution=row.actor_attribution,
        ingested_by_principal_ref=row.ingested_by_principal_ref,
        ingested_by_auth_method=row.ingested_by_auth_method,
        ingested_by_credential_id=row.ingested_by_credential_id,
        trace_id=row.trace_id,
        span_id=row.span_id,
        task_id=row.task_id,
        decision_id=row.decision_id,
        model_id=row.model_id,
        input_hash=row.input_hash,
        output_hash=row.output_hash,
        operational=RecorderOperational.model_validate(
            (row.normalized_payload or {}).get("operational") or {}
        ),
        capture_mode=row.capture_mode,
        capture_gaps=list(row.capture_gaps or []),
        diagnostics=list(row.diagnostics or []),
        event_hash=row.event_hash,
        event_hash_version=int(row.event_hash_version or 1),
    )


def _recorder_artifact_specs(row: RecorderEvent) -> list[tuple[ArtifactSpec, list[str]]]:
    """Translate normalized runtime telemetry into conservative evidence nodes."""
    specs: list[tuple[ArtifactSpec, list[str]]] = []
    if row.model_id:
        specs.append(
            (
                ArtifactSpec(kind="model", identifier=row.model_id, version=row.model_version),
                ["recorder.model"],
            )
        )
    if row.policy_version:
        specs.append(
            (
                ArtifactSpec(
                    kind="policy",
                    identifier="decision-policy",
                    version=row.policy_version,
                ),
                ["recorder.policy_version"],
            )
        )
    if row.input_hash:
        specs.append(
            (
                ArtifactSpec(
                    kind="input",
                    identifier=f"recorder-event:{row.id}:input",
                    artifact_hash=row.input_hash,
                    metadata={
                        "protocol": row.protocol,
                        "event_kind": row.event_kind,
                        "hash_role": "runtime_input",
                    },
                ),
                ["recorder.input_hash"],
            )
        )
    if row.output_hash:
        specs.append(
            (
                ArtifactSpec(
                    kind="output",
                    identifier=f"recorder-event:{row.id}:output",
                    artifact_hash=row.output_hash,
                    metadata={
                        "protocol": row.protocol,
                        "event_kind": row.event_kind,
                        "hash_role": "runtime_output",
                    },
                ),
                ["recorder.output_hash"],
            )
        )
    tool_identifier = row.event_name if row.protocol == "mcp" else None
    if not tool_identifier and row.tool_call_id:
        tool_identifier = row.event_name or row.event_kind
    if tool_identifier and len(tool_identifier) <= 1024:
        is_result = bool(
            row.output_hash
            and (
                row.phase in {"completed", "response"}
                or row.event_kind.casefold().endswith((".result", ".response"))
            )
        )
        metadata = {
            "protocol": row.protocol,
            "event_kind": row.event_kind,
            "tool_call_id": row.tool_call_id,
        }
        if is_result:
            metadata["hash_role"] = "result"
        specs.append(
            (
                ArtifactSpec(
                    kind="tool",
                    identifier=tool_identifier,
                    artifact_hash=row.output_hash if is_result else None,
                    metadata=metadata,
                ),
                ["recorder.tool_call"],
            )
        )
    return specs


async def _index_recorder_row(
    db: AsyncSession,
    *,
    decision: DecisionRecord,
    row: RecorderEvent,
) -> tuple[int, int]:
    return await index_recorder_rows_batch(db, decision=decision, rows=[row])


async def _acquire_decision_recorder_fence(
    db: AsyncSession,
    *,
    namespace: str,
    decision_id: UUID,
) -> None:
    """Serialize decision creation, event insertion, and page completion."""

    if db.get_bind().dialect.name != "postgresql":
        return
    # DecisionRecord registration and evidence-link registration already use
    # this namespace fence. Taking it first gives multi-decision OTLP batches,
    # live Recorder inserts, and worker pages one deadlock-free lock order.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:namespace, :hash_seed))"),
        {
            "namespace": namespace,
            "hash_seed": _EVIDENCE_REGISTRATION_FENCE_HASH_SEED,
        },
    )
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, :hash_seed))"),
        {
            "identity": f"{namespace}:{decision_id}",
            "hash_seed": _DECISION_RECORDER_FENCE_HASH_SEED,
        },
    )


async def _update_recorder_kind_coverage(
    db: AsyncSession,
    *,
    decision: DecisionRecord,
    event_summaries: dict[str, list[dict[str, str]]],
    new_links_by_kind: Counter[str],
) -> None:
    kinds = sorted(event_summaries)
    if not kinds:
        return
    rows = list(
        (
            await db.execute(
                select(DecisionEvidenceKindCoverage)
                .where(
                    DecisionEvidenceKindCoverage.namespace == decision.namespace,
                    DecisionEvidenceKindCoverage.decision_id == decision.id,
                    DecisionEvidenceKindCoverage.kind.in_(kinds),
                )
                .with_for_update()
            )
        ).scalars()
    )
    by_kind = {row.kind: row for row in rows}
    if set(kinds) != set(by_kind):
        raise RecorderIntegrityError("Decision evidence coverage registration is incomplete")
    now = datetime.now(timezone.utc)
    for kind in kinds:
        row = by_kind[kind]
        gaps = set(row.gap_codes or [])
        if row.source_watermark is None:
            gaps.discard("normalization_pending")
            gaps.add("legacy_decision_coverage_unassessed")
        row.source_watermark = _hash(
            {
                "domain": "lians.recorder-evidence-coverage.v1",
                "decision_id": str(decision.id),
                "kind": kind,
                "prior_watermark": row.source_watermark,
                "events": event_summaries[kind],
            }
        )
        row.status = "partial" if gaps else "complete"
        row.indexer_version = _RECORDER_COVERAGE_INDEXER_VERSION
        row.normalization_scope = _RECORDER_COVERAGE_SCOPE
        row.gap_codes = sorted(gaps)[:32]
        row.indexed_artifact_count = int(row.indexed_artifact_count or 0) + int(
            new_links_by_kind[kind]
        )
        row.assessed_at = now
        row.updated_at = now


async def index_recorder_rows_batch(
    db: AsyncSession,
    *,
    decision: DecisionRecord,
    rows: list[RecorderEvent],
) -> tuple[int, int]:
    """Integrity-check and idempotently index one bounded Recorder page."""

    if not rows:
        return 0, 0
    if len(rows) > _DECISION_RECORDER_INDEX_LIMIT:
        raise RecorderIntegrityError("Recorder evidence page exceeds its hard bound")
    await _acquire_decision_recorder_fence(
        db,
        namespace=decision.namespace,
        decision_id=decision.id,
    )
    await assert_recorder_events_integrity(db, rows)
    artifact_candidates: list[tuple[str | None, ArtifactSpec, str | None, datetime | None]] = []
    descriptors: list[tuple[RecorderEvent, ArtifactSpec, list[str], str]] = []
    event_summaries: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if row.namespace != decision.namespace or row.decision_id != decision.id:
            raise RecorderIntegrityError("Recorder evidence page crosses its decision")
        if decision.barrier_group is not None and row.barrier_group not in {
            None,
            decision.barrier_group,
        }:
            raise RecorderIntegrityError("Recorder evidence page crosses its barrier")
        seen_kinds: set[str] = set()
        for spec, basis in _recorder_artifact_specs(row):
            identity = artifact_identity_hash(
                barrier_group=row.barrier_group,
                kind=spec.kind,
                identifier=spec.identifier,
                version=spec.version,
                hash_algorithm=spec.hash_algorithm,
                artifact_hash=spec.artifact_hash,
            )
            artifact_candidates.append(
                (
                    row.barrier_group,
                    spec,
                    row.ingested_by_principal_ref,
                    row.recorded_at,
                )
            )
            descriptors.append((row, spec, basis, identity))
            seen_kinds.add(spec.kind)
        for kind in seen_kinds:
            event_summaries.setdefault(kind, []).append(
                {"event_id": str(row.id), "event_hash": row.event_hash}
            )
    artifacts: dict[str, EvidenceArtifact] = {}
    artifacts_created = 0
    for offset in range(
        0,
        len(artifact_candidates),
        _RECORDER_EVIDENCE_BULK_PAGE_SIZE,
    ):
        page_artifacts, page_created = await ensure_artifacts_bulk(
            db,
            namespace=decision.namespace,
            candidates=artifact_candidates[offset : offset + _RECORDER_EVIDENCE_BULK_PAGE_SIZE],
        )
        artifacts.update(page_artifacts)
        artifacts_created += page_created
    link_candidates = [
        (
            artifacts[identity],
            [*basis, f"recorder.event:{row.id}"],
            row.recorded_at,
        )
        for row, _spec, basis, identity in descriptors
    ]
    links_created = 0
    new_artifact_ids: set[UUID] = set()
    for offset in range(
        0,
        len(link_candidates),
        _RECORDER_EVIDENCE_BULK_PAGE_SIZE,
    ):
        page_created, page_artifact_ids = await ensure_links_bulk(
            db,
            namespace=decision.namespace,
            decision=decision,
            candidates=link_candidates[offset : offset + _RECORDER_EVIDENCE_BULK_PAGE_SIZE],
        )
        links_created += page_created
        new_artifact_ids.update(page_artifact_ids)
    new_links_by_kind: Counter[str] = Counter(
        artifact.kind for artifact in artifacts.values() if artifact.id in new_artifact_ids
    )
    await _update_recorder_kind_coverage(
        db,
        decision=decision,
        event_summaries=event_summaries,
        new_links_by_kind=new_links_by_kind,
    )
    return artifacts_created, links_created


def _decision_recorder_filters(decision: DecisionRecord) -> list[Any]:
    filters: list[Any] = [
        RecorderEvent.namespace == decision.namespace,
        RecorderEvent.decision_id == decision.id,
    ]
    if decision.barrier_group is not None:
        filters.append(
            or_(
                RecorderEvent.barrier_group.is_(None),
                RecorderEvent.barrier_group == decision.barrier_group,
            )
        )
    return filters


async def _mark_recorder_job_coverage(
    db: AsyncSession,
    *,
    decision: DecisionRecord,
    job: RecorderEvidenceIndexJob,
    state: str,
) -> None:
    """Keep normalized completeness closed until the fixed snapshot is indexed."""

    if state not in {"pending", "completed", "failed"}:
        raise ValueError("Unsupported Recorder evidence coverage state")
    rows = list(
        (
            await db.execute(
                select(DecisionEvidenceKindCoverage)
                .where(
                    DecisionEvidenceKindCoverage.namespace == decision.namespace,
                    DecisionEvidenceKindCoverage.decision_id == decision.id,
                    DecisionEvidenceKindCoverage.kind.in_(_RECORDER_COVERAGE_KINDS),
                )
                .with_for_update()
            )
        ).scalars()
    )
    if len(rows) != len(_RECORDER_COVERAGE_KINDS):
        raise RecorderIntegrityError("Decision evidence coverage registration is incomplete")
    now = datetime.now(timezone.utc)
    for row in rows:
        gaps = set(row.gap_codes or [])
        gaps.discard("recorder_index_pending")
        gaps.discard("recorder_index_failed")
        if state == "pending":
            gaps.add("recorder_index_pending")
        elif state == "failed":
            gaps.add("recorder_index_failed")
        row.source_watermark = _hash(
            {
                "domain": "lians.recorder-evidence-job.v1",
                "decision_id": str(decision.id),
                "kind": row.kind,
                "prior_watermark": row.source_watermark,
                "job_id": str(job.id),
                "state": state,
                "snapshot_max_recorded_at": _utc(job.snapshot_max_recorded_at).isoformat(),
                "snapshot_max_event_id": str(job.snapshot_max_event_id),
                "snapshot_event_count": int(job.snapshot_event_count),
                "events_indexed": int(job.events_indexed),
            }
        )
        row.status = "partial" if gaps else "complete"
        row.indexer_version = _RECORDER_COVERAGE_INDEXER_VERSION
        row.normalization_scope = _RECORDER_COVERAGE_SCOPE
        row.gap_codes = sorted(gaps)[:32]
        row.assessed_at = now
        row.updated_at = now


async def _enqueue_recorder_evidence_index_job(
    db: AsyncSession,
    *,
    decision: DecisionRecord,
    total: int,
    snapshot_max_recorded_at: datetime,
    snapshot_max_event_id: UUID,
) -> RecorderEvidenceIndexJob:
    existing = (
        await db.execute(
            select(RecorderEvidenceIndexJob).where(
                RecorderEvidenceIndexJob.namespace == decision.namespace,
                RecorderEvidenceIndexJob.decision_id == decision.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            int(existing.snapshot_event_count) != total
            or _utc(existing.snapshot_max_recorded_at) != _utc(snapshot_max_recorded_at)
            or existing.snapshot_max_event_id != snapshot_max_event_id
        ):
            raise RecorderIntegrityError(
                "Recorder evidence snapshot identity changed after registration"
            )
        return existing
    now = datetime.now(timezone.utc)
    job = RecorderEvidenceIndexJob(
        id=uuid.uuid4(),
        namespace=decision.namespace,
        barrier_group=decision.barrier_group,
        barrier_scope=_barrier_scope(decision.barrier_group),
        decision_id=decision.id,
        queued_by_principal_ref=decision.recorded_by_principal_ref,
        queued_by_auth_method=decision.recorded_by_auth_method,
        status="pending",
        snapshot_max_recorded_at=snapshot_max_recorded_at,
        snapshot_max_event_id=snapshot_max_event_id,
        snapshot_event_count=total,
        events_indexed=0,
        artifacts_created=0,
        links_created=0,
        pages_completed=0,
        processing_attempts=0,
        consecutive_failures=0,
        attempt_limit=get_settings().recorder_evidence_index_worker_max_attempts,
        next_attempt_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.flush()
    await _mark_recorder_job_coverage(
        db,
        decision=decision,
        job=job,
        state="pending",
    )
    await chain_log(
        db,
        decision.namespace,
        decision.recorded_by_principal_ref,
        "recorder_evidence_index_queued",
        content_hash=_hash(
            {
                "job_id": str(job.id),
                "snapshot_max_event_id": str(job.snapshot_max_event_id),
                "snapshot_event_count": int(job.snapshot_event_count),
            }
        ),
        payload={
            "job_id": str(job.id),
            "decision_id": str(decision.id),
            "snapshot_event_count": int(job.snapshot_event_count),
        },
    )
    return job


async def index_recorder_evidence_for_decision(
    db: AsyncSession,
    decision: DecisionRecord,
) -> tuple[int, int]:
    """Back-link recorder events that arrived before their DecisionRecord."""
    await _acquire_decision_recorder_fence(
        db,
        namespace=decision.namespace,
        decision_id=decision.id,
    )
    filters = _decision_recorder_filters(decision)
    total = int(
        (await db.execute(select(func.count(RecorderEvent.id)).where(*filters))).scalar_one() or 0
    )
    if total > _DECISION_RECORDER_INDEX_LIMIT:
        boundary = (
            await db.execute(
                select(RecorderEvent.recorded_at, RecorderEvent.id)
                .where(*filters)
                .order_by(RecorderEvent.recorded_at.desc(), RecorderEvent.id.desc())
                .limit(1)
            )
        ).one_or_none()
        if boundary is None:
            raise RecorderIntegrityError(
                "Recorder evidence snapshot count has no terminal boundary"
            )
        await _enqueue_recorder_evidence_index_job(
            db,
            decision=decision,
            total=total,
            snapshot_max_recorded_at=boundary[0],
            snapshot_max_event_id=boundary[1],
        )
        return 0, 0
    rows = list(
        (
            await db.execute(
                select(RecorderEvent)
                .where(*filters)
                .order_by(RecorderEvent.recorded_at.asc(), RecorderEvent.id.asc())
                .limit(_DECISION_RECORDER_INDEX_LIMIT + 1)
            )
        )
        .scalars()
        .all()
    )
    if len(rows) > _DECISION_RECORDER_INDEX_LIMIT:
        raise RecorderIntegrityError(
            "Recorder evidence snapshot changed while indexing synchronously"
        )
    return await index_recorder_rows_batch(db, decision=decision, rows=rows)


def run_readiness(row: RecorderRun) -> RecorderRunReadiness:
    time_to_readiness_ms = None
    if row.ready_at is not None:
        start = _utc(row.first_recorded_at)
        ready = _utc(row.ready_at)
        time_to_readiness_ms = max(0, int((ready - start).total_seconds() * 1000))
    return RecorderRunReadiness(
        run_id=row.id,
        correlation_type=row.correlation_type,
        boundary_kind=row.boundary_kind,
        status=row.status,
        event_count=row.event_count,
        protocols=list(row.protocols or []),
        score=row.readiness_score,
        receipt_ready=row.receipt_ready,
        ready_at=row.ready_at,
        missing_fields=list(row.completeness_gaps or []),
        diagnostics=list(row.diagnostics or []),
        first_event_at=row.first_occurred_at,
        last_event_at=row.last_occurred_at,
        time_to_readiness_ms=time_to_readiness_ms,
    )


def _terminal_status(event: NormalizedRecorderEvent, current: str) -> str:
    status = (event.status or "").casefold()
    if status in {"error", "failed", "failure", "canceled", "cancelled", "rejected"}:
        return "failed"
    if current == "failed":
        return current
    if event.phase in {"completed", "response"} or status in {"ok", "completed", "success"}:
        return "completed"
    return current or "open"


def _update_run(
    run: RecorderRun,
    event: NormalizedRecorderEvent,
    recorded_at: datetime,
    *,
    ingested_by_principal_ref: str,
    ingested_by_auth_method: str,
) -> None:
    run.first_occurred_at = min(_utc(run.first_occurred_at), event.occurred_at)
    run.last_occurred_at = max(_utc(run.last_occurred_at), event.occurred_at)
    run.last_recorded_at = recorded_at
    run.updated_at = recorded_at
    run.event_count = int(run.event_count or 0) + 1
    run.protocols = sorted({*(run.protocols or []), event.protocol})
    run.agent_id = run.agent_id or event.agent_id
    run.subject_id = run.subject_id or event.subject_id
    run.session_id = run.session_id or event.session_id
    run.trace_id = run.trace_id or event.trace_id
    run.task_id = run.task_id or event.task_id
    run.decision_id = run.decision_id or event.decision_id
    if event.boundary_kind == "decision":
        run.boundary_kind = "decision"
    run.status = _terminal_status(event, run.status)
    run.capture_state = {
        name: bool((run.capture_state or {}).get(name) or value)
        for name, value in _capture_state(event).items()
    }
    score, ready, missing = _readiness(run.capture_state)
    run.readiness_score = score
    run.completeness_gaps = missing
    if ready and not run.receipt_ready:
        run.ready_at = recorded_at
    run.receipt_ready = ready
    run.diagnostics = _merge_diagnostics(run.diagnostics or [], event.diagnostics)
    run.extension_attributes = {**(run.extension_attributes or {}), **event.extensions}
    run.ingested_by_principal_refs = sorted(
        {*(run.ingested_by_principal_refs or []), ingested_by_principal_ref}
    )
    run.ingested_by_auth_methods = sorted(
        {*(run.ingested_by_auth_methods or []), ingested_by_auth_method}
    )


async def ingest_recorder_event(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    envelope: RecorderEnvelope,
    ingested_by_principal_ref: str,
    ingested_by_auth_method: str,
    ingested_by_credential_id: str | None,
    normalized: NormalizedRecorderEvent | None = None,
    received_at: datetime | None = None,
) -> RecorderIngestResult:
    """Persist one event exactly once and update its correlated boundary."""
    if not ingested_by_principal_ref.startswith("lians:principal:v1:"):
        raise RecorderIntegrityError("Recorder ingestion requires a canonical principal ref")
    if ingested_by_auth_method not in {"api_key", "oidc_bearer"}:
        raise RecorderIntegrityError("Recorder ingestion authentication method is invalid")
    if ingested_by_credential_id is not None and len(ingested_by_credential_id) > 128:
        raise RecorderIntegrityError("Recorder credential identifier is too long")
    recorded_at = _utc(received_at or datetime.now(timezone.utc))
    event = normalized or normalize_recorder_envelope(envelope, received_at=recorded_at)
    raw_subject_id = event.subject_id
    persisted_subject_ref = (
        await assert_subject_not_erased(db, raw_subject_id, namespace) if raw_subject_id else None
    )
    if raw_subject_id and persisted_subject_ref:
        event.subject_id = persisted_subject_ref
        event.normalized_payload = replace_subject_identifier(
            event.normalized_payload, raw_subject_id, persisted_subject_ref
        )
        event.extensions = replace_subject_identifier(
            event.extensions, raw_subject_id, persisted_subject_ref
        )
        if event.correlation_value == raw_subject_id:
            event.correlation_value = persisted_subject_ref
            event.correlation_hash = _hash(
                {
                    "type": event.correlation_type,
                    "value": persisted_subject_ref,
                }
            )
    event.capture_gaps = [
        gap for gap in event.capture_gaps if gap not in {"agent_identity", "principal_context"}
    ]
    event.diagnostics = _merge_diagnostics(
        event.diagnostics,
        [
            {
                "code": "authenticated_ingestion_principal",
                "severity": "info",
                "message": (
                    "Producer identity was derived from the authenticated credential; "
                    "envelope actor fields remain unverified claims."
                ),
            }
        ],
    )
    event.normalized_payload = {
        **event.normalized_payload,
        "ingestion": {
            "principal_ref": ingested_by_principal_ref,
            "auth_method": ingested_by_auth_method,
            "credential_id": ingested_by_credential_id,
        },
    }
    event.dedup_key = _hash(
        {
            "source_dedup_key": event.dedup_key,
            "ingested_by_principal_ref": ingested_by_principal_ref,
        }
    )
    # Count every schema-valid ingest attempt, including deduplicated retries.
    # This makes quota enforcement resistant to retry floods while invalid
    # envelopes roll back without consuming capacity.
    await reserve_namespace_usage(
        db,
        namespace=namespace,
        recorder_events=1,
        estimated_ingest_bytes=estimate_ingest_bytes(envelope),
        capture_modes=(event.capture_mode,),
    )
    scope = _barrier_scope(barrier_group)
    duplicate_filters = (
        RecorderEvent.namespace == namespace,
        RecorderEvent.barrier_scope == scope,
        RecorderEvent.dedup_key == event.dedup_key,
    )
    existing = (
        await db.execute(select(RecorderEvent).where(*duplicate_filters))
    ).scalar_one_or_none()
    if existing is not None:
        await assert_recorder_event_integrity(db, existing)
        run = await db.get(RecorderRun, existing.run_id)
        if run is None:
            raise RuntimeError("Recorder event references a missing run boundary")
        return RecorderIngestResult(
            accepted=False,
            duplicate=True,
            event=_event_out(existing),
            readiness=run_readiness(run),
        )

    run = await _get_or_create_run(
        db,
        namespace=namespace,
        barrier_group=barrier_group,
        event=event,
        recorded_at=recorded_at,
    )
    # Multiple protocol events for one run can arrive concurrently. Serialize
    # the read/merge/write of the denormalized readiness aggregate so no event
    # count, protocol, diagnostic, or evidence dimension is lost.
    locked_run = (
        await db.execute(select(RecorderRun).where(RecorderRun.id == run.id).with_for_update())
    ).scalar_one_or_none()
    if locked_run is None:
        raise RuntimeError("Recorder run disappeared during ingestion")
    run = locked_run
    claimed_actor = bool(
        event.agent_id
        or event.principal_id
        or (event.normalized_payload.get("actor") or {}).get("claimed_roles")
    )
    row = RecorderEvent(
        id=uuid.uuid4(),
        namespace=namespace,
        run_id=run.id,
        barrier_group=barrier_group,
        barrier_scope=scope,
        schema_version=event.schema_version,
        protocol=event.protocol,
        event_kind=event.event_kind,
        event_name=event.event_name,
        phase=event.phase,
        status=event.status,
        source_event_id=event.source_event_id,
        dedup_key=event.dedup_key,
        idempotency_key_hash=event.idempotency_key_hash,
        source_payload_hash=event.source_payload_hash,
        event_hash="0" * 64,
        event_hash_version=2,
        occurred_at=event.occurred_at,
        recorded_at=recorded_at,
        ingested_by_principal_ref=ingested_by_principal_ref,
        ingested_by_auth_method=ingested_by_auth_method,
        ingested_by_credential_id=ingested_by_credential_id,
        actor_attribution="claimed_unverified" if claimed_actor else "not_supplied",
        agent_id=event.agent_id,
        subject_id=event.subject_id,
        session_id=event.session_id,
        trace_id=event.trace_id,
        span_id=event.span_id,
        parent_span_id=event.parent_span_id,
        task_id=event.task_id,
        context_id=event.context_id,
        message_id=event.message_id,
        tool_call_id=event.tool_call_id,
        decision_id=event.decision_id,
        model_id=event.model_id,
        model_version=event.model_version,
        policy_version=event.policy_version,
        input_hash=event.input_hash,
        output_hash=event.output_hash,
        capture_mode=event.capture_mode,
        normalized_payload=event.normalized_payload,
        extension_attributes=event.extensions,
        capture_gaps=event.capture_gaps,
        diagnostics=event.diagnostics,
    )
    row.event_hash = compute_recorder_event_hash(row)
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        existing = (
            await db.execute(select(RecorderEvent).where(*duplicate_filters))
        ).scalar_one_or_none()
        if existing is None:
            raise
        await assert_recorder_event_integrity(db, existing)
        existing_run = await db.get(RecorderRun, existing.run_id)
        if existing_run is None:
            raise RuntimeError("Recorder event references a missing run boundary")
        return RecorderIngestResult(
            accepted=False,
            duplicate=True,
            event=_event_out(existing),
            readiness=run_readiness(existing_run),
        )

    _update_run(
        run,
        event,
        recorded_at,
        ingested_by_principal_ref=ingested_by_principal_ref,
        ingested_by_auth_method=ingested_by_auth_method,
    )
    await db.flush()
    indexed_artifacts = 0
    indexed_links = 0
    if row.decision_id is not None:
        decision_filters = [
            DecisionRecord.id == row.decision_id,
            DecisionRecord.namespace == namespace,
        ]
        if barrier_group is not None:
            decision_filters.append(
                or_(
                    DecisionRecord.barrier_group.is_(None),
                    DecisionRecord.barrier_group == barrier_group,
                )
            )
        decision = (
            await db.execute(select(DecisionRecord).where(*decision_filters))
        ).scalar_one_or_none()
        if decision is not None:
            indexed_artifacts, indexed_links = await _index_recorder_row(
                db,
                decision=decision,
                row=row,
            )
    await chain_log(
        db,
        namespace,
        ingested_by_principal_ref,
        "recorder_ingest",
        content_hash=row.event_hash,
        payload={
            "recorder_event_id": str(row.id),
            "recorder_run_id": str(run.id),
            "protocol": event.protocol,
            "event_kind": event.event_kind,
            "event_hash_version": row.event_hash_version,
            "actor_claimed_unverified": claimed_actor,
            "capture_gaps": event.capture_gaps,
            "evidence_artifacts_created": indexed_artifacts,
            "evidence_links_created": indexed_links,
        },
    )
    await assert_recorder_event_integrity(db, row)
    return RecorderIngestResult(
        accepted=True,
        duplicate=False,
        event=_event_out(row),
        readiness=run_readiness(run),
    )


async def get_run_for_auth(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    run_id: UUID,
) -> RecorderRun | None:
    filters = [RecorderRun.id == run_id, RecorderRun.namespace == namespace]
    barrier = _barrier_filter(RecorderRun.barrier_group, barrier_group)
    if barrier is not None:
        filters.append(barrier)
    return (await db.execute(select(RecorderRun).where(*filters))).scalar_one_or_none()


async def list_run_events(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    run_id: UUID,
    limit: int,
    before_recorded_at: datetime | None = None,
    before_id: UUID | None = None,
) -> RecorderEventPage:
    """Return one exact-cardinality, keyset-traversable immutable event page."""

    filters = [RecorderEvent.namespace == namespace, RecorderEvent.run_id == run_id]
    barrier = _barrier_filter(RecorderEvent.barrier_group, barrier_group)
    if barrier is not None:
        filters.append(barrier)
    page_filters = list(filters)
    if before_recorded_at is not None and before_id is not None:
        cursor_time = _utc(before_recorded_at)
        page_filters.append(
            or_(
                RecorderEvent.recorded_at < cursor_time,
                and_(
                    RecorderEvent.recorded_at == cursor_time,
                    RecorderEvent.id < before_id,
                ),
            )
        )
    total_subquery = (
        select(func.count()).select_from(RecorderEvent).where(*filters).scalar_subquery()
    )
    page_result = list(
        (
            await db.execute(
                select(
                    RecorderEvent.id,
                    RecorderEvent.recorded_at,
                    total_subquery.label("collection_total"),
                )
                .where(*page_filters)
                .order_by(RecorderEvent.recorded_at.desc(), RecorderEvent.id.desc())
                .limit(limit + 1)
            )
        ).all()
    )
    page_index = page_result[:limit]
    page_ids = [item.id for item in page_index]
    # The scalar subquery and event page share one statement snapshot. An empty
    # page has no carrier row, so only that case needs a separate exact count;
    # it can never make total smaller than returned (zero).
    total = (
        int(page_result[0].collection_total)
        if page_result
        else int(
            (
                await db.execute(select(func.count()).select_from(RecorderEvent).where(*filters))
            ).scalar_one()
        )
    )
    json_characters = 0
    for offset in range(0, len(page_ids), _RECORDER_EVENT_PAGE_BIND_BATCH):
        chunk = page_ids[offset : offset + _RECORDER_EVENT_PAGE_BIND_BATCH]
        json_characters += int(
            (
                await db.execute(
                    select(
                        func.coalesce(
                            func.sum(
                                func.coalesce(
                                    func.length(cast(RecorderEvent.normalized_payload, Text)),
                                    0,
                                )
                                + func.coalesce(
                                    func.length(cast(RecorderEvent.extension_attributes, Text)),
                                    0,
                                )
                                + func.coalesce(
                                    func.length(cast(RecorderEvent.capture_gaps, Text)),
                                    0,
                                )
                                + func.coalesce(
                                    func.length(cast(RecorderEvent.diagnostics, Text)),
                                    0,
                                )
                            ),
                            0,
                        )
                    ).where(RecorderEvent.id.in_(chunk))
                )
            ).scalar_one()
            or 0
        )
    estimated_bytes = (
        json_characters * _RECORDER_EVENT_JSON_MATERIALIZATION_MULTIPLIER
        + len(page_ids) * _RECORDER_EVENT_ROW_OVERHEAD_BYTES
    )
    byte_limit = get_settings().content_export_page_bytes_limit
    if estimated_bytes > byte_limit:
        raise RecorderEventPageCapacityExceeded(
            estimated_bytes=estimated_bytes,
            byte_limit=byte_limit,
        )

    hydrated_by_id: dict[UUID, RecorderEvent] = {}
    for offset in range(0, len(page_ids), _RECORDER_EVENT_PAGE_BIND_BATCH):
        chunk = page_ids[offset : offset + _RECORDER_EVENT_PAGE_BIND_BATCH]
        hydrated = (
            (
                await db.execute(
                    select(RecorderEvent)
                    .where(RecorderEvent.id.in_(chunk))
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        hydrated_by_id.update((row.id, row) for row in hydrated)
    if set(hydrated_by_id) != set(page_ids):
        raise RecorderIntegrityError("Recorder event page changed between inventory and hydration")
    page_rows = [hydrated_by_id[event_id] for event_id in page_ids]
    for offset in range(0, len(page_rows), _DECISION_RECORDER_INDEX_LIMIT):
        await assert_recorder_events_integrity(
            db,
            page_rows[offset : offset + _DECISION_RECORDER_INDEX_LIMIT],
        )
    return RecorderEventPage(
        events=[_event_out(row) for row in page_rows],
        total=total,
        has_more=len(page_result) > limit,
    )


_ACTION_TEXT = {
    "agent_identity": "Capture a stable agent or service identity.",
    "temporal_boundary": "Send the source event timestamp instead of relying on ingestion time.",
    "correlation_id": "Propagate a run, trace, task, session, or decision identifier.",
    "model_identity": "Capture the resolved model identifier and version.",
    "input_capture": "Capture or hash the decision input.",
    "output_capture": "Capture or hash the model or tool output.",
    "outcome_status": "Emit a terminal outcome or status event.",
    "policy_context": "Attach the evaluated policy version.",
    "principal_context": "Attach the authenticated principal or workload identity.",
    "evidence_trace": "Link evidence, a tool call, or a trace identifier.",
}


async def first_receipt_readiness(
    db: AsyncSession,
    *,
    namespace: str,
    barrier_group: str | None,
    agent_id: str | None,
    limit: int,
) -> FirstReceiptReadinessSummary:
    filters = [RecorderRun.namespace == namespace]
    barrier = _barrier_filter(RecorderRun.barrier_group, barrier_group)
    if barrier is not None:
        filters.append(barrier)
    if agent_id:
        filters.append(RecorderRun.agent_id == agent_id)

    total_result = await db.execute(select(func.count()).select_from(RecorderRun).where(*filters))
    total = int(total_result.scalar_one())
    ready_filters = [*filters, RecorderRun.receipt_ready.is_(True)]
    ready_count = int(
        (
            await db.execute(select(func.count()).select_from(RecorderRun).where(*ready_filters))
        ).scalar_one()
    )
    first_ready = (
        await db.execute(
            select(RecorderRun)
            .where(*ready_filters)
            .order_by(RecorderRun.ready_at.asc(), RecorderRun.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    rows = (
        (
            await db.execute(
                select(RecorderRun)
                .where(*filters)
                .order_by(RecorderRun.updated_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    missing_counts = Counter(gap for row in rows for gap in (row.completeness_gaps or []))
    actions = [
        _ACTION_TEXT[name] for name, _count in missing_counts.most_common(5) if name in _ACTION_TEXT
    ]
    return FirstReceiptReadinessSummary(
        namespace=namespace,
        evaluated_at=datetime.now(timezone.utc),
        total_runs=total,
        ready_runs=ready_count,
        waiting_runs=total - ready_count,
        readiness_rate=ready_count / total if total else 0.0,
        first_ready_run_id=first_ready.id if first_ready else None,
        first_ready_at=first_ready.ready_at if first_ready else None,
        next_actions=actions,
        runs=[run_readiness(row) for row in rows],
    )
