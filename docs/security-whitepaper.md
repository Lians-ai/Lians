# Lians Security Whitepaper

This document describes the security architecture of Lians for security reviewers,
risk committees, and procurement teams in regulated industries. It is a companion
to [threat-model.md](threat-model.md), [soc2-hipaa-readiness.md](soc2-hipaa-readiness.md),
[compliance.md](compliance.md), and [hipaa.md](hipaa.md).

## 1. What Lians is

A self-hostable memory layer for AI agents with a bitemporal data model and a
compliance spine: a tamper-evident audit chain, per-subject encryption with
crypto-shred erasure, and database-layer information barriers. Deployable fully
inside your perimeter (air-gap mode) — no agent data needs to leave the network.

## 2. Data classification & flow

| Data | Sensitivity | Protection |
|------|-------------|------------|
| Memory content | May contain PII/PHI/MNPI | AES-256-GCM at rest under a per-subject key; TLS in transit |
| Embeddings | Derived | Stored in pgvector; local embedding provider keeps text in-perimeter |
| Audit events | Integrity-critical | SHA-256 hash chain; append-only; optional Merkle anchoring |
| API keys | Secret | Stored only as SHA-256 hashes; individually revocable |
| Subject keys (DEKs) | Secret | Wrapped by a master key (KMS: env/AWS/Azure/Vault); destroyed on erasure |

Write path: client → TLS → API (authn/z, rate-limit, request-id) → encrypt →
Postgres (RLS-enforced) + append audit row → optional SIEM stream / webhooks.

## 3. Authentication & authorization

- **API keys** over `X-API-Key`, stored as SHA-256 hashes, scoped to a namespace,
  individually revocable.
- **Scopes** (`read` / `write` / `admin`) checked on every request; admin audit
  endpoints additionally require an `X-Admin-Secret`.
- **RBAC roles** (`owner` / `analyst` / `compliance` / `readonly`) expand to scope
  sets at auth time.
- **SSO** is integrated at the gateway via forward-auth / OIDC — see [sso.md](sso.md).

## 4. Tenant & information-barrier isolation

- **Namespace isolation** is enforced by PostgreSQL Row-Level Security: every
  authenticated request sets a transaction-scoped `app.current_namespace`, and
  policies restrict every query to that namespace.
- **Information barriers** (Chinese walls) are enforced by a **RESTRICTIVE** RLS
  policy keyed on `agentmem.barrier_group` (migration 0013), so cross-barrier
  reads are denied at the database layer even for the table owner — verified in CI
  against a non-superuser role. The application server cannot leak across a wall
  because the wall is below it.

> Operational requirement: run the application as a **non-superuser, non-BYPASSRLS**
> Postgres role. Superusers bypass RLS by design.

## 5. Encryption & key management

- Content is encrypted with a per-subject AES-256-GCM key (DEK). DEKs are wrapped
  by a master key resolved from a configurable KMS (`env` / `aws` / `azure` /
  `vault`).
- **Crypto-shred erasure** (GDPR Art. 17 / HIPAA): destroying a subject's DEK
  renders all their content permanently unreadable, while the SHA-256 content
  hashes remain in the audit chain — the erasure is provable. A signed erasure
  certificate is available.

## 6. Auditability & monitoring

- Every state change appends a row to a SHA-256 hash chain (SEC 17a-4). Integrity
  is verifiable on demand (`/v1/admin/audit/verify`) and exportable for examiners.
- **SIEM streaming** forwards every audit event (fire-and-forget) to a collector
  (Splunk HEC / Datadog / Elastic) for real-time alerting and retention.
- Prometheus metrics + Grafana dashboard; OpenTelemetry tracing; structured JSON
  access logs with a propagated request ID; `/livez` + `/readyz` probes.

## 7. Hardening & deployment

- Air-gap mode hard-fails at startup if any configuration would send data
  externally. Rate limiting per API key. Idempotency keys for exactly-once writes.
- Secrets are provided via environment / KMS, never committed. `ADMIN_SECRET` and
  `MASTER_ENCRYPTION_KEY` must be set in production (startup warns otherwise).
- See [deploy.md](deploy.md) for the production checklist (TLS, non-superuser DB
  role, secret rotation, network isolation, backup/PITR).

## 8. Responsible disclosure

Security contact and policy: [SECURITY.md](SECURITY.md).
