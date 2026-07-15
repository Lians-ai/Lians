# The LIANS dispute ladder

LIANS scales on two connected axes. Memory parity keeps it installed in agent
stacks; the dispute ledger makes those installations authoritative when an AI
decision is challenged.

## Shared product contract

Every consequential decision can be appended through `POST /v1/decisions` with
the actor, outcome, portable reason codes, model and policy versions, applicable
regime, decision time, point-in-time knowledge cutoff, and cited memory IDs.
Corrections link to the prior record instead of rewriting history.

`GET /v1/decisions/{id}/evidence-pack` exports Evidence Pack v1 containing the
decision record, complete knowledge snapshot at decision time, cited evidence,
retention and legal-hold posture, audit-chain verification, and a deterministic
pack hash. The export is itself recorded in the tamper-evident audit chain.

## Trigger-gated expansion

| Rung | Dispute and buyer | Entry trigger | Kill criterion |
|---|---|---|---|
| 1 | EU regulator / Annex III AI vendor | Active now | Under 5% replies and universal “later” |
| 2 | Rejected US consumer / lending, insurance, HR vendor | Three paying pilots or three unprompted US-only asks | 30 touches under 5% engagement |
| 3 | Bank or insurer validator / AI vendor | Fintech customer or failed MRM review | Two quarters with no pilot |
| 4 | Plaintiff / legal ops or GC | Customer hold request or legible agent-liability panic | Dormant until triggered |
| 5 | Auditor / certifier | Two engagements requesting LIANS records | No open-format adoption within one year of traction |
| 6 | Counterparty / transacting organizations | Real volume and two parties request a shared record | Never pre-build |

Only one rung may be in active build. Regimes are schema views and adapters, not
separate products: Article 12 emphasizes system records and oversight; adverse
action emphasizes reason codes and review; model risk emphasizes version and
validation lineage; e-discovery emphasizes legal hold and chain of custody;
attestation tools consume the open evidence format.

Memory remains capped at roughly 20% of effort unless a published evaluation
shows a prospect-noticeable retrieval deficit, in which case parity work
preempts ladder work for one sprint.
