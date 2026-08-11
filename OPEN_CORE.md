# Lians open-core boundary

Lians is built as an open-core company. The public repository is a useful,
production-capable foundation for recording and verifying agent decision
evidence. The complete Lians experience also includes commercial software and
operational services that are not distributed in this repository.

This boundary exists for two reasons:

1. developers need portable formats, local tools, and independent verification
   they can inspect and trust; and
2. Lians needs a durable business that can operate, secure, support, and improve
   consequential agent systems for customers.

## Lians Community

Unless a file says otherwise, source code in this public repository is licensed
under Apache License 2.0. The Community software includes:

- the public evidence formats, schemas, and offline verifiers;
- SDKs, MCP tools, framework hooks, and documented ingestion interfaces;
- local and self-hosted memory and decision-evidence workflows;
- point-in-time reconstruction, provenance, and public benchmark harnesses;
- community deployment examples; and
- reproducible demos and tests.

Community is intentionally useful. It is not a trial, a time-limited binary, or
a crippled client that requires a Lians account to verify its own evidence.

## How hosted access is paid without crippling Community

The source boundary and the hosted-service boundary solve different problems.
Community operators control their own deployment and can use the Apache-licensed
features available in the version they run. Lians Cloud is an operated service:
its server verifies the customer's subscription and issues API keys with only
the scopes included in that plan.

The hosted free tier exposes the five-second path: write memory, recall it at a
point in time, compile only the relevant memories into a token-budgeted context
block, inspect the result, and integrate through an API key. Paid hosted
plans add operated capacity and increasingly advanced scopes such as audit,
governance, graph, webhooks, compliance reporting, adaptive learning, and
enterprise deployment controls. A browser lock is never treated as an
authorization boundary; the service and data plane enforce the scopes.

This lets an individual developer prove value without a sales call while
preserving commercial value in managed operation, private modules, enterprise
controls, service levels, and accountability.

## Lians Platform and commercial delivery

The following categories belong to Lians' commercial product and delivery
boundary. They may be supplied through a managed service, a private deployment,
a customer-specific repository, or a commercial agreement. They are not
licensed under this repository's Apache license unless Lians explicitly says so:

- hosted multi-tenant control plane and operator console;
- organization and fleet administration across agents, environments, and teams;
- managed retention, signing-key custody, evidence delivery, and restore
  operations;
- managed evaluation, release-assurance, investigation, and optimization
  workflows as they become generally available;
- enterprise identity provisioning, access-governance integrations, and
  customer-specific policy packs;
- private connectors, customer adapters, and robotics or edge packaging;
- deployment automation for Lians-operated environments;
- security reviews, procurement evidence, implementation, upgrades, incident
  response, support, and service-level commitments; and
- customer-specific configurations, data, runbooks, benchmarks, and reports.

Commercial availability varies by product maturity and contract. This document
does not claim that every listed commercial capability is generally available
today. See [COMMERCIAL.md](COMMERCIAL.md) for current offers.

## What must remain private

Do not commit any of the following to this public repository:

- customer names, data, traces, credentials, configurations, or deliverables;
- sales pipelines, investor materials, pricing negotiations, outreach lists, or
  internal product strategy;
- production secrets, infrastructure state, private deployment manifests, or
  incident runbooks;
- proprietary control-plane, billing, enterprise UI, or operator-console code;
- private optimization datasets, scorer calibration data, or customer-specific
  evaluation suites; or
- code contractually owned by, licensed from, or restricted for a customer or
  partner.

The automated public-boundary check in `scripts/check_public_boundary.py`
rejects common private paths and sensitive file types. It is a guardrail, not a
substitute for review.

## Contribution boundary

Public contributions are welcome for formats, SDKs, integrations, the local or
self-hosted engine, verifiers, tests, documentation, and reproducible
benchmarks. Feature requests for the commercial control plane, customer
deployments, or private integrations should go through Lians rather than a
public issue.

Contributions accepted into this repository are licensed under Apache License
2.0. The license does not grant rights to Lians names, logos, or other brand
assets. See [TRADEMARKS.md](TRADEMARKS.md).

## Previously published versions

Code already released under Apache License 2.0 remains available under that
license. This boundary is prospective: it governs where new work belongs and
how Lians packages the product. It does not attempt to revoke rights previously
granted to a released version.

## Repository model

The intended GitHub structure is:

| Repository | Visibility | Purpose |
|---|---|---|
| `Lians-ai/Lians` | Public | Community software, public specifications, SDKs, verifiers, and benchmarks |
| `Lians-ai/Lians-Platform` | Private | Hosted control plane, operator experience, commercial modules, and managed-service automation |
| `Lians-ai/Lians-Deployments` | Private | Production infrastructure, environment policy, SRE runbooks, and release operations |
| customer delivery repositories | Private | Contract-specific integrations and evidence, isolated by customer |

Public packages should depend only on public source. Private repositories may
consume versioned Community packages and public specifications.

The required access, branch, security, and release settings are documented in
[docs/github-governance.md](docs/github-governance.md).
