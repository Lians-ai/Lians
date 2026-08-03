<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/images/logo.png" width="340" alt="Lians logo">
  </a>
</p>

# @lians-ai/lians

**Provider-neutral decision evidence and runtime control for TypeScript and Node.** Record verifiable decision boundaries, enforce protected actions, reconstruct historical context, and retain governed bitemporal memory.

## Install

```bash
npm install @lians-ai/lians
```

This client connects to a self-hosted or managed Lians server. For zero-setup local prototyping, use the Python SDK's SQLite-backed `LocalLiansClient`.

## Quickstart

```ts
import { LiansClient } from "@lians-ai/lians";

const client = new LiansClient({
  baseUrl: "https://mem.yourfirm.internal",
  apiKey: process.env.LIANS_API_KEY!,
});

await client.addMemory({
  agent_id: "equity-desk",
  content: "NVDA FY2026 revenue guidance raised to $40B",
  event_time: "2025-11-19T16:00:00Z",
  metadata: { ticker: "NVDA", metric: "revenue_guidance" },
});

const { memories } = await client.recall({
  agent_id: "equity-desk",
  query: "NVDA revenue guidance",
});

const snapshot = await client.snapshot({
  agent_id: "equity-desk",
  as_of: "2025-03-01T00:00:00Z",
});

const report = await client.backtestCheck({
  agent_id: "equity-desk",
  as_of: "2025-01-01T00:00:00Z",
});
```

## Universal Recorder and runtime Gate

```ts
import { LiansClient, liansEvent } from "@lians-ai/lians";

const event = liansEvent(
  "decision.completed",
  { model_id: "review-v1", input: { synthetic: true }, output: "review" },
  { runId: "synthetic-run-1", idempotencyKey: "synthetic-run-1:decision.completed" },
);
const capture = await client.ingestRecorderEvent(event);
const identity = await client.whoami();
```

Native Lians, OTLP GenAI, MCP JSON-RPC, and A2A builders default to hash-only
capture. The typed client also covers Gate policies/evaluations, trusted receipt
keys, investigations, remediation tasks, attested closure, and Investigator
queue/report reads. See the
[synthetic local quickstart](../../../docs/quickstart-recorder.md).

Investigator report v1.1 exposes independent evidence, timeline, control-history,
case, task, and closure limits. Check `report.coverage.complete` and the individual
collection windows before relying on an embedded packet as complete. Capped review
and approval prefixes are explicitly partial, not valid.

### Bounded asynchronous delivery

Use `RecorderSink` instead of issuing one HTTP request per callback:

```ts
import { LiansClient, RecorderSink, liansEvent } from "@lians-ai/lians";

const client = new LiansClient({
  baseUrl: "https://lians.example",
  apiKey: process.env.LIANS_API_KEY!,
});
const recorder = new RecorderSink(client, {
  maxBufferedEvents: 2_000,
  maxBufferedBytes: 32 * 1024 * 1024,
  maxBatchSize: 100,
  maxConcurrency: 2,
  maxAttempts: 5,
  overflowPolicy: "reject_newest",
  terminalDeliveryPolicy: "reject",
  onCaptureGap: (gap) => console.warn("Recorder capture gap", {
    reason: gap.reason,
    failureClass: gap.failureClass,
  }),
});

const delivery = recorder.record(liansEvent(
  "agent.step.completed",
  { phase: "completed", status: "ok", model_id: "review-v1" },
  {
    runId: "run-018f",
    eventId: "run-018f:step:4",
    idempotencyKey: "run-018f:step:4",
  },
));

await delivery;       // barrier for this event
await recorder.flush(); // barrier for everything accepted before this call
await recorder.close(); // prevent submissions and drain once
```

If a decision had more than 500 Recorder events before it was recorded, Lians
commits a durable fixed-snapshot evidence job instead of rejecting the decision
or indexing a prefix. Inspect and, after remediation, retry it with
`recorderEvidenceIndexJob(jobId)` and `retryRecorderEvidenceIndexJob(jobId)`.
`recorderEvidenceIndexJobForDecision(decisionId)` discovers the job from the
authoritative decision. Coverage remains explicitly partial until the exact
snapshot completes.

Use `recorderRunEventsPage(runId, { beforeRecordedAt, beforeId })` to traverse
large run boundaries. It validates exact-total and completeness headers and
returns the next paired `(recorded_at, id)` cursor; `recorderRunEvents` remains
the legacy single-array-page convenience call.

The queue is bounded by event count and serialized bytes, batches are bounded by
count and bytes, and HTTP concurrency is explicit. Each envelope is serialized
once after its event and idempotency identities are assigned; every retry is
recreated from that immutable snapshot. The default retry classifier retries
network/timeout failures, HTTP 408/425/429, and 5xx responses with bounded full
jitter. A valid delta-seconds or HTTP-date `Retry-After` value is honored as a
non-jittered floor, capped by `maxRetryDelayMs`. Other 4xx responses and
malformed transport responses are terminal.

`reject_newest`, `drop_newest`, and `drop_oldest` make overflow behavior
explicit. `terminalDeliveryPolicy: "reject"` makes `record()`, `flush()`, and
`close()` expose unconfirmed delivery; `"drop"` keeps a worker moving but still
increments a closed-vocabulary capture-gap counter. Gap callbacks receive no
event ID, tenant, URL, response body, exception message, or payload.

This is an in-process buffer, not a durable queue. A process crash can lose
accepted buffered events before it can report a gap. Put a durable outbox in
front of the sink when that loss boundary is unacceptable, and provide a
business-stable `event_id`/`idempotency_key` so replay after a restart deduplicates.
See [`examples/buffered-recorder.ts`](./examples/buffered-recorder.ts).

### Vercel AI SDK callbacks

The optional `@lians-ai/lians/vercel-ai` entry point has no dependency on `ai`.
It implements only the current public `onStepFinish`/`onFinish` callbacks and,
for streams, public `onAbort`/`onError` callbacks:

```ts
import { streamText } from "ai";
import { LiansClient, RecorderSink } from "@lians-ai/lians";
import { createVercelAiStreamRecorderCallbacks } from "@lians-ai/lians/vercel-ai";

const recorder = new RecorderSink(new LiansClient({
  baseUrl: "https://lians.example",
  apiKey: process.env.LIANS_API_KEY!,
}));
const requestId = crypto.randomUUID();
const model = "openai/gpt-5-mini";
const prompt = "Explain the decision in one paragraph.";

const result = streamText({
  model,
  prompt,
  ...createVercelAiStreamRecorderCallbacks(recorder, {
    runId: requestId,
    operationId: "answer-customer",
    modelId: "provider/model-version",
    captureMode: "metadata_only",
  }),
});

for await (const part of result.textStream) process.stdout.write(part);
await recorder.close();
```

If the call already has lifecycle callbacks, compose them explicitly; spreading
two callback objects with the same keys replaces the earlier function. Put the
Recorder callback in a `finally` block if evidence should still be attempted
when an application callback throws. Use a current AI SDK release whose stable
`onStepFinish` event includes the zero-based `stepNumber`; without that source
identity the adapter reports `adapter_failure` instead of inventing an ID.

The adapter never reads error objects, provider response bodies, headers,
provider metadata, prompts, or tool arguments/results. Metadata-only capture is
the default; `hash_only` hashes final text locally with Web Crypto and refuses
oversized hash inputs. Raw/full capture is intentionally unavailable. Recorded
events disclose that generation start/input and experimental tool lifecycle
boundaries were not observed. Aborts and errors create payload-free source-gap
records; on abort, the AI SDK does not call `onFinish`.

`runId`, `operationId`, `operationName`, `modelId`, and optional attribution
fields are stored as plaintext correlation metadata. Keep prompts, customer
content, credentials, and secret-bearing URLs out of those fields.

Vercel's lifecycle start/tool callbacks and Telemetry Integration are currently
documented as experimental, so this SDK does not bind them or import private AI
SDK modules. This keeps patch releases from silently breaking evidence capture.
See the official [event callbacks](https://ai-sdk.dev/docs/ai-sdk-core/event-listeners),
[stream error/abort handling](https://ai-sdk.dev/docs/ai-sdk-core/error-handling),
and [experimental telemetry](https://ai-sdk.dev/docs/ai-sdk-core/telemetry)
documentation, plus [`examples/vercel-ai-recorder.ts`](./examples/vercel-ai-recorder.ts).

`evaluateGate()` returns an opaque permit only for `allow`.
`consumeGateExecutionPermit()` must be called by the exact separately
credentialed mediator with the actual action, target, decision, and canonical
request digest immediately before dispatch. Evaluation list/get calls never
return the token. The example consumes only when
`LIANS_MEDIATOR_API_KEY` supplies that separate credential; otherwise it uses
an intentionally unredeemable placeholder and never prints the token. See
[Gate execution permits](../../../docs/gate-execution-permits.md).

## Complete list traversal

Use `listDecisionsPage`, `listLedgerEventsPage`, and
`listEvidenceArtifactsPage` for exact totals and stable keyset traversal of the
legacy JSON-array endpoints. Each returns a typed `CompatibilityListPage` with
`items`, exact `total`, `returned`, `has_more`, `page_complete`, strict
`collection_complete`, and the paired `next_cursor`. The client rejects missing
or inconsistent pagination headers rather than fabricating completeness.

## Decision dependency impact

The fast assessment returns typed indexed/legacy-fallback diagnostics,
including whether the result total is a lower bound. For exhaustive work, use
the durable snapshot workflow and resume it in bounded batches:

```ts
const fast = await client.assessDecisionImpact({
  dependency_kind: "policy",
  dependency_value: "credit-policy-17",
  change_type: "retired",
});
if (fast.total_is_lower_bound) {
  console.log(fast.analysis_mode, fast.legacy_candidates_scanned);
}

let job = await client.startExhaustiveImpactAssessment({
  idempotency_key: "policy-17-retired-v1",
  dependency_kind: "policy",
  dependency_value: "credit-policy-17",
  change_type: "retired",
});
while (job.status === "pending" || job.status === "running") {
  job = await client.advanceExhaustiveImpactAssessment(job.id, {
    page_size: 250,
    max_pages: 10,
  });
}

let after = 0;
for (;;) {
  const page = await client.listExhaustiveImpactAssessmentResults(job.id, {
    after,
    limit: 200,
  });
  page.items.forEach(handleAffectedDecision);
  if (page.next_cursor === null) break;
  after = page.next_cursor;
}
```

`getExhaustiveImpactAssessment()` reads durable progress after a restart or
from a separate worker.

## Optimistic mutation preconditions

Mutable review/control resources require the version returned by the read that
informed the write. For supersession review, pass the exact relationship value,
including `null`, from the selected queue item:

```ts
const review = await client.reviewSupersessions();
const item = review.items[0];
if (item) {
  await client.confirmSupersession(item.memory_id, {
    expected_superseded_by: item.superseded_by,
    reviewer_note: "Compared both source documents.",
  });
}
```

`review.total` is exact. A bounded page is the whole unresolved queue only when
`review.complete` is true. When `has_more` is true, request the next page with
`beforeChainPosition: review.next_chain_position!`.

Conflict pages likewise disclose exact cardinality and continue with the paired
`afterDetectedAt` and `afterId` values; neither cursor component is valid alone.

Investigation/task updates and closures use `expected_updated_at`; task creation
uses the parent case's `expected_case_updated_at`; webhook updates use
`expected_updated_at`, and webhook deletion takes it as the second method
argument. A stale value is a concurrency conflict: refresh the resource, review
the new state, and make a new decision instead of blindly retrying the mutation.

Responses containing generated credentials, webhook secrets, Gate permits,
approval or closure statements are non-cacheable. The TypeScript client never
automatically retries mutations; move returned secrets directly into a secret
manager and do not log the response. After an ambiguous outcome, reconcile
through the corresponding read/list endpoint. Decrypting a closure statement is
an explicit admin-only read:

```ts
const attestation = await client.closureAttestation("case", caseId, {
  includeStatement: true,
});
```

## Expiring workload credentials

Use a human OIDC bearer—not an API key or the cross-tenant break-glass secret—to
manage credentials inside its verified tenant boundary:

```ts
const identityAdmin = new LiansClient({
  baseUrl: "https://lians.example",
  accessToken: humanOidcToken,
});
const created = await identityAdmin.createWorkloadCredential({
  label: "production-recorder",
  role: "analyst",
  ttl_seconds: 86_400,
});
await storeInSecretManager(created.secret); // returned only once
```

See [tenant workload credentials](../../../docs/workload-credentials.md) for
least-privilege, barrier, expiry, and rotation guarantees.

## Why Lians

- Bitemporal facts with event time and ingestion time
- Deterministic supersession before memories reach the model
- Point-in-time recall and lookahead-bias checks
- Tamper-evident audit history and a crypto-erasure workflow
- Information barriers through PostgreSQL row-level security

See the [published benchmark results](https://github.com/Lians-ai/Lians/blob/master/docs/benchmark.md), [regulated-memory evaluation](https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md), and [public correction ledger](https://github.com/Lians-ai/Lians/blob/master/docs/gtm/public-right-of-reply-2026-07-17.md). The evaluation includes runnable adapters so results can be reproduced and challenged.

## TypeScript-first

Every request and response is a named interface exported from the package root. Errors throw a typed `LiansError` with the HTTP status.

Full documentation: [github.com/Lians-ai/Lians](https://github.com/Lians-ai/Lians)
