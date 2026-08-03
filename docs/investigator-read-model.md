# Investigator report completeness

`GET /v1/investigator/decisions/{decision_id}` is a bounded derived read model.
It does not replace the append-only Decision, Recorder, Gate, review, case,
remediation, closure, or audit records that it references.

## Deterministic read windows

The v1.1 report accepts independent limits for timeline events, evidence links,
control history, cases, remediation tasks, and closure attestations. Defaults are
chosen for an interactive investigation packet rather than an unbounded export:

| Query parameter | Default | Maximum | Embedded collections |
|---|---:|---:|---|
| `timeline_limit` | 200 | 1,000 | ledger timeline |
| `evidence_limit` | 500 | 5,000 | evidence links and their artifacts |
| `control_history_limit` | 200 | 2,000 | Gate, approval, and review histories |
| `case_limit` | 100 | 1,000 | direct and Gate-linked cases |
| `task_limit` | 500 | 5,000 | tasks belonging to returned cases |
| `closure_limit` | 500 | 5,000 | attestations for returned cases and tasks |

Every embedded collection has a `coverage` entry with its requested limit,
returned count, known total or lower bound, truncation flag, completeness flag,
stable ordering, and exact scope. A child collection is incomplete when its own
limit is reached or its parent case/task window is incomplete. `coverage.complete`
is true only when the audit scope, all embedded windows, and the receipt's
cited-evidence scope are complete. Disabling audit verification, a barrier-scoped
audit view, or a capped audit-chain scan therefore keeps the packet incomplete.

Limits use an extra-row probe, so returning exactly the requested number no longer
silently implies truncation. Stable UUID tie-breakers make every prefix
deterministic. Increase an individual limit or follow the report's authoritative
source links when its window is incomplete. For complete namespace audit-chain
verification, use the dedicated offline/compliance workflow rather than an
interactive Investigator response.

## Truthful integrity and risk

Review integrity is verified only from sequence one forward. A capped prefix is
reported as `partial`, never `ok`. Approval series use the same rule:
`approval_attestations_status` is `valid`, `missing`, `invalid`, or `partial`, and
the compatibility boolean is null when validity was not established. A detected
hash/sequence violation remains `tampered` or `invalid` even when a later portion
was omitted.

Risk counts are not calculated from the embedded prefixes. Gate disposition
counts, the latest Gate and review state, open/critical case counts, overdue task
count, and maximum evidence risk are independently aggregated across the complete
visible decision scope. Truncating packet detail therefore cannot turn a known
denial, critical case, overdue task, or high-risk dependency into a lower score.
An incomplete packet adds `investigator_read_model_incomplete`; partially verified
review or approval history adds a separate attention signal.

Receipt source completeness requires every referenced memory record to be visible
and inspected. Missing, barrier-hidden, or legacy over-limit references force the
source-provenance check to incomplete and are disclosed under
`receipt_completeness.evidence_scope`.

## Isolation and interpretation

Every base query, aggregate, subquery, and child lookup applies the exact namespace
and information-barrier visibility predicate. Gate-linked cases are resolved with
a scoped database subquery, not from the bounded Gate output window. A
barrier-scoped principal cannot request namespace-wide audit verification. Queue
and report responses are marked `Cache-Control: no-store`; expanded statements and
review notes additionally require admin scope.

The report establishes what Lians recorded and which visible controls/evidence are
connected. It does not establish substantive correctness, causal certainty, or the
absence of evidence that was never captured. Treat reachable evidence and impact
results as investigation leads, and preserve the machine-readable coverage object
with any exported packet.
