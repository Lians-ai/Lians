import { describe, expect, it } from "@jest/globals";

import { RecorderSink } from "./recorder-sink.js";
import type { RecorderBatchResult, RecorderEnvelope, RecorderIngestResult } from "./types.js";
import {
  createVercelAiRecorderCallbacks,
  createVercelAiStreamRecorderCallbacks,
} from "./vercel-ai.js";

function accepted(id: string): RecorderIngestResult {
  return {
    accepted: true,
    duplicate: false,
    event: { id },
    readiness: {},
  } as unknown as RecorderIngestResult;
}

function collectingSink(captured: RecorderEnvelope[]): RecorderSink {
  return new RecorderSink({
    async ingestRecorderBatch(events: RecorderEnvelope[]): Promise<RecorderBatchResult> {
      captured.push(...events);
      return {
        received: events.length,
        accepted: events.length,
        duplicates: 0,
        rejected: 0,
        results: events.map((item) => accepted(item.event_id ?? "missing")),
        rejections: [],
        ready_run_ids: [],
      };
    },
  }, { maxBatchDelayMs: 0 });
}

describe("Vercel AI SDK stable callback adapter", () => {
  it("uses deterministic per-step identities and does not retain callback text/tool data", async () => {
    const captured: RecorderEnvelope[] = [];
    const sink = collectingSink(captured);
    const callbacks = createVercelAiRecorderCallbacks(sink, {
      runId: "run-42",
      operationId: "answer",
      modelId: "provider/model",
    });
    const secret = "private callback output";

    await callbacks.onStepFinish({
      stepNumber: 2,
      text: secret,
      finishReason: "tool-calls",
      usage: { inputTokens: 10, outputTokens: 4, totalTokens: 14 },
      toolCalls: [{ input: secret }],
      toolResults: [{ output: secret }],
    });
    await sink.flush();

    expect(captured[0]?.event_id).toBe("vercel-ai:6:run-42:6:answer:step:2");
    expect(captured[0]?.idempotency_key).toBe(captured[0]?.event_id);
    const encoded = JSON.stringify(captured[0]);
    expect(encoded).not.toContain(secret);
    expect(encoded).toContain("tool_lifecycle_not_observed");
    expect(encoded).toContain("output_content_omitted_by_policy");
  });

  it("records payload-free abort/error boundaries and aggregate source gaps", async () => {
    const captured: RecorderEnvelope[] = [];
    const sink = collectingSink(captured);
    const callbacks = createVercelAiStreamRecorderCallbacks(sink, {
      runId: "run-99",
      operationId: "stream",
    });
    const secret = "credential-bearing provider error";

    await callbacks.onAbort({ steps: [{ private: secret }] });
    await callbacks.onError({ error: new Error(secret) });
    await sink.flush();

    expect(JSON.stringify(captured)).not.toContain(secret);
    expect(captured.map((item) => item.event_id)).toEqual([
      "vercel-ai:6:run-99:6:stream:abort",
      "vercel-ai:6:run-99:6:stream:error",
    ]);
    expect(sink.captureGapSummary().byReason).toMatchObject({
      source_aborted: 1,
      source_error: 1,
    });
  });
});
