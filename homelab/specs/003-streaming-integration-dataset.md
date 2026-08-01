# Spec 003: streaming integration dataset

Status: implemented local-lab slice

## Partner question

Can a partner engineer preflight a representative synthetic or already
de-identified memory dataset, stream it through the real local Lians API, and
receive an honest compatibility and capacity receipt without uploading the file
to Lians or loading the entire dataset into memory?

## Observable user story

The evaluator selects one versioned resource profile, validates an NDJSON file,
starts the existing integration stack, runs bounded parallel ingestion, and
opens a sanitized receipt containing the exact accepted and failed counts,
elapsed time, observed request rate, and latency percentiles.

## Integration contract

The first UTF-8 line is a `homelab-dataset/v1` header with only `$schema`,
`classification`, `dataset_id`, and `agent_id`. Every later line is one memory
record with the documented memory fields. Empty lines, duplicate JSON keys,
unknown fields, non-finite numbers, oversized lines, and unsupported encodings
fail closed.

| Direction | Protocol/surface | Authentication | Expected evidence |
|---|---|---|---|
| Local NDJSON → preflight | streaming file read | local filesystem policy | SHA-256, byte and record counts |
| Bounded workers → Lians | `POST /v1/memories` | generated local API key | per-request success/failure and latency |
| Local workload → receipt | atomic JSON write | local filesystem | sanitized capacity receipt |

The lab deliberately uses per-record writes because they have deterministic
idempotency keys. The existing batch route is not a higher-throughput transport
candidate until it has an explicit dataset-level idempotency contract.

## Requirements

- `DATASET-001`: validate the complete file before the first API write.
- `DATASET-002`: validation and ingestion stream records and apply backpressure;
  they do not materialize the complete dataset in memory.
- `DATASET-003`: synthetic input is the default. De-identified input requires
  the exact local acknowledgement used by the bounded scenario path.
- `DATASET-004`: resource profiles set hard record, byte, line, concurrency, and
  request-timeout ceilings. A profile is a safety envelope, not a capacity claim.
- `DATASET-005`: each request uses a deterministic idempotency key derived from
  the validated dataset hash and record position.
- `DATASET-006`: the receipt reports exact requested, processed, succeeded, and
  failed counts plus elapsed time, observed rate, and p50/p95/p99 latency.
- `DATASET-007`: the receipt contains no content, metadata names or values,
  timestamps, sources, agent ID, API key, or rejected raw values.
- `DATASET-008`: custom repository-local datasets must be direct ignored
  `homelab/datasets/*.local.ndjson` files; external files are allowed after their
  resolved target passes validation.

## Acceptance checks

1. The checked-in synthetic dataset validates and ingests locally.
2. Duplicate fields, secrets/direct identifiers, nested metadata, malformed
   records, excess bytes, excess records, and excess per-line bytes fail before
   any write.
3. A de-identified dataset fails without acknowledgement and passes with it.
4. A generated dataset can exceed the ten-memory scenario limit while the
   Python process remains bounded by the worker queue and latency accumulator.
5. Re-running the same file does not create duplicate writes.
6. The emitted receipt contains only the fields documented above.

## What passing means

Passing establishes that the exact validated file completed against the listed
local build within the selected safety envelope. It is useful evidence for
integration discovery and pilot sizing. It does not establish production
capacity, semantic retrieval quality, availability, security, privacy,
compliance, or support for arbitrary source-system exports.
