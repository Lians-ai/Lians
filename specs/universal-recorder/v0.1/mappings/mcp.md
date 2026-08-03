# Model Context Protocol mapping

The MCP adapter accepts an individual MCP JSON-RPC 2.0 request, response, or
notification as the envelope `payload`. This matches the upstream
[MCP base message model](https://modelcontextprotocol.io/specification/2025-03-26/basic/index)
and the current [`tools/call` schema](https://modelcontextprotocol.io/specification/2025-11-25/schema).

## Tool-call request

```json
{
  "protocol": "mcp",
  "occurred_at": "2026-08-02T02:15:00Z",
  "actor": {"agent_id": "claims-agent", "principal_id": "workload:claims-prod"},
  "correlation": {"session_id": "mcp-session-44", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"},
  "payload": {
    "jsonrpc": "2.0",
    "id": 18,
    "method": "tools/call",
    "params": {
      "name": "approve_claim",
      "arguments": {"claim_id": "CLM-8821", "amount": 4750}
    }
  },
  "extensions": {"lians.policy.version": "claims-v8"}
}
```

Send the JSON-RPC response as a second envelope with the same session/trace and
request ID. Because a response does not carry the original method or tool name,
set `event_type` to `mcp.tool.result` or add `mcp.tool.name` to `extensions` when
that context is available.

## Field map

| MCP field | Recorder field | Notes |
|---|---|---|
| `method` | `event_kind` | `tools/call` maps to `mcp.tool.call`; other methods become `mcp.<method>`. |
| `params.name` | `event_name` | The invoked tool name. |
| JSON-RPC `id` | `tool_call_id` | Request and response phases remain distinct dedup records. |
| `params.arguments` | input hash/content | Hash-only by default. |
| `result` or `error` | output hash/content | Errors also produce failed run status. |
| request/notification/response shape | phase | Request has method + ID, notification has method only, response has result/error. |
| `correlation.session_id` | boundary fallback | Include the transport session because JSON-RPC IDs can be reused. |
| `correlation.trace_id` | cross-protocol boundary | Preferred when the host already propagates an OTLP trace. |

The adapter records resources, prompts, sampling, elicitation, logging, and
future methods without a vendor-specific schema. `tools/call` receives richer
semantic extraction because tool arguments and results are evidence-bearing.

## Long-running MCP tasks

MCP tasks are experimental in the 2025-11-25 revision and can turn one tool
call into a create-task response followed by `tasks/get`, `tasks/result`, or
cancel operations. Instrumentations should propagate one explicit
`correlation.run_id` across that sequence and place the MCP task ID in
`correlation.task_id`. This avoids relying on changing JSON-RPC request IDs.

Never copy Authorization headers or transport credentials into extensions.
Known secret fields are always redacted, but omitting credentials at the source
is the stronger control.
