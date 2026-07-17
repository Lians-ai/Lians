<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/images/logo.png" width="340" alt="Lians logo">
  </a>
</p>

# @lians-ai/lians

**Bitemporal long-term memory for TypeScript and Node.** Keep current facts clean, reconstruct what an agent knew at a past time, and retain tamper-evident audit records.

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
