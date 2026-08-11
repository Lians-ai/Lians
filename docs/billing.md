# Commercial billing boundary

This public repository does not contain the implementation or operating
runbook for Lians' commercial billing, entitlements, customer provisioning, or
private control plane.

The public SDKs and self-hosted Community software do not require a Lians
license key. A commercial agreement may grant access to a managed service,
private modules, contracted capacity, support, or customer-specific delivery.
Those entitlements are enforced and operated outside this repository.

Current public offers and prices are documented in
[pricing-tiers.md](pricing-tiers.md) and [../COMMERCIAL.md](../COMMERCIAL.md).
The signed order form is the source of truth for any customer.

## Public implementation rule

Do not publish:

- Stripe or Clerk provisioning logic for Lians-operated environments;
- plan-to-entitlement mappings used by the commercial service;
- administrative billing endpoints or operator-console code;
- production metering configuration, webhook secrets, or reconciliation jobs;
- customer identifiers, invoices, payment history, or usage exports; or
- pricing experiments and negotiation notes.

Generic usage metrics and open-source hooks may remain public when they are
useful to Community operators and do not expose Lians' commercial system.
