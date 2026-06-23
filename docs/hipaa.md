# Lian — HIPAA Technical Safeguards Mapping

**Audience**: Healthcare IT procurement, HIPAA compliance officers, Privacy/Security Officers reviewing Lian as a component of a covered entity's or business associate's AI infrastructure.

**Scope**: This document maps Lian's technical controls to the HIPAA Security Rule Technical Safeguards (45 CFR §164.312). It also covers PHI data flow, encryption posture, and the BAA requirement.

> **CRITICAL**: A Business Associate Agreement (BAA) **must be executed** between the covered entity (hospital, health system, payer) and the Lian operator **before any real patient data (PHI) is processed**. This document describes technical controls only — it does not substitute for a BAA, legal review, or HIPAA risk analysis.

---

## PHI in Lian: What Flows Through the System

When `DOMAIN_ADAPTER=healthcare`, the following structured keys may carry PHI under HIPAA's Safe Harbor or Expert Determination de-identification standards:

| Structured Key | PHI Category (HIPAA §164.514(b)) | Notes |
|----------------|----------------------------------|-------|
| `patient_id` | Direct identifier (MRN, member ID, beneficiary ID) | Always PHI |
| `encounter_id` | Dates / account numbers (if linked to a patient) | PHI when linked |
| `provider_id` | NPI (if patient-linkable in context) | Context-dependent |
| `condition` | Medical diagnosis / ICD-10 code | PHI when patient-linked |
| `medication` | Prescription information | PHI when patient-linked |
| `procedure_code` | CPT / HCPCS | PHI when patient-linked |

Memory **content** (the text stored alongside structured keys) may also contain PHI depending on what clinical agents write.

Lian does **not** process PHI itself — it stores, retrieves, and manages memory records that may contain PHI. This makes Lian a **business associate** when deployed to support a covered entity's operations.

---

## §164.312 — Technical Safeguards Mapping

### §164.312(a)(1) — Access Control (Required)

> "Implement technical policies and procedures for electronic information systems that maintain electronic protected health information to allow access only to those persons or software programs that have been granted access rights."

| Sub-specification | Required/Addressable | Lian Control |
|------------------|---------------------|-----------------|
| (a)(2)(i) Unique user identification | Required | API keys are per-agent identifiers; admin credentials are separate. Every memory write and recall records the `agent_id`. |
| (a)(2)(ii) Emergency access procedure | Required | Admin API key (`ADMIN_SECRET`) provides emergency override; barrier group session var is not set for admin routes, exposing all rows. Document this key in your organization's emergency access procedure. |
| (a)(2)(iii) Automatic logoff | Addressable | Stateless HTTP — sessions expire when JWT/API key TTL expires. No persistent sessions to time out. |
| (a)(2)(iv) Encryption and decryption | Required | AES-256-GCM per-subject encryption at rest; TLS 1.2+ in transit. |

**Information barriers (RLS) — §164.312(a)(1) strong implementation**:

Set `barrier_group=<care_team_id>` when provisioning each agent.  The PostgreSQL RLS policy (`FORCE ROW LEVEL SECURITY`) then enforces that:
- Care team A cannot read patient memories belonging to care team B
- This enforcement runs at the **database layer**, not the application layer — it cannot be bypassed by application bugs

Configuration:
```bash
POST /v1/admin/agents
{
  "agent_id": "icu-triage-agent",
  "namespace": "icu",
  "barrier_group": "icu-care-team"
}
```

---

### §164.312(b) — Audit Controls (Required)

> "Implement hardware, software, and/or procedural mechanisms that record and examine activity in information systems that contain or use electronic protected health information."

| Requirement | Lian Implementation |
|-------------|------------------------|
| Record activity | Every memory write, recall, supersession, and erasure writes an immutable audit event |
| Examine activity | `GET /v1/admin/audit/export?start=<ISO>&end=<ISO>` — structured NDJSON, filterable by agent, namespace, event type |
| Non-alteration of audit records | SHA-256 serial hash chain; `GET /v1/admin/audit/verify` returns `{"chain_valid": true/false}` — any retrospective alteration breaks the chain |
| Retention | Audit rows survive crypto-shred; only memory content is irrecoverable after erasure |

---

### §164.312(c)(1) — Integrity (Required)

> "Implement policies and procedures to protect electronic protected health information from improper alteration or destruction."

| Sub-specification | Required/Addressable | Lian Control |
|------------------|---------------------|-----------------|
| (c)(2) Mechanism to authenticate ePHI | Addressable | SHA-256 content hash stored on every memory row; checked during audit chain verification. Detects any post-write modification. |

The hash chain provides **non-repudiation of memory state**: if a record is altered after ingestion, `chain_valid` becomes `false` and the break point is identifiable to the specific memory row.

---

### §164.312(d) — Person or Entity Authentication (Required)

> "Implement procedures to verify that a person or entity seeking access to electronic protected health information is the one claimed."

| Requirement | Lian Control |
|-------------|-----------------|
| Agent authentication | HMAC-SHA256 signed API keys, scoped to namespace + agent_id |
| Admin authentication | Separate `ADMIN_SECRET` credential; not derivable from agent API keys |
| Mutual authentication (service-to-service) | TLS client certificates supported at the Postgres connection layer; configure via DATABASE_URL SSL parameters |

---

### §164.312(e)(1) — Transmission Security (Required)

> "Implement technical security measures to guard against unauthorized access to electronic protected health information that is being transmitted over an electronic communications network."

| Sub-specification | Required/Addressable | Lian Control |
|------------------|---------------------|-----------------|
| (e)(2)(i) Integrity controls | Addressable | TLS provides transmission integrity; HMAC signature on API keys prevents token substitution |
| (e)(2)(ii) Encryption | Addressable | TLS 1.2+ enforced at the reverse proxy / load balancer layer; configure `sslmode=require` on DATABASE_URL for Postgres transmission |

**Note**: TLS termination occurs at the network boundary. Lian does not handle TLS directly — deploy behind nginx, Caddy, or a cloud load balancer with TLS enforced.

---

## Deployment Configuration Checklist for PHI

```bash
# Required for any PHI deployment
DOMAIN_ADAPTER=healthcare
MASTER_ENCRYPTION_KEY=<32-byte base64 key>    # mandatory; no AGENTMEM_ALLOW_UNENCRYPTED
RLS_BARRIERS_ENABLED=true                      # DB-layer care team isolation
AIRGAP_MODE=true                               # PHI must not leave network
EMBEDDING_PROVIDER=sentence-transformers       # required by AIRGAP_MODE
SUPERSESSION_LLM_STAGE=false                   # required by AIRGAP_MODE (or use self-hosted LLM)

# Recommended
AUDIT_RETENTION_DAYS=2555                      # 7 years (common state law minimum for medical records)
CORS_ORIGINS=https://your-app.example.com     # restrict browser origins; never "*" for PHI
LOG_JSON=true                                  # structured logs for SIEM integration
```

### KMS configuration (choose one)

```bash
# AWS KMS (recommended for AWS-hosted deployments)
KMS_PROVIDER=aws
KMS_AWS_KEY_ID=arn:aws:kms:us-east-1:123456789012:key/...
KMS_AWS_REGION=us-east-1
KMS_AWS_ENCRYPTED_KEY=<base64 CiphertextBlob>

# Azure Key Vault (for Azure-hosted deployments)
KMS_PROVIDER=azure
KMS_AZURE_VAULT_URL=https://myvault.vault.azure.net/
KMS_AZURE_SECRET_NAME=Lian-master-key

# HashiCorp Vault (for on-prem or multi-cloud)
KMS_PROVIDER=vault
KMS_VAULT_ADDR=https://vault.internal:8200
KMS_VAULT_TOKEN=<token>
KMS_VAULT_PATH=Lian/master-key
```

---

## Crypto-Shred Erasure — HIPAA Right of Access and Deletion Requests

While HIPAA does not include a "right to erasure" equivalent to GDPR Article 17, covered entities may need to comply with **state laws** (e.g., CCPA for California health data, NY SHIELD Act) that do include deletion rights.

Crypto-shred satisfies these requirements:

```bash
# Irrecoverably destroy all PHI for a patient (by MRN / member ID)
POST /v1/erase
{
  "subject_id": "MRN-0012345"
}

# Verify erasure
GET /v1/erase/status?subject_id=MRN-0012345
# → {"erased": true, "erased_at": "2026-06-22T14:30:00Z", "audit_chain_intact": true}
```

After erasure:
- All memory content encrypted under that patient's DEK is immediately irrecoverable
- The audit hash chain survives (audit rows contain no PHI — only hashes, agent IDs, timestamps, and event types)
- Future Postgres backups restored from tape cannot recover the content (the DEK is gone from the KMS)

---

## Point-in-Time Recall — Clinical and Legal Use Cases

### Clinical use case: admission decision reconstruction

> "What did the triage agent know about patient MRN-9988 at 03:17 on the night of the admission?"

```python
memories = await client.recall_at(
    agent_id="icu-triage-agent",
    namespace="icu",
    query="patient condition and medication history",
    as_of="2025-11-14T03:17:00Z",
    metadata_filter={"patient_id": "MRN-9988"},
)
```

This reconstructs the exact knowledge state at that moment — accounting for out-of-order lab result ingestion, late-arriving medication records, and any supersession chains that occurred after the recall time.

No other memory layer answers this question correctly under out-of-order ingestion.

### Legal use case: malpractice discovery

An adverse event investigation requires proving what the AI agent knew — and did not know — at the time of the clinical decision. `recall_at` provides an auditable, hash-chain-verified answer:

- The response is reproducible: run the same query tomorrow and get the same answer
- The hash chain proves neither the query result nor the underlying memories were altered post-incident
- The audit export shows who queried the agent and when

---

## BAA Requirement and Current Status

Lian is currently an **open-source self-hosted product**. For regulated healthcare deployments:

1. **If self-hosting**: the covered entity is its own operator. A BAA between the CE and its own IT/infrastructure team is governed by internal policy, not an external agreement.
2. **If using a managed deployment**: a BAA with the managed service operator is required before any PHI is processed.

Lian does not currently offer a managed service. All deployments are self-hosted, so requirement (1) applies.

**Enterprise advisory**: consult your Privacy Officer and legal counsel before deploying any AI system that may process PHI. This document describes technical controls, not legal compliance.
