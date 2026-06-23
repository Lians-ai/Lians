# Lian — Enterprise Compliance Reference

**Audience**: Bank/capital markets procurement, vendor risk management, legal and compliance teams reviewing Lian for deployment in regulated environments.

This document maps Lian's technical controls to the specific regulatory obligations most commonly raised in financial-sector procurement questionnaires (DORA, EU AI Act, MiFID II, Basel III Model Risk).

---

## 1. DORA — EU Digital Operational Resilience Act (effective Jan 17 2025)

DORA Article 2 applies to financial entities and their ICT third-party service providers.  The key question for Lian procurement teams is: **self-hosted or managed service?**

### Self-hosted deployment (on-prem / private cloud)

When you deploy Lian on infrastructure you operate, **you are the ICT service provider**, not Lian.  Your DORA Article 30 obligations (the ICT third-party risk chapter) do not flow to us — they flow inward to your own infrastructure team.

This is the structural reason regulated institutions prefer self-hosted Lian over cloud-first competitors:

| Obligation | Managed SaaS vendor | Self-hosted Lian |
|------------|--------------------|--------------------|
| DORA Art. 30 — ICT contract requirements | Must negotiate with vendor | Internal policy; you control it |
| DORA Art. 28 — Concentration risk (cloud) | Applies if using cloud-hosted SaaS | Eliminated when on-prem |
| DORA Art. 26 — Operational resilience testing | Vendor penetration test scope | Your environment, your scope |
| Data residency guarantees | Contractual; vendor enforces | Technical; you enforce |
| Exit strategy and data portability | Dependent on vendor | Full control; export via `/v1/snapshot` |

> **Note:** Zep (the primary alternative) removed its self-hosted Community Edition in 2025, making it a cloud-only offering. Self-hosted deployment is available exclusively via the raw open-source Graphiti library, which provides no compliance stack, no audit API, and no managed schema.

### DORA operational resilience — Lian technical controls

| DORA Requirement | Lian Control |
|-----------------|-----------------|
| ICT incident detection and classification | Prometheus metrics (`/metrics`), OpenTelemetry trace export, structured JSON logs |
| Business continuity — RTO/RPO | Postgres streaming replication + WAL archiving; Redis for cache only (stateless degradation) |
| Backup and recovery | `GET /v1/snapshot` produces a point-in-time export with integrity hash chain; importable on any fresh instance |
| Third-party ICT risk (if cloud-deployed) | MASTER_ENCRYPTION_KEY under customer KMS (AWS KMS, Azure Key Vault, HashiCorp Vault) — encryption key never touches vendor infrastructure |
| Audit trail non-repudiation | SHA-256 serial hash chain; each event links to previous hash. `GET /v1/admin/audit/verify` detects any tampering. |

---

## 2. EU AI Act — High-Risk AI Systems (Art. 9–13, compliance deadline Aug 2026)

The EU AI Act classifies AI systems used in credit scoring, insurance underwriting, and employment as **high-risk** (Annex III). Lian is a memory layer, not an AI system itself, but it is a component of high-risk AI pipelines and must meet the record-keeping obligations imposed on those pipelines.

### Article 12 — Record-keeping

> "High-risk AI systems shall be designed and developed with capabilities enabling the automatic recording of events ('logs') throughout the lifetime of the AI system."

| Requirement | Lian Implementation |
|------------|------------------------|
| Automatic event recording | Every memory write, recall, supersession, and erasure writes an immutable audit event |
| Tamper-evident logs | SHA-256 serial hash chain; `GET /v1/admin/audit/verify` returns `{"chain_valid": true/false}` |
| Retention for lifetime of the system | Crypto-shred deletes content keys; audit log rows survive with hash links intact |
| Ability to reproduce decisions | `GET /v1/recall?as_of=<timestamp>` reconstructs exactly what the agent knew at any past moment |

### Article 13 — Transparency and provision of information

> "High-risk AI systems shall be designed and developed in such a way as to ensure that their operation is sufficiently transparent to enable deployers to understand the system's output and use it appropriately."

The `recall_at` / `as_of` endpoint answers the regulators' transparency question directly: **"Why did the agent produce that output on that date?"** The answer is a reproducible, auditable snapshot of the agent's knowledge state — not a probabilistic reconstruction.

### Article 9 — Risk management

Information barriers (PostgreSQL RLS, `barrier_group` per team/fund) are the control mechanism for isolating high-risk AI decision contexts within the same deployment — a requirement when a single platform serves multiple desks with different regulatory perimeters.

---

## 3. MiFID II — Audit Trail and Record-Keeping (Articles 16, 25)

MiFID II Article 16(7) requires investment firms to maintain records of all services, activities, and transactions for **five years** (seven years for certain instruments).

### Hash-chain audit trail — MiFID II alignment

| MiFID II Requirement | Lian Implementation |
|---------------------|------------------------|
| Record all transactions and services | Every memory write and recall is an audit event |
| Records must be stored in a medium that prevents alteration | Hash chain; each event commits to the hash of the prior event — altering any record breaks `chain_valid` |
| Records must be accessible for supervisory authority review | `GET /v1/admin/audit/export?start=<ISO>&end=<ISO>` produces a complete, verifiable log |
| Five-year retention minimum | Retention policy (PUT `/v1/admin/retention/<namespace>`, `keep_days=1825`) enforces this; content expires, audit row survives |
| Reconstruction of the order/decision process | `as_of` recall reconstructs agent knowledge at any past timestamp; decision chain is bitemporal (event time + system time both preserved) |

### SEC Rule 17a-4 (equivalent obligation for U.S. broker-dealers)

The same hash chain satisfies SEC Rule 17a-4 requirements for electronic record non-alteration:

- Non-rewriteable storage: the hash chain is append-only; no UPDATE path exists on audit rows
- Downloadable for examination: `GET /v1/admin/audit/export` (NDJSON or CSV)
- Third-party verification: the chain verification algorithm is open-source and auditable by the firm's own engineers

---

## 4. Basel III / SR 11-7 — Model Risk Management

The Federal Reserve's SR 11-7 guidance (and equivalent international Basel Committee guidance) requires financial firms to validate AI/ML models and maintain a **model inventory** with version history and risk ratings.

### Backtest contamination detection — model validation alignment

The most common model risk failure mode for AI agents is **look-ahead bias**: the agent's training or fine-tuning data included information that wasn't available at the time of the simulated decision.

`POST /v1/backtest/check` solves this:

```
{
  "agent_id": "quant-strategy-v2",
  "simulation_as_of": "2024-01-15T00:00:00Z",
  "window_hours": 48
}
```

Response flags every memory fact the agent possessed that was **ingested after** `simulation_as_of` — direct evidence for the model validation report that the backtest is clean.

| SR 11-7 Requirement | Lian Control |
|--------------------|-----------------|
| Model identification and inventory | Agent namespaces provide a natural model registry; `GET /v1/agents` lists all deployed agent IDs |
| Conceptual soundness | Supersession rule engine (SUPERSEDES/CONFIRMS/ADDS/CONTRADICTS) documents the agent's belief-update logic |
| Ongoing monitoring | Supersession confidence scores expose drift in agent belief-update quality over time |
| Change management | `GET /v1/snapshot?as_of=<date>` reconstructs the agent knowledge state at any prior date for before/after comparison |
| Validation of look-ahead bias | `/v1/backtest/check` API returns contaminating facts and the gap in hours — quantitative evidence for the validation report |

---

## 5. Information Barriers — Compliance Architecture for Multi-Desk Deployments

### The Chinese wall problem in AI agents

When a single AI deployment serves a firm's investment banking desk and its equity research desk simultaneously, the information barrier between them must extend to the memory layer. It is not sufficient to configure barriers at the application layer — an application-layer bug or misconfiguration can expose deal-sensitive information to research analysts.

Lian's information barriers are enforced **at the PostgreSQL layer** via `FORCE ROW LEVEL SECURITY`, meaning:

1. A misconfigured application cannot read across barrier groups — the DB policy rejects the query
2. A compromised application service with valid DB credentials still cannot read cross-barrier — the RLS policy runs under the table owner's security context, not the session user
3. The barrier is auditable: `barrier_group` is a column on every memory row, visible in the audit export

### Barrier configuration

```bash
# Provision separate agents with explicit barrier groups
POST /v1/admin/agents
{
  "agent_id": "ib-deal-desk-alpha",
  "namespace": "ib",
  "barrier_group": "ib-alpha-team"
}

POST /v1/admin/agents
{
  "agent_id": "equity-research-desk",
  "namespace": "research",
  "barrier_group": "research-team"
}
```

Each write and recall sets `SET LOCAL Lian.barrier_group = <group>` in the DB session, so the RLS policy enforces isolation without any application-layer check.

---

## 6. GDPR Crypto-Shred — Right to Erasure (Article 17)

GDPR Article 17 (Right to Erasure / "Right to be Forgotten") requires the ability to irrecoverably delete personal data on request, including from backups and audit logs.

Crypto-shred satisfies Article 17 without requiring physical deletion of backup media:

| GDPR Requirement | Lian Implementation |
|-----------------|------------------------|
| Erasure of personal data | `POST /v1/erase` destroys the per-subject DEK; all content encrypted under that key is immediately irrecoverable |
| Backup/archive coverage | Future restores that decrypt content will fail — the key is gone. Audit rows survive (no personal data in audit schema; only hashes). |
| Verification of erasure | `GET /v1/erase/status?subject_id=<id>` confirms key destruction timestamp |
| Audit survival | Hash chain rows are not encrypted with per-subject DEKs; audit trail is intact post-erasure |
| Erasure scope | Subject ID maps to patient_id / client_id / user_id depending on adapter; all memories under that subject are covered |

---

## 7. Procurement Questionnaire Reference

Common questions from bank information security and vendor risk management teams:

| Question | Response |
|----------|----------|
| Where is data stored? | Self-hosted: your Postgres + Redis on your infrastructure. No data leaves your network. |
| Who has access to encryption keys? | Customer controls the master key (KMS_PROVIDER). Lian never sees the plaintext key in cloud deployment. |
| Is the codebase auditable? | Yes — fully open source. Security teams can audit the full implementation before deployment. |
| Is there a SOC 2 report? | Not currently; planned. The compliance controls mapped here are technically equivalent to many SOC 2 CC requirements. Enterprise customers may conduct their own audit of the self-hosted deployment. |
| Can we do a penetration test? | Yes — self-hosted deployment is your environment; no approval needed. |
| Can you sign an NDA before reviewing the system? | Yes — contact via GitHub (etanbuns). |
| What is the exit strategy? | `GET /v1/snapshot` exports all memories + audit chain in portable NDJSON. Import into any Postgres instance using the migration scripts. No proprietary format lock-in. |
| What is the data retention policy? | Configurable per namespace via `PUT /v1/admin/retention/<namespace>`. Default: no automatic deletion. |
