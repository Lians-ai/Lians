# Lians Context Receipt

## Purpose

A Lians Context Receipt is a content-addressed record of the project context
selected for one AI task. It lets a person or system answer:

- What query was evaluated?
- What retrieval policy and time boundary were used?
- Which current records entered the context?
- Which records were excluded by the final budget or policy?
- Were unresolved conflicts present?
- What exact context was produced?

The receipt is evidence about context selection. It does not prove that a model's
answer is correct.

## Implemented canonical contract

The current implementation emits `lians.context-receipt.v2`. Its canonical JSON
contains:

| Field | Meaning |
|---|---|
| `schema` | Receipt schema identifier |
| `query_sha256` | Hash of the query; the plaintext query is not embedded |
| `as_of` | Optional requested point-in-time boundary |
| `reference_time` | Time pinned for the retrieval operation |
| `retrieval_receipt_sha256` | Hash of the underlying recall receipt |
| `retrieval_policy` | Bounded retrieval configuration |
| `retrieval_degraded` | Whether retrieval completed in a degraded mode |
| `ranking_policy` | Policy used to order the final context |
| `results` | IDs, content hashes, event times, sources, scores, and ranking evidence |
| `excluded` | Final context items excluded by budget or policy |
| `open_conflicts` | IDs and content hashes for unresolved conflicts |
| `budget` | Context budget and usage evidence |
| `context_sha256` | Hash of the exact assembled context |

Canonical JSON is encoded with sorted keys and compact separators before
SHA-256 hashing. The resulting `receipt_sha256` is the portable identifier.

The authorized `lians.memory-receipt-view.v1` is a human-readable projection.
It may show plaintext that the caller is already allowed to read, but it points
back to the privacy-minimal canonical receipt hash and is not itself the stable
verification contract.

## Verification

Given the canonical receipt JSON:

```python
import hashlib
import json

canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
assert hashlib.sha256(canonical.encode()).hexdigest() == receipt_sha256
```

Verification proves that the receipt payload has not changed since its hash was
created. Higher-assurance deployments can bind that hash into signed Evidence
Packs or Decision Envelopes.

## Privacy-safe share card

`lians share-card` is an explicit local command. It produces a copyable Markdown
card only after at least one connected client has successfully reused saved
context. The card contains no prompts, memory text, paths, project names,
credentials, receipt contents, source hashes, or installation identifier.

Example:

```text
Lians continuity verified
Fresh-task context reuse observed locally · 2 connected AI apps
No project or memory content is included in this card.
```

This is a local product-state statement, not a centrally verified certification
and not proof of cross-tool reuse unless the underlying client boundary has been
measured separately.

## Related contracts

- [Verifiable AI memory](verifiable-memory.md)
- [Decision evidence and reconstruction](decision-evidence.md)
- Implementation: `agentmem/src/lians/memory_service.py`
- Contract tests: `agentmem/tests/test_experiences.py`
