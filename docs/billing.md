# Commercial billing boundary

This public repository does not contain Lians' commercial plan authority,
checkout policy, revenue operations, or private control plane. It does contain
generic usage-metering hooks and a least-privilege provisioning contract that
were already released under Apache 2.0 and remain useful to self-hosters.

The public SDKs and self-hosted Community software do not require a Lians
license key. A commercial agreement may grant access to a managed service,
private modules, contracted capacity, support, or customer-specific delivery.
Hosted entitlements are verified by the Lians-operated service before it issues
plan-scoped API keys or permits a paid console action. Browser-side locks are
informational only. Self-hosted Community operators remain the administrators
of their own deployments.

Current public offers and prices are documented in
[pricing-tiers.md](pricing-tiers.md) and [../COMMERCIAL.md](../COMMERCIAL.md).
The signed order form is the source of truth for any customer.

## Public implementation rule

Do not publish:

- Lians checkout policy or payment-provider operations;
- plan-to-entitlement mappings used by the commercial service;
- proprietary operator-console modules;
- production metering configuration, webhook secrets, or reconciliation jobs;
- customer identifiers, invoices, payment history, or usage exports; or
- pricing experiments and negotiation notes.

Generic usage metrics, metering adapters, and narrow provisioning hooks may
remain public when they are useful to Community operators and do not make the
public repository the source of truth for paid access.
