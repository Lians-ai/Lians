# Native Universal Recorder hooks

Lians can capture framework lifecycle evidence without patching runtime
internals. The public Python SDK provides a bounded async sink and optional
adapters for Anthropic, Google ADK, OpenAI Agents, LangChain/LangGraph, and
CrewAI. Anthropic's adapter is intentionally an API-client boundary; it does not
claim the local tool visibility provided by an agent-runtime callback surface.

The default is local hash-only capture: callback inputs and outputs are reduced
to commitments before they enter the sink buffer or cross the SDK HTTP
boundary. Pass a deployment-held `commitment_key` (at least 32 bytes) to use
HMAC-SHA-256. Without it the interoperable SHA-256 default does **not** hide
low-entropy values: an attacker can guess values such as `yes`, small enum sets,
or common tool arguments offline. Metadata-only capture is also supported.
Native adapters deliberately reject `full` mode; submit an explicit Recorder
envelope when raw-content capture is truly required and approved.

Model identifiers and lifecycle status remain plaintext metadata so evidence
can be joined and investigated. LangChain and OpenAI component names are also
plaintext. CrewAI and Google ADK component names are private by default and
require an explicit `plaintext_component_names=True` opt-in. Anthropic and
Google ADK provider correlation identifiers are reduced to namespaced SHA-256
references before they enter an envelope. Treat every plaintext identifier as
tenant data; do not place prompts, credentials, or other secrets in it.

## Anthropic Python SDK

```bash
pip install 'lians-sdk[anthropic]'
python agentmem/sdk/python/examples/anthropic_recorder_middleware.py
```

```python
from anthropic import AsyncAnthropic
from lians import AsyncRecorderSink, build_anthropic_recorder_middleware

async with AsyncRecorderSink(async_lians_client) as recorder:
    middleware = build_anthropic_recorder_middleware(recorder)
    async with AsyncAnthropic(middleware=[middleware]) as claude:
        response = await claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": "Review this decision."}],
        )
    await middleware.aflush()
```

The adapter subclasses Anthropic's public `Middleware` and implements both
`handle` and `handle_async`. The supported floor is `anthropic>=0.120.2`; that
release documents that middleware runs once per HTTP attempt inside the SDK's
retry loop. Each attempt gets a private random correlation ID shared by its
start and completion/error events. The SDK's retry index is included, so two
provider attempts are not collapsed into one Recorder event. Lians delivery
retries keep each admitted event's frozen idempotency key, but a fresh process
cannot reproduce the random provider-attempt identity. Use an application
outbox or an explicit Recorder envelope when restart-replay identity is needed.

In `hash_only` mode, the JSON request body is committed locally. The adapter
classifies the URL into a fixed route family and ignores query parameters,
credentials, arbitrary headers, uploads, and exception messages. It reads only
the response status and Anthropic's documented `request-id`, which is converted
to a one-way reference. It deliberately does **not** parse the response wrapper:
response bodies, streaming chunks, model output, and server-tool results are
not committed by this hook. This avoids changing stream/eager-read behavior and
keeps middleware transparent. Record a typed response explicitly or combine
this hook with a runtime adapter when output evidence is required.

Anthropic returns every HTTP response, including 4xx/5xx, through middleware
before raising its typed error to the original caller. Lians therefore marks an
attempt `failed` from the public status code; connection/timeout exceptions are
marked `failed` when `call_next` raises. For a streaming request, `completed`
means that the response wrapper and headers returned—not that the stream body
was fully consumed. A later stream-iteration failure is outside this hook.

Anthropic's client tool runner executes application functions between Messages
API calls. Client middleware observes the surrounding API attempts, not the
actual local function start/end, exception, side effect, or authorization
decision. A tool result may influence the hash of a later request, but that is
not tool-execution evidence. The same limitation applies to any application
code outside the SDK. Do not use this adapter alone to claim Gate enforcement
or complete tool provenance.

Closing an Anthropic client does not constitute a Recorder flush. Call
`await middleware.aflush()` (or `await recorder.flush()`) before closing the
sink. `middleware.close()` is an async flush convenience and never closes the
shared sink. The synchronous `middleware.flush()` is confirmed only off the
sink's owning event loop; on that loop it raises and directs the caller to
`aflush()` rather than deadlocking.

### Verified Managed Agents webhooks

```python
from lians import anthropic_managed_agents_webhook_event

# Verification and five-minute freshness enforcement stay with Anthropic's SDK.
verified = claude.beta.webhooks.unwrap(raw_body, headers=request_headers)
envelope = anthropic_managed_agents_webhook_event(verified)
await recorder.submit(envelope)
```

The converter is pure and does not own an HTTP server, signing key, signature
verification, or replay cache. It must receive the typed result of Anthropic's
`unwrap()` helper. Managed Agents webhooks contain an event ID/type and resource
ID, not the current resource body; Lians records that identifier-only boundary
and does not silently fetch a possibly newer object. Provider event, session,
workspace, and organization IDs are one-way referenced. Anthropic retries a
delivery with the same event ID, so conversion is idempotent. Webhook ordering
is not guaranteed; preserve `created_at` and reconcile resource history when
order matters.

## Google Agent Development Kit (ADK)

```bash
pip install 'lians-sdk[google-adk]'
```

```python
from google.adk.apps import App
from google.adk.runners import Runner
from lians import AsyncRecorderSink, build_google_adk_recorder_plugin

async with AsyncRecorderSink(async_lians_client) as recorder:
    plugin = build_google_adk_recorder_plugin(recorder)
    app = App(name="review_app", root_agent=root_agent, plugins=[plugin])
    runner = Runner(app=app, session_service=session_service)
    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=message,
        ):
            consume(event)
    finally:
        # Runner.close() invokes BasePlugin.close(); Lians flushes but does not
        # close the shared Recorder sink.
        await runner.close()
```

The supported floor is `google-adk>=2.6.1`. The integration subclasses only the
public `google.adk.plugins.base_plugin.BasePlugin` and records runner, agent,
model, and tool start/success/error callbacks. Every hook returns `None`, so the
adapter never replaces a request/result or suppresses an error. Register it
first when later policy/caching plugins may short-circuit the remaining callback
chain.

Invocation, session, node, and public `function_call_id` values become
namespaced one-way references. Tool start/end pairing uses the public function
call ID. ADK v2.6.1 does not expose a model-call ID on the model callback, so
model start/end pairing uses a locked, bounded FIFO per invocation and agent
scope. Concurrent model calls that complete out of order can therefore be
mispaired; do not interpret those paired span IDs as stronger evidence than the
callback surface provides. Missing/evicted pairing state produces payload-free
gaps. `max_active_runs` (default 10,000) and
`max_pending_calls_per_run` (default 256) bound all retained state.

Model request/response and tool argument/result public exports are hashed
locally. ADK/Pydantic constructs `model_dump()` results before Lians can enforce
its traversal limit; configure ADK/model input and output bounds as an upstream
control. Error type is recorded, never its message or arguments. Agent/tool
names are generic by default; `plaintext_component_names=True` is an explicit
tenant-data opt-in. Model identifiers remain plaintext.

Coverage is scoped to the `App`/`Runner` where the plugin is registered. Every
independently created runner must register it. In ADK 2.6.1, `AgentTool`
propagates parent plugins by default, but `include_plugins=False` intentionally
creates an isolated child runner and therefore a capture gap. Do not assume a
remote Agent Engine or another deployment wrapper preserved the local plugin;
verify callback execution and Recorder readiness in that target environment.
Streaming deltas and events from runtimes that bypass these public callbacks
are not recorded, nor is model-private reasoning.

`BasePlugin.close()` performs a confirmed bounded flush and does not close the
shared sink. Close every runner before the sink. If several independently owned
runners use separate plugin instances with one sink, close them all before the
sink's context exits.

## Ten-minute LangChain/LangGraph setup

```bash
pip install 'lians-sdk[langchain]'
export LIANS_API_URL='https://lians.example'
export LIANS_API_KEY='...'
python agentmem/sdk/python/examples/native_recorder_hooks.py
```

For an existing chain or compiled LangGraph, the integration is four lines:

```python
import os

from lians import AsyncRecorderSink, RecorderAttribution
from lians import build_langchain_recorder_handler

async with AsyncRecorderSink(
    async_lians_client,
    commitment_key=os.environ.get("LIANS_RECORDER_COMMITMENT_KEY"),
) as recorder:
    callback = build_langchain_recorder_handler(
        recorder,
        attribution=RecorderAttribution(claimed_agent_id="claims-reviewer"),
    )
    result = await graph.ainvoke(
        state,
        config={"callbacks": [callback], "metadata": {"thread_id": thread_id}},
    )
    await recorder.flush()
```

Set `LIANS_RECORDER_COMMITMENT_KEY` to a secret of at least 32 bytes in
production; omitting it selects unkeyed SHA-256. The handler uses LangChain's
public `AsyncCallbackHandler`. LangGraph propagates
that callback configuration through compiled graph runs. It records chain,
chat-model/LLM, tool, retriever, agent-action/finish, and custom-event
boundaries. Root run, child run, and parent run IDs become Recorder
run/trace/span correlation.

It does not record per-token stream callbacks. A provider that fails to invoke
LangChain callbacks, work performed outside the configured runnable, and
model-private reasoning are not observable.

Run correlation, attribution, and point-event sequence state is protected by a
lock and bounded by `max_active_runs` (default 10,000). The least-recently-used
entry is evicted at the limit and a payload-free `langchain_state_evicted` gap
is recorded. Tune this limit to expected callback concurrency.

## OpenAI Agents SDK

```bash
pip install 'lians-sdk[openai-agents]'
```

```python
from agents import Runner
from lians import AsyncRecorderSink, RecorderAttribution
from lians import install_openai_agents_recorder

async with AsyncRecorderSink(async_lians_client) as recorder:
    processor = install_openai_agents_recorder(
        recorder,
        attribution=RecorderAttribution(claimed_agent_id="support-agent"),
    )
    result = await Runner.run(agent, request)
    await processor.aflush()
```

The processor is added through the public `add_trace_processor()` API; existing
OpenAI trace exporters are not replaced. The Agents SDK has no public processor
removal API, so `install_openai_agents_recorder()` is intentionally a
process-lifetime, install-once operation. Repeating the identical call returns
the installed processor; a different sink or configuration raises instead of
duplicating every event. In a service, create the sink at process startup,
install once, and close the sink only during process shutdown. It records trace and span starts/ends,
including the public trace/span/parent IDs and observable generation, function,
tool, guardrail, handoff, response, task, turn, and custom span types emitted by
the SDK. Actual coverage follows the spans the installed Agents SDK emits.

Do not call the Agents SDK's global `set_trace_processors()` or replace its trace
provider after installation: those APIs replace the registered surface, and the
SDK exposes no public list or per-processor removal/readback with which Lians
could reconcile its install registry.

Tracing disabled through `RunConfig`, environment configuration, or a runtime
policy produces no callbacks. Zero Data Retention configurations may make
Agents SDK tracing unavailable. Lians cannot see hidden chain-of-thought. The
Lians adapter's local hashing also does not change what another processor or the
Agents SDK's default exporter captures; configure the Agents SDK's own sensitive
trace-data setting separately.

`TracingProcessor.force_flush()` is synchronous. Off the sink's event-loop
thread it waits up to `synchronous_flush_timeout`. On the owning loop it cannot
block without deadlocking, so it schedules drainage, returns, and records an
`openai_agents_force_flush_deferred` disclosure. Only
`await processor.aflush()` or `await recorder.flush()` is a confirmed async
delivery boundary. `shutdown()` shares the same synchronous limitation and does
not own or close the sink.

## CrewAI

```bash
pip install 'lians-sdk[crewai]'
```

```python
import asyncio
from uuid import uuid4

from lians import AsyncRecorderSink, RecorderAttribution
from lians import build_crewai_recorder_listener

run_id = f"crew-{uuid4()}"
async with AsyncRecorderSink(async_lians_client) as recorder:
    listener = build_crewai_recorder_listener(
        recorder,
        run_id=run_id,
        attribution=RecorderAttribution(claimed_agent_id="research-crew"),
    )
    try:
        output = await asyncio.to_thread(crew.kickoff, inputs=inputs)
        # Current CrewAI dispatches handlers on its own executor. Drain that
        # first, then confirm Recorder delivery.
        await asyncio.to_thread(listener.flush_callbacks, timeout=30)
        await recorder.flush()
    finally:
        listener.close()
```

CrewAI uses a process-global event bus. Always call the listener's idempotent
`close()`/`unregister()` method (or use its synchronous context manager when no
explicit callback-drain step is needed). Current CrewAI exposes `off()`, so the final listener removes the
shared Lians dispatchers. CrewAI 1.0 has no public `off()`; on that supported
floor, close removes the listener from a process-wide weak subscriber set and
leaves at most one inert dispatcher per supported event class. It does not
retain the listener or capture later events.

Current CrewAI also exposes event-bus `flush()`, wrapped by
`listener.flush_callbacks()`. Call it before `recorder.flush()` when a confirmed
end-of-run boundary is required. CrewAI 1.0 lacks a public callback-drain API;
the wrapper records `crewai_callback_flush_unavailable` and raises rather than
claiming completion. Keep the listener through process shutdown or upgrade when
that older runtime cannot otherwise join its event futures.

A fixed `run_id` is correct for one scoped execution in a one-shot worker.
Services running multiple crews should construct one process-lifetime
listener, pass a resolver
`run_id=lambda source, event: ...` and a `source_filter` so unrelated executions
cannot be correlated into the same Recorder run, then close it at service
shutdown.

Envelope identity prefers CrewAI's public `event_id`, so callback thread
scheduling cannot change the same source event's idempotency key. On runtimes
without that field, identity derives from public event type, source fingerprint,
timestamp, emission sequence, correlation fields, and a bounded local event
commitment—not handler execution order. Run identifiers longer than 512
characters are replaced with a stable SHA-256 reference and disclosed through a
payload-free `crewai_run_id_hashed` gap. Crew/task/agent labels, including the
CrewAI `task_name` fallback that may contain a task description, are never sent
as names unless `plaintext_component_names=True` is explicitly selected.

The listener subscribes through public `BaseEventListener` handlers to:

- crew kickoff started/completed/failed;
- agent execution started/completed/error;
- task started/completed/failed;
- tool usage started/finished/error; and
- LLM call started/completed/failed.

Streaming chunks, CrewAI thinking/reasoning events, memory internals, and events
from runtimes that do not expose those public event classes are not captured.
An older CrewAI release missing any required event class fails at adapter setup
with an upgrade command instead of silently providing partial coverage.

## Delivery and loss contract

```python
import os

from lians import AsyncRecorderSink, RecorderSinkConfig

recorder = AsyncRecorderSink(
    async_lians_client,
    config=RecorderSinkConfig(
        max_buffered_events=2048,
        batch_size=100,
        backpressure="block",       # block | raise | drop_newest
        delivery_failure="halt",    # halt | drop
        max_delivery_attempts=5,
    ),
    # At least 32 bytes. Omit only if guessable-content disclosure is accepted.
    commitment_key=os.environ.get("LIANS_RECORDER_COMMITMENT_KEY"),
)
```

Each envelope is traversed through finite depth/item/byte limits, copied as
JSON-only data, validated against the Recorder v0.1 wire shape, and assigned an
event ID plus idempotency key before admission. Invalid/non-JSON events are
rejected individually with an `invalid_envelope` gap, so they cannot poison a
neighbor in a non-atomic HTTP batch. Retries reuse the frozen identities. If
neither identity was supplied, the SDK derives one from the canonical envelope.
For replay across process restarts, frameworks and durable outboxes should still
provide a business-stable run/tool/event identity.

Those limits cover SDK-owned traversal, copying, canonicalization, hashing, and
admission. A framework constructs its callback object first, and some public
framework `export()`/`model_dump()` methods materialize their result before the
SDK can inspect it; configure framework-side payload limits as a separate
control.

The buffer is intentionally in memory:

- `max_buffered_events` is a hard total admission bound across cross-thread
  callbacks already scheduled on the loop, queued events, and in-flight events.
- `block` applies to awaited async submissions and provides real backpressure.
- Synchronous framework callbacks never block. If total admission is full, the
  callback event is rejected as `callback_backpressure` before another loop
  closure is scheduled.
- `halt` stops delivery after retry exhaustion; `flush()` and `close()` raise a
  `RecorderDeliveryError`.
- `drop` keeps the worker alive and emits one payload-free capture gap per event
  whose persistence is unconfirmed after delivery attempts are exhausted.
- Non-atomic batches are the default so one invalid event cannot poison valid
  neighbors. Local validation enforces this before transport; server semantic
  rejections become payload-free `server_rejected` gaps.
- `close(drain=False)` and cancellation distinguish buffered events that were
  never sent from in-flight events whose persistence is ambiguous.
- `flush_interval_seconds` is the maximum accumulation window after the first
  event in a batch. A full batch or explicit `flush()` ends that window early.
- `Retry-After` delta-seconds and HTTP dates form a no-earlier-than floor; jitter
  is nonnegative and never moves a retry earlier. If the server asks for more
  than `retry_max_seconds`, delivery fails instead of violating the server's
  instruction or sleeping past the operator's configured bound.

Inspect `recorder.stats()` and `recorder.capture_gaps()` at every job boundary.
The gap window is bounded; `capture_gaps_total` remains monotonic for the sink
lifetime. A process crash can lose accepted in-memory events before disclosure.
Use a durable application outbox when crash-loss is unacceptable.

The async context manager attempts a drain even when the protected application
operation raises, because failure boundaries are often the most important
evidence. If both the application and drain fail, the original application
exception is preserved and a payload-free close-failure gap is retained.

## Attribution boundary

`RecorderAttribution.claimed_agent_id`, `claimed_principal_id`, and
`claimed_roles` are caller-reported correlation claims. They do not authenticate
an actor. Lians records the authenticated ingestion principal from the API key,
workload credential, or access token used by `AsyncLiansClient`; policies that
require authenticated attribution must evaluate that server-derived identity.

## Supported references

- [Anthropic SDK middleware](https://platform.claude.com/docs/en/cli-sdks-libraries/middleware)
- [Anthropic client tool runner boundary](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-runner)
- [Anthropic Managed Agents webhooks](https://platform.claude.com/docs/en/managed-agents/webhooks)
- [Anthropic Python middleware v0.120.2 source](https://github.com/anthropics/anthropic-sdk-python/blob/v0.120.2/src/anthropic/_middleware.py)
- [Google ADK plugins](https://adk.dev/plugins/)
- [Google ADK `BasePlugin` v2.6.1 source](https://github.com/google/adk-python/blob/v2.6.1/src/google/adk/plugins/base_plugin.py)
- [Google ADK `AgentTool` v2.6.1 propagation source](https://github.com/google/adk-python/blob/v2.6.1/src/google/adk/tools/agent_tool.py)
- [OpenAI Agents tracing processors](https://openai.github.io/openai-agents-python/tracing/)
- [LangChain async callback API](https://reference.langchain.com/python/langchain-core/callbacks/base)
- [CrewAI event listeners](https://docs.crewai.com/en/concepts/event-listener)
