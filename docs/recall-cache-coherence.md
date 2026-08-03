# Recall cache coherence and failure semantics

Lians uses recall caches only when it can prove cross-replica coherence. The
supported cached deployment is PostgreSQL plus Redis with
`RECALL_CACHE_ENABLED=true`. SQLite and other database backends bypass both the
Redis recall-result cache and the process-local working-set cache.

For each `(namespace, agent_id)`, Redis stores a random generation token.
Cached result keys and process-local working sets carry that generation. Recall
holds a shared PostgreSQL transaction advisory lock while it reads the
generation, obtains or computes the result, and commits the recall audit event.
Memory mutations hold the matching exclusive lock, replace the Redis generation
before the database commit, and then commit. This gives a clear ordering: a
recall completes before the mutation, or it observes the mutation's new
generation. An older process-local entry can never validate against the new
generation. Random tokens prevent unsafe generation reuse if Redis evicts a
generation key while older TTL-bound result keys still exist.

Old Redis result generations are not synchronously scanned or deleted. Their
configured `RECALL_CACHE_TTL_SECONDS` expiry reclaims them. The generation key
is intentionally retained and is small. Process-local entries honor
`SESSION_CACHE_TTL_SECONDS` and `SESSION_CACHE_MAX_ENTRIES`; scoring packs have
the same generation and lifetime as their working set.

Failure behavior is correctness-first:

- A Redis generation read failure is logged and that recall bypasses all caches.
- A Redis result read or write failure is logged and degrades to an uncached
  result.
- A generation increment failure is logged and aborts the associated durable
  memory mutation before commit. Operators should expect a failed write, not a
  successful write that could leave another replica's cached result valid.
- Every successful Redis, keyed, and semantic recall awaits an audit-chain append
  and database commit before returning. Audit failure therefore fails recall.
- Stripe meter submission remains the existing best-effort, process-local queue.
  It is invoked consistently after the recall audit commits, but it is not a
  durable billing-delivery guarantee and may be lost on queue overflow or process
  failure.

Disabling `RECALL_CACHE_ENABLED` removes the Redis dependency from memory writes
and makes all recalls read durable state directly. This is the recommended
fail-safe setting during Redis maintenance.
