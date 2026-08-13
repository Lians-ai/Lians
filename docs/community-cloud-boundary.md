# Lians Community and Commercial Boundary

This document defines what GitHub users receive and what Lians sells. It also
sets the boundary for public claims.

## Community edition on GitHub

Community is a useful, self-managed product rather than a crippled demo:

- local or customer-operated memory;
- durable `remember` and bounded `recall` workflows;
- connectors and setup paths for Codex, Cursor, Claude, Gemini, and MCP-compatible
  clients;
- existing temporal, provenance, and technical-evidence primitives published
  in this repository;
- community documentation and reproducible public benchmarks.

The Community connectors default to the smallest useful surface where
practical. For example, the Cursor and Gemini starter profiles expose only
`remember` and `recall`, and the Codex plugin injects a score-gated, bounded
slice of memory.

## Lians Personal and organization packages

Commercial value sits at the managed service and organizational boundary:

- Lians Personal at $10 per month for one managed account, a private memory
  workspace, 100,000 writes and 50,000 recalls per month, memory controls, and
  email setup support;
- hosted continuity across supported clients and devices;
- shared/team memory and organization administration;
- higher managed storage, write, recall, and operational limits;
- managed identity, policy, retention, and deployment controls;
- managed evidence operations, exports, monitoring, and evaluation gates;
- deployment review, support, SLAs, and contractual commitments where sold;
- private networking, residency, customer-managed keys, dedicated deployment,
  or air-gap assistance where contracted.

The public Lians Personal allowance is enforced through the production
subscription entitlement. Organization capabilities and limits are contract-
and environment-specific. A capability must not be presented as generally
available until it is operating in the customer's environment and covered by
the applicable order form or plan.

## Licensing boundary

Code already released under Apache 2.0 remains available under that license.
Changing documentation cannot revoke those rights. Lians can charge for hosted
operation, entitlements, service levels, support, and new proprietary
control-plane components that are not released in this repository.

Do not claim that a customer needs a license key to use code that this
repository already grants under Apache 2.0.

## Cross-provider claim boundary

Approved language:

> Lians is provider-neutral memory and evidence infrastructure. It can sit
> beside Codex, Cursor, Claude, Gemini, and other MCP-compatible or
> instrumentable AI workflows without changing the underlying model.

> Lians can reduce repeated context and improve useful work per input token when
> a smaller relevant recall replaces context that would otherwise be resent.
> Results depend on the workload and should be verified with an A/B evaluation.

Do not claim that Lians increases a provider's context window or quota, improves
every answer, lowers tokens on every turn, or makes every possible closed system
compatible without an integration point.

## What GitHub does not include as a service entitlement

Cloning the repository does not include a production Lians-hosted tenant,
cross-device account, organizational administration, higher hosted limits,
managed evidence review, legal or regulatory certification, an SLA, or a
support commitment. Those are separately provisioned commercial services.
