# Lians zero-knowledge sync protocol

> **Current status:** the device enrollment, encrypted revision chain,
> deletion propagation, deterministic conflict handling, and opaque reference
> server contract are implemented and tested in `lians_easy.sync`. The engine
> also persists tenant-isolated encrypted workspaces, device grants, and
> compare-and-swap revisions behind the explicit `sync` API-key scope. Consumer
> OAuth, recovery, billing enforcement, and the sync UI are not generally
> available yet. This document is an engineering contract, not a claim that
> Lians Cloud is live.

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

The authenticated server namespace must own exactly the workspaces, device
grants, and encrypted revisions associated with that account. A production
implementation persists only:

- public device descriptors and signed grants;
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
supports workspace creation and head inspection, signed device-grant
registration and listing, encrypted revision push/pull, and exact confirmed
workspace deletion. PostgreSQL row-level security is enabled and forced on all
three sync tables. `OpaqueSyncHTTPClient` gives the Bridge a bounded HTTPS
transport with redacted credentials, sanitized failures, stale-head handling,
and loopback-only plain HTTP for tests. These endpoints currently use scoped
Lians API keys; they are not the finished nontechnical sign-in experience.

The identity token, raw identity-provider subject, workspace key, device private
keys, decrypted profile, memory count, project name, source, and receipt body
must not be written to sync logs. The existing OAuth boundary derives an opaque
tenant namespace from a verified issuer, tenant, and subject; cloud routes
should reuse that mapping rather than create a second account identity.

## Recovery and revocation gates

The first consumer release may use **another approved device** as the only
recovery method. That tradeoff must be stated before sync is enabled. Server-
side password reset cannot decrypt a workspace and must not be presented as
memory recovery.

Before hosted sync is generally available, Lians still needs:

- native-app authorization-code flow with PKCE and a tested Google sign-in;
- explicit device revocation plus workspace-key rotation for remaining devices;
- a recovery-key or trusted-contact design, separately opted into by the user;
- bounded retry/backoff, offline indicators, and conflict-review UI;
- account deletion that removes encrypted objects and entitlement metadata;
- restore, multi-device race, clean-device, and provider-outage qualification;
- an external review of the protocol and implementation.

Until those gates pass, use the encrypted `.liansbackup` flow for supported
device migration and disaster recovery.
