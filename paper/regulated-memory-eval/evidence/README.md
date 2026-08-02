# Machine-readable evaluation evidence

This directory archives exact outputs from the regulated-memory harness. Every live-run file records the harness commit, package version, adapter mode, execution configuration, timestamp, and per-invariant details.

Validate an evidence file against `schema.json` before citing it. The JSON Schema separates executed results from capability assessments so an unexecuted vendor column cannot be mistaken for live evidence.

## Archived runs

| File | System | Status | Result |
|---|---|---|---|
| `lians-local-2026-07-17.json` | Lians 0.4.1 with `LocalLiansClient` | Executed live | 5 of 5 invariants passed |

## Evidence still required

The July 4 mem0 OSS and Graphiti OSS runs were summarized in `docs/regulated-eval-results.md`, but their original machine-readable outputs were not archived. They must be rerun and saved here before the preprint leaves draft status. No JSON files are reconstructed from narrative descriptions.

Vendor-hosted columns and capability-assessed columns must use `execution_status: capability_assessed` unless a complete live harness report is available.
