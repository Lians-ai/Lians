/**
 * Dependency-free, bounded asynchronous delivery for Universal Recorder events.
 *
 * The sink snapshots each envelope exactly once and assigns its event and
 * idempotency identities before it enters the queue. Retries therefore resend
 * identical JSON even if the caller later mutates the original object.
 */

import type {
  RecorderBatchResult,
  RecorderCaptureMode,
  RecorderEnvelope,
  RecorderIngestResult,
  RecorderProtocol,
} from "./types.js";

const GAP_REASONS = [
  "queue_overflow",
  "sink_closed",
  "serialization_failed",
  "event_too_large",
  "invalid_event",
  "invalid_event_identity",
  "delivery_rejected",
  "retry_exhausted",
  "invalid_response",
  "source_aborted",
  "source_error",
  "adapter_failure",
] as const;

const FAILURE_CLASSES = [
  "none",
  "network",
  "timeout",
  "throttled",
  "client",
  "server",
  "invalid_response",
  "unknown",
] as const;

const PROTOCOLS: readonly RecorderProtocol[] = ["lians", "otlp.genai", "mcp", "a2a"];
const CAPTURE_MODES: readonly RecorderCaptureMode[] = [
  "metadata_only",
  "hash_only",
  "full",
];

export type RecorderOverflowPolicy =
  | "reject_newest"
  | "drop_newest"
  | "drop_oldest";
export type RecorderTerminalDeliveryPolicy = "reject" | "drop";
export type RecorderRetryDecision = "retry" | "terminal";
export type RecorderSinkState = "open" | "closing" | "closed";
export type RecorderCaptureGapReason = (typeof GAP_REASONS)[number];
export type RecorderFailureClass = (typeof FAILURE_CLASSES)[number];
export type RecorderGapProtocol = RecorderProtocol | "mixed" | "unknown";
export type RecorderGapCaptureMode = RecorderCaptureMode | "mixed" | "unknown";

export interface RecorderBatchTransport {
  ingestRecorderBatch(
    events: RecorderEnvelope[],
    options?: { atomic?: boolean },
  ): Promise<RecorderBatchResult>;
}

export interface RecorderRetryContext {
  attempt: number;
  maxAttempts: number;
  failureClass: RecorderFailureClass;
}

export interface RecorderCaptureGap {
  /** Closed vocabulary; never an exception message or server response body. */
  reason: RecorderCaptureGapReason;
  affectedEvents: number;
  attempts: number;
  failureClass: RecorderFailureClass;
  protocol: RecorderGapProtocol;
  captureMode: RecorderGapCaptureMode;
  overflowPolicy: RecorderOverflowPolicy;
  terminalDeliveryPolicy: RecorderTerminalDeliveryPolicy;
  observedAt: string;
}

export interface RecorderCaptureGapSummary {
  total: number;
  byReason: Record<RecorderCaptureGapReason, number>;
  reporterFailures: number;
}

export interface RecorderSinkStats {
  state: RecorderSinkState;
  bufferedEvents: number;
  bufferedBytes: number;
  queuedEvents: number;
  inFlightBatches: number;
  acceptedEvents: number;
  duplicateEvents: number;
  droppedEvents: number;
  terminalFailures: number;
  overflowRejections: number;
  retryAttempts: number;
  captureGaps: number;
  reporterFailures: number;
}

export interface RecorderDeliveryResult {
  eventId: string;
  idempotencyKey: string;
  status: "accepted" | "duplicate" | "dropped";
  attempts: number;
  gapReason?: RecorderCaptureGapReason;
  result?: RecorderIngestResult;
}

export interface RecorderFlushResult {
  events: number;
  delivered: number;
  duplicates: number;
  dropped: number;
  failed: number;
}

export interface RecorderSinkOptions {
  /** Includes queued and in-flight events. Range: 1..10,000. */
  maxBufferedEvents?: number;
  /** Includes queued and in-flight serialized envelopes. Range: 1 KiB..512 MiB. */
  maxBufferedBytes?: number;
  /** Server batch limit is 500. Range: 1..500. */
  maxBatchSize?: number;
  /** Serialized request batch bound. Range: 1 KiB..32 MiB. */
  maxBatchBytes?: number;
  /** Per-envelope serialized bound. Range: 256 bytes..16 MiB. */
  maxEventBytes?: number;
  /** Concurrent HTTP batches. Range: 1..16. Defaults to 1 to preserve order. */
  maxConcurrency?: number;
  /** Delivery attempts including the initial request. Range: 1..20. */
  maxAttempts?: number;
  /** Initial full-jitter retry cap. Range: 1 ms..60 s. */
  baseRetryDelayMs?: number;
  /** Maximum full-jitter retry cap. Range: 1 ms..5 min. */
  maxRetryDelayMs?: number;
  /** Maximum time to coalesce a partial batch. Range: 0..60 s. */
  maxBatchDelayMs?: number;
  overflowPolicy?: RecorderOverflowPolicy;
  terminalDeliveryPolicy?: RecorderTerminalDeliveryPolicy;
  /** Non-atomic batches isolate a malformed event instead of poisoning peers. */
  atomicBatches?: boolean;
  classifyError?: (
    error: unknown,
    context: RecorderRetryContext,
  ) => RecorderRetryDecision;
  /** Receives only bounded metadata. Reporter failures never break delivery. */
  onCaptureGap?: (gap: RecorderCaptureGap) => void | PromiseLike<void>;
  /** Timing jitter only; event identity always uses cryptographic randomness. */
  random?: () => number;
}

interface NormalizedOptions {
  maxBufferedEvents: number;
  maxBufferedBytes: number;
  maxBatchSize: number;
  maxBatchBytes: number;
  maxEventBytes: number;
  maxConcurrency: number;
  maxAttempts: number;
  baseRetryDelayMs: number;
  maxRetryDelayMs: number;
  maxBatchDelayMs: number;
  overflowPolicy: RecorderOverflowPolicy;
  terminalDeliveryPolicy: RecorderTerminalDeliveryPolicy;
  atomicBatches: boolean;
  classifyError?: RecorderSinkOptions["classifyError"];
  onCaptureGap?: RecorderSinkOptions["onCaptureGap"];
  random: () => number;
}

interface PreparedEvent {
  /** Canonical snapshot reparsed for each attempt so transports cannot mutate retries. */
  serialized: string;
  eventId: string;
  idempotencyKey: string;
  bytes: number;
  protocol: RecorderGapProtocol;
  captureMode: RecorderGapCaptureMode;
}

interface PendingEvent extends PreparedEvent {
  promise: Promise<RecorderDeliveryResult>;
  resolve: (value: RecorderDeliveryResult) => void;
  reject: (reason: unknown) => void;
  settled: boolean;
}

export interface RecorderCaptureGapContext {
  affectedEvents?: number;
  attempts?: number;
  failureClass?: RecorderFailureClass;
  protocol?: RecorderGapProtocol;
  captureMode?: RecorderGapCaptureMode;
}

class InvalidRecorderResponseError extends Error {
  constructor() {
    super("Recorder transport returned an invalid batch response");
    this.name = "InvalidRecorderResponseError";
  }
}

class RecorderPreparationError extends Error {
  constructor(
    public readonly reason: "serialization_failed" | "event_too_large" | "invalid_event",
    public readonly prepared: Omit<PreparedEvent, "serialized" | "bytes">,
  ) {
    super(`Recorder event preparation failed: ${reason}`);
    this.name = "RecorderPreparationError";
  }
}

export class RecorderSinkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RecorderSinkError";
  }
}

export class RecorderConfigurationError extends RecorderSinkError {
  constructor(message: string) {
    super(message);
    this.name = "RecorderConfigurationError";
  }
}

export class RecorderClosedError extends RecorderSinkError {
  constructor() {
    super("Recorder sink is closing or closed");
    this.name = "RecorderClosedError";
  }
}

export class RecorderOverflowError extends RecorderSinkError {
  constructor() {
    super("Recorder sink capacity is exhausted");
    this.name = "RecorderOverflowError";
  }
}

export class RecorderDeliveryError extends RecorderSinkError {
  constructor(
    public readonly reason: RecorderCaptureGapReason,
    public readonly attempts: number,
    public readonly failureClass: RecorderFailureClass,
  ) {
    super(`Recorder delivery failed: ${reason} (${failureClass})`);
    this.name = "RecorderDeliveryError";
  }
}

export class RecorderFlushError extends RecorderSinkError {
  constructor(public readonly result: RecorderFlushResult) {
    super(`Recorder flush completed with ${result.failed} failed event(s)`);
    this.name = "RecorderFlushError";
  }
}

/**
 * Default retry policy. It retries fetch-style network failures, timeouts,
 * HTTP 408/425/429, and 5xx responses. Other 4xx and unknown programming
 * errors are terminal. A numeric `retryAfterMs` is used only as a bounded
 * delay floor. No response body or exception message is inspected.
 */
export function defaultRecorderRetryClassifier(error: unknown): RecorderRetryDecision {
  const failureClass = recorderFailureClass(error);
  return failureClass === "network"
    || failureClass === "timeout"
    || failureClass === "throttled"
    || failureClass === "server"
    ? "retry"
    : "terminal";
}

/** Classify an error without retaining its message, URL, body, or stack. */
export function recorderFailureClass(error: unknown): RecorderFailureClass {
  try {
    if (error instanceof InvalidRecorderResponseError) return "invalid_response";

    const status = httpStatus(error);
    if (status !== undefined) {
      if (status === 408) return "timeout";
      if (status === 425 || status === 429) return "throttled";
      if (status >= 500 && status <= 599) return "server";
      if (status >= 400 && status <= 499) return "client";
      return "unknown";
    }

    const name = errorName(error);
    if (name === "AbortError" || name === "TimeoutError") return "timeout";
    if (error instanceof SyntaxError) return "invalid_response";
    if (error instanceof TypeError) return "network";
    return "unknown";
  } catch {
    return "unknown";
  }
}

/**
 * Bounded Recorder sink suitable for request handlers, agents, and workers.
 * Callers must observe each record promise or call `flush()`/`close()`.
 * This in-memory queue cannot disclose or recover events lost in a process
 * crash; use a durable outbox when that boundary is unacceptable.
 */
export class RecorderSink {
  private readonly options: NormalizedOptions;
  private readonly queue: PendingEvent[] = [];
  private readonly outstanding = new Set<PendingEvent>();
  private readonly gaps: Record<RecorderCaptureGapReason, number> = emptyGapCounts();
  private sinkState: RecorderSinkState = "open";
  private bufferedBytes = 0;
  private inFlightBatches = 0;
  private batchTimer: ReturnType<typeof setTimeout> | undefined;
  private forceDrainCount = 0;
  private closePromise: Promise<RecorderFlushResult> | undefined;
  private acceptedEvents = 0;
  private duplicateEvents = 0;
  private droppedEvents = 0;
  private terminalFailures = 0;
  private overflowRejections = 0;
  private retryAttempts = 0;
  private gapTotal = 0;
  private reporterFailures = 0;

  constructor(
    private readonly transport: RecorderBatchTransport,
    options: RecorderSinkOptions = {},
  ) {
    if (!transport || typeof transport.ingestRecorderBatch !== "function") {
      throw new RecorderConfigurationError(
        "RecorderSink requires a transport with ingestRecorderBatch()",
      );
    }
    this.options = normalizeOptions(options);
  }

  /**
   * Snapshot and enqueue one event. The promise settles at accepted,
   * duplicate, explicitly dropped, or terminal delivery.
   */
  record(event: RecorderEnvelope): Promise<RecorderDeliveryResult> {
    if (this.sinkState !== "open") {
      this.reportCaptureGap("sink_closed", eventGapContext(event));
      return Promise.reject(new RecorderClosedError());
    }

    let prepared: PreparedEvent;
    try {
      prepared = prepareEvent(event, this.options.maxEventBytes);
    } catch (error) {
      if (error instanceof RecorderPreparationError) {
        return this.handleUnqueuedTerminal(error.prepared, error.reason);
      }
      this.reportCaptureGap("invalid_event_identity", eventGapContext(event));
      this.terminalFailures += 1;
      return Promise.reject(
        error instanceof RecorderSinkError
          ? error
          : new RecorderConfigurationError("Recorder event identity is unavailable"),
      );
    }

    if (!this.hasCapacity(prepared.bytes)) {
      if (this.options.overflowPolicy === "drop_oldest") {
        while (!this.hasCapacity(prepared.bytes) && this.queue.length > 0) {
          const oldest = this.queue.shift();
          if (oldest) this.settleDropped(oldest, "queue_overflow", 0);
        }
      }
      if (!this.hasCapacity(prepared.bytes)) {
        return this.handleOverflow(prepared);
      }
    }

    const pending = pendingEvent(prepared);
    this.queue.push(pending);
    this.outstanding.add(pending);
    this.bufferedBytes += pending.bytes;
    this.schedulePump();
    return pending.promise;
  }

  /**
   * Wait for the events outstanding at call time. Later records may be sent,
   * but are not part of this flush result.
   */
  async flush(): Promise<RecorderFlushResult> {
    const snapshot = Array.from(this.outstanding, (item) => item.promise);
    this.forceDrainCount += 1;
    this.cancelBatchTimer();
    this.pump(true);
    try {
      const outcomes = await Promise.allSettled(snapshot);
      const result: RecorderFlushResult = {
        events: outcomes.length,
        delivered: 0,
        duplicates: 0,
        dropped: 0,
        failed: 0,
      };
      for (const outcome of outcomes) {
        if (outcome.status === "rejected") {
          result.failed += 1;
        } else if (outcome.value.status === "accepted") {
          result.delivered += 1;
        } else if (outcome.value.status === "duplicate") {
          result.duplicates += 1;
        } else {
          result.dropped += 1;
        }
      }
      if (result.failed > 0) throw new RecorderFlushError(result);
      return result;
    } finally {
      this.forceDrainCount -= 1;
      if (this.queue.length > 0) this.schedulePump();
    }
  }

  /** Prevent new records, drain everything already accepted, and close once. */
  close(): Promise<RecorderFlushResult> {
    if (this.closePromise) return this.closePromise;
    this.sinkState = "closing";
    this.closePromise = (async () => {
      this.cancelBatchTimer();
      try {
        return await this.flush();
      } finally {
        this.sinkState = "closed";
        this.cancelBatchTimer();
      }
    })();
    return this.closePromise;
  }

  /** A privacy-safe process snapshot with no event, tenant, URL, or error identity. */
  stats(): RecorderSinkStats {
    return {
      state: this.sinkState,
      bufferedEvents: this.outstanding.size,
      bufferedBytes: this.bufferedBytes,
      queuedEvents: this.queue.length,
      inFlightBatches: this.inFlightBatches,
      acceptedEvents: this.acceptedEvents,
      duplicateEvents: this.duplicateEvents,
      droppedEvents: this.droppedEvents,
      terminalFailures: this.terminalFailures,
      overflowRejections: this.overflowRejections,
      retryAttempts: this.retryAttempts,
      captureGaps: this.gapTotal,
      reporterFailures: this.reporterFailures,
    };
  }

  /** Return bounded aggregate gap counts, optionally resetting the local counters. */
  captureGapSummary(options: { reset?: boolean } = {}): RecorderCaptureGapSummary {
    const summary: RecorderCaptureGapSummary = {
      total: this.gapTotal,
      byReason: { ...this.gaps },
      reporterFailures: this.reporterFailures,
    };
    if (options.reset) {
      for (const reason of GAP_REASONS) this.gaps[reason] = 0;
      this.gapTotal = 0;
      this.reporterFailures = 0;
    }
    return summary;
  }

  /** Allow public framework adapters to report bounded source-side gaps. */
  reportCaptureGap(
    reason: RecorderCaptureGapReason,
    context: RecorderCaptureGapContext = {},
  ): void {
    const boundedReason = includes(GAP_REASONS, reason) ? reason : "adapter_failure";
    const affectedEvents = boundedInteger(context.affectedEvents ?? 1, 1, 10_000);
    const attempts = boundedInteger(context.attempts ?? 0, 0, this.options.maxAttempts);
    const failureClass = context.failureClass === undefined
      ? "none"
      : includes(FAILURE_CLASSES, context.failureClass)
        ? context.failureClass
        : "unknown";
    const gap: RecorderCaptureGap = {
      reason: boundedReason,
      affectedEvents,
      attempts,
      failureClass,
      protocol: boundedGapProtocol(context.protocol),
      captureMode: boundedGapCaptureMode(context.captureMode),
      overflowPolicy: this.options.overflowPolicy,
      terminalDeliveryPolicy: this.options.terminalDeliveryPolicy,
      observedAt: new Date().toISOString(),
    };
    this.gaps[boundedReason] += affectedEvents;
    this.gapTotal += affectedEvents;

    const reporter = this.options.onCaptureGap;
    if (!reporter) return;
    try {
      const result = reporter(gap);
      if (result && typeof result.then === "function") {
        Promise.resolve(result).catch(() => {
          this.reporterFailures += 1;
        });
      }
    } catch {
      this.reporterFailures += 1;
    }
  }

  private handleUnqueuedTerminal(
    prepared: Omit<PreparedEvent, "serialized" | "bytes">,
    reason: "serialization_failed" | "event_too_large" | "invalid_event",
  ): Promise<RecorderDeliveryResult> {
    this.terminalFailures += 1;
    this.reportCaptureGap(reason, {
      protocol: prepared.protocol,
      captureMode: prepared.captureMode,
    });
    if (this.options.terminalDeliveryPolicy === "reject") {
      return Promise.reject(new RecorderDeliveryError(reason, 0, "client"));
    }
    this.droppedEvents += 1;
    return Promise.resolve({
      eventId: prepared.eventId,
      idempotencyKey: prepared.idempotencyKey,
      status: "dropped",
      attempts: 0,
      gapReason: reason,
    });
  }

  private handleOverflow(prepared: PreparedEvent): Promise<RecorderDeliveryResult> {
    this.reportCaptureGap("queue_overflow", {
      protocol: prepared.protocol,
      captureMode: prepared.captureMode,
    });
    if (this.options.overflowPolicy === "reject_newest") {
      this.overflowRejections += 1;
      return Promise.reject(new RecorderOverflowError());
    }
    this.droppedEvents += 1;
    return Promise.resolve({
      eventId: prepared.eventId,
      idempotencyKey: prepared.idempotencyKey,
      status: "dropped",
      attempts: 0,
      gapReason: "queue_overflow",
    });
  }

  private hasCapacity(nextBytes: number): boolean {
    return this.outstanding.size < this.options.maxBufferedEvents
      && this.bufferedBytes + nextBytes <= this.options.maxBufferedBytes;
  }

  private schedulePump(): void {
    if (this.queue.length === 0 || this.inFlightBatches >= this.options.maxConcurrency) return;
    if (this.forceDrainCount > 0 || this.queue.length >= this.options.maxBatchSize) {
      this.cancelBatchTimer();
      this.pump(true);
      return;
    }
    if (this.batchTimer !== undefined) return;
    if (this.options.maxBatchDelayMs === 0) {
      this.pump(true);
      return;
    }
    this.batchTimer = setTimeout(() => {
      this.batchTimer = undefined;
      this.pump(true);
    }, this.options.maxBatchDelayMs);
  }

  private cancelBatchTimer(): void {
    if (this.batchTimer !== undefined) {
      clearTimeout(this.batchTimer);
      this.batchTimer = undefined;
    }
  }

  private pump(force = this.forceDrainCount > 0): void {
    while (
      this.queue.length > 0
      && this.inFlightBatches < this.options.maxConcurrency
    ) {
      if (!force && this.queue.length < this.options.maxBatchSize) {
        this.schedulePump();
        return;
      }
      const batch = this.takeBatch();
      if (batch.length === 0) return;
      this.inFlightBatches += 1;
      void this.deliverBatch(batch).finally(() => {
        this.inFlightBatches -= 1;
        if (this.queue.length > 0) {
          if (this.forceDrainCount > 0) this.pump(true);
          else this.schedulePump();
        }
      });
    }
  }

  private takeBatch(): PendingEvent[] {
    const batch: PendingEvent[] = [];
    let bytes = 0;
    while (batch.length < this.options.maxBatchSize && this.queue.length > 0) {
      const next = this.queue[0];
      if (!next) break;
      if (batch.length > 0 && bytes + next.bytes > this.options.maxBatchBytes) break;
      this.queue.shift();
      batch.push(next);
      bytes += next.bytes;
    }
    return batch;
  }

  private async deliverBatch(items: PendingEvent[]): Promise<void> {
    for (let attempt = 1; attempt <= this.options.maxAttempts; attempt += 1) {
      try {
        const response = await this.transport.ingestRecorderBatch(
          items.map((item) => parseEnvelopeSnapshot(item.serialized)),
          { atomic: this.options.atomicBatches },
        );
        try {
          this.applyBatchResponse(items, response, attempt);
        } catch {
          throw new InvalidRecorderResponseError();
        }
        return;
      } catch (error) {
        const failureClass = recorderFailureClass(error);
        const decision = this.retryDecision(error, {
          attempt,
          maxAttempts: this.options.maxAttempts,
          failureClass,
        });
        if (decision === "retry" && attempt < this.options.maxAttempts) {
          this.retryAttempts += 1;
          await delay(this.retryDelay(attempt, error));
          continue;
        }
        const reason: RecorderCaptureGapReason = failureClass === "invalid_response"
          ? "invalid_response"
          : decision === "retry"
            ? "retry_exhausted"
            : "delivery_rejected";
        this.failBatch(items, reason, attempt, failureClass);
        return;
      }
    }
  }

  private applyBatchResponse(
    items: PendingEvent[],
    response: RecorderBatchResult,
    attempts: number,
  ): void {
    if (!isBatchResponseShape(response, items.length)) {
      throw new InvalidRecorderResponseError();
    }
    const rejected = new Map<number, true>();
    for (const rejection of response.rejections) {
      if (
        !rejection
        || typeof rejection !== "object"
        || !Number.isInteger(rejection.index)
        || rejection.index < 0
        || rejection.index >= items.length
        || rejected.has(rejection.index)
      ) {
        throw new InvalidRecorderResponseError();
      }
      rejected.set(rejection.index, true);
    }
    if (response.results.length !== items.length - rejected.size) {
      throw new InvalidRecorderResponseError();
    }
    if (response.results.some((result) => !isRecorderIngestResultShape(result))) {
      throw new InvalidRecorderResponseError();
    }
    const accepted = response.results.filter((result) => result.accepted).length;
    const duplicates = response.results.filter((result) => result.duplicate).length;
    if (
      response.accepted !== accepted
      || response.duplicates !== duplicates
      || response.rejected !== rejected.size
      || accepted + duplicates + rejected.size !== items.length
    ) {
      throw new InvalidRecorderResponseError();
    }

    let resultIndex = 0;
    for (let index = 0; index < items.length; index += 1) {
      const item = items[index];
      if (!item) throw new InvalidRecorderResponseError();
      if (rejected.has(index)) {
        this.settleFailure(item, "delivery_rejected", attempts, "client");
        continue;
      }
      const result = response.results[resultIndex];
      resultIndex += 1;
      if (!result) throw new InvalidRecorderResponseError();
      this.settleDelivered(item, result, attempts);
    }
  }

  private retryDecision(error: unknown, context: RecorderRetryContext): RecorderRetryDecision {
    const classifier = this.options.classifyError;
    if (!classifier) return defaultRecorderRetryClassifier(error);
    try {
      return classifier(error, context) === "retry" ? "retry" : "terminal";
    } catch {
      return "terminal";
    }
  }

  private retryDelay(failedAttempt: number, error: unknown): number {
    const exponent = Math.min(20, Math.max(0, failedAttempt - 1));
    const cap = Math.min(
      this.options.maxRetryDelayMs,
      this.options.baseRetryDelayMs * 2 ** exponent,
    );
    let random = 0.5;
    try {
      random = this.options.random();
    } catch {
      // A custom jitter source must not strand in-flight events.
    }
    if (!Number.isFinite(random)) random = 0.5;
    random = Math.min(0.999999999, Math.max(0, random));
    const jitter = Math.floor(random * cap);
    const serverFloor = Math.min(
      this.options.maxRetryDelayMs,
      retryAfterMilliseconds(error) ?? 0,
    );
    return Math.max(jitter, serverFloor);
  }

  private failBatch(
    items: PendingEvent[],
    reason: RecorderCaptureGapReason,
    attempts: number,
    failureClass: RecorderFailureClass,
  ): void {
    const protocol = combinedProtocol(items);
    const captureMode = combinedCaptureMode(items);
    this.reportCaptureGap(reason, {
      affectedEvents: items.length,
      attempts,
      failureClass,
      protocol,
      captureMode,
    });
    for (const item of items) {
      this.settleFailure(item, reason, attempts, failureClass, false);
    }
  }

  private settleDelivered(
    item: PendingEvent,
    result: RecorderIngestResult,
    attempts: number,
  ): void {
    if (!this.beginSettlement(item)) return;
    if (result.duplicate) this.duplicateEvents += 1;
    else this.acceptedEvents += 1;
    item.resolve({
      eventId: item.eventId,
      idempotencyKey: item.idempotencyKey,
      status: result.duplicate ? "duplicate" : "accepted",
      attempts,
      result,
    });
  }

  private settleFailure(
    item: PendingEvent,
    reason: RecorderCaptureGapReason,
    attempts: number,
    failureClass: RecorderFailureClass,
    report = true,
  ): void {
    if (!this.beginSettlement(item)) return;
    this.terminalFailures += 1;
    if (report) {
      this.reportCaptureGap(reason, {
        attempts,
        failureClass,
        protocol: item.protocol,
        captureMode: item.captureMode,
      });
    }
    if (this.options.terminalDeliveryPolicy === "reject") {
      item.reject(new RecorderDeliveryError(reason, attempts, failureClass));
    } else {
      this.droppedEvents += 1;
      item.resolve({
        eventId: item.eventId,
        idempotencyKey: item.idempotencyKey,
        status: "dropped",
        attempts,
        gapReason: reason,
      });
    }
  }

  private settleDropped(
    item: PendingEvent,
    reason: RecorderCaptureGapReason,
    attempts: number,
  ): void {
    if (!this.beginSettlement(item)) return;
    this.droppedEvents += 1;
    this.reportCaptureGap(reason, {
      attempts,
      protocol: item.protocol,
      captureMode: item.captureMode,
    });
    item.resolve({
      eventId: item.eventId,
      idempotencyKey: item.idempotencyKey,
      status: "dropped",
      attempts,
      gapReason: reason,
    });
  }

  private beginSettlement(item: PendingEvent): boolean {
    if (item.settled) return false;
    item.settled = true;
    this.outstanding.delete(item);
    this.bufferedBytes = Math.max(0, this.bufferedBytes - item.bytes);
    return true;
  }
}

function normalizeOptions(options: RecorderSinkOptions): NormalizedOptions {
  if (options.classifyError !== undefined && typeof options.classifyError !== "function") {
    throw new RecorderConfigurationError("classifyError must be a function");
  }
  if (options.onCaptureGap !== undefined && typeof options.onCaptureGap !== "function") {
    throw new RecorderConfigurationError("onCaptureGap must be a function");
  }
  if (options.random !== undefined && typeof options.random !== "function") {
    throw new RecorderConfigurationError("random must be a function");
  }
  if (options.atomicBatches !== undefined && typeof options.atomicBatches !== "boolean") {
    throw new RecorderConfigurationError("atomicBatches must be a boolean");
  }
  const normalized: NormalizedOptions = {
    maxBufferedEvents: integerOption(
      "maxBufferedEvents",
      options.maxBufferedEvents ?? 1_000,
      1,
      10_000,
    ),
    maxBufferedBytes: integerOption(
      "maxBufferedBytes",
      options.maxBufferedBytes ?? 32 * 1024 * 1024,
      1_024,
      512 * 1024 * 1024,
    ),
    maxBatchSize: integerOption("maxBatchSize", options.maxBatchSize ?? 100, 1, 500),
    maxBatchBytes: integerOption(
      "maxBatchBytes",
      options.maxBatchBytes ?? 1024 * 1024,
      1_024,
      32 * 1024 * 1024,
    ),
    maxEventBytes: integerOption(
      "maxEventBytes",
      options.maxEventBytes ?? 256 * 1024,
      256,
      16 * 1024 * 1024,
    ),
    maxConcurrency: integerOption("maxConcurrency", options.maxConcurrency ?? 1, 1, 16),
    maxAttempts: integerOption("maxAttempts", options.maxAttempts ?? 5, 1, 20),
    baseRetryDelayMs: integerOption(
      "baseRetryDelayMs",
      options.baseRetryDelayMs ?? 250,
      1,
      60_000,
    ),
    maxRetryDelayMs: integerOption(
      "maxRetryDelayMs",
      options.maxRetryDelayMs ?? 10_000,
      1,
      300_000,
    ),
    maxBatchDelayMs: integerOption(
      "maxBatchDelayMs",
      options.maxBatchDelayMs ?? 25,
      0,
      60_000,
    ),
    overflowPolicy: options.overflowPolicy ?? "reject_newest",
    terminalDeliveryPolicy: options.terminalDeliveryPolicy ?? "reject",
    atomicBatches: options.atomicBatches ?? false,
    classifyError: options.classifyError,
    onCaptureGap: options.onCaptureGap,
    random: options.random ?? Math.random,
  };
  if (!["reject_newest", "drop_newest", "drop_oldest"].includes(normalized.overflowPolicy)) {
    throw new RecorderConfigurationError("overflowPolicy is invalid");
  }
  if (!["reject", "drop"].includes(normalized.terminalDeliveryPolicy)) {
    throw new RecorderConfigurationError("terminalDeliveryPolicy is invalid");
  }
  if (normalized.maxEventBytes > normalized.maxBatchBytes) {
    throw new RecorderConfigurationError("maxEventBytes cannot exceed maxBatchBytes");
  }
  if (normalized.maxBatchBytes > normalized.maxBufferedBytes) {
    throw new RecorderConfigurationError("maxBatchBytes cannot exceed maxBufferedBytes");
  }
  if (normalized.baseRetryDelayMs > normalized.maxRetryDelayMs) {
    throw new RecorderConfigurationError("baseRetryDelayMs cannot exceed maxRetryDelayMs");
  }
  return normalized;
}

function integerOption(name: string, value: number, min: number, max: number): number {
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new RecorderConfigurationError(`${name} must be an integer from ${min} to ${max}`);
  }
  return value;
}

function prepareEvent(event: RecorderEnvelope, maxEventBytes: number): PreparedEvent {
  if (!event || typeof event !== "object") {
    throw new RecorderConfigurationError("Recorder event must be an object");
  }
  const eventId = stableIdentity(event.event_id, "event_id");
  const idempotencyKey = stableIdentity(event.idempotency_key ?? eventId, "idempotency_key");
  const protocol = boundedProtocol(event.protocol);
  const captureMode = boundedCaptureMode(event.capture?.mode);
  const identity = { eventId, idempotencyKey, protocol, captureMode };

  let serialized: string;
  try {
    serialized = JSON.stringify({
      ...event,
      event_id: eventId,
      idempotency_key: idempotencyKey,
    });
    if (typeof serialized !== "string") {
      throw new Error("Recorder event did not serialize to JSON");
    }
  } catch {
    throw new RecorderPreparationError("serialization_failed", identity);
  }
  const bytes = utf8ByteLength(serialized);
  if (bytes > maxEventBytes) {
    throw new RecorderPreparationError("event_too_large", identity);
  }
  let snapshot: unknown;
  try {
    snapshot = JSON.parse(serialized) as unknown;
  } catch {
    throw new RecorderPreparationError("serialization_failed", identity);
  }
  validateEnvelopeSnapshot(snapshot, identity);
  return {
    serialized,
    ...identity,
    bytes,
  };
}

function validateEnvelopeSnapshot(
  value: unknown,
  identity: Omit<PreparedEvent, "serialized" | "bytes">,
): asserts value is RecorderEnvelope {
  if (!isRecord(value)) invalidRecorderEvent(identity);
  if (!includes(PROTOCOLS, value.protocol)) invalidRecorderEvent(identity);
  if (value.schema_version !== undefined && value.schema_version !== "0.1") {
    invalidRecorderEvent(identity);
  }
  optionalBoundedText(value, "event_type", 128, identity);
  optionalBoundedText(value, "subject_id", 512, identity);
  if (
    value.occurred_at !== undefined
    && value.occurred_at !== null
    && (typeof value.occurred_at !== "string" || !Number.isFinite(Date.parse(value.occurred_at)))
  ) {
    invalidRecorderEvent(identity);
  }
  if (!isRecord(value.payload) || Object.keys(value.payload).length > 1_000) {
    invalidRecorderEvent(identity);
  }
  if (value.extensions !== undefined) {
    if (!isRecord(value.extensions) || Object.keys(value.extensions).length > 256) {
      invalidRecorderEvent(identity);
    }
  }
  validateActor(value.actor, identity);
  validateCorrelation(value.correlation, identity);
  validateCapture(value.capture, identity);
}

function validateActor(
  value: unknown,
  identity: Omit<PreparedEvent, "serialized" | "bytes">,
): void {
  if (value === undefined) return;
  if (!isRecord(value)) invalidRecorderEvent(identity);
  optionalBoundedText(value, "agent_id", 255, identity);
  optionalBoundedText(value, "principal_id", 512, identity);
  if (value.roles !== undefined) {
    if (
      !Array.isArray(value.roles)
      || value.roles.length > 100
      || value.roles.some((role) => typeof role !== "string" || role.length < 1 || role.length > 255)
    ) {
      invalidRecorderEvent(identity);
    }
  }
  const authenticationContext = value.authentication_context;
  if (
    authenticationContext !== undefined
    && (!isRecord(authenticationContext) || Object.keys(authenticationContext).length > 100)
  ) {
    invalidRecorderEvent(identity);
  }
  const extensions = value.extensions;
  if (extensions !== undefined && (!isRecord(extensions) || Object.keys(extensions).length > 256)) {
    invalidRecorderEvent(identity);
  }
}

function validateCorrelation(
  value: unknown,
  identity: Omit<PreparedEvent, "serialized" | "bytes">,
): void {
  if (value === undefined) return;
  if (!isRecord(value)) invalidRecorderEvent(identity);
  for (const key of ["trace_id", "span_id", "parent_span_id", "decision_id"] as const) {
    optionalBoundedText(value, key, 64, identity);
  }
  for (const key of [
    "run_id",
    "session_id",
    "task_id",
    "context_id",
    "message_id",
    "tool_call_id",
  ] as const) {
    optionalBoundedText(value, key, 512, identity);
  }
  if (value.extensions !== undefined) {
    if (!isRecord(value.extensions) || Object.keys(value.extensions).length > 256) {
      invalidRecorderEvent(identity);
    }
  }
}

function validateCapture(
  value: unknown,
  identity: Omit<PreparedEvent, "serialized" | "bytes">,
): void {
  if (value === undefined) return;
  if (!isRecord(value)) invalidRecorderEvent(identity);
  if (value.mode !== undefined && !includes(CAPTURE_MODES, value.mode)) {
    invalidRecorderEvent(identity);
  }
  if (value.sensitive_fields !== undefined) {
    if (
      !Array.isArray(value.sensitive_fields)
      || value.sensitive_fields.length > 100
      || value.sensitive_fields.some((field) => typeof field !== "string")
    ) {
      invalidRecorderEvent(identity);
    }
  }
}

function optionalBoundedText(
  record: Record<string, unknown>,
  key: string,
  maximum: number,
  identity: Omit<PreparedEvent, "serialized" | "bytes">,
): void {
  const value = record[key];
  if (
    value !== undefined
    && value !== null
    && (typeof value !== "string" || value.length < 1 || value.length > maximum)
  ) {
    invalidRecorderEvent(identity);
  }
}

function invalidRecorderEvent(
  identity: Omit<PreparedEvent, "serialized" | "bytes">,
): never {
  throw new RecorderPreparationError("invalid_event", identity);
}

function stableIdentity(value: string | undefined, field: string): string {
  const identity = value ?? secureEventId();
  if (typeof identity !== "string" || identity.length < 1 || identity.length > 512) {
    throw new RecorderConfigurationError(`${field} must contain 1 to 512 characters`);
  }
  return identity;
}

/** Generate a cryptographically random UUID without adding a runtime dependency. */
export function secureRecorderEventId(): string {
  return secureEventId();
}

function secureEventId(): string {
  const runtime = globalThis as unknown as {
    crypto?: {
      randomUUID?: () => string;
      getRandomValues?: (target: Uint8Array) => Uint8Array;
    };
  };
  if (runtime.crypto?.randomUUID) return runtime.crypto.randomUUID();
  if (runtime.crypto?.getRandomValues) {
    const bytes = runtime.crypto.getRandomValues(new Uint8Array(16));
    const byte6 = bytes[6];
    const byte8 = bytes[8];
    if (byte6 === undefined || byte8 === undefined) {
      throw new RecorderConfigurationError("Secure event identity generation failed");
    }
    bytes[6] = (byte6 & 0x0f) | 0x40;
    bytes[8] = (byte8 & 0x3f) | 0x80;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex
      .slice(6, 8)
      .join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
  }
  if (typeof require === "function") {
    try {
      const nodeCrypto = require("node:crypto") as { randomUUID?: () => string };
      if (nodeCrypto.randomUUID) return nodeCrypto.randomUUID();
    } catch {
      // Browser/edge runtimes should provide Web Crypto instead.
    }
  }
  throw new RecorderConfigurationError(
    "No Web Crypto randomUUID/getRandomValues implementation is available; supply event_id",
  );
}

function pendingEvent(prepared: PreparedEvent): PendingEvent {
  let resolve!: (value: RecorderDeliveryResult) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<RecorderDeliveryResult>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { ...prepared, promise, resolve, reject, settled: false };
}

function isBatchResponseShape(value: unknown, expected: number): value is RecorderBatchResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<RecorderBatchResult>;
  return result.received === expected
    && Array.isArray(result.results)
    && Array.isArray(result.rejections);
}

function isRecorderIngestResultShape(value: unknown): value is RecorderIngestResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<RecorderIngestResult>;
  return typeof result.accepted === "boolean"
    && typeof result.duplicate === "boolean"
    && result.accepted !== result.duplicate;
}

function parseEnvelopeSnapshot(serialized: string): RecorderEnvelope {
  // Preparation produced this exact JSON. Reparse per attempt so an arbitrary
  // transport cannot change a later retry by mutating its input object.
  return JSON.parse(serialized) as RecorderEnvelope;
}

function httpStatus(error: unknown): number | undefined {
  try {
    if (!error || typeof error !== "object") return undefined;
    const status = (error as { status?: unknown }).status;
    return typeof status === "number" && Number.isInteger(status) ? status : undefined;
  } catch {
    return undefined;
  }
}

function errorName(error: unknown): string | undefined {
  try {
    if (!error || typeof error !== "object") return undefined;
    const name = (error as { name?: unknown }).name;
    return typeof name === "string" ? name : undefined;
  } catch {
    return undefined;
  }
}

function retryAfterMilliseconds(error: unknown): number | undefined {
  try {
    if (!error || typeof error !== "object") return undefined;
    const value = (error as { retryAfterMs?: unknown }).retryAfterMs;
    return typeof value === "number" && Number.isFinite(value) && value >= 0
      ? Math.floor(value)
      : undefined;
  } catch {
    return undefined;
  }
}

function boundedProtocol(value: unknown): RecorderGapProtocol {
  return includes(PROTOCOLS, value) ? value : "unknown";
}

function boundedCaptureMode(value: unknown): RecorderGapCaptureMode {
  return includes(CAPTURE_MODES, value) ? value : "unknown";
}

function boundedGapProtocol(value: unknown): RecorderGapProtocol {
  return value === "mixed" || value === "unknown" ? value : boundedProtocol(value);
}

function boundedGapCaptureMode(value: unknown): RecorderGapCaptureMode {
  return value === "mixed" || value === "unknown" ? value : boundedCaptureMode(value);
}

function eventGapContext(event: unknown): RecorderCaptureGapContext {
  try {
    if (!isRecord(event)) return { protocol: "unknown", captureMode: "unknown" };
    const capture = isRecord(event.capture) ? event.capture : undefined;
    return {
      protocol: boundedProtocol(event.protocol),
      captureMode: boundedCaptureMode(capture?.mode),
    };
  } catch {
    return { protocol: "unknown", captureMode: "unknown" };
  }
}

function combinedProtocol(items: PendingEvent[]): RecorderGapProtocol {
  const first = items[0]?.protocol ?? "unknown";
  return items.every((item) => item.protocol === first) ? first : "mixed";
}

function combinedCaptureMode(items: PendingEvent[]): RecorderGapCaptureMode {
  const first = items[0]?.captureMode ?? "unknown";
  return items.every((item) => item.captureMode === first) ? first : "mixed";
}

function boundedInteger(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, Math.floor(value)));
}

function includes<const T extends readonly unknown[]>(values: T, value: unknown): value is T[number] {
  return values.includes(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function emptyGapCounts(): Record<RecorderCaptureGapReason, number> {
  return Object.fromEntries(GAP_REASONS.map((reason) => [reason, 0])) as Record<
    RecorderCaptureGapReason,
    number
  >;
}

function utf8ByteLength(value: string): number {
  let bytes = 0;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code <= 0x7f) bytes += 1;
    else if (code <= 0x7ff) bytes += 2;
    else if (code >= 0xd800 && code <= 0xdbff && index + 1 < value.length) {
      const next = value.charCodeAt(index + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        bytes += 4;
        index += 1;
      } else {
        bytes += 3;
      }
    } else {
      bytes += 3;
    }
  }
  return bytes;
}

function delay(milliseconds: number): Promise<void> {
  if (milliseconds <= 0) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
