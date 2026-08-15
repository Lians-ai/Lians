# Lians zero-knowledge sync protocol

> **Current status:** the device enrollment, encrypted revision chain,
> deletion propagation, deterministic conflict handling, and opaque reference
> server contract are implemented and tested in `lians_easy.sync`. The engine
> also persists tenant-isolated encrypted workspaces, device grants, and
> compare-and-swap revisions behind either the explicit `sync` API-key scope or
> a verified consumer `memory:sync` OAuth scope. The native Bridge now implements
> system-browser Authorization Code + PKCE, encrypted rotating-token storage,
> and automatic pull-before-recall / write-through-after-change orchestration.
> The packaged App now includes consumer sync controls and a short-code Add
> Device / approval flow with encrypted resumable local state. Signed device
> removal now rotates the workspace key only to surviving devices and publishes
> a fresh encrypted snapshot. User-held encrypted backups now provide a tested
> all-devices-lost recovery path. A production identity provider, cloud-only
> recovery, billing enforcement, external cryptographic review, and signed
> release qualification are not generally available yet.
> This document is an engineering contract, not a claim that Lians Cloud is live.

## Product promise

Signing in tells Lians Cloud whose encrypted workspace to route. It does not
give Lians Cloud the ability to read that workspace. Memory content, project
names, sources, activity, receipts, counts, and the local profile name are
encrypted on the device before upload.

The consumer experience should use ordinary language:

1. **Turn on sync** and sign in with an account.
2. On another computer, choose **Add this device**.
3. The existing device shows the new device name and an eight-character code.
4. The user compares the code and chooses **Approve**.
5. The new device receives the workspace key encrypted specifically for its
   local device key, then imports encrypted revisions.
6. Activity says **Synced just now · 2 devices · Lians cannot read your memory**.

No passphrase, recovery phrase, API key, JSON file, terminal, or cloud storage
choice appears in the normal path.

## Cryptographic boundary

Each device derives two domain-separated keys from its OS-protected Lians root:

- an X25519 exchange key used only to enroll that device; and
- an Ed25519 signing key used to approve devices and sign revision envelopes.

The workspace starts with a random 256-bit key. The key is protected locally
with the existing device root and never uploaded in plaintext. An approved
device wraps it for the recipient using ephemeral X25519, HKDF-SHA-256, and
AES-256-GCM. The signed grant binds the workspace, key epoch, request, recipient
keys, approver keys, and issue time. Enrollment requests expire after at most
30 minutes and include a human-verifiable code.

Every revision is a complete, validated profile snapshot encrypted with
AES-256-GCM. Its authenticated header contains only protocol metadata:

- workspace ID and key epoch;
- monotonically increasing revision number;
- previous encrypted-object hash;
- public device ID and creation time; and
- cipher name and random nonce.

The author signs the header and ciphertext. The object hash covers that
signature. A cloud service can therefore reject unknown writers, reordered
revisions, duplicate revisions, stale compare-and-swap writes, malformed
envelopes, and oversized objects without receiving the workspace key or
plaintext payload.

## Device removal and future confidentiality

The App lists active and previously removed devices without exposing public
keys or workspace identifiers. Removing a device is a two-step action. The
initiating active device creates a fresh random workspace key, wraps it
individually to every other active device, and signs a rotation document that
binds the old and new epochs, exact prior cloud head, removed device, surviving
device registry, and every encrypted key wrap.

The service verifies the signer and exact active registry transactionally,
marks the target unable to write, advances the key epoch, deletes revisions
encrypted with the obsolete key, and resets the encrypted revision head. The
initiator then uploads a complete profile snapshot under the new key. An
offline surviving device verifies the signed rotation and decrypts only its own
wrap before accepting new revisions. A removed device receives no wrap, cannot
decrypt future revisions, and cannot publish.

This is future confidentiality, not remote erasure. A removed computer may
retain memory it downloaded before removal. The App states that limitation next
to the action and preserves signed rotation evidence instead of claiming that
old local copies disappeared.

## Merge, correction, and forgetting

The local merge runs in one SQLite transaction and re-encrypts incoming content
with the destination device's own local root. The rules are intentionally
narrow:

- new immutable memory, activity, and receipt IDs are appended;
- identical records are skipped;
- pause and resume state uses the later validated update time;
- one forward correction link may advance from empty to a specific successor;
- a permanent forget tombstone always wins and removes content, hashes, token
  estimates, metadata, and pause state on every device;
- divergent content, provenance, or two different corrections of the same
  memory never use last-writer-wins. The entire incoming revision is rejected
  and shown as a conflict that needs review.

This prevents an offline device from resurrecting forgotten memory and prevents
an automatic merge from silently choosing between contradictory corrections.

## Cloud storage contract

The authenticated server namespace must own exactly the workspaces, short-lived
enrollment exchanges, device grants, signed key rotations, and encrypted
revisions associated with that account. A production
implementation persists only:

- public device descriptors and signed grants;
- signed removal evidence and recipient-encrypted future-key wraps;
- encrypted revision envelopes and their public chain metadata;
- account entitlement, quota, and operational metadata; and
- security events that do not include decrypted profile fields.

The reference `OpaqueRevisionLog` models the server's transaction boundary. A
production write is accepted only when `revision == head + 1` and
`previous_hash == head.object_hash`. A stale writer receives a precondition
failure, pulls the missing encrypted revisions, merges locally, and retries.

The durable `/v1/sync` API implements that same boundary with a 1.5 MB encrypted
revision limit under the deployment-wide 2 MB request cap, at most 20 active
devices, 100 revisions per pull page, and a 10,000-revision retention gate. It
supports workspace creation and head inspection, tenant-scoped expiring
enrollment request/approval exchange, signed device-grant registration and
listing, device listing, signed removal and key rotation, encrypted revision
push/pull, and exact confirmed workspace deletion. PostgreSQL row-level
security is enabled and forced on all five sync tables.
`OpaqueSyncHTTPClient` gives the Bridge a bounded HTTPS
transport with redacted API-key or rotating OAuth bearer credentials, sanitized
failures, stale-head handling, and loopback-only plain HTTP for tests. Consumer
tokens are verified for signature, issuer, audience, lifetime, and `memory:sync`
scope. Their issuer, optional organization, and subject become a server-secret
HMAC-derived opaque namespace; the sync tables receive none of those raw values.

`CloudSyncService` serializes Bridge, MCP, and hook processes against one signed
local state file. Connected clients pull encrypted revisions before recall,
listing, context injection, and local mutations, then publish after remember,
correction, pause, scope change, or confirmed forgetting. Cloud or identity
provider failure never prevents the local mutation. A bounded, non-secret
retry marker is shared by Bridge, MCP, and short-lived hook processes, starting
at five seconds and capping at five minutes. While that pause is active,
automatic operations use local memory immediately instead of repeating the
15-second cloud timeout. The App says that Lians is working locally, shows when
automatic sync will retry, and keeps **Try sync now** available as a deliberate
backoff bypass. A successful sync clears the marker. The package test suite
exercises the critical sequence from a Cursor-origin preference to a separate
Codex device, then back through Claude correction and cross-device forgetting.
Confirmed cloud deletion removes every encrypted workspace and pending device
exchange in the authenticated account namespace, plus the local retry marker
and sync state. It then signs out so a later memory change cannot recreate a
cloud copy without the user deliberately turning sync on again.

The identity token, raw identity-provider subject, workspace key, device private
keys, decrypted profile, memory count, project name, source, and receipt body
must not be written to sync logs. The existing OAuth boundary derives an opaque
tenant namespace from a verified issuer, tenant, and subject; cloud routes
should reuse that mapping rather than create a second account identity.

## Recovery and revocation gates

Lians supports two deliberately separate recovery paths. An approved device can
add a replacement through the matching-code flow. If every approved device is
lost, a clean device can verify and import a user-held encrypted
`.liansbackup`, re-encrypt its contents for the new device, and start a fresh
encrypted cloud workspace. The recovery screen shows memory, activity, and
receipt counts before import and requires an explicit second confirmation.

Backup recovery requires both the file and its separately kept passphrase.
Lians cannot reset that encryption. The inaccessible prior cloud workspace may
remain as ciphertext until account deletion, and the App states that boundary
instead of claiming it was remotely erased. Server-side password reset cannot
decrypt a workspace and must not be presented as memory recovery.

Before hosted sync is generally available, Lians still needs:

- production authorization-server configuration and a tested Google sign-in;
- an optional cloud-only recovery-key or trusted-contact design for users who
  do not keep an encrypted backup;
- conflict-review UI for competing, stale, or superseded memories;
- identity-provider account and billing-entitlement deletion orchestration
  (all Lians sync objects already have one confirmed deletion path);
- restore, multi-device race, clean-device, and provider-outage qualification;
- an external review of the protocol and implementation.

The encrypted `.liansbackup` flow is the supported zero-knowledge device
migration and disaster-recovery path. Cloud-only recovery remains unavailable.
