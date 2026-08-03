/**
 * Optional Vercel AI SDK Recorder callbacks.
 *
 * This module has no runtime dependency on `ai`. It uses only the stable,
 * public per-call callback shape and deliberately does not import private
 * modules, monkeypatch providers, or implement the experimental telemetry API.
 */

import { liansEvent } from "./recorder.js";
import type { RecorderEnvelope } from "./types.js";
import type { RecorderSink } from "./recorder-sink.js";

const FINISH_REASONS = [
  "stop",
  "length",
  "content-filter",
  "tool-calls",
  "error",
  "other",
] as const;

export type VercelAiRecorderCaptureMode = "metadata_only" | "hash_only";
export type VercelAiFinishReason = (typeof FINISH_REASONS)[number] | "unknown";

/** Minimal structural shape shared by current generateText/streamText usage. */
export interface VercelAiUsageLike {
  inputTokens?: unknown;
  outputTokens?: unknown;
  totalTokens?: unknown;
  reasoningTokens?: unknown;
  cachedInputTokens?: unknown;
}

/** Stable public `onStepFinish` data consumed by the adapter. */
export interface VercelAiStepFinishEvent {
  stepNumber: number;
  text?: unknown;
  finishReason?: unknown;
  usage?: VercelAiUsageLike;
  toolCalls?: readonly unknown[];
  toolResults?: readonly unknown[];
  warnings?: readonly unknown[];
  files?: readonly unknown[];
  sources?: readonly unknown[];
}

/** Stable public `onFinish` data consumed by the adapter. */
export interface VercelAiFinishEvent {
  text?: unknown;
  finishReason?: unknown;
  totalUsage?: VercelAiUsageLike;
  usage?: VercelAiUsageLike;
  steps?: readonly unknown[];
  toolCalls?: readonly unknown[];
  toolResults?: readonly unknown[];
  warnings?: readonly unknown[];
  files?: readonly unknown[];
  sources?: readonly unknown[];
}

/** Stable public stream `onAbort` data consumed by the adapter. */
export interface VercelAiAbortEvent {
  steps?: readonly unknown[];
}

/** Stable public stream `onError` data; the error itself is never retained. */
export interface VercelAiErrorEvent {
  error?: unknown;
}

export interface VercelAiRecorderOptions {
  /** Business-stable ID for the full application run. Range: 1..180 chars. */
  runId: string;
  /** Business-stable ID for this exact generateText/streamText call. */
  operationId: string;
  /** Human-readable operation label; defaults to `vercel-ai.generate`. */
  operationName?: string;
  /** Resolved or requested model identifier; no provider response is inspected. */
  modelId?: string;
  /** Caller-reported attribution labels, not authenticated identity. */
  agentId?: string;
  principalId?: string;
  roles?: readonly string[];
  subjectId?: string;
  sessionId?: string;
  taskId?: string;
  decisionId?: string;
  /** Metadata-only by default. Full/raw capture is intentionally unsupported. */
  captureMode?: VercelAiRecorderCaptureMode;
  /** Prevents an extra unbounded allocation while hashing callback text. */
  maxHashCharacters?: number;
}

export interface VercelAiRecorderCallbacks {
  onStepFinish(event: VercelAiStepFinishEvent): Promise<void>;
  onFinish(event: VercelAiFinishEvent): Promise<void>;
}

export interface VercelAiStreamRecorderCallbacks extends VercelAiRecorderCallbacks {
  onAbort(event: VercelAiAbortEvent): Promise<void>;
  onError(event: VercelAiErrorEvent): Promise<void>;
}

interface NormalizedVercelAiOptions {
  runId: string;
  operationId: string;
  operationName: string;
  modelId?: string;
  agentId?: string;
  principalId?: string;
  roles: string[];
  subjectId?: string;
  sessionId?: string;
  taskId?: string;
  decisionId?: string;
  captureMode: VercelAiRecorderCaptureMode;
  maxHashCharacters: number;
}

interface EventFacts {
  phase: "completed" | "aborted" | "error";
  status: "ok" | "aborted" | "error";
  stepNumber?: number;
  text?: unknown;
  finishReason?: unknown;
  usage?: VercelAiUsageLike;
  stepCount?: number;
  toolCallCount?: number;
  toolResultCount?: number;
  warningCount?: number;
  fileCount?: number;
  sourceCount?: number;
  terminalGap?: "source_aborted" | "source_error";
}

export class VercelAiRecorderConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "VercelAiRecorderConfigurationError";
  }
}

/**
 * Build callbacks for `generateText` or `streamText` completion boundaries.
 * The callback waits only for optional local hashing, never for HTTP delivery.
 */
export function createVercelAiRecorderCallbacks(
  sink: RecorderSink,
  options: VercelAiRecorderOptions,
): VercelAiRecorderCallbacks {
  const adapter = new VercelAiRecorderAdapter(sink, options);
  return {
    onStepFinish: (event) => adapter.onStepFinish(event),
    onFinish: (event) => adapter.onFinish(event),
  };
}

/** Build completion plus stable stream abort/error callbacks for `streamText`. */
export function createVercelAiStreamRecorderCallbacks(
  sink: RecorderSink,
  options: VercelAiRecorderOptions,
): VercelAiStreamRecorderCallbacks {
  const adapter = new VercelAiRecorderAdapter(sink, options);
  return {
    onStepFinish: (event) => adapter.onStepFinish(event),
    onFinish: (event) => adapter.onFinish(event),
    onAbort: (event) => adapter.onAbort(event),
    onError: (event) => adapter.onError(event),
  };
}

class VercelAiRecorderAdapter {
  private readonly options: NormalizedVercelAiOptions;

  constructor(
    private readonly sink: RecorderSink,
    options: VercelAiRecorderOptions,
  ) {
    if (!sink || typeof sink.record !== "function" || typeof sink.reportCaptureGap !== "function") {
      throw new VercelAiRecorderConfigurationError("A RecorderSink is required");
    }
    this.options = normalizeOptions(options);
  }

  async onStepFinish(event: VercelAiStepFinishEvent): Promise<void> {
    try {
      await this.recordStepFinish(event);
    } catch {
      this.reportAdapterFailure();
    }
  }

  private async recordStepFinish(event: VercelAiStepFinishEvent): Promise<void> {
    if (!Number.isInteger(event?.stepNumber) || event.stepNumber < 0 || event.stepNumber > 1_000_000) {
      this.sink.reportCaptureGap("adapter_failure", {
        protocol: "lians",
        captureMode: this.options.captureMode,
      });
      return;
    }
    const stepNumber = event.stepNumber;
    await this.enqueue(
      "vercel.ai.step.finished",
      this.identity(`step:${stepNumber}`),
      {
        phase: "completed",
        status: "ok",
        stepNumber,
        text: event.text,
        finishReason: event.finishReason,
        usage: event.usage,
        toolCallCount: count(event.toolCalls),
        toolResultCount: count(event.toolResults),
        warningCount: count(event.warnings),
        fileCount: count(event.files),
        sourceCount: count(event.sources),
      },
    );
  }

  async onFinish(event: VercelAiFinishEvent): Promise<void> {
    try {
      await this.enqueue("vercel.ai.finished", this.identity("finish"), {
        phase: "completed",
        status: "ok",
        text: event?.text,
        finishReason: event?.finishReason,
        usage: event?.totalUsage ?? event?.usage,
        stepCount: count(event?.steps),
        toolCallCount: count(event?.toolCalls),
        toolResultCount: count(event?.toolResults),
        warningCount: count(event?.warnings),
        fileCount: count(event?.files),
        sourceCount: count(event?.sources),
      });
    } catch {
      this.reportAdapterFailure();
    }
  }

  async onAbort(event: VercelAiAbortEvent): Promise<void> {
    try {
      this.sink.reportCaptureGap("source_aborted", {
        protocol: "lians",
        captureMode: this.options.captureMode,
      });
      await this.enqueue("vercel.ai.aborted", this.identity("abort"), {
        phase: "aborted",
        status: "aborted",
        stepCount: count(event?.steps),
        terminalGap: "source_aborted",
      });
    } catch {
      this.reportAdapterFailure();
    }
  }

  async onError(_event: VercelAiErrorEvent): Promise<void> {
    // Deliberately ignore the error object: it may contain prompts, URLs,
    // headers, provider bodies, credentials, or arbitrary application state.
    try {
      this.sink.reportCaptureGap("source_error", {
        protocol: "lians",
        captureMode: this.options.captureMode,
        failureClass: "unknown",
      });
      await this.enqueue("vercel.ai.error", this.identity("error"), {
        phase: "error",
        status: "error",
        terminalGap: "source_error",
      });
    } catch {
      this.reportAdapterFailure();
    }
  }

  private identity(suffix: string): string {
    const run = this.options.runId;
    const operation = this.options.operationId;
    return `vercel-ai:${run.length}:${run}:${operation.length}:${operation}:${suffix}`;
  }

  private reportAdapterFailure(): void {
    try {
      this.sink.reportCaptureGap("adapter_failure", {
        protocol: "lians",
        captureMode: this.options.captureMode,
      });
    } catch {
      // Recorder callbacks never alter the model operation's outcome.
    }
  }

  private async enqueue(
    eventType: string,
    identity: string,
    facts: EventFacts,
  ): Promise<void> {
    try {
      const envelope = await this.envelope(eventType, identity, facts);
      // Framework completion callbacks should not inherit transport latency or
      // failure. Applications obtain the delivery barrier from sink.flush().
      void this.sink.record(envelope).catch(() => undefined);
    } catch {
      this.reportAdapterFailure();
    }
  }

  private async envelope(
    eventType: string,
    identity: string,
    facts: EventFacts,
  ): Promise<RecorderEnvelope> {
    const captureGaps = [
      "generation_start_not_observed",
      "input_content_not_observed",
      "provider_metadata_omitted_by_policy",
    ];
    if ((facts.toolCallCount ?? 0) > 0 || (facts.toolResultCount ?? 0) > 0) {
      captureGaps.push("tool_lifecycle_not_observed");
      captureGaps.push("tool_content_omitted_by_policy");
    }

    let outputHash: string | undefined;
    if (this.options.captureMode === "metadata_only") {
      captureGaps.push("output_content_omitted_by_policy");
    } else if (typeof facts.text !== "string") {
      captureGaps.push("output_hash_unavailable");
    } else if (facts.text.length > this.options.maxHashCharacters) {
      captureGaps.push("output_hash_input_too_large");
    } else {
      try {
        outputHash = await sha256(facts.text);
      } catch {
        captureGaps.push("output_hash_unavailable");
      }
    }
    if (facts.terminalGap) captureGaps.push(facts.terminalGap);

    const payload: Record<string, unknown> = {
      name: this.options.operationName,
      framework: "vercel-ai-sdk",
      phase: facts.phase,
      status: facts.status,
      model_id: this.options.modelId,
      step_number: facts.stepNumber,
      step_count: facts.stepCount,
      finish_reason: finishReason(facts.finishReason),
      usage: safeUsage(facts.usage),
      tool_call_count: facts.toolCallCount,
      tool_result_count: facts.toolResultCount,
      warning_count: facts.warningCount,
      file_count: facts.fileCount,
      source_count: facts.sourceCount,
      output_hash: outputHash,
      capture_gaps: captureGaps,
    };

    return liansEvent(eventType, payload, {
      eventId: identity,
      idempotencyKey: identity,
      occurredAt: new Date(),
      subjectId: this.options.subjectId,
      agentId: this.options.agentId,
      principalId: this.options.principalId,
      roles: this.options.roles,
      runId: this.options.runId,
      sessionId: this.options.sessionId,
      taskId: this.options.taskId,
      decisionId: this.options.decisionId,
      captureMode: this.options.captureMode,
      sensitiveFields: [
        "input",
        "output",
        "prompt",
        "text",
        "tool_calls",
        "tool_results",
        "provider_metadata",
        "headers",
        "error",
      ],
      extensions: {
        "lians.integration.name": "vercel-ai-sdk",
        "lians.integration.surface": "stable-callbacks",
        "lians.capture.gaps": captureGaps,
      },
    });
  }
}

function normalizeOptions(options: VercelAiRecorderOptions): NormalizedVercelAiOptions {
  if (!options || typeof options !== "object") {
    throw new VercelAiRecorderConfigurationError("Vercel AI Recorder options are required");
  }
  const captureMode = options.captureMode ?? "metadata_only";
  if (captureMode !== "metadata_only" && captureMode !== "hash_only") {
    throw new VercelAiRecorderConfigurationError(
      "captureMode must be metadata_only or hash_only; raw full capture is unsupported",
    );
  }
  if (options.roles !== undefined && !Array.isArray(options.roles)) {
    throw new VercelAiRecorderConfigurationError("roles must be an array");
  }
  const roles = Array.from(options.roles ?? [], (role, index) =>
    boundedText(`roles[${index}]`, role, 255));
  if (roles.length > 100) {
    throw new VercelAiRecorderConfigurationError("roles cannot contain more than 100 items");
  }
  const maxHashCharacters = options.maxHashCharacters ?? 1_000_000;
  if (!Number.isInteger(maxHashCharacters) || maxHashCharacters < 1 || maxHashCharacters > 4_000_000) {
    throw new VercelAiRecorderConfigurationError(
      "maxHashCharacters must be an integer from 1 to 4000000",
    );
  }
  return {
    runId: boundedText("runId", options.runId, 180),
    operationId: boundedText("operationId", options.operationId, 180),
    operationName: boundedText(
      "operationName",
      options.operationName ?? "vercel-ai.generate",
      128,
    ),
    modelId: optionalText("modelId", options.modelId, 512),
    agentId: optionalText("agentId", options.agentId, 255),
    principalId: optionalText("principalId", options.principalId, 512),
    roles,
    subjectId: optionalText("subjectId", options.subjectId, 512),
    sessionId: optionalText("sessionId", options.sessionId, 512),
    taskId: optionalText("taskId", options.taskId, 512),
    decisionId: optionalUuid("decisionId", options.decisionId),
    captureMode,
    maxHashCharacters,
  };
}

function boundedText(name: string, value: unknown, maximum: number): string {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum) {
    throw new VercelAiRecorderConfigurationError(
      `${name} must contain 1 to ${maximum} characters`,
    );
  }
  return value;
}

function optionalText(name: string, value: unknown, maximum: number): string | undefined {
  return value === undefined ? undefined : boundedText(name, value, maximum);
}

function optionalUuid(name: string, value: unknown): string | undefined {
  if (value === undefined) return undefined;
  const result = boundedText(name, value, 36);
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(result)) {
    throw new VercelAiRecorderConfigurationError(`${name} must be a UUID string`);
  }
  return result;
}

function count(value: readonly unknown[] | undefined): number {
  return Array.isArray(value) ? Math.min(value.length, 1_000_000) : 0;
}

function finishReason(value: unknown): VercelAiFinishReason | undefined {
  if (value === undefined) return undefined;
  return includes(FINISH_REASONS, value) ? value : "unknown";
}

function safeUsage(value: VercelAiUsageLike | undefined): Record<string, number> | undefined {
  if (!value || typeof value !== "object") return undefined;
  const result: Record<string, number> = {};
  copyCount(result, "input_tokens", value.inputTokens);
  copyCount(result, "output_tokens", value.outputTokens);
  copyCount(result, "total_tokens", value.totalTokens);
  copyCount(result, "reasoning_tokens", value.reasoningTokens);
  copyCount(result, "cached_input_tokens", value.cachedInputTokens);
  return Object.keys(result).length > 0 ? result : undefined;
}

function copyCount(target: Record<string, number>, key: string, value: unknown): void {
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
    target[key] = Math.min(Number.MAX_SAFE_INTEGER, Math.floor(value));
  }
}

async function sha256(value: string): Promise<string> {
  const runtime = globalThis as unknown as {
    crypto?: {
      subtle?: {
        digest(algorithm: string, data: Uint8Array): Promise<ArrayBuffer>;
      };
    };
  };
  if (!runtime.crypto?.subtle) {
    throw new VercelAiRecorderConfigurationError("Web Crypto SHA-256 is unavailable");
  }
  const digest = await runtime.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function includes<const T extends readonly unknown[]>(values: T, value: unknown): value is T[number] {
  return values.includes(value);
}
