# ADR 004: controlled customer-run local samples

Status: accepted

Supersedes the synthetic-only input restriction in ADR 003 for this customer-run
bundle. Synthetic input remains the default and the only input used in committed
fixtures, CI, public demos, and Lians-operated environments.

## Decision

The customer-run homelab may accept one bounded JSON scenario that is either:

- synthetic; or
- de-identified by the customer before use, with an explicit local acknowledgement.

The scenario is processed only by the customer's local Docker engine. The
launcher rejects files over 64 KiB, unsupported schemas or fields, more than ten
memories, nested metadata, common secret formats, common direct identifiers, and
sensitive metadata labels. A custom file is never copied into the repository or
an exported report. Reports intentionally contain its SHA-256, classification,
`scenario_id`, declared `decision_type`, and counts; those two identifiers must
be opaque and non-sensitive. Reports exclude `agent_id`, `subject_id`, query,
outcome, reason codes, recall-filter names and values, and all memory fields.

## Why

Partner engineers need to exercise a realistic request through the same API,
telemetry, decision-envelope, and verification path without sending source data
to Lians. A small, explicit local format offers that learning path while keeping
the default scenario reviewable and synthetic.

## Boundaries

- The scanner is a guardrail, not a de-identification or DLP product.
- The customer is responsible for authorization and de-identification.
- Raw sample-derived state can exist in local named volumes while the lab runs.
- `dispose` removes containers and named volumes; sanitized reports remain.
- Passing proves only the declared local scenario and component versions.
- Passing does not establish production compatibility, retrieval quality,
  performance, security, privacy, compliance, availability, or Grafana catalog
  status.
