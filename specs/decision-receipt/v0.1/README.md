# Lians Decision Receipt v0.1

Decision Receipt v0.1 is a provider-neutral JSON envelope for the recorded
evidence boundary of a consequential AI decision. It describes who or what
acted, what model and policy were recorded, which sources and tools were
cited, what human review occurred, how complete the capture was, and whether
the exported payload still matches its digest and optional deployment
signature.

The normative machine-readable contract is [`schema.json`](schema.json). The
schema identifier is:

```text
https://lians.ai/specs/decision-receipt/v0.1/schema.json
```

## What v0.1 proves

A successful hash verification proves that the protected receipt fields have
not changed since the digest was computed. The protected fields include the
decision boundary, actor, model, input/output hashes, sources, tools, policy,
authorization, human review, reconstruction manifest, completeness result,
and the audit-chain verification statement.

New server-issued receipts use a verified DecisionRecord v3. v3 binds the
canonical principal, principal type, optional named role, effective scopes,
observed authentication method, and non-secret credential reference that
authorized the write. Verified v2 records remain integrity-verifiable during
the rolling transition, but they contain no historical authorization snapshot
and therefore cannot satisfy the `authorization.context` completeness check.
`actor.agent_id` and `actor.claimed_agent_id` remain workload-supplied labels,
not authenticated identities. Before building a receipt, Lians recomputes the
versioned DecisionRecord hash and verifies its unique immutable
`decision_recorded` EventLog binding. Legacy-unverified rows are not eligible
for receipt signing or export.

`authorization.recording_write` is derived only from the authenticated v3
snapshot. Any caller-supplied `metadata.authorization` or
`metadata.permissions` value is preserved separately under
`authorization.declared_workflow_context` with `verified: false`; it can never
upgrade the receipt's authorization completeness.

An Ed25519 signature additionally proves that the digest was signed by the
private key corresponding to the receipt's public key. Authenticating the
issuer requires the verifier to supply a separately trusted public key; an
embedded public key alone proves self-consistency, not organizational identity.
The unprotected signature metadata `key_id` must match the protected
`issuer.key_id`, but a matching label is not a substitute for pinning the key.

The receipt does **not** claim deterministic reproduction of a model response,
legal or regulatory compliance, source truthfulness, or completeness beyond
the explicit completeness checks in the receipt. A receipt can be valid and
still have an incomplete evidence grade.

## Canonicalization and integrity

The protected payload is every top-level member except `integrity`.

1. Serialize the protected payload as UTF-8 JSON with keys sorted recursively,
   no insignificant whitespace, and non-ASCII characters preserved. In Python,
   the equivalent is:

   ```python
   json.dumps(
       payload,
       sort_keys=True,
       separators=(",", ":"),
       ensure_ascii=False,
       allow_nan=False,
   )
   ```

2. Compute SHA-256 over those UTF-8 bytes and encode the result as 64 lowercase
   hexadecimal characters in `integrity.receipt_hash`.
3. If present, the Ed25519 signature is computed over the 32 raw digest bytes,
   not over the hexadecimal text. The raw 32-byte public key and 64-byte
   signature are base64 encoded.

The canonicalization identifier is `json-sort-keys-utf8-v1`. This deliberately
small v0.1 algorithm is deterministic for the JSON values emitted by Lians.
Producers must not emit non-finite numbers such as `NaN` or `Infinity`.
This is not RFC 8785 JSON Canonicalization Scheme. Cross-language producers
must reproduce the serialization above exactly; in particular, fractional or
very large numbers inside open extension objects can have different lexical
forms across JSON runtimes and should be encoded as strings in v0.1.

## Portability and conformance vectors

The [`conformance/`](conformance/) package contains a language-neutral manifest,
deterministic unsigned and Ed25519-signed receipt fixtures, a protected-payload
tamper mutation, independently supplied matching and wrong public-key cases,
and a standalone Python reference runner. The manifest pins the exact fixture
files, canonical UTF-8 lengths and SHA-256 values, public-key hashes, and a
Unicode/escaping canonicalization probe. The runner imports no Lians package
code, so third-party producers can check their output without trusting the
server implementation they are testing.

The fixture signature uses globally known RFC 8032 **test-only** material. The
package stores public keys only and stores no private key. Never register the
fixture key ID or key material in a deployment trust registry, signer, KMS, or
secret manager. A real issuer must generate its own signing key and distribute
its public trust anchor through a separate authenticated channel. See the
[conformance semantics and commands](conformance/README.md) before consuming
the vectors.

## Verify a receipt

Install the repository package, then verify a JSON file:

```bash
lians-receipt verify receipt.json
```

Use standard input and request a machine-readable report:

```bash
cat receipt.json | lians-receipt verify - --json
```

Require an Ed25519 signature and pin the issuer to an independently obtained
public key. Keys may be raw hexadecimal or base64:

```bash
lians-receipt verify receipt.json \
  --require-signature \
  --trusted-public-key-file deployment-receipt-key.pub
```

`--require-signature` without a trusted public key requires only a
cryptographically valid self-signature. It does not authenticate the issuer.

Exit status `0` means valid, `1` means verification failed, and `2` means the
receipt or key input could not be read or parsed.

The CLI and `/v1/receipts/verify` endpoint have no JSON Schema runtime
dependency. They perform the v0.1 envelope checks needed for safe version and
algorithm selection, recompute the digest, and verify an optional Ed25519
signature. They do not perform full nested field-by-field schema validation.
Consumers that require those diagnostics must additionally validate with a
JSON Schema Draft 2020-12 implementation.

## Required sections

| Section | Purpose |
|---|---|
| `decision` | Decision identity, outcome, valid-time/record-time boundaries, regime, and record hash |
| `actor` | Claimed agent label plus authenticated recorder and v3 authorization provenance |
| `model` | Provider/model/version and instruction or configuration hashes |
| `artifacts` | Input and output hashes |
| `tools` | Recorded tool definitions and result hashes |
| `sources` | Cited source versions, validity windows, content, and hashes |
| `policy` | Policy version and recorded evaluation |
| `authorization` | Verified v3 recording-write snapshot and separately labeled caller-declared workflow context |
| `human_review` | Review status, reviewer, and review time |
| `correlation` | Session, trace, and span identifiers |
| `reconstruction` | Valid-time and record-time boundaries plus the snapshot manifest |
| `audit_chain` | Audit-chain verification statement protected by the receipt hash |
| `completeness` | Weighted capture checks, grade, and explicit missing evidence |
| `integrity` | Canonicalization, receipt digest, and optional signature |

## Normalized evidence graph extension

Lians-issued receipts carry the optional
`audit_chain.lians_evidence_graph` extension. Its closed machine-readable
contract is
[`evidence-graph-manifest.schema.json`](evidence-graph-manifest.schema.json).
It binds the decision to normalized source, policy, model, tool, permission,
instruction, input, and output artifacts through explicit `direct` or
`reachable` relations.

The server reads a fixed `snapshot_max_link_sequence`, emits entries in
canonical `(relation, link_id)` order, and refuses the receipt export with
`evidence_graph_requires_paged_export` when more than 10,000 visible links
would make the portable manifest partial. `complete: true` means every link at
that registration watermark is present. It does not mean every kind was fully
normalized: that separate claim is exposed as
`normalization.normalized_complete` and is also reflected by the zero-weight
`evidence.normalization` completeness check. Missing normalization therefore
cannot be hidden by an otherwise high weighted score.

`manifest_hash` is SHA-256 over the canonical manifest object with only
`manifest_hash` removed. The enclosing receipt digest and optional signature
protect the manifest again in context. Verifiers check the decision binding,
closed shapes, cardinalities, unique UUID edges, canonical ordering, coverage
state, and both hashes. `audit_chain.receipt_exported_at` records the export
time; v0.1 retains the decision's `recorded_at` as top-level `issued_at` for
wire compatibility.

## Standards mappings

Decision Receipt v0.1 is provider-neutral. These mappings describe how
normalized standards evidence can support receipt fields without weakening the
distinction between observed execution, retrieved material, cited evidence, and
authenticated actor provenance:

- [OpenTelemetry GenAI](mappings/opentelemetry-genai.md)
- [Model Context Protocol](mappings/mcp.md)
- [Agent2Agent Protocol](mappings/a2a.md)

No mapping automatically promotes a trace, tool call, message, or artifact to
causal decision evidence. The event must be integrity-checked and explicitly
bound to the authoritative decision inside the same tenant and information-
barrier view; remaining gaps stay in `completeness.missing`.

## Compatibility and evolution

Consumers must reject unsupported `receipt_version` or canonicalization
identifiers instead of guessing. Additive or breaking top-level changes require
a new published schema version. Domain-specific data can be carried inside the
open principal, authorization, policy-evaluation, audit-chain, and tool objects
without changing the v0.1 envelope.

Receipts can contain cited source content and identifiers. Treat them as
sensitive evidence artifacts and apply the same access, retention, redaction,
and residency controls as the underlying decision record.

The Lians API exports hash-only source entries by default: `sources[].content`
is `null` while the source identity, version, validity window, and content hash
remain integrity-protected. An authorized caller must explicitly request
`include_source_content=true` when a full-content evidence transfer is intended.
