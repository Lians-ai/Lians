# Evidence Pack Signing Key Custody

Version 1.0

This document defines the operational custody and verification model for Lians
Evidence Pack v2 Ed25519 signatures. A valid signature proves possession of a
private key. Signer identity exists only when the public key is matched through
an independently trusted registry.

## Security boundary

The Evidence Pack signing key is separate from:

- subject-content encryption keys;
- API authentication keys;
- the audit-chain hash state; and
- database encryption credentials.

Compromise of the signing key permits forged signatures but does not decrypt
stored memory content. Compromise must still be treated as a security incident
because recipients may rely on signer identity.

## Where the private key lives

The current Lians signer accepts a base64-encoded raw 32-byte Ed25519 seed
through `EVIDENCE_SIGNING_PRIVATE_KEY`. This is suitable for local development
and for production only when a deployment platform injects it from an approved
secret manager.

Production requirements:

1. Generate the key inside an approved HSM, KMS, Vault, or offline ceremony.
2. Store the private seed only in the approved secret system. Never store it in
   Git, a container image, PostgreSQL, an Evidence Pack, logs, tickets, or
   application telemetry.
3. Grant secret-read permission only to the workload identity that renders
   signed Evidence Packs. Console users, support roles, CI jobs, and database
   roles do not receive private-key access.
4. Inject the key at runtime and keep it only in process memory. Disable core
   dumps and prevent environment inspection by untrusted workloads.
5. Separate permission to deploy the signer from permission to read or rotate
   the signing secret. Record both operations in the infrastructure audit log.

For environments that require non-exportable keys, the raw-key signer is not
sufficient. Use a dedicated signing service backed by the organization's HSM or
KMS and treat that integration as a deployment requirement.

## Key identifiers

`EVIDENCE_SIGNING_KEY_ID` is a routing label, for example
`evidence-prod-2026-q3`. It is not proof of identity and must never be trusted
without matching the public key through an independent channel.

Key IDs must be unique and must not be reused after retirement or revocation.

## Public trust registry

Recipients need a durable registry containing every public key that may be
required to verify a retained Evidence Pack:

```json
{
  "schema": "https://lians.ai/schemas/evidence-signing-keyring/v1",
  "issuer": "example-bank",
  "keys": [
    {
      "key_id": "evidence-prod-2026-q3",
      "algorithm": "Ed25519",
      "public_key": "base64-encoded-32-byte-public-key",
      "status": "active",
      "valid_from": "2026-07-01T00:00:00Z",
      "valid_until": null,
      "revoked_at": null
    }
  ]
}
```

Publish the registry through an independently authenticated channel such as a
customer trust center, signed configuration repository, or regulated records
system. Preserve historical versions under immutable retention. The public key
embedded in a pack enables cryptographic verification, but it does not establish
who controlled that key.

## Rotation procedure

1. Generate a new key and unique key ID.
2. Add the new public key to the trust registry with `status=preactive`.
3. Distribute or replicate the updated registry before the signer changes.
4. Change the signer to the new private key and key ID.
5. Confirm a newly generated pack verifies against the independent registry.
6. Mark the new key `active` and the previous key `retired`.
7. Keep the retired public key in the registry for at least the longest Evidence
   Pack retention period. Never delete it merely because it no longer signs new
   packs.
8. Destroy or archive the retired private key according to the organization's
   retention and legal-hold policy. Verification requires only the public key.

Normal retirement does not invalidate signatures made during the key's valid
period.

## Compromise and revocation

If compromise is suspected:

1. Stop signing with the affected key.
2. Publish `status=revoked`, `revoked_at`, and a reason in the trust registry.
3. Generate and publish a replacement key through the rotation procedure.
4. Use the evidence graph to identify packs issued by the affected key ID and
   time range.
5. Notify relying parties and reissue packs when policy requires it.
6. Preserve the revoked public key and incident record. Deleting it makes
   historical investigation harder.

Cryptographic validity and trust acceptance are separate. A signature from a
revoked key can remain mathematically valid while policy rejects it.

## Recipient verification

Recipients should:

1. recompute the canonical manifest and pack hashes;
2. verify the Ed25519 signature;
3. resolve `key_id` in an independently obtained trust registry;
4. compare the embedded public key with the registry key;
5. evaluate signing time against the key validity and revocation record; and
6. record the registry version used for verification.

The bundled verifier accepts a trusted public key and expected key ID:

```bash
lians-verify-evidence pack.json \
  --trusted-public-key evidence-prod-2026-q3.pub \
  --expected-key-id evidence-prod-2026-q3
```

Retired-key verification works because the retired public key remains available.
No private key is required.

## Evidence to retain for review

- Key-generation approval and custody record
- Secret-manager, KMS, or HSM access policy
- Public trust-registry history
- Activation, retirement, and revocation timestamps
- Rotation test pack and verification result
- Workload deployment and secret-access audit logs
- Incident records for any suspected compromise
