/** Bounded, retry-safe Universal Recorder delivery. */

import {
  LiansClient,
  RecorderSink,
  liansEvent,
} from "../src/index.js";

async function main(): Promise<void> {
const client = new LiansClient({
  baseUrl: process.env.LIANS_URL ?? "http://localhost:8000",
  apiKey: process.env.LIANS_API_KEY,
  accessToken: process.env.LIANS_ACCESS_TOKEN,
});

const recorder = new RecorderSink(client, {
  maxBufferedEvents: 2_000,
  maxBufferedBytes: 32 * 1024 * 1024,
  maxBatchSize: 100,
  maxBatchBytes: 1024 * 1024,
  maxConcurrency: 2,
  maxAttempts: 5,
  overflowPolicy: "reject_newest",
  terminalDeliveryPolicy: "reject",
  onCaptureGap: ({ reason, failureClass, affectedEvents }) => {
    // These fields come from closed vocabularies and contain no payload or ID.
    console.warn("Recorder capture gap", { reason, failureClass, affectedEvents });
  },
});

const runId = "invoice-review:018f";
const deliveries: Array<ReturnType<RecorderSink["record"]>> = [];

for (let step = 0; step < 12; step += 1) {
  const stableId = `${runId}:step:${step}`;
  const delivery = recorder.record(liansEvent(
    "agent.step.completed",
    {
      name: "invoice-review",
      phase: "completed",
      status: "ok",
      model_id: "review-model-v3",
      step_number: step,
    },
    {
      runId,
      eventId: stableId,
      idempotencyKey: stableId,
      captureMode: "metadata_only",
    },
  ));
  // Install a rejection observer immediately; flush remains the job barrier.
  void delivery.catch(() => undefined);
  deliveries.push(delivery);
}

try {
  await recorder.flush();
  await Promise.all(deliveries);
} finally {
  await recorder.close();
}
}

void main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : "Recorder example failed");
  process.exitCode = 1;
});
