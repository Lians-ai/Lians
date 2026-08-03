# Decision Receipt v0.1 conformance package

This package gives independent producers deterministic, language-neutral test
vectors for the Decision Receipt v0.1 schema, canonical protected-payload hash,
Ed25519 signature message, and external trust decision. The Python reference
runner imports no `lians` code and performs no network requests.

The receipt schema remains the normative envelope contract. `manifest.json` is
the normative conformance-case manifest; `manifest.schema.json` and
`mutation.schema.json` define its machine-readable shape.

The deterministic v0.1 fixtures intentionally exercise the base envelope and
do not contain the optional normalized evidence graph extension. Producers
that emit `audit_chain.lians_evidence_graph` must additionally validate it
against
[`../evidence-graph-manifest.schema.json`](../evidence-graph-manifest.schema.json),
recompute its `manifest_hash` after removing only that member, and then compute
the enclosing receipt hash. The extension is bounded to 10,000 entries; a
producer must fail closed instead of labeling a truncated manifest complete.

## Package contents

- `fixtures/valid-unsigned.json` is a complete synthetic receipt with a valid
  protected-payload digest and no signature.
- `fixtures/valid-signed.json` has a valid Ed25519 signature over the raw
  32-byte SHA-256 digest and embeds the matching public test key.
- `mutations/protected-payload-tamper.json` is a restricted RFC 6902 `replace`
  operation. The runner applies it in memory to the signed fixture without
  changing `integrity`; the resulting receipt remains schema-valid but its
  protected digest and signature no longer verify.
- `trust/test-only-ed25519-public-key.base64` is the independently pinned
  public key for the positive signed case.
- `trust/wrong-test-only-ed25519-public-key.base64` is an intentional mismatch
  for the negative trust case.
- `reference_runner.py` verifies the published suite or a producer's receipt.

`manifest.json` pins the exact receipt and mutation file hashes, raw public-key
hashes, protected canonical byte lengths, declared receipt hashes, and computed
protected-payload hashes. Its canonicalization probe covers recursive key
ordering, UTF-8 non-ASCII text, a control-character escape, quote and reverse
solidus escaping, arrays, booleans, null, and an integer. A conforming
implementation must reproduce the probe's exact UTF-8 hex, length, and digest.

## Exact case semantics

| Case | Expected result |
|---|---|
| `valid-unsigned` | Schema and digest valid; signature absent; overall valid only because this case neither requires a signature nor supplies a trust anchor. It proves integrity, not issuer identity. |
| `valid-signed-trusted` | Schema, digest, signature, issuer key-ID binding, and independently pinned raw public key all valid. |
| `invalid-protected-payload-tamper` | The protected `decision.outcome` is replaced. Schema remains valid; the declared digest differs from the recomputed digest; verification of the signature against the recomputed raw digest fails; overall invalid. |
| `invalid-wrong-trust-anchor` | Protected digest and self-signature are valid, but the embedded public key differs from the independently supplied key; overall invalid. |

Signature validity and issuer authentication are separate results. A verifier
must not trust `integrity.signature.public_key` merely because that key verifies
its own signature. Trust requires an exact match to public key material obtained
through an authenticated channel outside the receipt.

## Run the suite

Use Python 3.11 or newer with `jsonschema` and `cryptography` installed:

```bash
python specs/decision-receipt/v0.1/conformance/reference_runner.py --suite
```

For a machine-readable report:

```bash
python specs/decision-receipt/v0.1/conformance/reference_runner.py --suite --json
```

Verify output from an independent producer with the published schema and a
public key acquired from that producer's authenticated trust channel:

```bash
python specs/decision-receipt/v0.1/conformance/reference_runner.py \
  --receipt producer-receipt.json \
  --require-signature \
  --trusted-public-key-file producer-public-key.base64 \
  --trusted-key-id producer-key-2026-08 \
  --json
```

Exit status `0` means all selected expectations passed or the supplied receipt
is valid. Status `1` means a conformance mismatch or invalid receipt. Status `2`
means malformed inputs, unsafe suite paths, unavailable verification
dependencies, or corrupted suite material.

The runner rejects duplicate JSON member names, non-UTF-8 input, non-finite
numbers, non-canonical base64, and suite paths that escape the specification
directory. It intentionally provides no signing feature and handles no private
key material.

## Test-key warning

The signed fixture uses globally known RFC 8032 test-vector material. The files
in `trust/` are public keys only; no private key is stored here. Any associated
private value is public test data, not a secret. Never use the fixture key ID or
key material in a deployment, KMS, signer, secret manager, or trust registry.
Generate a deployment-specific key and distribute only its public trust anchor
over a separately authenticated channel.
