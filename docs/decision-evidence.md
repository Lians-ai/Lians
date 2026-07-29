# Decision evidence and reconstruction

Lians is the cross-platform system of record for consequential AI decisions.
It captures the memory, trace, policy, model, prompt, tool, and human evidence
that shaped a decision, then preserves enough context to reconstruct it later.

The product is designed for regulated organizations that run agents across more
than one model provider, cloud, or framework. Lians does not require the buyer
to replace those runtimes. It provides one evidence plane across them.

## Who buys it

The primary buyer is the executive responsible for proving that production AI
operated within the organization's controls:

- Head of compliance or AI governance
- Model risk management
- Operational risk and incident response
- Regulated recordkeeping and legal discovery
- Internal audit

The budget normally comes from AI governance, model risk, compliance
recordkeeping, or operational resilience. The initial purchase trigger is not
"we need better agent memory." It is one of these:

- A source, model, or policy changed and the organization cannot identify the
  decisions exposed to the change.
- An examiner or internal review asks what information an agent used at a
  particular time.
- An incident team cannot reconcile traces across Bedrock, Azure OpenAI,
  Anthropic, or a direct model integration.
- A governance team has policies and assessments but lacks runtime evidence
  that the controls operated.

Lians can support those workflows, but using Lians does not by itself establish
compliance with any law or regulation.

## The unavoidable workflow

### 1. Open an envelope

Open the correlation boundary before the agent starts a consequential action:

```http
POST /v1/decision-envelopes
Content-Type: application/json

{
  "agent_id": "underwriter-1",
  "decision_type": "credit_application",
  "regime": "ECOA_REG_B",
  "subject_id": "applicant-42",
  "trace_id": "0123456789abcdef0123456789abcdef",
  "knowledge_as_of": "2026-07-20T12:00:00Z",
  "completeness_profile": "regulated_recordkeeping"
}
```

The envelope remains open while the agent retrieves context, evaluates policy,
calls tools, and requests human review.

### 2. Bind evidence as work happens

Pass the envelope ID to recall or context. Lians binds the content-addressed
recall receipt and every returned memory version before returning the response:

```http
POST /v1/recall
Content-Type: application/json

{
  "agent_id": "underwriter-1",
  "query": "verified applicant income",
  "decision_envelope_id": "2e5cc939-d464-4ed6-8742-63ac5bd4d8bb"
}
```

OTLP traces bind automatically when their trace ID matches the envelope. Policy
decisions, tool calls, tool results, and reviews can bind through
`POST /v1/records/events` or the evidence endpoint.

### 3. Seal the decision

```http
POST /v1/decision-envelopes/{envelope_id}/seal
Content-Type: application/json

{
  "outcome": "declined",
  "reason_codes": ["DTI_HIGH"],
  "decided_at": "2026-07-20T12:00:02Z",
  "model_id": "credit-v3",
  "model_version": "3.2.1",
  "model_artifact_hash": "64-character-sha256",
  "policy_id": "credit-policy",
  "policy_version": "2026-07",
  "policy_artifact_hash": "64-character-sha256",
  "input_hash": "64-character-sha256",
  "output_hash": "64-character-sha256"
}
```

Sealing creates the append-only decision record and returns its evidence
completeness assessment.

## Honest completeness grades

Lians never labels an incomplete record verified.

The definitions are cumulative and normative:

| Grade | Minimum base requirements | What Lians can claim |
|---|---|---|
| Recorded | Sealed decision with `record_hash` | The append-only decision and its integrity commitment exist. |
| Reconstructable | Recorded + `knowledge_as_of` + material influence evidence | The captured point-in-time context and material influences can be assembled. |
| Verifiable | Reconstructable + input hash + output hash + hashes on every material evidence edge | A recipient can independently check the integrity of the committed decision evidence. |
| Replayable | Verifiable + exact model + exact prompt + content-addressed trace + replay-manifest hash | The declared identity and dependency commitments required to attempt deterministic replay are present. |

Each response includes every failed check, the grade it blocks, and a concrete
remediation. Profiles can add requirements without weakening the base grades:

- `standard`
- `regulated_recordkeeping`
- `human_review`

Customers can add recognized checks through `required_checks`. Unknown checks
are rejected, so a typo cannot silently lower the standard.

The complete normative definitions, profile additions, exclusions, and stable
gap codes are published in
[Completeness Grades](completeness-grades.md).

## Reconstruction is not replay

`GET /v1/decisions/{decision_id}/reconstruction` returns one correlated view:

- Decision and envelope
- Completeness assessment
- Point-in-time knowledge snapshot
- Evidence graph
- Linked ledger events
- OTLP spans
- Ordered timeline

Reconstruction remains useful when an external API was nondeterministic or an
exact runtime is no longer available. Replayability is a higher grade and
requires a replay manifest that commits to the missing runtime dependencies.

## Blast-radius alerts

The normalized evidence graph makes impact analysis a production workflow:

```http
GET /v1/evidence/blast-radius
  ?evidence_type=external
  &source_id=vendor-risk-feed
  &source_version=2026-07-20
```

The response identifies every sealed decision and open envelope connected to
that exact source, version, or artifact hash.

`POST /v1/evidence/changes` records a revision, retraction, compromise, expiry,
policy change, or model change. It returns the blast radius immediately and
emits the `evidence.blast_radius` webhook event through the durable delivery
queue.

This moves Lians from post-incident forensics to pre-incident exposure
management.

## Portable Evidence Pack v2

Request `version=v2` from the evidence-pack endpoint to include:

- Decision Envelope and evidence graph
- Completeness grade and named gaps
- Point-in-time knowledge snapshot
- Audit-chain verification
- Retention posture
- Canonical manifest and pack hashes
- Optional Ed25519 signature

When no signing key is configured, the pack says `unsigned`. It never implies a
signer identity. When signed, the offline verifier can validate the signature
and match the embedded public key to independently trusted configuration. A key
ID can select or check the intended key, but the label alone is not signer
identity proof:

```bash
lians-verify-evidence pack.json \
  --trusted-public-key lians-evidence-public-key.txt
```

Private-key custody, access separation, normal rotation, compromise handling,
and verification after retirement are defined in
[Evidence Pack Signing Key Custody](evidence-signing-key-custody.md). The
bundled raw-key signer requires secret-manager injection in production.
Organizations that require a non-exportable key must connect a dedicated HSM or
KMS-backed signing service.

## ValidMind validation view

`GET /v1/integrations/validmind/evidence-readiness` returns:

- Decision counts by completeness grade
- Reconstructable and verifiable rates
- Most frequent evidence gaps
- Decision-level assessment records

This gives model-risk teams an inspectable readiness surface before deeper
workflow integration is ordered by real customer feedback.

## Product boundary

Lians integrates with orchestration frameworks, model providers, policy engines,
observability tools, and governance systems. It does not need to replace them.

Build adjacent capabilities only when they add an evidence source, improve
reconstruction fidelity, strengthen verification, or serve another evidence
consumer. Do not turn Lians into another generic agent framework, vector
database, policy language, or trace dashboard.
