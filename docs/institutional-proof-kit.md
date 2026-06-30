# Lians Institutional Proof Kit

This is the evidence packet Lians should hand to financial institutions, medical
organizations, legal teams, vendor-risk reviewers, and security architects.

Lians should be pitched as a regulated-memory control plane, not as a generic
vector-memory library. The product claim is simple:

> Lians proves what an AI agent knew, when it knew it, who could see it, and
> whether that memory was allowed to influence a regulated decision.

## 1. Procurement Narrative

Most memory products optimize for recall quality, personalization, or graph
context. Those are useful, but regulated buyers have a higher bar:

- The memory layer must suppress stale facts before they reach the model.
- The system must reconstruct the agent's knowledge state at any past timestamp.
- Sensitive facts must remain inside desk, matter, patient, or team barriers.
- Erasure must be provable without destroying the audit chain.
- Every material state change must be exportable for review.

Lians' differentiator is that correctness, auditability, access control, and
erasure are part of the memory primitive itself.

## 2. Technical Proof Artifacts

| Artifact | Why it matters | Existing reference |
|---|---|---|
| Bitemporal recall demo | Shows stale facts are excluded and past state is reproducible | `POST /v1/recall` with `as_of`, `GET /v1/audit/reconstruct` |
| Supersession benchmark | Proves revised facts close old validity windows | `docs/benchmark.md`, `agentmem/tests/test_supersession_benchmark.py` |
| Audit-chain verification | Detects tampering in historical records | `GET /v1/admin/audit/verify` |
| Erasure certificate | Proves content is unrecoverable while hashes remain | `POST /v1/erase`, `GET /v1/erase/{subject_id}/certificate` |
| Information-barrier test | Shows cross-barrier access fails below the app layer | RLS migrations and barrier tests |
| Backtest contamination report | Flags facts unavailable at simulation time | `POST /v1/backtest/check` |
| Relationship graph path | Answers conflict-of-interest and related-party questions | `GET /v1/graph/path` |
| Compliance report | Summarizes supersessions, erasures, conflicts, and chain status | `GET /v1/compliance/report` |

## 3. Security and Compliance Packet

Institutional buyers should receive these documents as a single review bundle:

- `docs/security-whitepaper.md`
- `docs/threat-model.md`
- `docs/soc2-hipaa-readiness.md`
- `docs/hipaa.md`
- `docs/compliance.md`
- `docs/deploy.md`
- `docs/testing.md`
- `docs/benchmark.md`
- `docs/competitive-landscape.md`
- `docs/compare-mem0.md`
- `docs/compare-zep.md`

Each document should be reviewed before any formal sales process to ensure the
claims match the current implementation and deployment model.

## 4. Required Enterprise Answers

| Buyer question | Lians answer |
|---|---|
| Can we self-host? | Yes. Postgres + pgvector, Redis, FastAPI. Kubernetes manifests and Docker Compose are included. |
| Can data stay inside our perimeter? | Yes. Self-hosted and air-gap modes are supported; local embeddings can avoid external model calls. |
| Who controls encryption keys? | The customer can control the master key through env, AWS KMS, Azure Key Vault, or Vault. |
| Can you prove deletion? | Yes. Subject-level keys are destroyed and an erasure certificate preserves non-content hashes for audit. |
| Can you prove no memory was altered? | Yes. Event rows are chained with SHA-256 hashes and can be verified on demand. |
| Can teams be isolated? | Yes. Namespaces and information barriers are enforced with PostgreSQL Row-Level Security in self-hosted deployments. |
| Can we export and exit? | Yes. Snapshot and audit export endpoints produce portable records. |
| Is this a certification? | No. Lians provides technical controls; certifications such as SOC 2, HIPAA attestation, or customer-specific audits apply to a deployment and operator. |

## 5. Gaps to Close Before Large Institutional Pilots

These should be tracked explicitly rather than hidden in marketing copy:

- SOC 2 Type II report or auditor-backed readiness assessment.
- Formal BAA process for managed healthcare deployments.
- FIPS 140-validated crypto option for customers that require it.
- WORM storage reference architecture for SEC 17a-4 deployments.
- SAML/OIDC reference gateway with group-to-barrier mapping.
- Memory admission policy engine for prompt injection, source trust, and high-risk fact approval.
- Independent benchmark reproduction instructions for mem0, Zep/Graphiti, and Lians.
- Connector roadmap for Bloomberg/FactSet, FHIR/HL7, iManage/NetDocuments, Relativity, Snowflake, and Databricks.

## 6. Demo Flow for Institutional Buyers

1. Ingest a fact, revise it, and show present recall excludes the stale version.
2. Run `recall_at` before and after the revision timestamp.
3. Show the memory lineage proving why the old fact was superseded.
4. Assign two agents to different barrier groups and show cross-barrier recall fails.
5. Run a graph path query for a conflict-of-interest or related-party relationship.
6. Crypto-shred a subject and retrieve the erasure certificate.
7. Verify the audit chain and export a compliance report.

This demo should take less than ten minutes and should not require a hosted
service, external embedding API, or customer data.
