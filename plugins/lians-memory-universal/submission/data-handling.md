# Lians Memory data handling

This document describes the implemented hosted MCP data path for `remember`, `recall`, and `forget_memory`. The canonical `https://mcp.lians.ai/mcp` route is live at production build `e72fad2c7f98ecf54b6553a90bf8d862046c1abc` with schema `0030_force_hosted_mcp_rls`. HTTPS, protected-resource metadata, and the unauthenticated OAuth challenge have passed the repository checker. During minute `2026-08-10T03:41Z`, a sanitized production OAuth E2E also passed OIDC discovery, DCR, browser login and callback, token exchange, repository JWT verification, the authenticated endpoint checker, MCP remember/recall/confirmed-forget calls, and cleanup. The `Lians, Ai` business identity is verified in the OpenAI portal, the submitter's Apps Management owner role is validated, and the public synthetic two-record reviewer fixture is live and verified. Secure portal credential delivery, developer-mode rehearsal, the skill/domain/tool scans, portal country selection, submission, and publication remain pending. The production operator's audit-retention and managed-backup decisions are recorded below.

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

## Production processors and recipients

OpenAI, including ChatGPT and Codex, is the client surface that sends the user-selected snippet or query and receives the requested result. Auth0 by Okta provides OAuth identity and account access; Lians derives the opaque hosted namespace before memory operations. Fly.io hosts the application and encrypted PostgreSQL volumes. Upstash provides operational rate-limiting state and does not serve as a backup of active memory content. Resend delivers account verification and recovery email. The [Lians privacy policy](https://www.lians.ai/privacy) publishes these roles and their applicable data categories.

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

The audit log is append-only and tamper-evident. The configured `audit_retention_days` is a minimum-retention policy, not an automatic audit-deletion deadline. In the current implementation, privacy-minimal audit records and non-content deletion evidence remain indefinitely in the active audit chain. On 2026-08-09, the production operator approved this indefinite retention policy for the pseudonymous, content-free audit chain and published it in the [Lians privacy policy](https://www.lians.ai/privacy). This decision must be reassessed if audit fields, purposes, recipients, or identifiability change.

## External audit and backup policy

**Status: approved and publicly disclosed on 2026-08-09; revalidate before submission and after any storage change.**

The application code proves active-store crypto-shredding. Production and staging PostgreSQL currently use encrypted Fly.io volumes with automatic daily snapshots enabled. The provider configuration and current snapshot inventory both report a five-day retention period. [Fly.io documents](https://fly.io/docs/volumes/snapshots/) that volume snapshots are point-in-time copies, are retained for five days by default, and can restore data into a new volume.

The resulting production policy is intentionally explicit:

1. Privacy-minimal, content-free audit records and non-content deletion evidence remain indefinitely in the active append-only audit chain.
2. Active memory content is crypto-shredded by confirmed deletion or scheduled expiry, but encrypted content captured in an earlier Fly snapshot can remain recoverable until that snapshot expires, for no more than the configured five-day snapshot window.
3. A snapshot restore can reintroduce the historical state captured by that snapshot. A restored database must therefore remain out of public service until the operator checks retention and deletion state; the service does not claim that a pre-deletion snapshot already contains a later tombstone.
4. Hosted recall-result and working-set caches are bypassed. Upstash is used for operational state and rate limiting, not as a backup of active memory content.
5. The [Lians privacy policy](https://www.lians.ai/privacy) publishes the indefinite audit policy, the five-day encrypted snapshot window, and the possibility of recovery until snapshot expiry.

These facts satisfy the current operator policy gate. They must be reverified if the database, cache, replica, export, snapshot, disaster-recovery, or audit architecture changes.

## Evidence and operator checks

- Hosted MCP contract: [`openai_mcp.py`](../../../agentmem/src/lians/openai_mcp.py)
- OAuth identity derivation: [`openai_oauth.py`](../../../agentmem/src/lians/openai_oauth.py)
- Encryption implementation: [`crypto.py`](../../../agentmem/src/lians/crypto.py)
- Retention, audit, and erasure behavior: [`memory_service.py`](../../../agentmem/src/lians/memory_service.py)
- Reviewer workflow: [`reviewer-guide.md`](./reviewer-guide.md)
- Production checklist: [`openai-universal-plugin-production.md`](../../../docs/openai-universal-plugin-production.md)

This disclosure must be rechecked whenever storage providers, retention settings, identity derivation, audit fields, encryption, or the public tool contract changes.
