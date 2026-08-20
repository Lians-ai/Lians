# GDPR Right-to-Erasure for AI Memory: Why Crypto-Shredding Is the Only Real Answer

*July 2026 · Lians engineering. This is a technical argument, not legal advice;
the design decisions below are how Lians implements Art. 17, and they apply to
any memory system.*

**TL;DR:** In an AI memory system, "we deleted the row" is not erasure. A data
subject's information survives in embeddings, derived facts, graph edges,
caches, backups, and audit logs. Logical deletion can't chase all of those, and
provably so. The only architecture that makes erasure *provable* is
**crypto-shredding**: encrypt every subject's content under a per-subject key,
and erase by destroying the key. Everything ciphered under it - everywhere it
was copied - becomes unrecoverable at once, while the audit trail survives.

## The problem: memory systems multiply personal data

A GDPR Art. 17 request ("erase everything about this person") sounds like a
`DELETE WHERE subject_id = X`. In an agent memory layer, the subject's data has
already fanned out into at least six places:

1. **The memory rows** - the obvious part.
2. **Embeddings.** A vector is a lossy function of the content, but not an
   anonymous one: embedding-inversion attacks recover substantial plaintext
   from dense vectors, and membership inference can confirm the subject was
   present. Deleting text while keeping its vector is not erasure.
3. **Derived facts.** Extraction pipelines produce summaries, entity profiles,
   and consolidated "user facts" downstream of the original message. Deleting
   the source leaves the derivative ("prefers X, diagnosed with Y") intact.
4. **Graph edges.** Relationship stores keep `Person - treated_by→ Provider`
   triples that outlive the conversation that created them.
5. **Caches and replicas.** Recall hot caches, read models, search indexes.
6. **Backups and audit logs.** The one place you *cannot* delete from without
   destroying the evidentiary value regulated customers keep the log for -
   and, under WORM/SEC 17a-4-style retention, the one place you may be legally
   *forbidden* to delete from. Erasure and retention obligations collide
   head-on.

Any "delete API" that only handles №1 is a compliance story that fails its
first serious review. The dirty secret of the current agent-memory category is
that almost every system does exactly №1.

## Why logical deletion can't be proven

Even a diligent implementation that chases rows, vectors, edges, and caches has
two unfixable problems:

- **You can't prove a negative.** An auditor asks: "show me this person's data
  is gone." With logical deletion, the honest answer is "we ran deletes against
  every store we know about." Unknown copies (old snapshot, replicated index,
  debug dump) are exactly the ones the process misses.
- **Backups.** Restoring any backup taken before the deletion resurrects the
  subject. Rewriting backups defeats their purpose and often violates
  retention rules.

## Crypto-shredding: erase by key destruction

The architecture that resolves this - inherited from disk-encryption practice
(NIST SP 800-88 calls it *cryptographic erase*) - is:

1. On first write about a data subject, generate a **per-subject data
   encryption key (DEK)** - in Lians, AES-256-GCM, keyed by
   `(namespace, subject_id)`.
2. Encrypt the subject's memory **content** with their DEK before it touches
   the database. Embeddings for that content are stored alongside, tied to the
   same subject.
3. Wrap DEKs with a master key (env, AWS KMS, Azure Key Vault, or HashiCorp
   Vault - `KMS_PROVIDER`).
4. **To erase:** destroy the DEK, null the subject's embeddings and derived
   values, tombstone the rows, and write an **erasure event into the audit
   chain**. Content in every copy of the database - including every backup -
   is now ciphertext with no key in existence.

The proof changes shape. Instead of "we looked everywhere," it's: *the key no
longer exists; here is the erasure certificate; decrypting the remaining bytes
is equivalent to breaking AES-256.* That's a statement an auditor can accept
without trusting your inventory of copies.

### The part people miss: the audit trail survives

Art. 17 erasure and record-retention duties (SEC 17a-4, FINRA 4511, HIPAA §164.316,
tax law) coexist only if the *fact that data existed and was erased* is
separable from the *content*. In Lians:

- The audit chain stores **content hashes and event metadata**, not plaintext,
  so the hash-chain (tamper evidence) remains verifiable after the shred -
  including over erased entries.
- Erased memories become **tombstones**: `erased_at` set, content
  unrecoverable, lineage intact. Point-in-time reconstructions (`/v1/snapshot`,
  `/v1/audit/reconstruct`) return the tombstone, never a ghost of the content.
- The erasure itself emits a **signed erasure certificate** (request reference,
  subject, counts, timestamp) - the artifact you hand the data subject or the
  supervisory authority.

### What about the model/LLM itself?

Crypto-shredding covers the memory layer. If you fine-tune models on user
content, that's a separate (and much harder) unresolved problem - one more
reason regulated deployments keep personal data in retrievable memory with
erasure guarantees *instead of* baking it into weights.

## Checklist: evaluate any memory vendor's erasure claim

Ask these six questions. "Yes" to all six is rare in the current market:

1. Is deletion **cryptographic** (key destruction) or logical (row deletes)?
2. Are **embeddings** destroyed or provably useless after erasure - including
   in the vector index?
3. Are **derived facts and graph edges** attributable to a subject and covered
   by the same erasure?
4. Do **backups** become unrecoverable without rewriting them?
5. Does erasure produce a **certificate** you can hand an authority?
6. Does the **audit trail survive** erasure with its integrity guarantees
   intact - and does point-in-time reconstruction return tombstones rather
   than resurrecting content?

Test №2 empirically: ingest synthetic PII, erase the subject, then attempt
recovery via (a) retrieval queries, (b) raw store inspection, and (c)
nearest-neighbor probing around where the embedding used to live. The
[regulated-memory eval](regulated-eval-results.md) automates this class of
check; erasure is one of its five invariants.

## Try it

```python
from lians import LocalLiansClient
from datetime import datetime, timezone

with LocalLiansClient() as mem:
    mem.add(agent_id="care-team", subject_id="MRN-0042",
            content="Patient reports penicillin allergy",
            event_time=datetime(2026, 3, 1, tzinfo=timezone.utc))

    cert = mem.erase(subject_id="MRN-0042", request_ref="dsr-2026-118")
    # -> {"subject_id": "MRN-0042", "memories_erased": 1, "request_ref": "dsr-2026-118"}

    mem.audit_export(verify=True)   # hash chain still verifies, tombstone present
```

Full erasure design: [compliance.md](compliance.md) ·
[security-whitepaper.md](security-whitepaper.md) · [hipaa.md](hipaa.md)

## Related

- [Point-in-time vs validity windows: what "temporal memory" actually means](point-in-time-vs-validity-windows.md)
- [SEC 17a-4 / WORM storage for agent memory](worm-storage.md)
- [Regulated-memory eval: five invariants, six systems](regulated-eval-results.md)
