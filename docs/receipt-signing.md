# Decision Receipt signing

Receipt signing is downstream of the authenticated DecisionRecord integrity
boundary. Before any local or Vault provider receives a digest, Lians
recomputes the declared DecisionRecord v2/v3 hash and verifies its unique immutable
`decision_recorded` EventLog binding. Legacy-unverified or mutated records fail
closed instead of being freshly signed. A verified v2 receipt explicitly marks
authorization-snapshot completeness false; v3 binds the complete effective
authorization snapshot. See
[DecisionRecord authenticity and integrity](decision-record-integrity.md).

Lians Decision Receipt v0.1 supports two Ed25519 signing providers:

- `local` preserves compatibility with a raw 32-byte private key held by the API
  process;
- `vault-transit` keeps the private key in HashiCorp Vault Transit and sends only
  the already-computed 32-byte receipt SHA-256 digest for signing.

Unsigned receipts are permitted only as an explicit development posture. They
remain hash-verifiable, and their completeness result names the missing
deployment signature. Production must configure one valid signer.

## Production Helm mapping

The supported chart keeps receipt credentials in
`existingSecrets.receipts.name`, separate from application, database, and KMS
Secrets. `config.receipts.provider=local` requires only
`localPrivateKeyKey`; the rendered pod receives `RECEIPT_SIGNING_PRIVATE_KEY`
and no Vault token. `provider=vault-transit` requires that field to be empty and
requires exactly one of `vaultTokenKey` or `vaultTokenFileKey`; the rendered pod
receives no local private key.

The preferred file mode projects the configured Secret key read-only at
`/run/secrets/lians/receipt-vault-token/token` and sets
`RECEIPT_VAULT_TOKEN_FILE` to that absolute path. Direct token environment
injection remains a compatibility option. The chart rejects both-or-neither,
requires the exact version and public-key pin, and requires explicit non-world-
open Vault egress CIDRs whenever either receipt Transit or master-key Vault is
selected. Include both clusters' CIDRs when those services are separate.

## Vault Transit trust contract

Create a non-derived Ed25519 key at a dedicated Transit mount. For example:

```bash
vault secrets enable -path=lians-receipts transit
vault write -f lians-receipts/keys/production-receipts type=ed25519 derived=false
vault read -format=json lians-receipts/keys/production-receipts
```

Record the positive version number and that version's raw 32-byte public key.
Configure both explicitly; `0` and `latest` are rejected. The public key may be
base64 or hexadecimal in Lians configuration.

```dotenv
RECEIPT_SIGNING_PROVIDER=vault-transit
RECEIPT_SIGNING_KEY_ID=lians-us-east-receipts-v1
RECEIPT_SIGNING_PRIVATE_KEY=
RECEIPT_VAULT_ADDR=https://vault.example.com
RECEIPT_VAULT_TOKEN=
RECEIPT_VAULT_TOKEN_FILE=/run/secrets/lians/vault-token
RECEIPT_VAULT_NAMESPACE=admin/payments
RECEIPT_VAULT_MOUNT_POINT=lians-receipts
RECEIPT_VAULT_KEY_NAME=production-receipts
RECEIPT_VAULT_KEY_VERSION=1
RECEIPT_VAULT_PUBLIC_KEY=<raw-public-key-base64>
RECEIPT_VAULT_TIMEOUT_SECONDS=5
```

Configure exactly one token source. A direct `RECEIPT_VAULT_TOKEN` remains
available for compatibility. The preferred token file must be an absolute path
to a regular, read-only file no larger than 8 KiB. Lians reopens and validates it
for every Vault request, so a Vault Agent token sink—including one authenticated
through Kubernetes—can rotate without restarting the API. Mount it read-only
into the application container; the credential writer can atomically replace the
source from its own separately authorized mount.

The application accepts a Vault origin only—never URL credentials, a path,
query, or fragment. Production requires HTTPS. Mount and key names are restricted
to single safe path segments, requests never follow redirects, and all network
timeouts are bounded to 0.25–10 seconds. An optional Vault Enterprise namespace
is sent as `X-Vault-Namespace` only after printable, segment-safe validation.
The signer-owned HTTP client ignores process proxy environment variables so a
stray `HTTPS_PROXY` cannot receive the Vault token. Vault JSON responses are
streamed through a 1 MiB decoded-body cap before parsing. A caller-injected HTTP
client is an advanced test/integration boundary and owns its own proxy posture.

Grant the workload token only `read` for the one metadata path and `update` for
the one signing path:

```hcl
path "lians-receipts/keys/production-receipts" {
  capabilities = ["read"]
}

path "lians-receipts/sign/production-receipts" {
  capabilities = ["update"]
}
```

Do not grant key creation, rotation, configuration, export, backup, or deletion
to the application identity. Apply Vault's normal short-lived authentication,
renewal, audit-device, TLS, availability, and optional HSM controls outside
Lians. Never place the token in a values file or application log. Readiness may
publish only the provider, algorithm, deployment key ID, pinned version, and a
public-key fingerprint; it never publishes the Vault address, namespace, key
path, token-file path, or credential content.

## Fail-closed signing flow

At signer load, Lians reads the named key metadata and requires all of the
following:

1. key type `ed25519`, signing support, and `derived=false`;
2. the pinned positive version exists and is still permitted for signing;
3. the public key returned for exactly that version matches the configured raw
   public-key pin.

For each receipt, Lians submits the base64 encoding of the exact 32 digest bytes,
the pinned `key_version`, and `prehashed=false`. In this contract the digest is
the Ed25519 message; Vault is not asked to select Ed25519ph or perform an extra
application-level hash. Lians accepts only an exact
`vault:v<positive-version>:<canonical-base64>` response for the pinned version,
then verifies the 64-byte signature locally against the pinned public key.
Nothing is emitted if metadata, version, encoding, network availability, or
local verification fails.

The resulting portable receipt retains the v0.1 signature shape: algorithm,
deployment key ID, raw public key, and canonical base64 signature. Vault-specific
syntax is not exposed in the receipt.

## Rotation

Treat rotation as a coordinated trust rollout:

1. rotate or import the new Vault Ed25519 key version under operator authority;
2. retrieve and independently verify the new raw public key;
3. publish a new deployment `RECEIPT_SIGNING_KEY_ID` and trusted-key registry
   entry with an explicit validity window;
4. roll out the new positive `RECEIPT_VAULT_KEY_VERSION` and matching
   `RECEIPT_VAULT_PUBLIC_KEY` together;
5. verify readiness and a signed canary receipt before revoking the prior key.

Old receipts retain their embedded key ID and public key, but issuer trust still
comes from the independently governed registry. Keep prior public verification
material for the full receipt retention period. Never silently repoint an
existing deployment key ID to different key material.
