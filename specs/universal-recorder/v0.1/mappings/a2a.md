# Agent2Agent Protocol mapping

The A2A adapter accepts Task, Message, TaskStatusUpdateEvent, and
TaskArtifactUpdateEvent objects. It follows the upstream
[A2A protocol data model](https://a2a-protocol.org/dev/specification/), including
the binding-neutral `kind`, `taskId`, `contextId`, `messageId`, status, parts,
and artifact fields.

## Task status example

```json
{
  "protocol": "a2a",
  "actor": {"agent_id": "procurement-agent", "principal_id": "workload:buyer-prod"},
  "payload": {
    "kind": "status-update",
    "taskId": "task-77",
    "contextId": "purchase-2026-441",
    "status": {
      "state": "TASK_STATE_COMPLETED",
      "timestamp": "2026-08-02T02:17:41Z"
    },
    "final": true
  },
  "extensions": {
    "gen_ai.model.id": "buyer-agent-4.2",
    "lians.policy.version": "procurement-v11"
  }
}
```

## Field map

| A2A field | Recorder field | Notes |
|---|---|---|
| `kind` | `event_kind` | Prefixed with `a2a.`; task state is appended when present. |
| Task `id` / event `taskId` | `task_id` | Primary A2A execution boundary and dedup component. |
| `contextId` | `context_id` | Correlates related tasks and messages when no task exists. |
| `messageId` | `message_id` | Stable identity for Message deduplication. |
| `status.state` | normalized status/phase | Completed, failed, canceled, and rejected are terminal. |
| user Message `parts` | input hash/content | Message parts are communication, not task output. |
| agent Message `parts` | output hash/content | Direct-response messages can be an output. |
| Task `artifacts` | output/evidence | Artifacts are the preferred A2A task output. |
| Artifact update `artifact` | output/evidence | Incremental chunks correlate by task ID. |
| `status.timestamp` | source time | Envelope `occurred_at` is authoritative when both exist. |
| envelope actor | agent/principal | A2A objects do not by themselves prove workload identity. |

The A2A specification distinguishes Messages (communication) from Artifacts
(task results) and warns that message histories may be incomplete. A receipt
should therefore cite terminal task status and artifacts whenever available,
not assume Task history is an exhaustive execution log.

## Streaming and push delivery

A2A streaming and push notifications may redeliver updates. Preserve source
`messageId`, `taskId`, `contextId`, event kind, and task state; the Recorder's
protocol-derived dedup key then absorbs at-least-once delivery. If an agent emits
multiple artifact chunks with identical task and kind, add a source `event_id`
or an explicit `idempotency_key` for each chunk.
