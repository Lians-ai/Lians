/** Stable public Vercel AI SDK callbacks with bounded Lians delivery. */

import { randomUUID } from "node:crypto";
import { streamText } from "ai";
import { LiansClient, RecorderSink } from "../src/index.js";
import {
  createVercelAiStreamRecorderCallbacks,
} from "../src/vercel-ai.js";

async function main(): Promise<void> {
const recorder = new RecorderSink(new LiansClient({
  baseUrl: process.env.LIANS_URL ?? "http://localhost:8000",
  apiKey: process.env.LIANS_API_KEY,
  accessToken: process.env.LIANS_ACCESS_TOKEN,
}), {
  maxBufferedEvents: 1_000,
  maxBatchSize: 100,
  overflowPolicy: "reject_newest",
  terminalDeliveryPolicy: "reject",
  onCaptureGap: ({ reason, failureClass, affectedEvents }) => {
    console.warn("Recorder capture gap", { reason, failureClass, affectedEvents });
  },
});

const runId = randomUUID();

try {
  const result = streamText({
    model: process.env.AI_MODEL ?? "openai/gpt-5-mini",
    prompt: "Give one concise reason to use optimistic concurrency.",
    ...createVercelAiStreamRecorderCallbacks(recorder, {
      runId,
      operationId: "explain-optimistic-concurrency",
      operationName: "support.answer",
      modelId: process.env.AI_MODEL ?? "openai/gpt-5-mini",
      captureMode: "metadata_only",
    }),
  });

  // A stream must be consumed for finish/abort/error callbacks to be observed.
  for await (const text of result.textStream) process.stdout.write(text);
  await recorder.flush();
} finally {
  await recorder.close();
}
}

void main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : "Vercel AI Recorder example failed");
  process.exitCode = 1;
});
