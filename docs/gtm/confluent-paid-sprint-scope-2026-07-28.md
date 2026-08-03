# Lians × Confluent — paid AI Evidence Readiness Sprint

## Commercial status

This document is a decision-ready scope for the July 28 conversation. It becomes
an order form only after the buyer, legal entity, workflow, dates, billing
contact, and authorized signer are confirmed. Confluent should not be named as
the buyer unless its representative explicitly accepts that role.

## Buyer inputs to complete live

- Buyer legal entity: `[required]`
- Consequential AI workflow: `[required]`
- Business or product owner: `[required]`
- Technical owner: `[required]`
- Success criterion: `[required]`
- Authorized signer: `[required]`
- Billing contact: `[required]`
- Purchase-order requirement: `[yes / no / unknown]`
- Target kickoff week: `[required]`

## Fixed commercial terms

- Engagement: two-week AI Evidence Readiness Sprint
- Fee: **$4,500 USD fixed**
- Kickoff payment: **$2,250 after signature and before work begins**
- Final payment: **$2,250 due within five business days of delivery**
- Data boundary: one synthetic or sanitized workflow; no production credentials
- Commercial boundary: no free pilot, unpaid proof of concept, or speculative
  production integration

## Workflow hypothesis

The buyer operates an AI decision or agent action whose evidence changes over
time. Events are transported through Confluent Cloud and correlated to traces,
while Lians preserves the exact decision-time state and dependency graph.

The selected workflow must include enough of the following to test the
commercial problem:

- the decision or action;
- retrieved sources and memory;
- policy and permission state;
- model and tool-call metadata;
- approval or human review;
- downstream effects.

## Lians delivers

1. A bounded Confluent or OpenTelemetry-to-Lians evidence path for the selected
   workflow.
2. Decision-ID and trace-ID correlation.
3. Point-in-time reconstruction of the evidence available when the action
   occurred.
4. Dependency and downstream-change-impact mapping.
5. Checks for missing sources, stale or conflicting memories, duplicates, and
   broken references.
6. One independently verifiable evidence receipt.
7. A concise production-readiness gap report.
8. A recorded technical and commercial walkthrough.

## Customer provides

- One reproducible synthetic or sanitized workflow.
- Representative events or one OpenTelemetry trace.
- A technical owner for two working sessions.
- Definitions of the decision, evidence, and downstream outcome that matter.
- Timely access to the named business owner for acceptance.

## Acceptance criteria

The sprint is accepted when the buyer can:

- select the agreed decision;
- reconstruct the contemporaneous sources, memory, policy, permissions, model,
  tools, and approvals available at that point;
- distinguish later or superseded facts;
- identify material evidence gaps and downstream dependencies;
- verify the evidence receipt; and
- receive the written production recommendation and walkthrough.

## Exclusions

Production rollout, regulatory certification, legal advice, custom model
development, unrestricted customer data, and unlimited integrations are outside
this sprint. Sensitive production data requires separate security review and
written agreement.

## Decision requested

If Confluent is the buyer, complete the buyer inputs above and authorize Lians to
issue the customer-specific order form and kickoff invoice.

If Confluent is not the buyer, identify one named customer or internal workflow
owner and make a warm introduction that preserves the **$4,500 paid** framing.
