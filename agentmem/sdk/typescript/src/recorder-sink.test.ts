import { describe, expect, it } from "@jest/globals";

import { LiansError } from "./client.js";
import {
  RecorderClosedError,
  RecorderFlushError,
  RecorderOverflowError,
  RecorderSink,
  defaultRecorderRetryClassifier,
} from "./recorder-sink.js";
import type {
  RecorderBatchResult,
  RecorderEnvelope,
  RecorderIngestResult,
} from "./types.js";

function event(id: string, payload: Record<string, unknown> = {}): RecorderEnvelope {
  return {
    protocol: "lians",
    event_id: id,
    idempotency_key: id,
    capture: { mode: "metadata_only" },
    payload,
  };
}

function accepted(id: string): RecorderIngestResult {
  return {
    accepted: true,
    duplicate: false,
    event: { id },
    readiness: {},
  } as unknown as RecorderIngestResult;
}

function batch(events: RecorderEnvelope[]): RecorderBatchResult {
  return {
    received: events.length,
    accepted: events.length,
    duplicates: 0,
    rejected: 0,
    results: events.map((item) => accepted(item.event_id ?? "missing")),
    rejections: [],
    ready_run_ids: [],
  };
}

describe("RecorderSink delivery contract", () => {
  it("replays the identical frozen envelope and identity after a retry", async () => {
    const attempts: string[] = [];
    const transport = {
      async ingestRecorderBatch(events: RecorderEnvelope[]): Promise<RecorderBatchResult> {
        attempts.push(JSON.stringify(events));
        if (attempts.length === 1) {
          events[0]!.payload = { transport_mutation: true };
          throw new LiansError(503, "not retained", "temporary");
        }
        return batch(events);
      },
    };
    const sink = new RecorderSink(transport, {
      maxBatchDelayMs: 0,
      maxAttempts: 2,
      baseRetryDelayMs: 1,
      maxRetryDelayMs: 1,
      random: () => 0,
    });
    const source = event("run-1:step:0", { state: "original" });
    const delivery = sink.record(source);
    source.payload.state = "caller-mutation";

    await expect(delivery).resolves.toMatchObject({
      eventId: "run-1:step:0",
      idempotencyKey: "run-1:step:0",
      status: "accepted",
      attempts: 2,
    });
    expect(attempts).toHaveLength(2);
    expect(attempts[1]).toBe(attempts[0]);
  });

  it("bounds queued plus in-flight work and rejects the newest event explicitly", async () => {
    let release!: (value: RecorderBatchResult) => void;
    const firstResponse = new Promise<RecorderBatchResult>((resolve) => {
      release = resolve;
    });
    let calls = 0;
    const transport = {
      ingestRecorderBatch(events: RecorderEnvelope[]): Promise<RecorderBatchResult> {
        calls += 1;
        return calls === 1 ? firstResponse : Promise.resolve(batch(events));
      },
    };
    const sink = new RecorderSink(transport, {
      maxBufferedEvents: 1,
      maxBufferedBytes: 1_024,
      maxBatchBytes: 1_024,
      maxEventBytes: 512,
      maxBatchDelayMs: 0,
      overflowPolicy: "reject_newest",
    });

    const first = sink.record(event("one"));
    await expect(sink.record(event("two"))).rejects.toBeInstanceOf(RecorderOverflowError);
    expect(sink.stats()).toMatchObject({ bufferedEvents: 1, inFlightBatches: 1 });
    release(batch([event("one")]));
    await first;
    await sink.close();
  });

  it("turns malformed responses into terminal, privacy-safe capture gaps", async () => {
    const observed: unknown[] = [];
    const sink = new RecorderSink({
      async ingestRecorderBatch(): Promise<RecorderBatchResult> {
        return {
          received: 1,
          accepted: 1,
          duplicates: 0,
          rejected: 0,
          results: [],
          rejections: [],
          ready_run_ids: [],
        };
      },
    }, {
      maxBatchDelayMs: 0,
      maxAttempts: 1,
      onCaptureGap: (gap) => {
        observed.push(gap);
      },
    });
    const secret = "do-not-report-this-payload";

    await expect(sink.record(event("do-not-report-this-id", { secret })))
      .rejects.toMatchObject({ reason: "invalid_response" });
    const disclosure = JSON.stringify(observed);
    expect(disclosure).not.toContain(secret);
    expect(disclosure).not.toContain("do-not-report-this-id");
    expect(disclosure).toContain("invalid_response");
  });

  it("rejects a locally invalid envelope before a non-atomic transport call", async () => {
    let transportCalls = 0;
    const sink = new RecorderSink({
      async ingestRecorderBatch(events: RecorderEnvelope[]): Promise<RecorderBatchResult> {
        transportCalls += 1;
        return batch(events);
      },
    });
    const malformed = {
      ...event("invalid"),
      protocol: "private-protocol",
    } as unknown as RecorderEnvelope;

    await expect(sink.record(malformed)).rejects.toMatchObject({ reason: "invalid_event" });
    expect(transportCalls).toBe(0);
  });

  it("exposes failed terminal delivery through flush and closes idempotently", async () => {
    const sink = new RecorderSink({
      async ingestRecorderBatch(): Promise<RecorderBatchResult> {
        throw new LiansError(400, "payload omitted from gaps", "terminal");
      },
    }, { maxBatchDelayMs: 60_000, maxAttempts: 1 });
    const delivery = sink.record(event("terminal"));
    void delivery.catch(() => undefined);

    await expect(sink.flush()).rejects.toBeInstanceOf(RecorderFlushError);
    await expect(delivery).rejects.toMatchObject({ reason: "delivery_rejected" });
    const firstClose = sink.close();
    expect(sink.close()).toBe(firstClose);
    await firstClose;
    await expect(sink.record(event("late"))).rejects.toBeInstanceOf(RecorderClosedError);
  });

  it("classifies only bounded transient categories as retryable by default", () => {
    expect(defaultRecorderRetryClassifier({ status: 429 })).toBe("retry");
    expect(defaultRecorderRetryClassifier({ status: 503 })).toBe("retry");
    expect(defaultRecorderRetryClassifier({ status: 409 })).toBe("terminal");
    expect(defaultRecorderRetryClassifier(new Error("programming failure"))).toBe("terminal");
  });
});
