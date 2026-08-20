# Verifiable AI memory

Lians is the trust layer between what AI knows and what AI does.

> Remember the right things. Forget the wrong ones. Prove every important
> answer.

The category is **verifiable AI memory**. Token-efficient retrieval is an
important measured advantage, but it is not the category. Storage, vector
search, observability, and governance are ingredients. The product outcome is
AI memory that a person can inspect and control and that an organization can
reconstruct and verify.

## One contract, two scales

| Product promise | Personal AI | Organizational AI |
|---|---|---|
| Remember the right things | Preferences, projects, facts, and prior decisions persist across sessions | Governed knowledge persists across agents, applications, and model providers |
| Forget the wrong ones | A person can inspect, correct, or erase a record | Policies, retention, barriers, and crypto-shred prevent stale or prohibited use |
| Prove every important answer | A Memory Receipt explains which authorized memories shaped a response | The Decision Ledger reconstructs evidence, policy, tools, review, and point-in-time state |

The API and SDKs provide the shared control contract. A consumer application can
render that contract as a memory-control screen; an enterprise application can
connect the same records to Decision Envelopes and signed Evidence Packs.

## The product primitives

### Memory Firewall

The Memory Firewall is the product name for the controls that decide which
memories may enter or re-enter an AI context:

- admission control can accept, reject, or hold sensitive writes for review;
- temporal validity and supersession keep replaced facts out of current recall;
- record-level forgetting removes selected ciphertext, embeddings, and metadata;
- subject-level erasure crypto-shreds the subject key;
- namespace and information-barrier checks isolate tenants and groups;
- context budgets and policy exclusions keep the final prompt bounded.

The event log remains append-only. A correction creates a new version linked by
`SUPERSEDES`; it never rewrites the historical record.

### Memory Receipt

Recall and context responses now contain two complementary artifacts:

1. The canonical `lians.recall-receipt.v2` or
   `lians.context-receipt.v2` payload is privacy-minimal and
   content-addressed by `receipt_sha256`. Evidence Packs and Decision Envelopes
   bind to this hash.
2. `receipt_view` is an authorized, human-readable
   `lians.memory-receipt-view.v1` projection. It includes a plain-language
   headline, the selected memory content and source, event and validity times,
   ranking reasons, integrity and degradation status, token estimate, and - when
   context is assembled - the records excluded by the context budget or policy.

The human view is deliberately outside the canonical hash. That keeps the
verification contract stable and privacy-minimal while allowing an authorized
interface to explain and control the plaintext it already has permission to
read. `receipt_view.receipt_sha256` always points back to the canonical receipt.

For a recall call, `exclusion_scope` is `not_evaluated`: the receipt explains
the selected result set but does not claim to enumerate every candidate that
the Memory Firewall rejected. For assembled context, it is
`context_budget_and_policy` and the bounded final exclusions are included.

### Memory Controls

The control API is intentionally small:

| Method | Endpoint | Meaning |
|---|---|---|
| `GET` | `/v1/memories?agent_id=...&state=current` | List the visible current, superseded, erased, or complete memory history |
| `POST` | `/v1/memories/{id}/correct` | Append a user-confirmed replacement and close the selected live version |
| `POST` | `/v1/memories/{id}/forget` | Irreversibly clear one record after `confirm: true`, retaining an audit tombstone |
| `POST` | `/v1/erase` | Crypto-shred every memory for a subject by destroying the subject key |

Record-level forgetting and subject-level erasure are different promises.
`forget` removes the selected record's ciphertext, embedding, and metadata and
records the action in the audit chain. It intentionally retains non-content
custody fields such as the record ID, subject reference, timestamps, and audit
hash. `/v1/erase` is the stronger subject-wide crypto-shred operation.

Local Python example:

```python
from lians import LocalLiansClient

lians = LocalLiansClient()
current = lians.list_memories(agent_id="assistant-1", state="current")
original = current["items"][0]

corrected = lians.correct_memory(
    original["id"],
    "My preferred departure airport is Newark.",
)

lians.forget_memory(corrected["id"], confirm=True)
```

Hosted clients expose the same operations. The asynchronous Python client uses
`await client.list_memories(...)`, `await client.correct_memory(...)`, and
`await client.forget_memory(..., confirm=True)`. The TypeScript client provides
`listMemories`, `correctMemory`, and `forgetMemory`.

### Decision Ledger

For important organizational actions, a Decision Envelope binds memory
receipts to prompts, model and tool activity, policy decisions, outcomes, and
human review. Sealing the envelope produces a completeness grade. A signed
Evidence Pack can be verified offline. When a source, policy, model, or other
input changes, blast-radius analysis identifies the decisions that depended on
it.

This is evidence reconstruction, not a promise that a nondeterministic model
will emit identical text when replayed. See
[Decision evidence and reconstruction](decision-evidence.md) and
[completeness grades](completeness-grades.md).

## Efficiency is proof, not positioning

On the published LoCoMo token-efficiency benchmark, Lians top-50 reached 90.0%
judged accuracy with 2,656 mean context tokens, compared with 18,218 tokens for
full-conversation context: 85.4% fewer context tokens. This is evidence that
Lians can deliver less-but-better context. It is not a universal latency claim;
end-to-end latency still depends on the model, provider, network, workload, and
deployment.

See the [benchmark methodology and results](../agentmem/docs/benchmarks/locomo-token-efficiency-2026-07-10.md).

## Product decision gate

A product change belongs in Lians when it strengthens at least one of these
outcomes without weakening the others:

1. **Right memory:** improve relevance, freshness, correction, or learning.
2. **User control:** make memory easier to inspect, understand, correct, move,
   or forget.
3. **Verifiable evidence:** make an important answer or decision easier to
   reconstruct and independently verify.
4. **Useful work per token:** preserve or improve quality with less unnecessary
   context, measured by a reproducible evaluation.
5. **One-to-enterprise scale:** keep the interaction understandable for a
   person while retaining isolation, policy, security, and audit guarantees for
   an organization.

Features that only add another generic storage, orchestration, or dashboard
layer should not define the roadmap unless they materially advance this
contract.
