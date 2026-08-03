from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest
from lians import (
    AsyncRecorderSink,
    RecorderAttribution,
    anthropic_managed_agents_webhook_event,
    build_anthropic_recorder_middleware,
    build_google_adk_recorder_plugin,
)


class FakeRecorderClient:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    async def ingest_recorder_batch(
        self,
        events: list[dict[str, Any]],
        *,
        atomic: bool = True,
    ) -> dict[str, Any]:
        self.calls.append(events)
        return {
            "received": len(events),
            "accepted": len(events),
            "duplicates": 0,
            "rejected": 0,
            "results": [],
            "rejections": [],
            "ready_run_ids": [],
        }


def _install_fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    anthropic = types.ModuleType("anthropic")

    class Middleware:
        pass

    anthropic.Middleware = Middleware  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)


def _install_fake_google_adk(monkeypatch: pytest.MonkeyPatch) -> None:
    google = types.ModuleType("google")
    adk = types.ModuleType("google.adk")
    plugins = types.ModuleType("google.adk.plugins")
    base_plugin = types.ModuleType("google.adk.plugins.base_plugin")

    class BasePlugin:
        def __init__(self, name: str) -> None:
            self.name = name

    base_plugin.BasePlugin = BasePlugin  # type: ignore[attr-defined]
    plugins.base_plugin = base_plugin  # type: ignore[attr-defined]
    adk.plugins = plugins  # type: ignore[attr-defined]
    google.adk = adk  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.adk", adk)
    monkeypatch.setitem(sys.modules, "google.adk.plugins", plugins)
    monkeypatch.setitem(sys.modules, "google.adk.plugins.base_plugin", base_plugin)


@pytest.mark.asyncio
async def test_anthropic_middleware_hashes_request_without_parsing_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_anthropic(monkeypatch)
    client = FakeRecorderClient()
    request = SimpleNamespace(
        method="post",
        url="/v1/messages?api_key=must-not-be-recorded",
        json={
            "model": "claude-test",
            "messages": [{"role": "user", "content": "private prompt"}],
        },
        retries_taken=1,
        stream=False,
        headers={"x-api-key": "credential-must-not-be-recorded"},
    )
    response = SimpleNamespace(
        status_code=200,
        headers={"request-id": "req_private_provider_identifier"},
        body="private model output",
    )
    async with AsyncRecorderSink(client) as sink:
        middleware = build_anthropic_recorder_middleware(
            sink,
            attribution=RecorderAttribution(claimed_agent_id="reviewer"),
        )
        assert middleware.handle(request, lambda value: response) is response
        await middleware.aflush()

    events = [event for batch in client.calls for event in batch]
    serialized = repr(events)
    assert len(events) == 2
    assert "private prompt" not in serialized
    assert "private model output" not in serialized
    assert "credential-must-not-be-recorded" not in serialized
    assert "must-not-be-recorded" not in serialized
    assert "req_private_provider_identifier" not in serialized
    assert events[0]["payload"]["input_hash"]
    # None-valued commitments are omitted by the canonical envelope serializer.
    assert events[1]["payload"].get("output_hash") is None
    assert events[1]["payload"]["status_code"] == 200
    assert events[0]["payload"]["retry_index"] == 1


@pytest.mark.asyncio
async def test_anthropic_middleware_marks_http_error_response_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_anthropic(monkeypatch)
    client = FakeRecorderClient()
    request = SimpleNamespace(
        method="post",
        url="/v1/messages",
        json={"model": "claude-test"},
        retries_taken=0,
        stream=True,
    )
    response = SimpleNamespace(status_code=429, headers={"request-id": "request-private"})
    async with AsyncRecorderSink(client) as sink:
        middleware = build_anthropic_recorder_middleware(sink)
        assert middleware.handle(request, lambda value: response) is response
        await middleware.aflush()

    events = [event for batch in client.calls for event in batch]
    assert events[1]["event_type"] == "anthropic.api_attempt.failed"
    assert events[1]["payload"]["status"] == "error"
    assert events[1]["payload"]["response_observation_boundary"] == (
        "headers_returned_body_not_consumed"
    )


def test_managed_agents_webhook_converter_requires_verified_shape_and_hashes_ids() -> None:
    verified = {
        "type": "event",
        "id": "event_provider_private",
        "created_at": "2026-08-02T12:00:00Z",
        "data": {
            "type": "session.status_idled",
            "id": "session_provider_private",
            "organization_id": "organization_provider_private",
            "workspace_id": "workspace_provider_private",
        },
    }

    first = anthropic_managed_agents_webhook_event(verified)
    second = anthropic_managed_agents_webhook_event(verified)

    assert first["event_id"] == second["event_id"]
    assert first["idempotency_key"] == second["idempotency_key"]
    assert "provider_private" not in repr(first)
    assert first["payload"]["provider_event_type"] == "session.status_idled"
    assert first["payload"]["verification_boundary"].endswith("required_before_conversion")
    with pytest.raises(ValueError, match="verified_event.type"):
        anthropic_managed_agents_webhook_event({**verified, "type": "unverified"})


@pytest.mark.asyncio
async def test_google_adk_plugin_uses_public_callbacks_and_private_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_google_adk(monkeypatch)
    client = FakeRecorderClient()
    context = SimpleNamespace(
        invocation_id="invocation_provider_private",
        session=SimpleNamespace(id="session_provider_private"),
        agent_name="claims_agent_private_name",
        node_path="claims_agent_private_name/1",
        run_id="1",
        attempt_count=1,
        user_content={"text": "private user prompt"},
        output={"text": "private agent output"},
        function_call_id="tool_call_provider_private",
    )
    agent = SimpleNamespace(name="claims_agent_private_name")
    model_request = SimpleNamespace(
        model="gemini-test",
        model_dump=lambda mode="python": {"contents": ["private model prompt"]},
    )
    model_response = SimpleNamespace(
        model_dump=lambda mode="python": {"content": "private model output"},
    )
    tool = SimpleNamespace(name="lookup_customer_private_name")

    async with AsyncRecorderSink(client) as sink:
        plugin = build_google_adk_recorder_plugin(sink)
        await plugin.before_run_callback(invocation_context=context)
        await plugin.before_agent_callback(agent=agent, callback_context=context)
        await plugin.before_model_callback(callback_context=context, llm_request=model_request)
        await plugin.after_model_callback(callback_context=context, llm_response=model_response)
        await plugin.before_tool_callback(
            tool=tool,
            tool_args={"customer": "private tool argument"},
            tool_context=context,
        )
        await plugin.after_tool_callback(
            tool=tool,
            tool_args={"customer": "private tool argument"},
            tool_context=context,
            result={"customer": "private tool result"},
        )
        await plugin.after_agent_callback(agent=agent, callback_context=context)
        await plugin.after_run_callback(invocation_context=context)
        await plugin.aflush()

    events = [event for batch in client.calls for event in batch]
    serialized = repr(events)
    assert len(events) == 8
    for private_value in (
        "invocation_provider_private",
        "session_provider_private",
        "tool_call_provider_private",
        "claims_agent_private_name",
        "lookup_customer_private_name",
        "private user prompt",
        "private model prompt",
        "private model output",
        "private tool argument",
        "private tool result",
        "private agent output",
    ):
        assert private_value not in serialized
    assert all(
        event["correlation"]["run_id"].startswith("google-adk-invocation") for event in events
    )
    tool_events = [event for event in events if ".tool." in event["event_type"]]
    assert all(
        event["correlation"]["tool_call_id"].startswith("google-adk-tool-call")
        for event in tool_events
    )
    assert all(event["payload"]["name"] == "tool" for event in tool_events)


@pytest.mark.asyncio
async def test_google_adk_correlation_state_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_google_adk(monkeypatch)
    client = FakeRecorderClient()
    async with AsyncRecorderSink(client) as sink:
        plugin = build_google_adk_recorder_plugin(
            sink,
            max_active_runs=1,
            max_pending_calls_per_run=1,
        )
        for invocation in ("invocation-a", "invocation-b"):
            context = SimpleNamespace(
                invocation_id=invocation,
                session=SimpleNamespace(id=f"session-{invocation}"),
                agent_name="agent",
                node_path="agent/1",
                run_id="1",
                attempt_count=1,
            )
            request = SimpleNamespace(
                model="gemini-test",
                model_dump=lambda mode="python": {"contents": []},
            )
            await plugin.before_model_callback(callback_context=context, llm_request=request)
        await sink.flush()
        gaps = sink.capture_gaps()

    assert any(gap.reason == "google_adk_run_state_evicted" for gap in gaps)
