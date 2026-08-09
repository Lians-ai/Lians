# Lians Memory data handling

This document describes the implemented hosted MCP data path for `remember`, `recall`, and `forget_memory`. The canonical endpoint `https://mcp.lians.ai/mcp` is planned and is not live yet. Managed-backup deletion remains an external production-policy gate described below.

## Collection boundary

Lians Memory accepts only a narrow memory snippet that the user explicitly selects for durable storage. It does not accept a full conversation transcript, silently capture future messages, or accept caller-supplied event time or arbitrary metadata.

| Operation | User data processed | Durable effect |
| --- | --- | --- |
| `remember` | One explicit `content` snippet, a project label, and an optional non-secret retry key | Creates one encrypted, tenant-scoped memory record after safety and admission checks pass |
| `recall` | A narrow query, project label, and result/token bounds | Returns bounded context to the authenticated caller and writes a privacy-minimal audit receipt; it does not change stored memory content |
| `forget_memory` | A memory UUID and an explicit confirmation boolean | With `confirm: true`, crypto-shreds the one matching active hosted memory in the authenticated tenant |

The implemented deterministic filters reject detected instances of:

- credentials and authentication material, including passwords, API keys, access or refresh tokens, MFA or one-time codes, private keys, provider tokens, cloud credentials, and JWTs;
- payment-card numbers;
- detected personal or regulated identifiers such as Social Security, medical-record, provider, passport, driver's-license, taxpayer, or national-ID numbers;
- detected protected health information;
- material non-public information;
- prompt-injection or instruction-override content;
- bulk transcripts and content that fails the durable-memory admission policy.

These controls are pattern- and policy-based; they reduce risk but are not represented as exhaustive detection of every possible sensitive value. Users must not submit restricted data.

## Identity and tenant isolation

The server verifies OAuth before a tool runs. It transforms the verified OAuth issuer, required tenant claim, and subject with HMAC-SHA256 under a private server secret to produce an opaque namespace and subject fingerprint. The hosted identity path does not persist the raw OAuth subject or bearer token.

Every tool call is authorized against that derived tenant namespace and the required scope. Project identifiers are derived inside the authenticated namespace. `forget_memory` additionally requires that the reference identify an active record created through the hosted OpenAI MCP surface; a missing, erased, foreign-tenant, or non-hosted reference is reported only as `not_found`.

## Encryption and active storage

The hosted MCP assigns a unique internal subject identifier to each memory. Each memory therefore receives its own random 32-byte content key. Content is encrypted with AES-256-GCM, and the per-memory key is wrapped under the configured master-key provider. Active records store ciphertext rather than the submitted plaintext.

Recall decrypts only authorized candidate content for bounded processing and delivery to the authenticated caller. Hosted operations bypass the process-wide plaintext content-key cache, so an unwrapped per-memory key lives only in the active call stack. Plaintext must not be emitted to application logs, telemetry, or audit records.

## Privacy-minimal audit data

Hosted audit records store tenant- and purpose-separated keyed HMACs and allowlisted operational controls, not raw stored content or raw recall queries. The allowlisted fields can include operation and record references, server timestamps, policy or routing controls, bounded result identifiers, a receipt HMAC, numeric storage accounting, counts, and privacy/degradation flags. Recall query variants are represented by keyed HMACs. Rejections record risk tags and reasons rather than rejected raw content.

Audit data supports authorization review, abuse investigation, deletion evidence, and operational diagnosis. It is not a second memory store and must not be used to reconstruct user content.

The service applies a database-backed, per-tenant UTC-day ceiling to hosted remember and recall operations that can grow append-only audit data. The default production limit is 5,000 audit events per tenant per day, in addition to the weighted per-minute request limit. A confirmed `forget_memory` remains available at that ceiling so a growth control cannot block user-requested crypto-erasure.

## Retention and deletion

The default active-content retention policy is 365 days and is configurable from 1 through 3650 days. Production must keep scheduled retention pruning enabled and verify its execution. Expired active content is removed on the prune cycle, so deletion is not promised at the exact instant a record reaches its retention age. A valid legal hold can suspend scheduled expiry and must be disclosed in the public privacy policy.

Active-content pruning removes the encrypted content and derived embedding, metadata, and live projection, then destroys the memory's wrapped content key. Hosted recalls bypass the shared result and working-set caches so those caches do not become a second durable plaintext store.

A confirmed `forget_memory` call performs the same active-store crypto-shredding immediately for the selected hosted record: it removes encrypted content and derived active data and destroys that memory's wrapped key. Repeating the call returns `not_found` and does not reveal cross-tenant details.

The audit log is append-only and tamper-evident. The configured `audit_retention_days` is a minimum-retention policy, not an automatic audit-deletion deadline. In the current implementation, privacy-minimal audit records and non-content deletion evidence remain indefinitely in the active audit chain. Production submission is blocked until the operator either (a) implements and validates chain-safe audit archival/deletion with a published window or (b) publishes and obtains legal/privacy approval for indefinite retention of these pseudonymous, content-free records.

## External audit and backup gates

**Status: pending operator evidence; blocks production submission.**

The application code proves active-store crypto-shredding, but it does not prove the retention or deletion behavior of managed database backups, replicas, snapshots, exports, or disaster-recovery media. No backup deletion window is promised in this repository.

Before submission, the production operator must:

1. Approve and publish the append-only audit-retention policy or implement and validate chain-safe audit expiry.
2. Obtain provider-backed evidence for the maximum backup-retention and deletion window across every production copy.
3. Document how restores preserve deletion tombstones or otherwise prevent a forgotten record from becoming active again.
4. Verify that replicas, exports, and disaster-recovery workflows are covered.
5. Put the verified windows and limitations in the public privacy policy and reviewer materials.
6. Record approval of that evidence in the production checklist and submission metadata.

If these facts are not verified, the plugin is not ready to submit.

## Evidence and operator checks

- Hosted MCP contract: [`openai_mcp.py`](../../../agentmem/src/lians/openai_mcp.py)
- OAuth identity derivation: [`openai_oauth.py`](../../../agentmem/src/lians/openai_oauth.py)
- Encryption implementation: [`crypto.py`](../../../agentmem/src/lians/crypto.py)
- Retention, audit, and erasure behavior: [`memory_service.py`](../../../agentmem/src/lians/memory_service.py)
- Reviewer workflow: [`reviewer-guide.md`](./reviewer-guide.md)
- Production checklist: [`openai-universal-plugin-production.md`](../../../docs/openai-universal-plugin-production.md)

This disclosure must be rechecked whenever storage providers, retention settings, identity derivation, audit fields, encryption, or the public tool contract changes.
