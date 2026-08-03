# Lians security architecture

This document is the concise security-review view of Lians. The normative
adversary analysis, residual risks, and operator evidence checklist are in the
[threat model](threat-model.md). Deployment code is not a certification of
SOC 2, HIPAA, SEC, FINRA, GDPR, or any other legal requirement; customers must
map and operate controls for their own use, jurisdiction, and risk.

## System purpose

Lians is independent decision-evidence and AI-control infrastructure. It records
what an AI system saw and did, binds consequential decisions to declared model,
policy, identity, tool, source, and review context, evaluates actions through a
runtime Gate, and reconstructs affected decisions and remediation after evidence
or policy changes.

The core security invariant is that an authenticated tenant and information
barrier determine every protected read and write. Ordinary request fields cannot
choose a different namespace, role, scope, principal type, or barrier. Evidence
integrity is append-oriented and independently verifiable, but a verifier must
still establish issuer trust and compare database evidence with an external
anchor.

## Protected data

| Data class | Principal risk | Protection and explicit limit |
|---|---|---|
| Memory/source content | PII, PHI, MNPI, privilege, secrets | Per-subject AES-256-GCM content key, TLS, capture minimization, RLS, retention/erasure. Hashes and metadata may still be sensitive. |
| Recorder events | Prompts, arguments, outputs, model/tool behavior | `metadata_only`, `hash_only`, or explicitly approved `full` capture; secret-field redaction, size/quota policy, idempotency, correlation, and tenant/barrier binding. Plaintext sent by a producer exists in transit and may persist in the collector's encrypted raw queue before Lians minimization. |
| Decision and evidence graph | Outcomes, reason codes, dependencies, impact | Immutable record hashes, bitemporal boundaries, indexed direct/reachable impact, tenant/barrier RLS. Reachability is not proof of causation. |
| Receipts, Gate, approvals, and reviews | Authorization and oversight evidence | Protected receipt digest/signature, trusted-key registry, immutable policy/evaluation and attestation chains, encrypted approval/review/closure text, and one-time audience/request-bound execution permits. Provider access outside the mediator remains a bypass. |
| Integration delivery | Evidence copied to another processor | Encrypted transactional outbox, destination validation, HMAC, stable idempotency key, immutable attempts, retries/dead letter. Delivery is at least once and receiver reconciliation remains required. |
| Credentials and keys | Cross-tenant access or decryption/signing | Digest-only API/workload credentials, OIDC/SCIM lifecycle, bounded workload TTL, external master-key authority, versioned envelopes, separately controlled receipt key. Secrets necessarily used by an application exist in process memory. |
| Audit and backup evidence | Silent history replacement or loss | Serialized hash chain, append-only database controls, full verification, provider-native immutable backup storage, independently anchored provider attestation, restore drills. A privileged database operator can recompute an unanchored chain. |

## Identity and authorization

Lians accepts exactly one API key or bearer credential per request. API and
workload credentials are generated with high entropy and stored only as digests.
Tenant-issued workload credentials have bounded expiry, explicit scopes/role/
barrier, rotation lineage, revocation, and audited lifecycle; only a verified
human OIDC tenant administrator may issue them.

OIDC providers are registered per tenant with exact issuer, audience, algorithm,
claim, token-age, and JWKS policy. Subject bindings and SCIM group entitlements
are administrator-owned database records. Ambiguous roles/barriers, over-50
scope unions, and either direction of the 1,000-edge SCIM membership bound fail
closed without partial reconciliation.
Runtime identity is a versioned canonical reference containing the trusted provider
and binding UUID; raw issuer subjects are not approval identities. API/workload
credentials use their UUID rather than a free-form label. This prevents cross-issuer
subject and duplicate-label collisions in approvals, reviews, and audit evidence.
SCIM tokens are expiring and stored sealed. MFA, device assurance, token binding,
IdP step-up, and break-glass ceremony are external identity controls.

A separate global `X-Admin-Secret` exists for bootstrap and disaster recovery.
It activates the database administration sentinel and is therefore a high-impact
credential that requires independent network restriction, managed-secret custody,
two-person process, and alerting.

## Tenant isolation and governance

Application queries carry namespace and barrier predicates. PostgreSQL sessions
also set transaction-local namespace and barrier values, and protected tables use
forced namespace RLS plus restrictive information-barrier policies. The API-key
and OIDC-binding tables enable direct-access RLS but omit FORCE solely so their
table-owner, PUBLIC-revoked exact lookup functions can resolve one active record
before the tenant context exists. Readiness verifies that narrow function posture.
Production assumes the application role is `NOSUPERUSER NOBYPASSRLS`, does not
own protected tables, cannot assume an owner, and has current migrations.

A scoped principal may read untagged records in its tenant; a null barrier means
tenant-wide, not “deny.” Provisioning must therefore require a barrier wherever
organizational policy demands a wall. SQLite relies on application predicates
and is a local development profile, not the production multitenancy boundary.

Versioned namespace governance can constrain authoritative processing region,
allowed Recorder/OTLP capture modes, and daily event, decision, memory, and byte
reservations. Counters use database row locking. An inactive policy intentionally
preserves legacy unlimited behavior, so production readiness treats activation as
operator evidence rather than assuming it.

## Cryptography and key lifecycle

Memory content uses a random per-subject DEK and AES-256-GCM; destroying that DEK
makes remaining ciphertext unavailable through Lians. Tombstone identifiers,
hashes, audit evidence, integrations, and backups have separate retention and
erasure duties.

Master-derived fields and wrapped DEKs use authenticated, self-identifying v2
envelopes. Production accepts AWS KMS, Azure Key Vault, or Vault and rejects the
environment-key provider. A bounded keyring contains one current and at most one
previous key. The protected offline rotation operator verifies backup identity,
schema, privileges, triggers, immutable hashes, every ciphertext readback, and
zero remaining legacy/unknown values before recording a checkpoint. A
persistent database fence permits only the prepared current/previous IDs, and
write-conflicting locks make its final current-only narrowing atomic with the
verified rewrite. Historical backups require historical keys for their complete
retention period.

Decision Receipt v0.1 signs the protected receipt hash with Ed25519. Verification
checks the exact version/shape, canonical digest, signature, key ID, and—when
provided—the independently trusted public key. The embedded public key alone is
not issuer trust. The compatibility signer loads a raw private key into process
memory. The preferred Vault Transit signer sends only the 32-byte receipt digest,
pins an exact positive key version and raw public key, checks key metadata when
loaded, and locally verifies every remote signature before emission. Vault
availability, token lifecycle, public-key publication, revocation, and optional
HSM backing remain deployment trust decisions.

## Decision control and evidence integrity

Receipt validity proves integrity and possession of a signing key, not that a
source was true or every relevant input was recorded. The completeness grade
names missing declared evidence instead of treating it as present.

DecisionRecord v3 distinguishes the caller-claimed agent label from the
canonical authenticated recorder principal and binds principal, auth method,
non-secret credential reference, principal type, optional role, and complete
bounded effective scopes into the immutable record hash. Database
triggers reject hash-covered updates, deletion, and truncation; corrections are
new records linked with `supersedes_id`. Receipt/evidence-pack export first
recomputes that hash and verifies the exact immutable `decision_recorded` event
binding. Historical v1 rows are explicitly unverified and cannot be freshly
signed as authentic records. Verified v2 rows remain cryptographically
verifiable but truthfully disclose that their authorization snapshot is absent.

Gate resolves the real authenticated identity and selects the immutable policy
from administrator-authored exact action and target-prefix mappings,
verifies the linked decision's authenticated versioned hash and audit-event binding,
verifies trusted receipt material, checks linked decision/change context, and
records an immutable allow, deny, or review result. Restrictive defaults cover
actions with no matching rule; an action is allowed only when at least one rule
matches and every matched requirement passes. Every Gate request requires a linked
decision and target; policy IDs supplied by callers are assertions only. Decision
type, risk, policy version, current-source status, and known untrusted-content
signals are server-derived; a trusted
receipt must bind the same signed tenant, decision, type, and policy. Approvals are append-only,
role-bound, context-bound, expiring series; review events are chained; remediation
tasks need closure attestations before a case can close. Policy rules can require
recent approvals from principals registered as human, excluding API-key and
workload attestations from eligibility. That classification does not by itself
prove MFA, step-up, liveness, or device assurance. Consequential callers must place Gate
immediately before the protected side effect.

Each immutable policy also names one or more exact canonical enforcement
principals and a maximum permit TTL (60 seconds by default, 300 seconds hard
maximum). An allow result and exactly one opaque permit are inserted atomically;
deny/review creates none. The permit is bound to the policy, evaluation, action,
canonical target URI, linked decision, mediator identity, expiry, and SHA-256 of
the canonical provider/tool request. The plaintext token is returned once and
only its digest is stored. The separately authenticated mediator must present the
actual request claims to consume it once under row/advisory locking before
dispatch. Database RLS, foreign keys, exact-claim guards, unique consumption, and
update/delete/truncate triggers protect this boundary. Provider IAM must make the
mediator the exclusive caller; permits cannot block a separate direct credential.

Audit events serialize per namespace and bind ordered fields plus a versioned
payload representation into SHA-256 chains. Database triggers and constraints add
immutability to the highest-risk control records. Meaningful verification is
untruncated and compares the current chain with an independently retained receipt,
tip, Merkle/WORM export, or immutable backup anchor. Application convention alone
does not stop a privileged database owner.

## Egress and air-gap posture

`AIRGAP_MODE` rejects external embedding/LLM, SIEM, Stripe, and application OTLP
configuration at startup. It also disables legacy webhook registration/dispatch,
durable integration delivery, metering, and telemetry at call and worker
boundaries, and forces supported model libraries into offline mode.

That software posture is defense in depth, not a universal firewall. Operators
must enforce deny-by-default network egress and explicitly review DNS, database,
Redis, IdP/JWKS, KMS, package/model provisioning, and administrative paths. An
air-gapped deployment uses a pre-provisioned self-hosted model and proves the
boundary through independent network tests.

## Deployment, supply chain, and recovery

The production Helm chart requires digest-pinned images, existing Secrets,
separate runtime/migration database identities, external PostgreSQL/Redis,
external KMS, TLS ingress, peer-verifying PostgreSQL (`sslmode=verify-full`) and
Redis (`rediss://`) transport, non-root/read-only containers, dropped capabilities,
seccomp, no default service-account tokens, default-deny network policy,
isolated migration, probes, PDB/HPA, topology spreading, monitoring, and a
suspended immutable-backup reference. Its public surface registers no
break-glass routers. An enabled persistent OTLP queue additionally requires a
named encrypted StorageClass and explicit raw-payload custody policy. It
intentionally has no runnable production defaults.

Third-party GitHub Actions are pinned to immutable revisions. CI performs
dependency review and CodeQL. The production API workflow builds each supported
platform once, scans every exact staged payload digest, composes only those
digests, and signs/attests the resulting immutable index. Fly consumes that
already-verified digest and never performs a production-token source rebuild.
Backup and MCP candidates also fail on high/critical vulnerabilities; published
multi-architecture digests carry SBOM, provenance, GitHub attestations, and
keyless Cosign signatures. These controls prove origin and build inputs, not
runtime safety; admission must verify the exact deployed digest.

Recovery combines provider HA/PITR with verified logical bundles, provider-native
immutable handoff, isolated restore drills, audit/receipt verification, and
application-level reconstruction. Redis and collector queues are not database
backups. RPO/RTO, capacity, certificate/identity/KMS availability, incident
command, and restore success are continuously operated controls.

## Review references

- [Threat model and production evidence checklist](threat-model.md)
- [Production deployment](deploy.md)
- [Production operations](production-operations.md)
- [Master-key rotation](master-key-rotation.md)
- [Backup and restore](backup-restore.md)
- [Immutable provider handoff](worm-provider-handoff.md)
- [Software supply-chain verification](supply-chain-security.md)
- [Security reporting policy](../SECURITY.md)
