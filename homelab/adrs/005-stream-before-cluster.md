# ADR 005: stream before buying a cluster

Status: accepted

## Decision

Extend the single-host Compose lab with a streaming NDJSON memory-ingest path,
bounded concurrency, versioned resource profiles, and a sanitized capacity
receipt. Keep the existing lightweight proof as the default. Do not add
Kubernetes, distributed queues, a GPU requirement, or multi-node hardware until
a partner acceptance criterion requires one of those shapes.

The profile ceilings are guardrails. Only an observed receipt from the exact
host, file, build, and run may be described as measured performance.

## Why

The immediate partner question is whether Lians accepts a representative data
shape and produces evidence, not whether a startup can operate an enterprise
cluster in advance. Streaming validation and bounded backpressure allow larger
fixtures without copying the entire input into memory, while a single host keeps
the demo repeatable and inexpensive.

## Boundaries

- The validator is a safety guardrail, not a universal connector, DLP system, or
  de-identification service.
- Raw dataset-derived state remains in local volumes until `dispose` runs.
- Failed runs still emit aggregate failure counts but never raw values.
- A dedicated profile permits larger experiments; it does not certify the host
  or Lians for those ceilings.
- Hardware purchases follow measured bottlenecks: RAM first for concurrent local
  services, NVMe for retained telemetry/fixtures, and GPU only for a separately
  specified local model workload.

## Graduation trigger

Add a source-specific adapter only after its mapping is specified and tested.
Add a queue when retry/replay requirements exceed the bounded local worker pool.
Add a cluster only when a partner explicitly needs node-loss, high-availability,
or Kubernetes deployment evidence.
