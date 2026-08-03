/** Privacy-safe builders for Universal Recorder envelopes. */

import type {
  RecorderCaptureMode,
  RecorderEnvelope,
  RecorderProtocol,
} from "./types.js";
import { secureRecorderEventId } from "./recorder-sink.js";

export interface RecorderBuilderOptions {
  eventType?: string;
  eventId?: string;
  idempotencyKey?: string;
  occurredAt?: string | Date;
  subjectId?: string;
  agentId?: string;
  principalId?: string;
  roles?: string[];
  runId?: string;
  traceId?: string;
  spanId?: string;
  parentSpanId?: string;
  sessionId?: string;
  taskId?: string;
  contextId?: string;
  messageId?: string;
  toolCallId?: string;
  decisionId?: string;
  captureMode?: RecorderCaptureMode;
  sensitiveFields?: string[];
  extensions?: Record<string, unknown>;
}

export interface OtlpGenAiSpanOptions extends RecorderBuilderOptions {
  traceId: string;
  spanId: string;
  operation: string;
  model: string;
  input?: unknown;
  output?: unknown;
  endedAt?: string | Date;
  attributes?: Record<string, unknown>;
}

function eventId(): string {
  return secureRecorderEventId();
}

function iso(value: string | Date | undefined): string | undefined {
  return value instanceof Date ? value.toISOString() : value;
}

function envelope(
  protocol: RecorderProtocol,
  payload: Record<string, unknown>,
  options: RecorderBuilderOptions = {},
): RecorderEnvelope {
  const stableEventId = options.eventId ?? eventId();
  return {
    schema_version: "0.1",
    protocol,
    event_type: options.eventType,
    event_id: stableEventId,
    // Reusing the built envelope is retry-safe. Supply a business-stable key
    // when deduplication must survive process restarts.
    idempotency_key: options.idempotencyKey ?? stableEventId,
    occurred_at: iso(options.occurredAt),
    subject_id: options.subjectId,
    actor: {
      agent_id: options.agentId,
      principal_id: options.principalId,
      roles: options.roles ?? [],
    },
    correlation: {
      run_id: options.runId,
      trace_id: options.traceId,
      span_id: options.spanId,
      parent_span_id: options.parentSpanId,
      session_id: options.sessionId,
      task_id: options.taskId,
      context_id: options.contextId,
      message_id: options.messageId,
      tool_call_id: options.toolCallId,
      decision_id: options.decisionId,
    },
    capture: {
      // Raw request values are never logged by the SDK and the service stores
      // sensitive content as hashes unless a caller explicitly opts into full.
      mode: options.captureMode ?? "hash_only",
      sensitive_fields: options.sensitiveFields ?? [],
    },
    payload,
    extensions: options.extensions ?? {},
  };
}

/** Build one provider-neutral native Lians event. */
export function liansEvent(
  eventType: string,
  payload: Record<string, unknown>,
  options: RecorderBuilderOptions = {},
): RecorderEnvelope {
  return envelope("lians", payload, { ...options, eventType });
}

/** Build an OTLP GenAI semantic-convention span in Recorder JSON form. */
export function otlpGenAiSpan(options: OtlpGenAiSpanOptions): RecorderEnvelope {
  const attributes: Record<string, unknown> = {
    "gen_ai.operation.name": options.operation,
    "gen_ai.request.model": options.model,
    "gen_ai.agent.id": options.agentId,
    "gen_ai.input.messages": options.input,
    "gen_ai.output.messages": options.output,
    ...(options.attributes ?? {}),
  };
  const payload: Record<string, unknown> = {
    name: options.operation,
    traceId: options.traceId,
    spanId: options.spanId,
    parentSpanId: options.parentSpanId,
    attributes,
  };
  if (options.endedAt !== undefined) {
    const timestamp = options.endedAt instanceof Date
      ? options.endedAt
      : new Date(options.endedAt);
    payload.endTimeUnixNano = (BigInt(timestamp.getTime()) * 1_000_000n).toString();
  }
  return envelope("otlp.genai", payload, {
    ...options,
    eventType: `genai.${options.operation}`,
    eventId: `${options.traceId}:${options.spanId}`,
    idempotencyKey: options.idempotencyKey ?? `otlp:${options.traceId}:${options.spanId}`,
  });
}

/** Build an MCP JSON-RPC request or response with call/run correlation. */
export function mcpJsonRpcEvent(
  message: Record<string, unknown>,
  options: RecorderBuilderOptions & { runId: string; toolName?: string },
): RecorderEnvelope {
  const rpcId = message.id;
  const phase = "result" in message || "error" in message ? "response" : "request";
  const stable = options.idempotencyKey ?? `mcp:${options.runId}:${String(rpcId)}:${phase}`;
  return envelope("mcp", message, {
    ...options,
    eventId: stable,
    idempotencyKey: stable,
    toolCallId: rpcId === undefined ? undefined : String(rpcId),
    extensions: {
      ...(options.extensions ?? {}),
      ...(options.toolName ? { "mcp.tool.name": options.toolName } : {}),
    },
  });
}

/** Build an A2A task, message, status, or artifact event. */
export function a2aEvent(
  event: Record<string, unknown>,
  options: RecorderBuilderOptions = {},
): RecorderEnvelope {
  const taskId = options.taskId ?? stringValue(event.taskId) ?? stringValue(event.id);
  const contextId = options.contextId ?? stringValue(event.contextId);
  const messageId = options.messageId ?? stringValue(event.messageId);
  const kind = stringValue(event.kind) ?? "event";
  const status = isRecord(event.status) ? event.status : undefined;
  const state = stringValue(status?.state ?? event.status);
  const timestamp = stringValue(status?.timestamp);
  const artifacts = Array.isArray(event.artifacts) ? event.artifacts : [];
  const artifact = isRecord(event.artifact)
    ? event.artifact
    : isRecord(artifacts[0])
      ? artifacts[0]
      : undefined;
  const artifactId = stringValue(artifact?.artifactId ?? artifact?.artifact_id);
  const identity = messageId
    ? `message:${messageId}`
    : artifactId
      ? `artifact:${String(taskId)}:${artifactId}`
      : state || timestamp
        ? `status:${String(taskId)}:${kind}:${String(state)}:${String(timestamp)}`
        : `event:${taskId ?? eventId()}:${kind}`;
  const stable = options.idempotencyKey ?? `a2a:${identity}`;
  return envelope("a2a", event, {
    ...options,
    eventType: kind,
    eventId: stable,
    idempotencyKey: stable,
    taskId,
    contextId,
    messageId,
  });
}

function stringValue(value: unknown): string | undefined {
  return value === undefined || value === null ? undefined : String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
