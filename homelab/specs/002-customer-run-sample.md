# Spec 002: bounded customer-run local sample

Status: implemented MVP

## Partner question

Can a partner engineer run a small synthetic or already de-identified scenario
through the declared Lians, OpenTelemetry, Grafana, and evidence path entirely on
their own Docker host?

## Observable user story

The evaluator copies the versioned example, edits a bounded local JSON file,
validates it, launches the stack with one command, inspects the local dashboard,
exports a sanitized verification receipt, and disposes all local runtime state.

## Integration contract

| Direction | Protocol/surface | Authentication | Expected evidence |
|---|---|---|---|
| Local sample → Lians | MemoryAdd plus bound recall | generated local API key | sample hash, recall receipt |
| Local workload → Alloy | OTLP/HTTP JSON | loopback/internal network | correlated trace/span |
| Lians → local report | Decision Envelope and Evidence Pack v2 | generated local API key | IDs, hashes, checks, versions |

## Data policy and limits

- UTF-8 JSON using `homelab-sample/v1`, no duplicate or unknown fields.
- Synthetic by default; de-identified input requires an explicit acknowledgement.
- Maximum 64 KiB, ten memories, twenty flat metadata fields per memory.
- Common credentials, email addresses, U.S. SSNs, common U.S.-formatted phone
  numbers, payment-card numbers, and sensitive metadata labels fail closed.
- Custom `*.local.json` files are ignored by Git and mounted read-only.
- The launcher resolves final symlink targets before enforcing the path policy
  and creating the read-only mount.
- Reports intentionally export `scenario_id` and `decision_type`; those must be
  opaque and non-sensitive. Reports exclude `agent_id`, `subject_id`, query,
  outcome, reason codes, recall-filter names and values, and all memory fields.
- Local named volumes can contain sample-derived state until `dispose` runs.

## Requirements

- `SAMPLE-001`: default launch remains a one-command synthetic proof.
- `SAMPLE-002`: custom input is validated before containers are changed.
- `SAMPLE-003`: de-identified input requires an explicit acknowledgement.
- `SAMPLE-004`: sample hashes isolate recalls from earlier retained scenarios.
- `SAMPLE-005`: the verifier rebuilds, independently validates and hashes its
  read-only sample mount, and requires an exact match with the proof manifest.
- `SAMPLE-006`: the receipt exports only the documented sample manifest fields
  and evidence identifiers; prohibited sample fields remain excluded.
- `SAMPLE-007`: one explicit command deletes all lab containers and volumes.

## Acceptance checks

1. `check-sample` accepts the default fixture.
2. A de-identified fixture fails without the acknowledgement and passes with it.
3. Email, credential-shaped, payment-card, and sensitive-key fixtures fail.
4. `up` exits zero and the receipt reports all component checks passing.
5. The verifier independently recomputes the mounted file's sample manifest and
   rejects any mismatch with the proof. The receipt includes only the documented
   manifest fields and excludes the prohibited sample fields above.
6. `dispose` leaves no `lians-homelab` containers or named volumes.

## Limitations and graduation gate

Passing proves only that this declared local scenario completed against the
reported component versions. It does not establish broad dataset compatibility,
semantic retrieval quality, throughput, production operations, security,
privacy, compliance, availability, or Grafana catalog status. Any broader claim
requires a separately reviewed partner spec, representative approved test data,
measured acceptance thresholds, and a signed proof handoff.
