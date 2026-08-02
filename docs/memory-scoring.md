# Deterministic memory scoring

Lians evaluates memory quality locally with deterministic rules in
`agentmem/src/lians/scoring.py`. The scorer makes no network, LLM, or embedding
calls. Existing retrieval may still use the configured embedding provider to
find candidates; scoring explains and adjusts that existing retrieval signal.

## Components

Every component and the final score is bounded to `0.0–1.0`:

- `importance_score`: caller importance plus durable fact, decision, metric,
  date, constraint, and evidence signals.
- `confidence_score`: content specificity and the presence of event time,
  source, and structured metadata.
- `trust_score`: an explicit source/trust mapping (`system_verified` 1.0,
  `trusted_source` 0.9, `user_provided` 0.75, `chat` 0.65, `imported` 0.6,
  unknown 0.5, and `untrusted` 0.25). Privileged levels require a
  server-verified provenance input. Caller metadata trust claims are ignored,
  and public `source` values cannot self-assert `system_verified` or
  `trusted_source`.
- `freshness_score`: validity at an explicit reference time plus deterministic
  age decay. Historical recall uses `as_of` as the reference.
- `relevance_score`: lexical overlap combined with the existing bounded
  retrieval score when ranking recall candidates.
- `stability_score`: durable and structured facts score above greetings,
  thanks, and very short conversational text.
- `safety_score`: safe 1.0, review-needed 0.5, unsafe/quarantined 0.0.

Admission quality weights are importance 0.25, stability 0.20, confidence 0.20,
trust 0.15, safety 0.15, and freshness 0.05. Recall quality weights are
relevance 0.35, confidence 0.15, importance 0.15, trust 0.10, freshness 0.10,
stability 0.10, and safety 0.05. The response includes these weights and the
reasons used for every component.

## Admission and recall

Admission stores its breakdown under reserved metadata key `_score`. Held
candidates retain that explanation in the review queue. Approval keeps the
existing admission provenance and allows the recall scorer to reassess safety
as approved. Admission and reserved-metadata normalization run at the storage
boundary, so HTTP, embedded `LocalLiansClient`, local MCP, feedback corrections,
and other service callers cannot bypass them. Caller-provided `_admission` or
`_score` values are always replaced. A previously reviewed sealed admission or
admin-approved reflection uses an explicit internal trusted-review decision;
even then the content is rescored and injection-like text remains ineligible.

Safety is a gate, not a small penalty. Rejected, quarantined, injection-like,
blocked-source, or still-pending content receives a final score of zero and is
not returned by normal recall. Existing admission enforcement still decides
whether a write is rejected or held.

Recall candidate generation and bitemporal filtering are unchanged. Current
recall still reads `live_facts`; historical recall still selects only memory
versions valid at `as_of`. To protect measured retrieval quality, final recall
ranking blends the existing retrieval score at 0.8 with the seven-component
quality score at 0.2. These blend weights are returned as `ranking_weights`.
Ties use final score descending, event time descending, ingestion time
descending, and memory ID ascending.

Adaptive fusion retains the component breakdown associated with the strongest
facet input and reports that facet under `fusion.strongest_scope`. Later
order-producing stages (cross-encoder, MMR, or graph proximity) append a
`ranking_stages` record containing their raw objective/provenance, input score,
returned position, and output score. Because those objectives are not mutually
calibrated, `rank-calibration-v1` assigns non-overlapping position buckets while
retaining the prior score inside each bucket. Consequently the returned order,
the public `score`, and `score_breakdown.final_score` stay synchronized after
every enabled reranker.

Fast-recall cache keys carry a scoring schema version and the generation
captured before retrieval starts. A concurrent write or erasure makes an
in-flight fill unreachable rather than allowing it to republish stale recall.
Every recall-affecting supersession decision advances that generation. Privacy
erasure, retention pruning, and supersession rejection also commit a durable
invalidation job in the same database transaction as the mutation. Every
worker checks that database barrier before trusting a Redis hit and revalidates
the generation afterward. If Redis is unavailable, stale cache is bypassed
cross-worker, the API fails closed, the durable worker retries, and an explicit
request retry repairs the same operation even after the underlying data change
has already committed.

API memory objects may include optional `score_breakdown`; existing required
fields are unchanged. This is a rule-based quality and governance layer, not a
production ML ranker. It does not add semantic models, embeddings, or pgvector
features.
