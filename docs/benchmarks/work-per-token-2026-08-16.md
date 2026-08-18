# Large-workload work-per-token gate

On August 16, 2026, Lians tested a narrow product target: can local compilation
let Claude Code and Codex complete at least three times the same deterministic
work per provider-reported input token?

The target means `3x work per input token`, or 200% more completed work for the
same input-token budget. It does not mean that Lians changes a provider quota,
rate limit, context window, price, or subscription policy.

## Result

All four bounded paired runs preserved the exact expected answer and cleared
the predefined 3x gate.

| Provider | Synthetic workload | Raw input tokens | Lians input tokens | Reduction | Work per input token |
|---|---:|---:|---:|---:|---:|
| Claude Code, Pro sign-in | 1,000 social posts | 96,840 | 3,477 | 96.4% | 27.85x |
| Claude Code, Pro sign-in | 1,000 browser events | 103,960 | 3,441 | 96.7% | 30.21x |
| Codex, ChatGPT sign-in | 1,000 social posts | 71,197 | 14,342 | 79.9% | 4.96x |
| Codex, ChatGPT sign-in | 1,000 browser events | 76,884 | 14,343 | 81.3% | 5.36x |

The provider-reported totals include each CLI's fixed context and cache
accounting. That is why Codex and Claude report different totals for the same
user prompt. The run used Claude Code 2.1.210 with the `sonnet` alias and Codex
CLI 0.147.0 with its signed-in default model configuration. The exact resolved
Codex model was not recorded and is a limitation of this receipt.

Full-scale compiled-only checks then verified that the bounded brief still
produced the exact answer for 10,000 synthetic social posts and a 2,400-event
browser day:

| Provider | Compiled workload | Provider-reported input tokens | Exact answer |
|---|---:|---:|---:|
| Claude Code | 10,000 social posts | 3,494 | Yes |
| Codex | 10,000 social posts | 17,023 | Yes |
| Claude Code | 2,400 browser events | 3,440 | Yes |
| Codex | 2,400 browser events | 17,003 | Yes |

The full raw 10,000-post replay was not sent. The harness refuses paired raw
prompts above its 75,000-token estimate safety cap.

## What Lians compiled

The social-research compiler performed exact local deduplication and label
aggregation, then retained eight representative posts. The browser compiler
reduced chronological history to the latest state per surface, retained five
next-action records, and enforced published, hard-exclusion, and approval
guards. Raw synthetic records stayed in the local process.

The product-facing command uses the same pattern on a JSON array or JSON Lines
export without contacting an AI provider:

```bash
lians brief research posts.jsonl --output research-brief.json
lians brief browser browser-events.jsonl --output browser-brief.json
```

Research records need `text`, `content`, `body`, or `caption`. Optional `topic`,
`sentiment`, `tool`, and `engagement` fields improve the brief. Browser records
need a surface identifier such as `surface_id` or `url` plus `state` or
`status`; the last event for a surface wins. Credential-like records are
refused, and raw records are never sent by this command.

## Reproduce

Create an offline plan first:

```bash
lians experiment stretch --workload social-research --json
lians experiment stretch --workload browser-marketing --json
```

The live path refuses provider API-key authentication. A bounded paired run is
explicit:

```bash
lians experiment stretch --workload social-research --records 1000 \
  --run --provider claude --paired --output report.json
```

The eight sanitized machine-readable reports are in
[`artifacts/benchmarks`](../../artifacts/benchmarks/). Each records prompt
hashes, deterministic expected output, provider-reported usage, exact-answer
scoring, authentication class without account identifiers, and the claim
boundary.

## Limits

- These are deterministic synthetic fixtures, not customer data.
- Each reported row is one paired run, so it is a product gate rather than a
  population estimate.
- The research fixture starts with supplied labels. Production classification
  or extraction can consume additional tokens or local compute.
- Compression can discard evidence if a workflow schema or representative
  sample is poorly chosen. Real-user validation must measure answer quality,
  not token reduction alone.

The defensible statement is: in these four bounded synthetic runs, Lians
preserved the exact answer while exceeding 3x work per provider-reported input
token on both Claude Code and Codex. Broader quota or universal-savings claims
remain unsupported.
