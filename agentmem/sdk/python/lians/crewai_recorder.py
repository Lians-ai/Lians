"""Universal Recorder listener for CrewAI's public event bus."""

from __future__ import annotations

import math
import threading
import weakref
from collections.abc import Callable, Mapping
from datetime import date, datetime
from hashlib import sha256
from types import TracebackType
from typing import Any
from uuid import UUID

from ._recorder_adapter import adapter_event, public_mapping
from .recorder_sink import (
    AsyncRecorderSink,
    RecorderAttribution,
    RecorderSinkError,
    recorder_content_hash,
)

CrewRunId = str | Callable[[Any, Any], str]
CrewSourceFilter = Callable[[Any, Any], bool]


class _CrewBusState:
    def __init__(self, event_bus: Any) -> None:
        self.event_bus = event_bus
        self.listeners: weakref.WeakSet[Any] = weakref.WeakSet()
        self.handlers: dict[Any, Callable[..., None]] = {}


_CREW_REGISTRY_LOCK = threading.RLock()
_CREW_BUS_STATES: dict[int, _CrewBusState] = {}


def _crew_dispatcher(
    state: _CrewBusState, boundary: str
) -> Callable[[Any, Any], None]:
    # CrewAI current versions infer whether to pass RuntimeState by counting
    # parameters. Keep this public handler's signature at exactly two.
    def dispatch(source: Any, event: Any) -> None:
        with _CREW_REGISTRY_LOCK:
            listeners = tuple(state.listeners)
        for listener in listeners:
            listener._capture(source, event, boundary)

    return dispatch


def build_crewai_recorder_listener(
    sink: AsyncRecorderSink,
    *,
    run_id: CrewRunId,
    attribution: RecorderAttribution | None = None,
    source_filter: CrewSourceFilter | None = None,
    plaintext_component_names: bool = False,
) -> Any:
    """Create and register a closeable CrewAI ``BaseEventListener``.

    ``run_id`` may be a stable identifier for one crew execution or a resolver
    for applications running multiple crews through the process-global event
    bus. Use ``source_filter`` to prevent unrelated crews from entering the
    same evidence run. Call ``close()``/``unregister()`` (or use ``with``) when
    the listener's scope ends.

    Task descriptions, task names, crew names, and agent roles are not copied
    into Recorder metadata by default. Set ``plaintext_component_names=True``
    only when those labels are approved for evidence storage. Raw event values
    are committed locally and never enter the sink under ``hash_only`` capture.
    """

    try:
        import crewai.events as crew_events  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "crewai is required for native Recorder event listening. "
            "Install with: pip install 'lians-sdk[crewai]'"
        ) from exc

    required = {
        "BaseEventListener": "listener",
        "CrewKickoffStartedEvent": "crew.started",
        "CrewKickoffCompletedEvent": "crew.completed",
        "CrewKickoffFailedEvent": "crew.failed",
        "AgentExecutionStartedEvent": "agent.started",
        "AgentExecutionCompletedEvent": "agent.completed",
        "AgentExecutionErrorEvent": "agent.failed",
        "TaskStartedEvent": "task.started",
        "TaskCompletedEvent": "task.completed",
        "TaskFailedEvent": "task.failed",
        "ToolUsageStartedEvent": "tool.started",
        "ToolUsageFinishedEvent": "tool.completed",
        "ToolUsageErrorEvent": "tool.failed",
        "LLMCallStartedEvent": "llm.started",
        "LLMCallCompletedEvent": "llm.completed",
        "LLMCallFailedEvent": "llm.failed",
    }
    missing = [name for name in required if not hasattr(crew_events, name)]
    if missing:
        raise ImportError(
            "the installed crewai version does not expose the public event-listener "
            f"surface required by Lians (missing: {', '.join(missing)}). "
            "Upgrade with: pip install --upgrade 'lians-sdk[crewai]'"
        )

    base_listener = crew_events.BaseEventListener
    identity = attribution or RecorderAttribution()
    if identity.capture_mode == "full":
        raise ValueError(
            "native CrewAI listeners do not transport raw content; "
            "use hash_only or metadata_only"
        )

    class LiansCrewAIRecorderListener(base_listener):
        """Scoped listener registered on CrewAI's process-global event bus."""

        def __init__(self) -> None:
            self._lifecycle_lock = threading.RLock()
            self._event_bus: Any | None = None
            self._bus_state: _CrewBusState | None = None
            self._closed = False
            super().__init__()

        def setup_listeners(self, crewai_event_bus: Any) -> None:
            with self._lifecycle_lock:
                self._event_bus = crewai_event_bus
            with _CREW_REGISTRY_LOCK:
                state = _CREW_BUS_STATES.get(id(crewai_event_bus))
                if state is None or state.event_bus is not crewai_event_bus:
                    state = _CrewBusState(crewai_event_bus)
                    _CREW_BUS_STATES[id(crewai_event_bus)] = state
                state.listeners.add(self)
                self._bus_state = state
                for class_name, boundary in required.items():
                    if class_name == "BaseEventListener":
                        continue
                    event_class = getattr(crew_events, class_name)
                    if event_class in state.handlers:
                        continue

                    dispatch = _crew_dispatcher(state, boundary)
                    registered = crewai_event_bus.on(event_class)(dispatch)
                    state.handlers[event_class] = (
                        registered if callable(registered) else dispatch
                    )

        def close(self) -> None:
            """Idempotently unregister all handlers from the global event bus."""

            with self._lifecycle_lock:
                if self._closed:
                    return
                self._closed = True
                event_bus = self._event_bus
                state = self._bus_state
                self._bus_state = None
            if event_bus is None or state is None:
                return
            failures = 0
            with _CREW_REGISTRY_LOCK:
                state.listeners.discard(self)
                if state.listeners:
                    return
                off = getattr(event_bus, "off", None)
                if not callable(off):
                    # CrewAI 1.0 has no public off(). Keep one bounded dispatcher
                    # per event type; its WeakSet is empty, so this listener is
                    # fully inactive and is not retained by the process bus.
                    return
                for event_class, handler in tuple(state.handlers.items()):
                    try:
                        off(event_class, handler)
                    except Exception:  # noqa: BLE001 -- attempt every registration
                        failures += 1
                    else:
                        state.handlers.pop(event_class, None)
                if not state.handlers:
                    _CREW_BUS_STATES.pop(id(event_bus), None)
            if failures:
                raise RecorderSinkError(
                    f"CrewAI listener failed to unregister {failures} dispatcher(s)"
                )

        def flush_callbacks(self, *, timeout: float | None = 30.0) -> None:
            """Wait for CrewAI's already-emitted callbacks when supported."""

            if timeout is not None and (
                not math.isfinite(timeout) or timeout < 0
            ):
                raise ValueError("timeout must be finite and nonnegative, or None")
            with self._lifecycle_lock:
                if self._closed:
                    raise RecorderSinkError("CrewAI listener is already closed")
                event_bus = self._event_bus
            flush = getattr(event_bus, "flush", None)
            if not callable(flush):
                sink.disclose_gap(
                    "crewai_callback_flush_unavailable",
                    detail="installed CrewAI has no public event-bus flush()",
                )
                raise RecorderSinkError(
                    "the installed CrewAI version cannot confirm pending callback "
                    "drain; keep the listener until process shutdown or upgrade CrewAI"
                )
            try:
                completed = flush(timeout=timeout)
            except Exception as exc:
                sink.disclose_gap(
                    "crewai_callback_flush_failed",
                    detail=type(exc).__name__,
                )
                raise RecorderSinkError("CrewAI callback drain failed") from exc
            if completed is False:
                sink.disclose_gap(
                    "crewai_callback_flush_timeout",
                    detail="CrewAI event-bus callback drain timed out",
                )
                raise RecorderSinkError("CrewAI callback drain timed out")

        unregister = close

        def __enter__(self) -> Any:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            try:
                self.close()
            except RecorderSinkError as close_error:
                if exc is None:
                    raise
                sink.disclose_gap(
                    "crewai_listener_close_failed",
                    detail=type(close_error).__name__,
                )

        def _capture(self, source: Any, event: Any, boundary: str) -> None:
            try:
                with self._lifecycle_lock:
                    if self._closed:
                        return
                if source_filter is not None and not source_filter(source, event):
                    return
                resolved_run = run_id(source, event) if callable(run_id) else run_id
                if not isinstance(resolved_run, str):
                    sink.disclose_gap(
                        "crewai_invalid_run_id",
                        detail=f"resolver returned {type(resolved_run).__name__}",
                    )
                    return
                resolved_run = resolved_run.strip()
                if not resolved_run:
                    sink.disclose_gap(
                        "crewai_missing_run_id",
                        detail=f"{boundary} resolver returned an empty identifier",
                    )
                    return
                if len(resolved_run) > 512:
                    resolved_run = (
                        "lians:crewai-run:v1:"
                        + recorder_content_hash(
                            ["crewai-run", resolved_run],
                            max_depth=sink.config.max_value_depth,
                            max_items=sink.config.max_container_items,
                            max_bytes=sink.config.max_content_hash_bytes,
                        )
                    )
                    sink.disclose_gap(
                        "crewai_run_id_hashed",
                        detail="run identifier exceeded 512 characters",
                    )
                kind, phase = boundary.split(".", 1)
                data = public_mapping(event)
                event_type = _event_type(data, event)
                timestamp = data.get("timestamp") or getattr(event, "timestamp", None)
                public_event_id = _mapping_identifier(data, "event_id") or _safe_attr_id(
                    event, "event_id"
                )
                source_fingerprint = _mapping_identifier(
                    data, "source_fingerprint", "fingerprint"
                )
                entity_identity = _entity_identity(data, source)
                entity_ref = f"{sink.commitment_scheme}:{sink.content_hash(entity_identity)}"
                source_identity = _source_identity(
                    sink,
                    resolved_run=resolved_run,
                    boundary=boundary,
                    event_type=event_type,
                    public_event_id=public_event_id,
                    source_fingerprint=source_fingerprint,
                    timestamp=timestamp,
                    data=data,
                    entity_identity=entity_identity,
                )
                failed = phase == "failed"
                completed = phase == "completed"
                event_envelope = adapter_event(
                    framework="crewai",
                    kind=kind,
                    phase=phase,
                    source_identity=source_identity,
                    run_id=resolved_run,
                    trace_id=_trace_component(resolved_run) or "crewai-run",
                    span_id=_span_id(data, kind, public_event_id),
                    parent_span_id=_trace_component(
                        _mapping_identifier(
                            data,
                            "parent_event_id",
                            "parent_id",
                            "parent_run_id",
                        )
                    ),
                    task_id=_mapping_identifier(data, "task_id", "task_uuid"),
                    tool_call_id=(
                        _mapping_identifier(data, "tool_call_id", "call_id")
                        if kind == "tool"
                        else None
                    ),
                    attribution=identity,
                    occurred_at=timestamp,
                    name=_event_name(data, kind) if plaintext_component_names else kind,
                    model_id=_mapping_text(data, "model", "model_name", "model_id"),
                    status="error" if failed else ("completed" if completed else "running"),
                    observed_input=data if not completed and not failed else None,
                    observed_output=data if completed or failed else None,
                    metadata={
                        "event_type": event_type,
                        "entity_ref": entity_ref,
                        "error_type": _error_type(data) if failed else None,
                        "public_event_identity": public_event_id is not None,
                    },
                    content_hasher=sink.content_hash,
                    commitment_scheme=sink.commitment_scheme,
                )
                sink.submit_threadsafe(event_envelope)
            except Exception as exc:  # noqa: BLE001 -- callback must not break the crew
                sink.disclose_gap(
                    "crewai_callback_conversion_failed",
                    detail=type(exc).__name__,
                )

    return LiansCrewAIRecorderListener()


def _source_identity(
    sink: AsyncRecorderSink,
    *,
    resolved_run: str,
    boundary: str,
    event_type: str,
    public_event_id: str | None,
    source_fingerprint: str | None,
    timestamp: Any,
    data: Mapping[str, Any],
    entity_identity: tuple[str, ...],
) -> tuple[Any, ...]:
    if public_event_id is not None:
        return (resolved_run, boundary, "event_id", public_event_id)
    emission_sequence = data.get("emission_sequence")
    stable_data_hash = sink.content_hash(data)
    return (
        resolved_run,
        boundary,
        event_type,
        source_fingerprint or "no-source-fingerprint",
        _timestamp_identity(timestamp),
        (
            emission_sequence
            if isinstance(emission_sequence, int) and not isinstance(emission_sequence, bool)
            else None
        ),
        entity_identity,
        sink.commitment_scheme,
        stable_data_hash,
    )


def _event_type(mapping: Mapping[str, Any], event: Any) -> str:
    value = mapping.get("type") or getattr(event, "type", None)
    if isinstance(value, str):
        text = value[:128].strip()
        if text:
            return text
    return type(event).__name__[:128]


def _mapping_text(mapping: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = mapping.get(name)
        if isinstance(value, str):
            text = value[:512].strip()
            if text:
                return text
        if isinstance(value, UUID):
            return str(value)
    return None


def _mapping_identifier(mapping: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = mapping.get(name)
        text = _identifier_text(value)
        if text is not None:
            return _bounded_identifier(text)
    return None


def _safe_attr_id(value: Any, name: str) -> str | None:
    return _bounded_identifier(_identifier_text(getattr(value, name, None)))


def _identifier_text(value: Any) -> str | None:
    if isinstance(value, str):
        if len(value) > 4_096:
            return None
        text = value.strip()
        return text or None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _bounded_identifier(value: str | None) -> str | None:
    if value is None or len(value) <= 512:
        return value
    return "lians-id-v1:" + sha256(value.encode("utf-8")).hexdigest()


def _entity_identity(mapping: Mapping[str, Any], source: Any) -> tuple[str, ...]:
    identifiers = tuple(
        value
        for value in (
            _mapping_identifier(mapping, "crew_id"),
            _mapping_identifier(mapping, "task_id", "task_uuid"),
            _mapping_identifier(mapping, "tool_call_id", "call_id"),
            _mapping_identifier(mapping, "agent_id"),
            _mapping_identifier(mapping, "flow_id"),
            _mapping_identifier(mapping, "id"),
        )
        if value is not None
    )
    return identifiers or (f"{type(source).__module__}.{type(source).__qualname__}",)


def _event_name(mapping: Mapping[str, Any], fallback: str) -> str:
    return (
        _mapping_text(mapping, "tool_name", "crew_name", "task_name", "agent_role", "name")
        or fallback
    )


def _span_id(
    mapping: Mapping[str, Any],
    kind: str,
    public_event_id: str | None,
) -> str:
    candidate = _mapping_identifier(
        mapping,
        "task_id",
        "task_uuid",
        "tool_call_id",
        "call_id",
        "agent_id",
        "started_event_id",
    ) or public_event_id
    return _trace_component(candidate) or _trace_component(kind) or "crewai-event"


def _trace_component(value: str | None) -> str | None:
    if value is None or len(value) <= 64:
        return value
    return sha256(value.encode("utf-8")).hexdigest()


def _timestamp_identity(value: Any) -> str:
    if isinstance(value, str):
        return value[:128]
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:128]
    return f"{type(value).__module__}.{type(value).__qualname__}"


def _error_type(mapping: Mapping[str, Any]) -> str | None:
    error = mapping.get("error") or mapping.get("exception")
    return type(error).__name__ if error is not None else None


__all__ = ["CrewRunId", "CrewSourceFilter", "build_crewai_recorder_listener"]
