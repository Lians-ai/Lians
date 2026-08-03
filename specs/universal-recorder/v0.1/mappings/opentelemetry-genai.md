# OpenTelemetry GenAI mapping

This adapter accepts one flattened OTLP span per Recorder envelope. Full OTLP
JSON/protobuf exports continue to use `/v1/traces`; an OTLP collector or SDK can
also transform each span into the envelope below and send it to the Universal
Recorder.

The mapping follows the upstream
[OpenTelemetry GenAI semantic attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/).
GenAI conventions are still evolving, so v0.1 accepts both current structured
message attributes and legacy prompt/completion fallbacks.

## Envelope

```json
{
  "protocol": "otlp.genai",
  "correlation": {"session_id": "checkout-8127"},
  "payload": {
    "traceId": "4bf92f3577b34da6a3ce929d0e0e4736",
    "spanId": "00f067aa0ba902b7",
    "name": "chat underwriting-model",
    "startTimeUnixNano": "1785643200000000000",
    "endTimeUnixNano": "1785643200500000000",
    "attributes": {
      "gen_ai.operation.name": "chat",
      "gen_ai.request.model": "underwriting-model",
      "gen_ai.input.messages": [{"role": "user", "parts": [{"type": "text", "content": "..."}]}],
      "gen_ai.output.messages": [{"role": "assistant", "finish_reason": "stop", "parts": [{"type": "text", "content": "..."}]}],
      "lians.policy.version": "credit-v14"
    },
    "status": {"code": 1}
  }
}
```

`attributes` and `resourceAttributes` may be either ordinary JSON objects or
OTLP key/value arrays containing `stringValue`, `intValue`, `arrayValue`, and
the other standard AnyValue encodings.

## Field map

| OTLP field | Recorder field | Notes |
|---|---|---|
| `traceId`, `spanId`, `parentSpanId` | correlation identifiers | Trace ID is the default run boundary; trace + span is the dedup identity. |
| `startTimeUnixNano` | `occurred_at` | Envelope `occurred_at` wins when supplied. |
| `gen_ai.operation.name` | `event_kind` | Emitted as `genai.<operation>`; custom operations are preserved. |
| span `name` | `event_name` | Defaults to the operation or `unnamed-genai-span`. |
| `gen_ai.agent.name` / `gen_ai.agent.id` | `agent_id` | Falls back to resource `service.name`. |
| `gen_ai.conversation.id` / `session.id` | `session_id` | Useful when trace propagation is unavailable. |
| `gen_ai.response.model` | `model_id` | Falls back to `gen_ai.request.model`, then legacy `gen_ai.system`. |
| `gen_ai.input.messages` | input hash/content | Falls back to legacy `gen_ai.prompt` and `gen_ai.request.input`. |
| `gen_ai.output.messages` | output hash/content | Falls back to legacy `gen_ai.completion` and `gen_ai.response.output`. |
| span status | normalized status | OTLP error status becomes `error`; other values become `ok`. |
| `endTimeUnixNano` | phase | A completed span becomes `completed`; an open span becomes `started`. |
| `enduser.id` / `user.id` | principal | Prefer the envelope actor when identity is already authenticated. |

OpenTelemetry warns that input and output messages can contain sensitive data.
The Recorder therefore hashes those fields by default even when the exporting
SDK opted into content capture.

## Recommended Lians attributes

The following namespaced attributes improve receipt completeness without
changing upstream semantics:

- `lians.policy.version`
- `lians.evidence.ids`
- `lians.decision.id`
- `lians.principal.id`

Until these are registered conventions, also place authoritative identifiers
in the envelope's `correlation`, `actor`, or `extensions` object.
