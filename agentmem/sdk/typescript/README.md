# @lians-ai/lians

**Financial-grade AI memory for TypeScript/Node** — bitemporal facts, SEC 17a-4
audit chain, GDPR crypto-shred, information barriers. The TypeScript client for
[Lians](https://github.com/Lians-ai/Lians).

## Install

```bash
npm install @lians-ai/lians
# or: pnpm add @lians-ai/lians   /   yarn add @lians-ai/lians
```

Requires a running Lians server (`docker compose up --build`, or a managed
endpoint). For zero-setup local prototyping without a server, the Python SDK's
`LocalLiansClient` (SQLite) is the fastest path.

## Quickstart

```ts
import { LiansClient } from "@lians-ai/lians";

const client = new LiansClient({
  baseUrl: "https://mem.yourfirm.internal",
  apiKey: process.env.LIANS_API_KEY!,
});

// Write a fact with its event time
await client.addMemory({
  agent_id: "equity-desk",
  content: "NVDA FY2026 revenue guidance raised to $40B",
  event_time: "2025-11-19T16:00:00Z",
  metadata: { ticker: "NVDA", metric: "revenue_guidance" },
});

// Recall — superseded facts are excluded at the DB layer, never reach the LLM
const { memories } = await client.recall({
  agent_id: "equity-desk",
  query: "NVDA revenue guidance",
});

// GDPR Art. 17 crypto-shred — content becomes unreadable, audit trail survives
await client.eraseSubject({ subject_id: "subj-123", reason: "GDPR-REQ-1" });

// Point-in-time audit snapshot — what did we know on a past date?
const snap = await client.snapshot({ agent_id: "equity-desk", as_of: "2025-03-01T00:00:00Z" });

// Backtest lookahead-bias guard — flag facts unknowable at the simulation date
const report = await client.backtestCheck({ agent_id: "equity-desk", as_of: "2025-01-01T00:00:00Z" });
```

## What makes Lians different

| Feature | Lians | mem0 | Graphiti/Zep |
|---------|:----:|:----:|:-----------:|
| Bitemporal model (event + ingestion time) | ✓ | ✗ | ✓ |
| Supersession (stale facts excluded at DB layer) | ✓ | ✗ | Partial |
| SEC 17a-4 tamper-evident audit chain | ✓ | ✗ | ✗ |
| GDPR crypto-shred with audit survival | ✓ | ✗ | ✗ |
| Information barriers (PostgreSQL RLS) | ✓ | ✗ | ✗ |
| Backtest contamination detection | ✓ | ✗ | ✗ |

See the [regulated-eval head-to-head](https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md).

## TypeScript-first

Fully typed: every request and response is a named interface (`MemoryAdd`,
`RecallRequest`, `RecallResult`, `EraseRequest`, `KnowledgeSnapshot`, …), exported
from the package root. Errors throw a typed `LiansError` with the HTTP status.

Full documentation: [github.com/Lians-ai/Lians](https://github.com/Lians-ai/Lians)
