# SOC 2 / HIPAA Readiness

Lians provides the **technical controls** that underpin a SOC 2 Type II audit and
a HIPAA Security Rule assessment. Certification itself is organizational (policies,
an auditor, evidence over a period); this maps what the software gives you so your
auditor's control narrative is mostly "configure + evidence," not "build."

> Lians is software, not a certification. A SOC 2 report or HIPAA attestation is
> issued to *your deployment* by *your auditor*. This page accelerates that.

## SOC 2 Trust Services Criteria → Lians controls

| Criterion | Lians control |
|-----------|---------------|
| CC6.1 Logical access | API keys (hashed, revocable, scoped), RBAC roles, SSO at the gateway |
| CC6.1 Tenant isolation | PostgreSQL RLS namespace policy + RESTRICTIVE barrier policy |
| CC6.6 Encryption in transit | TLS (deployment) |
| CC6.7 Encryption at rest | Per-subject AES-256-GCM; KMS-wrapped DEKs |
| CC7.1 Detection / monitoring | Prometheus metrics, Grafana, OTEL traces, JSON access logs |
| CC7.2 Security event logging | SHA-256 audit chain + real-time SIEM streaming |
| CC7.2 Integrity monitoring | `verify_chain` tamper detection; optional Merkle anchoring |
| CC8.1 Change management | Alembic migrations; CI on every change (5 languages + Postgres) |
| A1.2 Availability | `/livez`+`/readyz` probes; health checks; rate limiting; idempotency |
| C1.1 Confidentiality / disposal | Crypto-shred erasure + provable erasure certificate |
| P4 Privacy / retention & deletion | Retention policy per namespace; GDPR/CCPA erasure |

## HIPAA Security Rule (§164.312 technical safeguards)

| Safeguard | Lians control |
|-----------|---------------|
| §164.312(a)(1) Access control | RLS isolation + barrier groups (care-team walls); RBAC |
| §164.312(a)(2)(iv) Encryption | Per-subject AES-256-GCM |
| §164.312(b) Audit controls | Tamper-evident hash chain + SIEM stream + export |
| §164.312(c)(1) Integrity | Content hashes; chain detects alteration |
| §164.312(d) Authentication | API keys / SSO; admin secret for privileged ops |
| §164.312(e)(1) Transmission security | TLS; air-gap mode prevents external egress |

A **Business Associate Agreement (BAA)** must be in place with the operator before
processing real PHI. See [hipaa.md](hipaa.md) for the full safeguard mapping.

## Readiness checklist (deployment)

- [ ] Run the app as a **non-superuser, non-BYPASSRLS** Postgres role (RLS depends on it)
- [ ] `MASTER_ENCRYPTION_KEY` set; DEKs wrapped by a real KMS (`aws`/`azure`/`vault`)
- [ ] `ADMIN_SECRET` set to a strong value; rotate on schedule
- [ ] TLS terminated in front of the API; HSTS
- [ ] `SIEM_URL` configured to your collector; alerting on `verify_chain` failures
- [ ] Backups encrypted + point-in-time recovery enabled; restore tested
- [ ] Retention policy set per namespace; erasure runbook documented
- [ ] `AIRGAP_MODE=true` if data must not leave the perimeter
- [ ] Access reviews: periodic API-key/role review; revoke on offboarding
- [ ] Penetration test of the deployed surface (annual)

See [deploy.md](deploy.md) and [security-whitepaper.md](security-whitepaper.md).
