"""Small OTLP/HTTP trace decoder and normalizer.

The receiver deliberately accepts the standard JSON and protobuf encodings but
stores a stable, vendor-neutral representation. It does not sample.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any


class OtlpDecodeError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: int
    start_time_unix_nano: str
    end_time_unix_nano: str
    status_code: int
    status_message: str | None
    service_name: str | None
    scope_name: str | None
    scope_version: str | None
    resource_attributes: dict[str, Any]
    attributes: dict[str, Any]
    events: list[dict[str, Any]]
    links: list[dict[str, Any]]
    is_genai: bool
    model_id: str | None
    model_version: str | None
    payload_hash: str


def _any_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    names = (
        "stringValue", "boolValue", "intValue", "doubleValue", "bytesValue",
        "arrayValue", "kvlistValue",
    )
    for name in names:
        if name not in value:
            continue
        item = value[name]
        if name == "arrayValue":
            return [_any_value(v) for v in item.get("values", [])]
        if name == "kvlistValue":
            return _attributes(item.get("values", []))
        return item
    return value


def _attributes(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {
        str(item["key"]): _any_value(item.get("value"))
        for item in (items or [])
        if item.get("key") is not None
    }


def _hex_id(value: Any, width: int) -> str:
    if isinstance(value, bytes):
        result = value.hex()
    else:
        result = str(value or "").lower()
        if len(result) != width or any(c not in "0123456789abcdef" for c in result):
            # Protobuf MessageToDict represents bytes as base64, whereas native
            # OTLP/JSON represents trace and span IDs as hexadecimal strings.
            try:
                result = base64.b64decode(str(value), validate=True).hex()
            except (binascii.Error, ValueError):
                pass
    if len(result) != width or any(c not in "0123456789abcdef" for c in result):
        raise OtlpDecodeError(f"invalid {width * 4}-bit OTLP identifier")
    return result


def _event(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "time_unix_nano": str(item.get("timeUnixNano", "0")),
        "name": str(item.get("name", "")),
        "attributes": _attributes(item.get("attributes")),
        "dropped_attributes_count": int(item.get("droppedAttributesCount", 0)),
    }


def _link(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "trace_id": _hex_id(item.get("traceId"), 32),
        "span_id": _hex_id(item.get("spanId"), 16),
        "trace_state": item.get("traceState"),
        "attributes": _attributes(item.get("attributes")),
    }


def normalize_trace_request(data: dict[str, Any]) -> list[NormalizedSpan]:
    normalized: list[NormalizedSpan] = []
    for resource_group in data.get("resourceSpans", []):
        resource = resource_group.get("resource") or {}
        resource_attrs = _attributes(resource.get("attributes"))
        service_name = resource_attrs.get("service.name")
        scope_groups = resource_group.get("scopeSpans")
        if scope_groups is None:  # OTLP JSON emitted by older SDKs
            scope_groups = resource_group.get("instrumentationLibrarySpans", [])
        for scope_group in scope_groups:
            scope = scope_group.get("scope") or scope_group.get("instrumentationLibrary") or {}
            for raw in scope_group.get("spans", []):
                attrs = _attributes(raw.get("attributes"))
                model_id = (
                    attrs.get("gen_ai.response.model")
                    or attrs.get("gen_ai.request.model")
                    or attrs.get("gen_ai.system")
                )
                model_version = attrs.get("gen_ai.response.model_version")
                is_genai = any(k.startswith("gen_ai.") for k in attrs)
                canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
                normalized.append(
                    NormalizedSpan(
                        trace_id=_hex_id(raw.get("traceId"), 32),
                        span_id=_hex_id(raw.get("spanId"), 16),
                        parent_span_id=(
                            _hex_id(raw["parentSpanId"], 16) if raw.get("parentSpanId") else None
                        ),
                        name=str(raw.get("name") or "unnamed"),
                        kind=int(raw.get("kind", 0)),
                        start_time_unix_nano=str(raw.get("startTimeUnixNano", "0")),
                        end_time_unix_nano=str(raw.get("endTimeUnixNano", "0")),
                        status_code=int((raw.get("status") or {}).get("code", 0)),
                        status_message=(raw.get("status") or {}).get("message"),
                        service_name=str(service_name) if service_name is not None else None,
                        scope_name=scope.get("name"),
                        scope_version=scope.get("version"),
                        resource_attributes=resource_attrs,
                        attributes=attrs,
                        events=[_event(item) for item in raw.get("events", [])],
                        links=[_link(item) for item in raw.get("links", [])],
                        is_genai=is_genai,
                        model_id=str(model_id) if model_id is not None else None,
                        model_version=(
                            str(model_version) if model_version is not None else None
                        ),
                        payload_hash=hashlib.sha256(canonical.encode()).hexdigest(),
                    )
                )
    return normalized


def decode_trace_request(body: bytes, content_type: str) -> list[NormalizedSpan]:
    if "json" in content_type:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OtlpDecodeError("invalid OTLP JSON request") from exc
        if not isinstance(payload, dict):
            raise OtlpDecodeError("OTLP request must be a JSON object")
        return normalize_trace_request(payload)
    if "protobuf" in content_type or content_type == "application/x-protobuf":
        try:
            from google.protobuf.json_format import MessageToDict
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
                ExportTraceServiceRequest,
            )
        except ImportError as exc:
            raise OtlpDecodeError(
                "protobuf OTLP support requires the 'lians[otel-receiver]' extra"
            ) from exc
        message = ExportTraceServiceRequest()
        try:
            message.ParseFromString(body)
        except Exception as exc:
            raise OtlpDecodeError("invalid OTLP protobuf request") from exc
        return normalize_trace_request(
            MessageToDict(message, preserving_proto_field_name=False)
        )
    raise OtlpDecodeError(
        "unsupported Content-Type; use application/json or application/x-protobuf"
    )
