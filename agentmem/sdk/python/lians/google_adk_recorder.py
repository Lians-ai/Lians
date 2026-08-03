"""Universal Recorder plugin for Google Agent Development Kit (ADK).

Only the documented ``google.adk.plugins.base_plugin.BasePlugin`` callback
surface is used.  The adapter does not import private ADK modules, mutate
callback values, or retain prompt/tool payloads after local commitment.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import threading
from collections import OrderedDict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import wraps
from typing import Any

from ._recorder_adapter import adapter_event, public_mapping
from .recorder_sink import AsyncRecorderSink, RecorderAttribution


@dataclass(frozen=True)
class _PendingCall:
    token: str
    model_id: str | None = None


@dataclass
class _RunState:
    sequence: int
    pending: dict[str, deque[_PendingCall]]


class _BoundedCorrelation:
    """Bounded, locked pairing state for callbacks without public call IDs."""

    def __init__(
        self,
        sink: AsyncRecorderSink,
        *,
        max_active_runs: int,
        max_pending_calls_per_run: int,
    ) -> None:
        self._sink = sink
        self._max_active_runs = max_active_runs
        self._max_pending_calls_per_run = max_pending_calls_per_run
        self._lock = threading.RLock()
        self._runs: OrderedDict[str, _RunState] = OrderedDict()

    def begin(
        self,
        run_key: str,
        scope: str,
        *,
        model_id: str | None = None,
    ) -> _PendingCall:
        with self._lock:
            state = self._touch(run_key)
            pending = self._next(state, scope, model_id=model_id)
            queue = state.pending.setdefault(scope, deque())
            if len(queue) >= self._max_pending_calls_per_run:
                queue.popleft()
                self._sink.disclose_gap(
                    "google_adk_pending_call_evicted",
                    detail="per-run callback pairing bound reached",
                )
            queue.append(pending)
            return pending

    def finish(self, run_key: str, scope: str) -> _PendingCall:
        with self._lock:
            state = self._touch(run_key)
            queue = state.pending.get(scope)
            if queue:
                pending = queue.popleft()
                if not queue:
                    state.pending.pop(scope, None)
                return pending
            self._sink.disclose_gap(
                "google_adk_unpaired_callback",
                detail="completion or error callback had no retained start",
            )
            return self._next(state, scope)

    def finish_run(self, run_key: str) -> None:
        with self._lock:
            state = self._runs.pop(run_key, None)
        if state is not None and any(state.pending.values()):
            self._sink.disclose_gap(
                "google_adk_run_closed_with_pending_callbacks",
                detail="ADK run ended before all callback pairs were observed",
            )

    def close(self) -> None:
        with self._lock:
            states = tuple(self._runs.values())
            self._runs.clear()
        if states:
            self._sink.disclose_gap(
                "google_adk_plugin_closed_with_active_runs",
                detail=f"{len(states)} bounded run correlation states discarded",
            )
        if any(any(state.pending.values()) for state in states):
            self._sink.disclose_gap(
                "google_adk_plugin_closed_with_pending_callbacks",
                detail="plugin closed before all callback pairs were observed",
            )

    def _touch(self, run_key: str) -> _RunState:
        state = self._runs.pop(run_key, None)
        if state is None:
            state = _RunState(sequence=0, pending={})
        self._runs[run_key] = state
        while len(self._runs) > self._max_active_runs:
            self._runs.popitem(last=False)
            self._sink.disclose_gap(
                "google_adk_run_state_evicted",
                detail="max_active_runs reached",
            )
        return state

    @staticmethod
    def _next(
        state: _RunState,
        scope: str,
        *,
        model_id: str | None = None,
    ) -> _PendingCall:
        state.sequence += 1
        return _PendingCall(
            token=f"{scope}:{state.sequence}",
            model_id=model_id,
        )


@dataclass(frozen=True)
class _ContextRefs:
    invocation_source: str
    run_id: str
    session_id: str | None
    trace_id: str
    agent_name: str
    node_source: str
    node_ref: str | None
    attempt_count: int


def build_google_adk_recorder_plugin(
    sink: AsyncRecorderSink,
    *,
    attribution: RecorderAttribution | None = None,
    name: str = "lians_recorder",
    plaintext_component_names: bool = False,
    max_active_runs: int = 10_000,
    max_pending_calls_per_run: int = 256,
    close_flush_timeout: float = 10.0,
) -> Any:
    """Return a Google ADK ``BasePlugin`` for runner-wide lifecycle capture.

    Register the returned instance in ``App(..., plugins=[plugin])`` (preferred
    by current ADK) or the compatible ``Runner(..., plugins=[plugin])`` path.
    It observes runner, agent, model, and tool success/error callbacks and
    always returns ``None``, so it cannot short-circuit ADK execution.

    Model requests/responses and tool arguments/results are locally committed
    in ``hash_only`` mode.  Component labels default to generic values because
    user-defined agent/tool names can contain tenant data.  Set
    ``plaintext_component_names=True`` only after reviewing those labels.
    """

    try:
        from google.adk.plugins.base_plugin import (  # type: ignore[import-not-found]
            BasePlugin,
        )
    except ImportError as exc:
        raise ImportError(
            "google-adk>=2.6.1 is required for native Recorder plugins. "
            "Install with: pip install 'lians-sdk[google-adk]'"
        ) from exc

    identity = attribution or RecorderAttribution()
    if identity.capture_mode == "full":
        raise ValueError(
            "native Google ADK callbacks do not transport raw content; "
            "use hash_only or metadata_only"
        )
    _positive_int("max_active_runs", max_active_runs)
    _positive_int("max_pending_calls_per_run", max_pending_calls_per_run)
    if not math.isfinite(close_flush_timeout) or close_flush_timeout <= 0:
        raise ValueError("close_flush_timeout must be finite and positive")
    plugin_name = _required_label(name, "name", 128)
    correlation = _BoundedCorrelation(
        sink,
        max_active_runs=max_active_runs,
        max_pending_calls_per_run=max_pending_calls_per_run,
    )

    def guarded_callback(method: Any) -> Any:
        @wraps(method)
        async def callback(*args: Any, **kwargs: Any) -> None:
            try:
                await method(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- never alter ADK execution
                sink.disclose_gap(
                    "google_adk_callback_conversion_failed",
                    detail=f"{method.__name__}:{type(exc).__name__}",
                )
            return None

        return callback

    class LiansGoogleAdkRecorderPlugin(BasePlugin):
        """Public ADK plugin with bounded, non-blocking Recorder submission."""

        def __init__(self) -> None:
            super().__init__(name=plugin_name)

        @guarded_callback
        async def before_run_callback(self, *, invocation_context: Any) -> None:
            refs = self._refs_or_gap(invocation_context, "before_run")
            if refs is None:
                return None
            self._submit(
                refs,
                kind="run",
                phase="started",
                token="runner",
                name="runner",
                status="running",
                observed_input=_public_value(
                    getattr(invocation_context, "user_content", None), sink=sink
                ),
            )
            return None

        @guarded_callback
        async def after_run_callback(self, *, invocation_context: Any) -> None:
            refs = self._refs_or_gap(invocation_context, "after_run")
            if refs is None:
                return None
            self._submit(
                refs,
                kind="run",
                phase="completed",
                token="runner",
                name="runner",
                status="completed",
            )
            correlation.finish_run(refs.run_id)
            return None

        @guarded_callback
        async def on_run_error_callback(
            self,
            *,
            invocation_context: Any,
            error: Exception,
        ) -> None:
            refs = self._refs_or_gap(invocation_context, "run_error")
            if refs is None:
                return None
            self._submit(
                refs,
                kind="run",
                phase="failed",
                token="runner",
                name="runner",
                status="error",
                error=error,
            )
            correlation.finish_run(refs.run_id)
            return None

        @guarded_callback
        async def before_agent_callback(
            self,
            *,
            agent: Any,
            callback_context: Any,
        ) -> None:
            refs = self._refs_or_gap(callback_context, "before_agent")
            if refs is None:
                return None
            agent_source = _component_source(agent, refs.agent_name)
            self._submit(
                refs,
                kind="agent",
                phase="started",
                token=self._agent_token(refs),
                name=_component_label(agent_source, "agent", plaintext_component_names),
                status="running",
                observed_input=_public_value(
                    getattr(callback_context, "user_content", None), sink=sink
                ),
            )
            return None

        @guarded_callback
        async def after_agent_callback(
            self,
            *,
            agent: Any,
            callback_context: Any,
        ) -> None:
            refs = self._refs_or_gap(callback_context, "after_agent")
            if refs is None:
                return None
            agent_source = _component_source(agent, refs.agent_name)
            self._submit(
                refs,
                kind="agent",
                phase="completed",
                token=self._agent_token(refs),
                name=_component_label(agent_source, "agent", plaintext_component_names),
                status="completed",
                observed_output=_public_value(getattr(callback_context, "output", None), sink=sink),
            )
            return None

        @guarded_callback
        async def on_agent_error_callback(
            self,
            *,
            agent: Any,
            callback_context: Any,
            error: Exception,
        ) -> None:
            refs = self._refs_or_gap(callback_context, "agent_error")
            if refs is None:
                return None
            agent_source = _component_source(agent, refs.agent_name)
            self._submit(
                refs,
                kind="agent",
                phase="failed",
                token=self._agent_token(refs),
                name=_component_label(agent_source, "agent", plaintext_component_names),
                status="error",
                error=error,
            )
            return None

        @guarded_callback
        async def before_model_callback(
            self,
            *,
            callback_context: Any,
            llm_request: Any,
        ) -> None:
            refs = self._refs_or_gap(callback_context, "before_model")
            if refs is None:
                return None
            model_id = _optional_label(getattr(llm_request, "model", None), 512)
            pending = correlation.begin(
                refs.run_id,
                self._model_scope(refs),
                model_id=model_id,
            )
            self._submit(
                refs,
                kind="model",
                phase="started",
                token=pending.token,
                name="model",
                model_id=model_id,
                status="running",
                observed_input=_public_value(llm_request, sink=sink),
                pairing="bounded_fifo_without_public_model_call_id",
            )
            return None

        @guarded_callback
        async def after_model_callback(
            self,
            *,
            callback_context: Any,
            llm_response: Any,
        ) -> None:
            refs = self._refs_or_gap(callback_context, "after_model")
            if refs is None:
                return None
            pending = correlation.finish(refs.run_id, self._model_scope(refs))
            self._submit(
                refs,
                kind="model",
                phase="completed",
                token=pending.token,
                name="model",
                model_id=pending.model_id,
                status="completed",
                observed_output=_public_value(llm_response, sink=sink),
                pairing="bounded_fifo_without_public_model_call_id",
            )
            return None

        @guarded_callback
        async def on_model_error_callback(
            self,
            *,
            callback_context: Any,
            llm_request: Any,
            error: Exception,
        ) -> None:
            refs = self._refs_or_gap(callback_context, "model_error")
            if refs is None:
                return None
            pending = correlation.finish(refs.run_id, self._model_scope(refs))
            model_id = pending.model_id or _optional_label(getattr(llm_request, "model", None), 512)
            self._submit(
                refs,
                kind="model",
                phase="failed",
                token=pending.token,
                name="model",
                model_id=model_id,
                status="error",
                error=error,
                pairing="bounded_fifo_without_public_model_call_id",
            )
            return None

        @guarded_callback
        async def before_tool_callback(
            self,
            *,
            tool: Any,
            tool_args: dict[str, Any],
            tool_context: Any,
        ) -> None:
            refs = self._refs_or_gap(tool_context, "before_tool")
            if refs is None:
                return None
            tool_source = _component_source(tool, "tool")
            pending, call_ref, pairing = self._begin_tool(refs, tool_context, tool_source)
            self._submit(
                refs,
                kind="tool",
                phase="started",
                token=pending.token,
                name=_component_label(tool_source, "tool", plaintext_component_names),
                status="running",
                observed_input=tool_args,
                tool_call_id=call_ref,
                pairing=pairing,
            )
            return None

        @guarded_callback
        async def after_tool_callback(
            self,
            *,
            tool: Any,
            tool_args: dict[str, Any],
            tool_context: Any,
            result: dict[str, Any],
        ) -> None:
            refs = self._refs_or_gap(tool_context, "after_tool")
            if refs is None:
                return None
            tool_source = _component_source(tool, "tool")
            pending, call_ref, pairing = self._finish_tool(refs, tool_context, tool_source)
            self._submit(
                refs,
                kind="tool",
                phase="completed",
                token=pending.token,
                name=_component_label(tool_source, "tool", plaintext_component_names),
                status="completed",
                observed_output=result,
                tool_call_id=call_ref,
                pairing=pairing,
            )
            return None

        @guarded_callback
        async def on_tool_error_callback(
            self,
            *,
            tool: Any,
            tool_args: dict[str, Any],
            tool_context: Any,
            error: Exception,
        ) -> None:
            refs = self._refs_or_gap(tool_context, "tool_error")
            if refs is None:
                return None
            tool_source = _component_source(tool, "tool")
            pending, call_ref, pairing = self._finish_tool(refs, tool_context, tool_source)
            self._submit(
                refs,
                kind="tool",
                phase="failed",
                token=pending.token,
                name=_component_label(tool_source, "tool", plaintext_component_names),
                status="error",
                tool_call_id=call_ref,
                error=error,
                pairing=pairing,
            )
            return None

        async def aflush(self, *, timeout: float | None = None) -> None:
            """Wait for confirmed delivery without closing the shared sink."""

            effective = close_flush_timeout if timeout is None else timeout
            if not math.isfinite(effective) or effective <= 0:
                raise ValueError("timeout must be finite and positive")
            if sink.owns_current_loop():
                await sink.flush(timeout=effective)
                return
            future = sink.request_flush_threadsafe()
            await asyncio.wait_for(asyncio.wrap_future(future), timeout=effective)

        async def close(self) -> None:
            """ADK Runner close hook: flush, but do not close the shared sink."""

            correlation.close()
            try:
                await self.aflush()
            except BaseException as exc:
                sink.disclose_gap(
                    "google_adk_plugin_close_flush_failed",
                    detail=type(exc).__name__,
                )
                raise

        @staticmethod
        def _refs_or_gap(context: Any, callback: str) -> _ContextRefs | None:
            try:
                return _context_refs(context)
            except Exception as exc:  # noqa: BLE001 -- observability is transparent
                sink.disclose_gap(
                    "google_adk_callback_context_invalid",
                    detail=f"{callback}:{type(exc).__name__}",
                )
                return None

        @staticmethod
        def _agent_token(refs: _ContextRefs) -> str:
            return f"agent:{refs.node_source}:{refs.agent_name}:attempt-{refs.attempt_count}"

        @staticmethod
        def _model_scope(refs: _ContextRefs) -> str:
            return _opaque_ref(
                "google-adk-model-scope",
                f"{refs.node_source}\x00{refs.agent_name}",
            )

        @staticmethod
        def _tool_scope(refs: _ContextRefs, tool_source: str) -> str:
            return _opaque_ref(
                "google-adk-tool-scope",
                f"{refs.node_source}\x00{tool_source}",
            )

        def _begin_tool(
            self,
            refs: _ContextRefs,
            tool_context: Any,
            tool_source: str,
        ) -> tuple[_PendingCall, str | None, str]:
            source_id = _optional_provider_id(getattr(tool_context, "function_call_id", None))
            if source_id is not None:
                return (
                    _PendingCall(token=f"tool-call:{source_id}"),
                    _opaque_ref("google-adk-tool-call", source_id),
                    "public_function_call_id",
                )
            sink.disclose_gap(
                "google_adk_missing_tool_call_id",
                detail="using bounded FIFO callback pairing",
            )
            pending = correlation.begin(refs.run_id, self._tool_scope(refs, tool_source))
            return pending, None, "bounded_fifo_without_function_call_id"

        def _finish_tool(
            self,
            refs: _ContextRefs,
            tool_context: Any,
            tool_source: str,
        ) -> tuple[_PendingCall, str | None, str]:
            source_id = _optional_provider_id(getattr(tool_context, "function_call_id", None))
            if source_id is not None:
                return (
                    _PendingCall(token=f"tool-call:{source_id}"),
                    _opaque_ref("google-adk-tool-call", source_id),
                    "public_function_call_id",
                )
            pending = correlation.finish(refs.run_id, self._tool_scope(refs, tool_source))
            return pending, None, "bounded_fifo_without_function_call_id"

        @staticmethod
        def _submit(
            refs: _ContextRefs,
            *,
            kind: str,
            phase: str,
            token: str,
            name: str,
            status: str,
            model_id: str | None = None,
            observed_input: Any = None,
            observed_output: Any = None,
            tool_call_id: str | None = None,
            error: BaseException | None = None,
            pairing: str = "public_lifecycle_identity",
        ) -> None:
            try:
                scoped_identity = (
                    replace(identity, session_id=refs.session_id)
                    if identity.session_id is None and refs.session_id is not None
                    else identity
                )
                span_source = f"{refs.invocation_source}:{token}"
                event = adapter_event(
                    framework="google_adk",
                    kind=kind,
                    phase=phase,
                    source_identity=(refs.invocation_source, token, phase),
                    run_id=refs.run_id,
                    trace_id=refs.trace_id,
                    span_id=_trace_ref(span_source),
                    parent_span_id=(
                        None
                        if kind == "run"
                        else (
                            _trace_ref(f"{refs.invocation_source}:runner")
                            if kind == "agent"
                            else _trace_ref(
                                f"{refs.invocation_source}:agent:"
                                f"{refs.node_source}:{refs.agent_name}:"
                                f"attempt-{refs.attempt_count}"
                            )
                        )
                    ),
                    task_id=refs.node_ref,
                    tool_call_id=tool_call_id,
                    attribution=scoped_identity,
                    name=name,
                    model_id=model_id,
                    status=status,
                    observed_input=observed_input,
                    observed_output=observed_output,
                    metadata={
                        "callback_surface": "google.adk.plugins.BasePlugin",
                        "callback_pairing": pairing,
                        "attempt_count": refs.attempt_count,
                        "error_type": type(error).__name__ if error is not None else None,
                        "component_name_capture": (
                            "plaintext_opt_in"
                            if plaintext_component_names
                            else "generic_private_default"
                        ),
                    },
                    content_hasher=sink.content_hash,
                    commitment_scheme=sink.commitment_scheme,
                )
                sink.submit_threadsafe(event)
            except Exception as exc:  # noqa: BLE001 -- plugin cannot alter ADK flow
                sink.disclose_gap(
                    "google_adk_callback_capture_failed",
                    detail=type(exc).__name__,
                )

    return LiansGoogleAdkRecorderPlugin()


def _context_refs(context: Any) -> _ContextRefs:
    invocation_source = _required_provider_id(
        getattr(context, "invocation_id", None), "context.invocation_id"
    )
    session = getattr(context, "session", None)
    session_source = _optional_provider_id(getattr(session, "id", None))
    agent_name = _optional_provider_id(getattr(context, "agent_name", None))
    if agent_name is None:
        agent = getattr(context, "agent", None)
        agent_name = _optional_provider_id(getattr(agent, "name", None)) or "agent"
    node_path = _optional_provider_id(getattr(context, "node_path", None))
    node_run = _optional_provider_id(getattr(context, "run_id", None))
    node_source = node_path or node_run or agent_name
    attempt = getattr(context, "attempt_count", 1)
    attempt_count = (
        attempt
        if isinstance(attempt, int) and not isinstance(attempt, bool) and 1 <= attempt <= 1_000_000
        else 1
    )
    return _ContextRefs(
        invocation_source=invocation_source,
        run_id=_opaque_ref("google-adk-invocation", invocation_source),
        session_id=(
            _opaque_ref("google-adk-session", session_source)
            if session_source is not None
            else None
        ),
        trace_id=_trace_ref(invocation_source),
        agent_name=agent_name,
        node_source=node_source,
        node_ref=(_opaque_ref("google-adk-node", node_source) if node_source else None),
        attempt_count=attempt_count,
    )


def _component_source(value: Any, fallback: str) -> str:
    return _optional_provider_id(getattr(value, "name", None)) or fallback


def _component_label(value: str, fallback: str, plaintext: bool) -> str:
    return value[:512] if plaintext else fallback


def _public_value(value: Any, *, sink: AsyncRecorderSink) -> Any:
    try:
        if value is None or isinstance(
            value,
            (
                str,
                int,
                float,
                bool,
                date,
                datetime,
                bytes,
                bytearray,
                Mapping,
                Sequence,
            ),
        ):
            return value
        exported = public_mapping(value)
    except Exception as exc:  # noqa: BLE001 -- disclose but never break ADK
        sink.disclose_gap(
            "google_adk_public_export_failed",
            detail=type(exc).__name__,
        )
        exported = {}
    return (
        exported if exported else {"type": f"{type(value).__module__}.{type(value).__qualname__}"}
    )


def _required_provider_id(value: Any, name: str) -> str:
    result = _optional_provider_id(value)
    if result is None:
        raise ValueError(f"{name} must be a non-empty public identifier")
    return result


def _optional_provider_id(value: Any) -> str | None:
    if isinstance(value, str):
        if len(value) > 4_096:
            return _opaque_ref("google-adk-long-id", value)
        text = value.strip()
        return text or None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _required_label(value: Any, name: str, maximum: int) -> str:
    result = _optional_label(value, maximum)
    if result is None:
        raise ValueError(f"{name} must be a non-empty string up to {maximum} characters")
    return result


def _optional_label(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str) or len(value) > maximum:
        return None
    text = value.strip()
    return text or None


def _positive_int(name: str, value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _opaque_ref(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}\x00{value}".encode()).hexdigest()
    return f"{namespace}:sha256:{digest}"


def _trace_ref(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = ["build_google_adk_recorder_plugin"]
