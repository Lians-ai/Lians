# Lians Packaging and Pricing

Lians should be packaged around the trust boundary of the deployment. Regulated
buyers care less about a small monthly tier ladder and more about whether memory
can run in their environment, under their keys, with evidence their risk team can
review.

> **Open-core boundary:** every software feature named below is Apache 2.0 and
> ships in the public repository — the packages differ in *deployment scope,
> support, review, and evidence*, never in withheld code. Do not describe any
> package as unlocking product features; that is mem0's model, and its absence
> is our differentiator.

## Developer

*For local prototypes, benchmarks, and framework integrations.*

**Deployment:** local library or single-node server

**Includes:**

- Memory writes and recalls
- Local SQLite mode
- Semantic search
- Basic bitemporal recall
- Python and TypeScript SDKs
- Community support

## Team

*For internal pilots and non-production agent workflows.*

**Deployment:** self-hosted Docker or small Kubernetes deployment

**Includes:**

- Everything in Developer
- Postgres + pgvector backend
- Domain adapters for finance, healthcare, and legal
- Audit log and memory lineage
- Conflict detection
- Webhooks
- Metrics and health checks
- Standard support

## Regulated Production

*For production agents that handle sensitive, time-dependent, or audited data.*

**Deployment:** customer cloud, private VPC, or on-prem

**Includes:**

- Everything in Team
- PostgreSQL Row-Level Security information barriers
- Crypto-shred erasure certificates
- Compliance reports
- Audit-chain verification and export
- Backtest contamination detection
- SIEM streaming
- Custom KMS support: AWS, Azure, Vault
- Go, Java, and C SDK support
- Deployment review and hardening checklist

## Enterprise / Air-Gap

*For banks, hospitals, law firms, insurers, and government environments with
strict residency, isolation, or procurement requirements.*

**Deployment:** private cloud, on-prem, or air-gapped

**Includes:**

- Everything in Regulated Production
- Air-gap mode
- Local embedding deployment guidance
- Dedicated onboarding
- Security architecture review
- Procurement evidence packet
- SSO/OIDC gateway guidance
- SLA and named support channel
- Optional custom connector development
- Annual contract pricing

## Managed Cloud

*For customers that want regulated-memory primitives without operating the stack.*

Managed Cloud should be sold only where the customer's compliance posture allows
hosted processing. Healthcare customers require an executed BAA before PHI is
processed. Financial and legal customers may require customer-managed keys,
private networking, regional residency, or dedicated environments.

## Pricing Principle

Developer pricing can be usage-based. Institutional pricing should be contract
based and tied to deployment boundary, support, compliance obligations, and
connector scope. Do not position the enterprise product as a $200/month SaaS
tier; that undersells the risk reduction Lians provides and misaligns with how
regulated buyers procure infrastructure.
