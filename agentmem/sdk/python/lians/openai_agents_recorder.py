"""Universal Recorder adapter for the OpenAI Agents SDK tracing surface.

This module uses the public ``TracingProcessor`` and ``add_trace_processor``
APIs.  It does not patch the Runner or inspect private model state.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256
from typing import Any
from uuid import UUID

from ._recorder_adapter import adapter_event, public_mapping, text_attr
from .recorder_sink import AsyncRecorderSink, RecorderAttribution, RecorderSinkError

_INSTALL_LOCK = threading.Lock()
_INSTALLED_PROCESSOR: Any | None = None
_INSTALLED_SINK: AsyncRecorderSink | None = None
_INSTALLED_ATTRIBUTION: RecorderAttribution | None = None
_INSTALLED_FLUSH_TIMEOUT: float | None = None


def build_openai_agents_recorder_processor(
    sink: AsyncRecorderSink,
    *,
    attribution: RecorderAttribution | None = None,
    synchronous_flush_timeout: float = 10.0,
) -> Any:
    """Return an OpenAI Agents SDK ``TracingProcessor`` writing to ``sink``.

    Trace and span lifecycle callbacks are observable.  Generation/tool values
    exposed in public span data are committed locally using the sink's SHA-256
    or HMAC-SHA-256 policy. Model-private reasoning, disabled tracing,
    ZDR-unavailable tracing, and events outside the Agents SDK tracing surface
    cannot be captured.
    """

    try:
        from agents.tracing import TracingProcessor  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "openai-agents is required for native Recorder tracing. "
            "Install with: pip install 'lians-sdk[openai-agents]'"
        ) from exc

    identity = attribution or RecorderAttribution()
    if identity.capture_mode == "full":
        raise ValueError(
            "native OpenAI Agents callbacks do not transport raw content; "
            "use hash_only or metadata_only"
        )
    if not math.isfinite(synchronous_flush_timeout) or synchronous_flush_timeout <= 0:
        raise ValueError("synchronous_flush_timeout must be finite and positive")

    class LiansOpenAIAgentsRecorderProcessor(TracingProcessor):
        """Public tracing processor; callback methods never block agent execution."""

        def on_trace_start(self, trace: Any) -> None:
            self._safe(self._trace, trace, "started")

        def on_trace_end(self, trace: Any) -> None:
            self._safe(self._trace, trace, "completed")

        def on_span_start(self, span: Any) -> None:
            self._safe(self._span, span, "started")

        def on_span_end(self, span: Any) -> None:
            self._safe(self._span, span, "completed")

        def force_flush(self) -> None:
            self._flush_from_sync_callback(synchronous_flush_timeout)

        async def aflush(self, *, timeout: float | None = None) -> None:
            """Await confirmed sink drainage from the sink's owning loop."""

            await sink.flush(
                timeout=synchronous_flush_timeout if timeout is None else timeout
            )

        def shutdown(self, timeout: float | None = None) -> None:
            # The processor does not own the shared client/sink lifecycle.
            self._flush_from_sync_callback(
                synchronous_flush_timeout if timeout is None else timeout
            )

        @staticmethod
        def _flush_from_sync_callback(timeout: float) -> None:
            try:
                future = sink.request_flush_threadsafe()
                if sink.owns_current_loop():
                    if future.done():
                        future.result()
                        return
                    # The Agents SDK callback is synchronous. Blocking its owner
                    # loop would deadlock, so only schedule here and disclose the
                    # distinction. ``await processor.aflush()`` is the guarantee.
                    sink.disclose_gap(
                        "openai_agents_force_flush_deferred",
                        detail="await processor.aflush() for confirmed drainage",
                    )
                    future.add_done_callback(
                        LiansOpenAIAgentsRecorderProcessor._report_deferred_flush
                    )
                    return
                future.result(timeout=timeout)
            except Exception as exc:  # noqa: BLE001 -- tracing flush must not break a run
                sink.disclose_gap(
                    "openai_agents_force_flush_failed",
                    detail=type(exc).__name__,
                )

        @staticmethod
        def _report_deferred_flush(future: Any) -> None:
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 -- processor callbacks never raise
                sink.disclose_gap(
                    "openai_agents_deferred_flush_failed",
                    detail=type(exc).__name__,
                )

        @staticmethod
        def _safe(callback: Any, value: Any, phase: str) -> None:
            try:
                callback(value, phase)
            except Exception as exc:  # noqa: BLE001 -- tracing callbacks must not raise
                sink.disclose_gap(
                    "openai_agents_callback_conversion_failed",
                    detail=type(exc).__name__,
                )

        @staticmethod
        def _trace(trace: Any, phase: str) -> None:
            trace_ref = _identifier(getattr(trace, "trace_id", None), maximum=512)
            if trace_ref is None:
                sink.disclose_gap(
                    "openai_agents_missing_trace_id",
                    detail=f"trace {phase} callback omitted trace_id",
                )
                return
            trace_id = _trace_component(trace_ref)
            exported = public_mapping(trace)
            group_id = exported.get("group_id") or getattr(trace, "group_id", None)
            session_id = _identifier(group_id, maximum=512)
            scoped_identity = (
                replace(identity, session_id=session_id)
                if session_id is not None and identity.session_id is None
                else identity
            )
            name = _safe_text(
                getattr(trace, "name", None) or exported.get("name"),
                fallback="agent-workflow",
                maximum=512,
            )
            event = adapter_event(
                framework="openai_agents",
                kind="trace",
                phase=phase,
                source_identity=(trace_ref, "trace", phase),
                run_id=trace_ref,
                trace_id=trace_id,
                attribution=scoped_identity,
                name=name,
                status="completed" if phase == "completed" else "running",
                observed_input=exported.get("metadata"),
                metadata={"workflow_name": name[:512]},
                content_hasher=sink.content_hash,
                commitment_scheme=sink.commitment_scheme,
            )
            sink.submit_threadsafe(event)

        @staticmethod
        def _span(span: Any, phase: str) -> None:
            trace_ref = _identifier(getattr(span, "trace_id", None), maximum=512)
            span_ref = _identifier(getattr(span, "span_id", None), maximum=512)
            if trace_ref is None or span_ref is None:
                sink.disclose_gap(
                    "openai_agents_missing_span_identity",
                    detail=f"span {phase} callback omitted trace_id or span_id",
                )
                return
            trace_id = _trace_component(trace_ref)
            span_id = _trace_component(span_ref)
            parent_ref = text_attr(span, "parent_id")
            span_data = getattr(span, "span_data", None)
            exported = public_mapping(span_data)
            kind = _safe_text(
                getattr(span_data, "type", None) or exported.get("type"),
                fallback="span",
                maximum=128,
            )
            error = getattr(span, "error", None)
            status = (
                "error"
                if error is not None
                else ("completed" if phase == "completed" else "running")
            )
            observed_input = _first(exported, "input", "inputs", "prompt", "arguments")
            observed_output = _first(
                exported, "output", "outputs", "result", "response", "completion"
            )
            if error is not None and observed_output is None:
                observed_output = error
            model_id = _mapping_text(exported, "model", "model_id")
            name = _mapping_text(exported, "name", "tool_name") or kind
            event = adapter_event(
                framework="openai_agents",
                kind=kind.replace(" ", "_")[:64],
                phase=phase,
                source_identity=(trace_ref, span_ref, phase),
                run_id=trace_ref,
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=(
                    _trace_component(parent_ref) if parent_ref is not None else None
                ),
                tool_call_id=(span_ref if "function" in kind or "tool" in kind else None),
                attribution=identity,
                occurred_at=(
                    text_attr(span, "ended_at")
                    if phase == "completed"
                    else text_attr(span, "started_at")
                ),
                name=name,
                model_id=model_id,
                status=status,
                observed_input=observed_input,
                observed_output=observed_output,
                metadata={
                    "span_type": kind[:128],
                    "error_type": type(error).__name__ if error is not None else None,
                },
                content_hasher=sink.content_hash,
                commitment_scheme=sink.commitment_scheme,
            )
            sink.submit_threadsafe(event)

    return LiansOpenAIAgentsRecorderProcessor()


def install_openai_agents_recorder(
    sink: AsyncRecorderSink,
    *,
    attribution: RecorderAttribution | None = None,
    synchronous_flush_timeout: float = 10.0,
) -> Any:
    """Install one process-lifetime processor without replacing exporters.

    The Agents SDK exposes processor addition but no public removal API. Calls
    with the same configuration are idempotent; attempting to install another
    Lians processor raises instead of silently duplicating every event.
    """

    try:
        from agents.tracing import add_trace_processor  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "openai-agents is required for native Recorder tracing. "
            "Install with: pip install 'lians-sdk[openai-agents]'"
        ) from exc
    identity = attribution or RecorderAttribution()
    global _INSTALLED_ATTRIBUTION
    global _INSTALLED_FLUSH_TIMEOUT
    global _INSTALLED_PROCESSOR
    global _INSTALLED_SINK
    with _INSTALL_LOCK:
        if _INSTALLED_PROCESSOR is not None:
            if (
                _INSTALLED_SINK is sink
                and _INSTALLED_ATTRIBUTION == identity
                and _INSTALLED_FLUSH_TIMEOUT == synchronous_flush_timeout
            ):
                return _INSTALLED_PROCESSOR
            raise RecorderSinkError(
                "a Lians OpenAI Agents processor is already installed for this "
                "process; reuse it and its sink, because the public Agents SDK "
                "does not expose processor removal"
            )
        processor = build_openai_agents_recorder_processor(
            sink,
            attribution=identity,
            synchronous_flush_timeout=synchronous_flush_timeout,
        )
        add_trace_processor(processor)
        _INSTALLED_PROCESSOR = processor
        _INSTALLED_SINK = sink
        _INSTALLED_ATTRIBUTION = identity
        _INSTALLED_FLUSH_TIMEOUT = synchronous_flush_timeout
        return processor


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if mapping.get(name) is not None:
            return mapping[name]
    return None


def _mapping_text(mapping: Mapping[str, Any], *names: str) -> str | None:
    value = _first(mapping, *names)
    return _safe_text(value, fallback=None, maximum=512)


def _safe_text(value: Any, *, fallback: str | None, maximum: int) -> str | None:
    if isinstance(value, str):
        text = (
            value[:maximum].strip()
            if len(value) > maximum
            else value.strip()
        )
    elif isinstance(value, UUID):
        text = str(value)
    elif isinstance(value, int) and not isinstance(value, bool):
        text = str(value)
    else:
        return fallback
    return text[:maximum] if text else fallback


def _identifier(value: Any, *, maximum: int) -> str | None:
    if isinstance(value, str):
        if len(value) > 4_096:
            return None
        text = value.strip()
    elif isinstance(value, UUID):
        text = str(value)
    elif isinstance(value, int) and not isinstance(value, bool):
        text = str(value)
    else:
        return None
    if not text:
        return None
    if len(text) <= maximum:
        return text
    return "lians-id-v1:" + sha256(text.encode("utf-8")).hexdigest()


def _trace_component(value: str | None) -> str:
    if value is None:
        return "openai-agents"
    if len(value) <= 64:
        return value
    return sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "build_openai_agents_recorder_processor",
    "install_openai_agents_recorder",
]
