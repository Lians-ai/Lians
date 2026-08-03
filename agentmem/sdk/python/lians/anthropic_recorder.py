"""Universal Recorder adapters for Anthropic's public Python SDK surfaces.

The client adapter is an Anthropic ``Middleware``.  It observes one HTTP
attempt at a time, exactly where the official SDK runs middleware inside its
retry loop.  It deliberately does not parse response bodies, inspect headers
other than ``request-id``, or imply visibility into application-side tools.

The Managed Agents helper converts an event *after* the caller has verified it
with ``client.beta.webhooks.unwrap(...)``.  It is intentionally not an HTTP
handler and does not own signature verification.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import Mapping
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from ._recorder_adapter import adapter_event, public_mapping
from .platform_types import RecorderEnvelope
from .recorder_sink import AsyncRecorderSink, RecorderAttribution, RecorderSinkError

_WEBHOOK_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_KNOWN_ROUTE_FAMILIES = (
    "messages",
    "sessions",
    "agents",
    "deployments",
    "vaults",
    "webhooks",
    "files",
    "models",
    "batches",
    "memory_stores",
    "organizations",
    "workspaces",
)


@dataclass(frozen=True)
class _Attempt:
    attempt_id: str
    method: str
    route_family: str
    retry_index: int
    stream: bool
    model_id: str | None
    request_body: Any


def build_anthropic_recorder_middleware(
    sink: AsyncRecorderSink,
    *,
    attribution: RecorderAttribution | None = None,
    synchronous_flush_timeout: float = 10.0,
) -> Any:
    """Return public Anthropic middleware that records API-attempt boundaries.

    The returned object implements both ``Middleware.handle`` and
    ``Middleware.handle_async`` and can therefore be registered on either
    ``Anthropic`` or ``AsyncAnthropic`` through ``middleware=[...]``.

    Under ``hash_only`` mode, the request JSON body is committed locally with
    the sink's SHA-256 or HMAC-SHA-256 policy.  Query parameters, credentials,
    arbitrary headers, uploaded file bodies, response bodies, and exception
    messages are never read or recorded.  Response evidence is limited to the
    HTTP status and a one-way reference to Anthropic's public ``request-id``.
    """

    try:
        from anthropic import Middleware  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "anthropic>=0.120.2 is required for native Recorder middleware. "
            "Install with: pip install 'lians-sdk[anthropic]'"
        ) from exc

    identity = attribution or RecorderAttribution()
    if identity.capture_mode == "full":
        raise ValueError(
            "native Anthropic middleware does not transport raw content; "
            "use hash_only or metadata_only"
        )
    if not math.isfinite(synchronous_flush_timeout) or synchronous_flush_timeout <= 0:
        raise ValueError("synchronous_flush_timeout must be finite and positive")

    class LiansAnthropicRecorderMiddleware(Middleware):
        """Anthropic public middleware; Recorder failures never alter API calls."""

        def handle(self, request: Any, call_next: Any) -> Any:
            attempt = self._attempt(request)
            self._record(attempt, phase="started")
            try:
                response = call_next(request)
            except BaseException as exc:
                self._record(attempt, phase="failed", error=exc)
                raise
            self._record(attempt, phase=_response_phase(response), response=response)
            return response

        async def handle_async(self, request: Any, call_next: Any) -> Any:
            attempt = self._attempt(request)
            self._record(attempt, phase="started")
            try:
                response = await call_next(request)
            except BaseException as exc:
                self._record(attempt, phase="failed", error=exc)
                raise
            self._record(attempt, phase=_response_phase(response), response=response)
            return response

        async def aflush(self, *, timeout: float | None = None) -> None:
            """Wait for confirmed Recorder delivery without closing the sink."""

            effective = synchronous_flush_timeout if timeout is None else timeout
            _validate_timeout(effective)
            if sink.owns_current_loop():
                await sink.flush(timeout=effective)
                return
            future = sink.request_flush_threadsafe()
            await asyncio.wait_for(asyncio.wrap_future(future), timeout=effective)

        def flush(self, *, timeout: float | None = None) -> None:
            """Synchronously wait for delivery when called off the sink loop."""

            effective = synchronous_flush_timeout if timeout is None else timeout
            _validate_timeout(effective)
            if sink.owns_current_loop():
                raise RecorderSinkError(
                    "synchronous Anthropic middleware flush would block the "
                    "Recorder event loop; await middleware.aflush() instead"
                )
            try:
                sink.request_flush_threadsafe().result(timeout=effective)
            except FutureTimeoutError as exc:
                raise TimeoutError("Anthropic Recorder flush timed out") from exc

        async def close(self, *, timeout: float | None = None) -> None:
            """Flush this adapter; the caller retains ownership of ``sink``."""

            await self.aflush(timeout=timeout)

        @staticmethod
        def _attempt(request: Any) -> _Attempt:
            try:
                return _attempt_from_request(request)
            except Exception as exc:  # noqa: BLE001 -- middleware must stay transparent
                sink.disclose_gap(
                    "anthropic_request_conversion_failed",
                    detail=type(exc).__name__,
                )
                return _Attempt(
                    attempt_id=f"anthropic-attempt-{uuid4()}",
                    method="UNKNOWN",
                    route_family="other",
                    retry_index=0,
                    stream=False,
                    model_id=None,
                    request_body=None,
                )

        @staticmethod
        def _record(
            attempt: _Attempt,
            *,
            phase: str,
            response: Any = None,
            error: BaseException | None = None,
        ) -> None:
            try:
                status_code = _status_code(response)
                request_ref = _response_request_ref(response)
                event = adapter_event(
                    framework="anthropic",
                    kind="api_attempt",
                    phase=phase,
                    source_identity=(attempt.attempt_id, phase),
                    run_id=attempt.attempt_id,
                    trace_id=_trace_ref(attempt.attempt_id),
                    attribution=identity,
                    name=attempt.route_family,
                    model_id=attempt.model_id,
                    status=(
                        "error"
                        if error is not None or (status_code is not None and status_code >= 400)
                        else ("running" if phase == "started" else "completed")
                    ),
                    observed_input=(attempt.request_body if phase == "started" else None),
                    metadata={
                        "http_method": attempt.method,
                        "route_family": attempt.route_family,
                        "retry_index": attempt.retry_index,
                        "streaming": attempt.stream,
                        "status_code": status_code,
                        "provider_request_ref": request_ref,
                        "error_type": type(error).__name__ if error is not None else None,
                        "response_observation_boundary": (
                            "headers_returned_body_not_consumed"
                            if response is not None
                            else (
                                "call_next_raised_without_response" if error is not None else None
                            )
                        ),
                        "response_body_capture": "not_parsed",
                        "tool_execution_capture": "not_observable_at_api_boundary",
                    },
                    content_hasher=sink.content_hash,
                    commitment_scheme=sink.commitment_scheme,
                )
                sink.submit_threadsafe(event)
            except Exception as exc:  # noqa: BLE001 -- Recorder cannot break provider calls
                sink.disclose_gap(
                    "anthropic_middleware_capture_failed",
                    detail=type(exc).__name__,
                )

    return LiansAnthropicRecorderMiddleware()


def anthropic_managed_agents_webhook_event(
    verified_event: Any,
    *,
    attribution: RecorderAttribution | None = None,
) -> RecorderEnvelope:
    """Convert an already-verified Managed Agents webhook to one envelope.

    Call Anthropic's ``client.beta.webhooks.unwrap(body, headers=...)`` first
    and pass its typed result here.  This pure converter neither receives HTTP
    requests nor verifies signatures/freshness.  The webhook contains resource
    identifiers rather than the current resource body; fetches performed after
    receipt are outside this evidence event.
    """

    identity = attribution or RecorderAttribution()
    if identity.capture_mode == "full":
        raise ValueError(
            "the Managed Agents webhook converter records metadata only; "
            "use hash_only or metadata_only"
        )
    top = _mapping_view(verified_event)
    provider_event_id = _required_text(
        _public_field(verified_event, top, "id"), "verified_event.id", 512
    )
    outer_type = _required_text(
        _public_field(verified_event, top, "type"), "verified_event.type", 64
    )
    if outer_type != "event":
        raise ValueError("verified_event.type must be 'event'")
    occurred_at = _timestamp_text(
        _public_field(verified_event, top, "created_at"),
        "verified_event.created_at",
    )

    raw_data = _public_field(verified_event, top, "data")
    data = _mapping_view(raw_data)
    provider_event_type = _required_text(
        _public_field(raw_data, data, "type"), "verified_event.data.type", 128
    )
    if _WEBHOOK_TYPE.fullmatch(provider_event_type) is None:
        raise ValueError("verified_event.data.type is not a bounded event type")
    resource_id = _required_text(_public_field(raw_data, data, "id"), "verified_event.data.id", 512)
    organization_id = _optional_text(_public_field(raw_data, data, "organization_id"), 512)
    workspace_id = _optional_text(_public_field(raw_data, data, "workspace_id"), 512)
    resource_kind = provider_event_type.split(".", 1)[0]
    run_id = _opaque_ref("anthropic-managed-resource", resource_id)
    scoped_identity = (
        replace(identity, session_id=run_id)
        if resource_kind == "session" and identity.session_id is None
        else identity
    )
    return adapter_event(
        framework="anthropic_managed_agents",
        kind="webhook",
        phase="observed",
        source_identity=(provider_event_id, provider_event_type, resource_id),
        run_id=run_id,
        trace_id=_trace_ref(provider_event_id),
        attribution=scoped_identity,
        occurred_at=occurred_at,
        name=resource_kind,
        status=provider_event_type.rsplit(".", 1)[-1],
        metadata={
            "provider_event_type": provider_event_type,
            "provider_event_ref": _opaque_ref("anthropic-managed-event", provider_event_id),
            "resource_kind": resource_kind,
            "resource_ref": run_id,
            "organization_ref": (
                _opaque_ref("anthropic-organization", organization_id)
                if organization_id is not None
                else None
            ),
            "workspace_ref": (
                _opaque_ref("anthropic-workspace", workspace_id)
                if workspace_id is not None
                else None
            ),
            "verification_boundary": "anthropic_sdk_unwrap_required_before_conversion",
            "resource_body_capture": "identifier_only_fetch_not_performed",
        },
    )


def _attempt_from_request(request: Any) -> _Attempt:
    method = _optional_text(getattr(request, "method", None), 16) or "UNKNOWN"
    method = method.upper()
    if not method.isascii() or not method.replace("_", "").isalpha():
        method = "UNKNOWN"
    body = getattr(request, "json", None)
    model_id: str | None = None
    if isinstance(body, Mapping):
        model_id = _optional_text(body.get("model"), 512)
    retry_value = getattr(request, "retries_taken", 0)
    retry_index = (
        retry_value
        if isinstance(retry_value, int)
        and not isinstance(retry_value, bool)
        and 0 <= retry_value <= 1_000_000
        else 0
    )
    return _Attempt(
        attempt_id=f"anthropic-attempt-{uuid4()}",
        method=method,
        route_family=_route_family(getattr(request, "url", None)),
        retry_index=retry_index,
        stream=bool(getattr(request, "stream", False)),
        model_id=model_id,
        request_body=body,
    )


def _route_family(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 4_096:
        return "other"
    path = urlsplit(value).path.lower()
    segments = {segment.replace("-", "_") for segment in path.split("/") if segment}
    for family in _KNOWN_ROUTE_FAMILIES:
        if family in segments:
            return family
    return "other"


def _status_code(response: Any) -> int | None:
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    return None


def _response_phase(response: Any) -> str:
    status_code = _status_code(response)
    return "failed" if status_code is not None and status_code >= 400 else "completed"


def _response_request_ref(response: Any) -> str | None:
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter("request-id")
    text = _optional_text(value, 512)
    return _opaque_ref("anthropic-request", text) if text is not None else None


def _public_field(value: Any, exported: Mapping[str, Any], name: str) -> Any:
    if name in exported:
        return exported[name]
    return getattr(value, name, None)


def _mapping_view(value: Any) -> Mapping[str, Any]:
    # The verified SDK model exposes documented attributes. Avoid dumping the
    # whole object (including any future fields) merely to read that fixed set.
    return public_mapping(value) if isinstance(value, Mapping) else {}


def _required_text(value: Any, name: str, maximum: int) -> str:
    text = _optional_text(value, maximum)
    if text is None:
        raise ValueError(f"{name} must be a non-empty string")
    return text


def _optional_text(value: Any, maximum: int) -> str | None:
    if isinstance(value, str):
        if len(value) > maximum:
            return None
        text = value.strip()
        return text or None
    return None


def _timestamp_text(value: Any, name: str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must include a timezone offset")
        return value.isoformat()
    text = _optional_text(value, 128)
    if text is None:
        raise ValueError(f"{name} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return text


def _opaque_ref(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}\x00{value}".encode()).hexdigest()
    return f"{namespace}:sha256:{digest}"


def _trace_ref(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _validate_timeout(value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be finite and positive")


__all__ = [
    "anthropic_managed_agents_webhook_event",
    "build_anthropic_recorder_middleware",
]
