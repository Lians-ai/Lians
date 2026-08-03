"""Contract tests for the public SDK Recorder sink and optional native hooks.

These tests are definitions only during the implementation-first build phase;
the comprehensive validation campaign executes them later.
"""
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import builtins
import inspect
import sys
import threading
import types
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

SDK_ROOT = Path(__file__).resolve().parents[1]
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

from lians import (
    AsyncRecorderSink,
    RecorderAttribution,
    RecorderDeliveryError,
    RecorderSinkConfig,
    RecorderSinkError,
    lians_event,
    recorder_content_hash,
    stabilize_recorder_envelope,
    validate_recorder_envelope,
)


class FakeRecorderClient:
    def __init__(
        self,
        *,
        failures: int = 0,
        rejection: bool = False,
        duplicate: bool = False,
    ) -> None:
        self.failures = failures
        self.rejection = rejection
        self.duplicate = duplicate
        self.calls: list[list[dict[str, Any]]] = []

    async def ingest_recorder_batch(
        self, events: list[dict[str, Any]], *, atomic: bool = True
    ) -> dict[str, Any]:
        self.calls.append(events)
        if len(self.calls) <= self.failures:
            raise OSError("ambiguous transport failure")
        if self.rejection:
            return {
                "received": len(events),
                "accepted": 0,
                "duplicates": 0,
                "rejected": 1,
                "results": [],
                "rejections": [{"index": 0, "code": "invalid", "detail": "invalid"}],
                "ready_run_ids": [],
            }
        if self.duplicate:
            return {
                "received": len(events),
                "accepted": 0,
                "duplicates": len(events),
                "rejected": 0,
                "results": [],
                "rejections": [],
                "ready_run_ids": [],
            }
        return {
            "received": len(events),
            "accepted": len(events),
            "duplicates": 0,
            "rejected": 0,
            "results": [],
            "rejections": [],
            "ready_run_ids": [],
        }


def test_stabilize_envelope_is_deterministic_and_does_not_mutate_input() -> None:
    event = {"protocol": "lians", "payload": {"b": 2, "a": 1}}
    first = stabilize_recorder_envelope(event)
    second = stabilize_recorder_envelope(event)

    assert "event_id" not in event
    assert first["event_id"] == second["event_id"]
    assert first["idempotency_key"] == first["event_id"]


def test_content_commitment_can_hide_low_entropy_values_with_hmac() -> None:
    key = b"deployment-held-recorder-key-0001"
    plain = recorder_content_hash("yes")
    first = recorder_content_hash("yes", key=key)
    second = recorder_content_hash("yes", key=key)

    assert first == second
    assert first != plain
    assert len(first) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("flush_interval_seconds", float("nan")),
        ("retry_initial_seconds", float("inf")),
        ("retry_max_seconds", float("-inf")),
        ("retry_jitter_ratio", float("nan")),
    ],
)
def test_sink_config_rejects_non_finite_timing_values(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        RecorderSinkConfig(**{field: value})


@pytest.mark.parametrize(
    "field",
    ["actor", "correlation", "capture", "extensions"],
)
def test_local_schema_rejects_explicit_null_for_default_object_fields(
    field: str,
) -> None:
    event = lians_event("schema-check", {})
    event[field] = None  # type: ignore[literal-required]

    with pytest.raises(RecorderSinkError):
        validate_recorder_envelope(event)


@pytest.mark.asyncio
async def test_invalid_envelope_isolated_before_non_atomic_batch() -> None:
    client = FakeRecorderClient()
    config = RecorderSinkConfig(batch_size=2, flush_interval_seconds=0.01)
    async with AsyncRecorderSink(client, config=config) as sink:
        invalid = await sink.submit(
            lians_event("bad", {"not_json": object()})
        )
        schema_invalid = await sink.submit(  # type: ignore[arg-type]
            {"protocol": "unsupported", "payload": {}}
        )
        valid = await sink.submit(lians_event("good", {"status": "ok"}))
        await sink.flush()

    assert invalid.accepted is False
    assert invalid.reason == "invalid_envelope"
    assert schema_invalid.accepted is False
    assert schema_invalid.reason == "invalid_envelope"
    assert valid.accepted is True
    assert [event["event_type"] for call in client.calls for event in call] == ["good"]


@pytest.mark.asyncio
async def test_cross_thread_ready_callbacks_share_the_total_admission_bound() -> None:
    client = FakeRecorderClient()
    config = RecorderSinkConfig(max_buffered_events=3, batch_size=1)
    sink = AsyncRecorderSink(client, config=config)
    await sink.start()
    submissions: list[Any] = []

    def submit_many() -> None:
        submissions.extend(
            sink.submit_threadsafe(lians_event(f"thread-{index}", {}))
            for index in range(20)
        )

    thread = threading.Thread(target=submit_many)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    snapshot = sink.stats()
    assert snapshot.enqueued == 3
    assert snapshot.buffered == 3

    await asyncio.sleep(0)
    results = [future.result(timeout=1) for future in submissions]
    assert sum(result.accepted for result in results) == 3
    await sink.close(drain=False)


@pytest.mark.asyncio
async def test_batch_window_accumulates_from_first_event_until_deadline() -> None:
    client = FakeRecorderClient()
    config = RecorderSinkConfig(batch_size=2, flush_interval_seconds=0.1)
    async with AsyncRecorderSink(client, config=config) as sink:
        await sink.submit(lians_event("first", {}))
        await asyncio.sleep(0)
        await asyncio.sleep(0.02)
        assert client.calls == []
        await sink.submit(lians_event("second", {}))
        await sink.flush()

    assert len(client.calls) == 1
    assert len(client.calls[0]) == 2


def test_retry_after_supports_http_date_and_never_jitters_below_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lians.recorder_sink as recorder_sink_module

    class Response:
        status_code = 429
        headers = {
            "Retry-After": format_datetime(
                datetime.now(timezone.utc) + timedelta(seconds=3), usegmt=True
            )
        }

    class RateLimited(Exception):
        response = Response()

    class NumericResponse:
        status_code = 429
        headers = {"Retry-After": "3"}

    class NumericRateLimited(Exception):
        response = NumericResponse()

    sink = AsyncRecorderSink(
        FakeRecorderClient(),
        config=RecorderSinkConfig(
            retry_initial_seconds=0.01,
            retry_max_seconds=10,
            retry_jitter_ratio=0.5,
        ),
    )
    floor = recorder_sink_module._retry_after_seconds(RateLimited())
    assert floor is not None and floor > 1
    monkeypatch.setattr(recorder_sink_module.random, "uniform", lambda low, high: low)
    assert sink._retry_delay(1, NumericRateLimited()) == 3
    capped = AsyncRecorderSink(
        FakeRecorderClient(),
        config=RecorderSinkConfig(
            retry_initial_seconds=0.01,
            retry_max_seconds=2,
        ),
    )
    with pytest.raises(RecorderDeliveryError, match="Retry-After exceeds"):
        capped._retry_delay(1, NumericRateLimited())


@pytest.mark.asyncio
async def test_retry_reuses_frozen_event_and_idempotency_identity() -> None:
    client = FakeRecorderClient(failures=1)
    config = RecorderSinkConfig(
        batch_size=1,
        max_delivery_attempts=2,
        retry_initial_seconds=0,
        retry_max_seconds=0,
    )
    async with AsyncRecorderSink(client, config=config) as sink:
        await sink.submit(lians_event("tool.completed", {"status": "completed"}))
        await sink.flush()

    assert len(client.calls) == 2
    assert client.calls[0][0]["event_id"] == client.calls[1][0]["event_id"]
    assert client.calls[0][0]["idempotency_key"] == client.calls[1][0]["idempotency_key"]


@pytest.mark.asyncio
async def test_server_rejection_is_a_payload_free_capture_gap() -> None:
    client = FakeRecorderClient(rejection=True)
    async with AsyncRecorderSink(client) as sink:
        submission = await sink.submit(
            lians_event("tool.completed", {"secret": "must-not-appear-in-gap"})
        )
        await sink.flush()
        gaps = sink.capture_gaps()

    assert gaps[-1].reason == "server_rejected"
    assert gaps[-1].event_id == submission.event_id
    assert "secret" not in repr(gaps[-1])
    assert "must-not-appear" not in repr(gaps[-1])


@pytest.mark.asyncio
async def test_confirmed_duplicate_counts_as_delivered_without_a_gap() -> None:
    client = FakeRecorderClient(duplicate=True)
    async with AsyncRecorderSink(client) as sink:
        await sink.submit(lians_event("tool.completed", {"status": "completed"}))
        await sink.flush()

        stats = sink.stats()
        assert stats.delivered == 1
        assert stats.duplicates == 1
        assert not sink.capture_gaps()


@pytest.mark.asyncio
async def test_halt_policy_surfaces_terminal_delivery_failure() -> None:
    client = FakeRecorderClient(failures=10)
    config = RecorderSinkConfig(
        batch_size=1,
        max_delivery_attempts=1,
        retry_initial_seconds=0,
        retry_max_seconds=0,
        delivery_failure="halt",
    )
    sink = AsyncRecorderSink(client, config=config)
    await sink.start()
    await sink.submit(lians_event("run.completed", {"status": "completed"}))
    with pytest.raises(RecorderDeliveryError):
        await sink.flush()
    with pytest.raises(RecorderDeliveryError):
        await sink.close()


@pytest.mark.asyncio
async def test_synchronous_callback_submission_is_nonblocking_and_bounded() -> None:
    client = FakeRecorderClient()
    config = RecorderSinkConfig(max_buffered_events=1, batch_size=1)
    sink = AsyncRecorderSink(client, config=config)
    await sink.start()
    first = sink.submit_threadsafe(lians_event("one", {})).result()
    second = sink.submit_threadsafe(lians_event("two", {})).result()
    await asyncio.sleep(0)
    await sink.close(drain=False)

    assert first.accepted is True
    assert second.accepted is False
    assert second.reason == "callback_backpressure"
    assert any(gap.reason == "callback_backpressure" for gap in sink.capture_gaps())


def test_native_adapter_attribution_names_claims_explicitly() -> None:
    attribution = RecorderAttribution(
        claimed_agent_id="agent-7",
        claimed_principal_id="caller-asserted-principal",
        claimed_roles=("reviewer",),
    )

    assert attribution.claimed_agent_id == "agent-7"
    assert attribution.claimed_principal_id == "caller-asserted-principal"


def test_openai_adapter_extra_error_is_actionable_when_dependency_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lians import build_openai_agents_recorder_processor

    real_import = builtins.__import__

    def without_agents(name: str, *args: Any, **kwargs: Any) -> Any:
        if name in {"agents", "agents.tracing"}:
            raise ImportError("synthetic missing optional dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_agents)
    with pytest.raises(ImportError, match=r"lians-sdk\[openai-agents\]"):
        build_openai_agents_recorder_processor(AsyncRecorderSink(FakeRecorderClient()))


def test_langchain_handler_uses_public_optional_surface() -> None:
    callbacks = pytest.importorskip("langchain_core.callbacks")
    from lians import build_langchain_recorder_handler

    handler = build_langchain_recorder_handler(AsyncRecorderSink(FakeRecorderClient()))
    assert isinstance(handler, callbacks.AsyncCallbackHandler)


@pytest.mark.asyncio
async def test_langchain_callback_locally_hashes_content_and_preserves_correlation() -> None:
    pytest.importorskip("langchain_core.callbacks")
    from lians import build_langchain_recorder_handler

    client = FakeRecorderClient()
    root = uuid4()
    child = uuid4()
    async with AsyncRecorderSink(client) as sink:
        handler = build_langchain_recorder_handler(
            sink,
            attribution=RecorderAttribution(claimed_agent_id="reviewer"),
        )
        await handler.on_chain_start(
            {"name": "root"},
            {"secret": "never-cross-the-sdk-boundary"},
            run_id=root,
            metadata={"thread_id": "thread-7"},
        )
        await handler.on_tool_start(
            {"name": "approve"},
            "private arguments",
            run_id=child,
            parent_run_id=root,
        )
        await handler.on_tool_end(
            "private result",
            run_id=child,
            parent_run_id=root,
        )
        await handler.on_chain_end({"approved": True}, run_id=root)
        await sink.flush()

    events = [event for call in client.calls for event in call]
    serialized = repr(events)
    assert "never-cross-the-sdk-boundary" not in serialized
    assert "private arguments" not in serialized
    assert "private result" not in serialized
    assert all(event["correlation"]["run_id"] == str(root) for event in events)
    tool_events = [event for event in events if ".tool." in str(event.get("event_type"))]
    assert all(event["correlation"]["span_id"] == str(child) for event in tool_events)
    assert all(event["correlation"]["parent_span_id"] == str(root) for event in tool_events)
    assert any(len(event["payload"].get("input_hash", "")) == 64 for event in events)


def test_crewai_listener_uses_public_optional_surface() -> None:
    pytest.importorskip("crewai")
    from lians import build_crewai_recorder_listener

    listener_factory = build_crewai_recorder_listener
    assert callable(listener_factory)


@pytest.mark.asyncio
async def test_langchain_run_state_is_locked_and_bounded() -> None:
    pytest.importorskip("langchain_core.callbacks")
    from lians import build_langchain_recorder_handler

    client = FakeRecorderClient()
    async with AsyncRecorderSink(client) as sink:
        handler = build_langchain_recorder_handler(sink, max_active_runs=2)
        for index in range(3):
            await handler.on_custom_event(
                "checkpoint",
                {"index": index},
                run_id=uuid4(),
            )
        await sink.flush()
        assert handler.active_run_count == 2
        assert any(
            gap.reason == "langchain_state_evicted" for gap in sink.capture_gaps()
        )


def test_openai_install_is_process_lifetime_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lians.openai_agents_recorder as openai_recorder
    from lians import install_openai_agents_recorder

    tracing = types.ModuleType("agents.tracing")

    class TracingProcessor:
        pass

    installed: list[Any] = []
    tracing.TracingProcessor = TracingProcessor  # type: ignore[attr-defined]
    tracing.add_trace_processor = installed.append  # type: ignore[attr-defined]
    agents = types.ModuleType("agents")
    agents.tracing = tracing  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agents", agents)
    monkeypatch.setitem(sys.modules, "agents.tracing", tracing)
    monkeypatch.setattr(openai_recorder, "_INSTALLED_PROCESSOR", None)
    monkeypatch.setattr(openai_recorder, "_INSTALLED_SINK", None)
    monkeypatch.setattr(openai_recorder, "_INSTALLED_ATTRIBUTION", None)
    monkeypatch.setattr(openai_recorder, "_INSTALLED_FLUSH_TIMEOUT", None)

    sink = AsyncRecorderSink(FakeRecorderClient())
    first = install_openai_agents_recorder(sink)
    second = install_openai_agents_recorder(sink)

    assert first is second
    assert installed == [first]
    with pytest.raises(RecorderSinkError, match="already installed"):
        install_openai_agents_recorder(AsyncRecorderSink(FakeRecorderClient()))


@pytest.mark.asyncio
async def test_openai_owner_loop_force_flush_is_explicitly_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lians import build_openai_agents_recorder_processor

    tracing = types.ModuleType("agents.tracing")

    class TracingProcessor:
        pass

    tracing.TracingProcessor = TracingProcessor  # type: ignore[attr-defined]
    agents = types.ModuleType("agents")
    agents.tracing = tracing  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agents", agents)
    monkeypatch.setitem(sys.modules, "agents.tracing", tracing)

    sink = AsyncRecorderSink(FakeRecorderClient())
    await sink.start()
    processor = build_openai_agents_recorder_processor(sink)
    processor.force_flush()
    assert any(
        gap.reason == "openai_agents_force_flush_deferred"
        for gap in sink.capture_gaps()
    )
    await processor.aflush()
    await sink.close()


@pytest.mark.asyncio
async def test_crewai_identity_is_replay_stable_private_and_unregisters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lians import build_crewai_recorder_listener

    class EventBus:
        def __init__(self) -> None:
            self.handlers: dict[type[Any], list[Any]] = {}

        def on(self, event_class: type[Any]) -> Any:
            def register(handler: Any) -> Any:
                self.handlers.setdefault(event_class, []).append(handler)
                return handler

            return register

        def off(self, event_class: type[Any], handler: Any) -> None:
            self.handlers[event_class].remove(handler)

        def emit(self, source: Any, event: Any) -> None:
            for handler in tuple(self.handlers.get(type(event), [])):
                handler(source, event)

    event_bus = EventBus()

    class BaseEventListener:
        def __init__(self) -> None:
            self.setup_listeners(event_bus)

    class PublicEvent:
        def __init__(self, **values: Any) -> None:
            self.values = values
            self.event_id = values.get("event_id")
            self.timestamp = values.get("timestamp")

        def model_dump(self, mode: str = "python") -> dict[str, Any]:
            return dict(self.values)

    crew_events = types.ModuleType("crewai.events")
    crew_events.BaseEventListener = BaseEventListener  # type: ignore[attr-defined]
    event_names = (
        "CrewKickoffStartedEvent",
        "CrewKickoffCompletedEvent",
        "CrewKickoffFailedEvent",
        "AgentExecutionStartedEvent",
        "AgentExecutionCompletedEvent",
        "AgentExecutionErrorEvent",
        "TaskStartedEvent",
        "TaskCompletedEvent",
        "TaskFailedEvent",
        "ToolUsageStartedEvent",
        "ToolUsageFinishedEvent",
        "ToolUsageErrorEvent",
        "LLMCallStartedEvent",
        "LLMCallCompletedEvent",
        "LLMCallFailedEvent",
    )
    event_classes: dict[str, type[Any]] = {}
    for event_name in event_names:
        event_class = type(event_name, (PublicEvent,), {})
        event_classes[event_name] = event_class
        setattr(crew_events, event_name, event_class)
    crewai = types.ModuleType("crewai")
    crewai.events = crew_events  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "crewai", crewai)
    monkeypatch.setitem(sys.modules, "crewai.events", crew_events)

    client = FakeRecorderClient()
    config = RecorderSinkConfig(batch_size=2, flush_interval_seconds=0.01)
    async with AsyncRecorderSink(client, config=config) as sink:
        listener = build_crewai_recorder_listener(sink, run_id="crew-run")
        assert all(
            len(inspect.signature(handler).parameters) == 2
            for handlers in event_bus.handlers.values()
            for handler in handlers
        )
        event = event_classes["TaskStartedEvent"](
            event_id="public-event-7",
            timestamp="2026-08-02T12:00:00+00:00",
            type="task_started",
            task_id="task-7",
            task_name="private customer escalation description",
        )
        event_bus.emit(object(), event)
        event_bus.emit(object(), event)
        await sink.flush()
        listener.close()
        event_bus.emit(object(), event)
        await sink.flush()

    events = [item for call in client.calls for item in call]
    assert len(events) == 2
    assert events[0]["event_id"] == events[1]["event_id"]
    assert "private customer escalation description" not in repr(events)
    assert events[0]["payload"]["name"] == "task"
    assert not any(event_bus.handlers.values())
