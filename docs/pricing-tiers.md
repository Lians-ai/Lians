# Lians Packaging and Pricing

Lians is one memory product with two default delivery modes. Users should not
have to choose among separate “developer,” “research,” “team,” or “regulated”
products before they understand the core memory loop.

## The product

Lians gives an AI tool five user-visible memory controls:

1. remember a useful fact, preference, constraint, or decision;
2. recall a bounded set of relevant current memories;
3. inspect what is stored;
4. correct a stale memory without losing its history; and
5. forget a memory with explicit confirmation.

The model and assistant remain the user's choice. Lians supplies the portable
memory layer beside them.

## The two default modes

| | **Lians Local** | **Lians Personal** |
|---|---|---|
| Customer | Developers, students, local evaluation, and self-managed projects | One person who wants hosted continuity without operating the memory layer |
| Deployment | Local library, MCP server, or customer-operated service | Lians-managed private workspace |
| Price | Free under Apache 2.0 | $10/month, cancel anytime |
| Capacity | Limited by the user's environment | 100,000 writes and 50,000 recalls per month |
| Support | Community documentation and public issues | Email setup support |

Local is a useful product, not a crippled trial. It includes provider-neutral
memory, SQLite mode, the basic five-control lifecycle, existing temporal and
provenance capabilities, public documentation, and reproducible benchmarks.
It does not include a Lians-hosted tenant, an SLA, or managed operation.

Personal includes one managed account and private workspace, hosted continuity
through supported connections, the published monthly allowance, memory export
and deletion controls, and email setup support.

Personal does not change the underlying model, increase a provider quota, or
promise lower token use on every task. It can reduce repeated context when a
small relevant recall replaces history that would otherwise be resent; verify
that result on the user's workflow.

## Organizations use the same product

An organization does not buy a different memory engine. It scopes how Lians is
deployed and operated for a shared workflow. A proposal may include only the
capabilities the target environment actually needs, such as:

- shared memory and organization administration;
- contracted storage, write, and recall limits;
- identity, policy, retention, and information-barrier operation;
- managed evidence exports, monitoring, and evaluation gates;
- private networking, regional residency, customer-managed keys, or a
  dedicated environment;
- customer-cloud, private-VPC, on-premises, or air-gapped deployment support;
- architecture review, named support, an SLA, or data-processing terms where
  separately agreed; and
- custom connector work when it is part of the signed scope.

These are deployment and service options, not four more public product tiers.
Technical primitives do not by themselves establish legal or regulatory
compliance; claims depend on the deployed configuration and customer controls.

## Packaging rule

The public choice is **Local or Personal**. Organization pricing is then tied
to the workflow, deployment boundary, managed capacity, support obligation,
evidence scope, and contractual risk. Do not quote an organization tier or
limit until its enforcement and entitlements operate in that environment.

Code already published under Apache 2.0 remains under that license. Commercial
value comes from hosted operation, managed capacity, organizational controls,
deployment assurance, support, and contractual commitments - not from pretending
the public code requires a new license.

See the [Community and commercial boundary](community-cloud-boundary.md) and
the [managed billing design](billing.md).
