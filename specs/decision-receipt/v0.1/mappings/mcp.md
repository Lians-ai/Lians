# Model Context Protocol to Decision Receipt v0.1

MCP requests, responses, and notifications can provide tool and source evidence
for a Decision Receipt when they are explicitly correlated to an authoritative
decision. JSON-RPC request IDs are transport-local and MUST be combined with a
run, trace, session, or Decision ID before they are used as durable correlation.

| MCP evidence | Receipt location | Required interpretation |
|---|---|---|
| `tools/list` definition and schema | `tools[].definition_hash` or open tool metadata | Proves the recorded definition, not that the tool was called. |
| `tools/call` name, arguments, and request ID | `tools[].call_hash` and normalized evidence link | Arguments are hash-only by default; credentials must never be copied into evidence. |
| tool result or JSON-RPC error | `tools[].result_hash` and decision/output evidence | A response proves the recorded result boundary, not external side-effect success. |
| `resources/read` identity, version, URI, and content digest | `sources` | Citation requires an explicit decision-evidence relation; resource availability alone is insufficient. |
| prompt/sampling messages | `artifacts` or open model/tool metadata | Capture mode and redaction rules apply before hashing. |
| transport session, trace, task, and tool-call IDs | `correlation` | Preserve enough scope to prevent JSON-RPC ID reuse from joining unrelated runs. |
| caller-supplied agent or principal values | claimed actor/authorization metadata | Authentication is derived from the Lians credential that ingested the event. |

Long-running MCP tasks SHOULD propagate one explicit Recorder `run_id` and the
MCP task ID across create, status, result, and cancellation messages. A receipt
MUST disclose absent terminal results or incomplete task history rather than
inferring success from a request acknowledgement.

See the [Universal Recorder MCP mapping](../../../universal-recorder/v0.1/mappings/mcp.md)
for the wire-level normalization rules.
