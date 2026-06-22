# TESTING.md — AgentMem Correctness Invariants

This document is for customers, compliance officers, and design partners who need
to verify that AgentMem's correctness guarantees hold on their deployment. It lists
the six named invariants, the tests that cover them, and how to reproduce the
verification on a fresh install.

---

## The six named invariants

These are the properties that define "correct" for AgentMem. A build that violates
any of them is wrong even if all tests pass. They are stable across versions; any
change that weakens them is a breaking change.

### I1 — Temporal soundness

> `recall(as_of=t)` returns exactly the facts that were valid at time `t` — no more,
> no less.

A fact is valid at `t` if `valid_from ≤ t` and (`valid_to IS NULL` or `valid_to > t`).
Superseded facts (with a non-null `valid_to`) must not appear in a query bounded to
a time before their supersession.

**Test coverage:** `tests/test_temporal.py` — property-based (Hypothesis); ~80 cases.
**Manual check:** see Benchmark 2 in `demo/index.html`.

---

### I2 — No silent loss

> Every fact ever written is permanently retrievable from the audit log, even after
> it is superseded or the subject's data is erased.

Supersession closes a fact's validity window; it does not delete it. Erasure
destroys the encrypted content but preserves the audit row. A compliance
reconstruction query (`GET /v1/audit/reconstruct`) can always prove a fact existed.

**Test coverage:** `tests/test_audit.py`, `tests/test_erasure.py`.
**Manual check:** write a fact, supersede it, then call `GET /v1/audit/reconstruct`
with an `as_of` before the supersession — the original fact appears.

---

### I3 — Audit immutability

> The `event_log` table is append-only. No row is ever updated or deleted, including
> during erasure operations or admin corrections.

Every write to `memories` generates a corresponding row in `event_log` with a
SHA-256 hash of the content. The hash of each row includes the hash of the previous
row (hash chain). `GET /v1/admin/audit/verify` checks the full chain and reports
any gap.

**Test coverage:** `tests/test_audit_chain.py`.
**Manual check:**

```bash
curl -H "X-API-Key: $KEY" http://localhost:8000/v1/admin/audit/verify
# → {"status": "ok", "rows_checked": N, "violations": []}
```

---

### I4 — Erasure with audit survival

> After `POST /v1/erase` for a subject, that subject's content is cryptographically
> unrecoverable AND the audit trail proves the content existed and was erased.

Erasure destroys the per-subject encryption key (crypto-shred). The ciphertext
remains in the database but is unreadable without the key. The `event_log` records
the erasure event with a certificate. `GET /v1/erase/{subject_id}/certificate`
returns a signed record of what was erased and when.

**Test coverage:** `tests/test_erasure.py`.
**Manual check:**

```bash
# Erase subject
curl -X POST -H "X-API-Key: $KEY" http://localhost:8000/v1/erase \
  -d '{"subject_id": "user-123", "namespace": "default"}'

# Verify certificate
curl -H "X-API-Key: $KEY" \
  http://localhost:8000/v1/erase/user-123/certificate?namespace=default
# → {"certificate_id": "...", "erased_at": "...", "chain_status": "ok", ...}
```

---

### I5 — Present-time validity preference

> When no `as_of` is specified, `recall` returns only currently-valid facts
> (where `valid_to IS NULL`). Superseded facts do not appear.

This is enforced at the database layer, not the prompt layer. The `valid_to IS NULL`
filter is part of the base query in `recall_memories`; it is not applied in
post-processing.

**Test coverage:** `tests/test_supersession_benchmark.py` (stale-fact contamination
test); `tests/test_recall_quality.py`.
**Benchmark reproduction:**

```bash
cd Ai_Mem_Soft
EMBEDDING_PROVIDER=local python agentmem/scripts/run_benchmark.py
# Benchmark 1 should show: "stale facts in top-5: 0 / 4"
```

---

### I6 — Tenant and barrier isolation

> No query crosses `namespace` boundaries. Within a namespace, agents assigned to
> a `barrier_group` cannot see memories from a different `barrier_group`.

Namespace isolation is enforced by a hard `WHERE namespace = ?` in every query.
Barrier enforcement is applied in `recall`, `get_knowledge_snapshot`, and the
MCP tools. An agent with `barrier_group = "equity"` cannot retrieve memories
tagged `barrier_group = "credit"`.

**Test coverage:** `tests/test_barrier.py`, `tests/test_concurrency.py` (concurrent
multi-namespace writes).
**Manual check:**

```bash
# Provision two API keys in the same namespace but different barrier groups
# Write a memory as barrier_group "A"
# Recall as barrier_group "B" — the memory must not appear
```

---

## Running the full verification

### Prerequisites

```bash
# Postgres + pgvector (for the full suite)
docker compose up -d

# Install
cd agentmem
pip install -e ".[dev]"
alembic upgrade head
```

### Full test suite

```bash
pytest -v
# Expected: 557 passed, 22 skipped (without pgvector TEST_DATABASE_URL)
# Expected: 565 passed, 30 skipped (with TEST_DATABASE_URL set to live Postgres)
```

### Invariant-targeted runs

```bash
# I1 — Temporal soundness
pytest tests/test_temporal.py -v

# I2 + I3 + I4 — Audit and erasure
pytest tests/test_audit.py tests/test_audit_chain.py tests/test_erasure.py -v

# I5 — Present-time validity
pytest tests/test_supersession_benchmark.py tests/test_recall_quality.py -v

# I6 — Isolation
pytest tests/test_barrier.py tests/test_concurrency.py -v
```

### Benchmark reproduction (no Postgres required)

```bash
cd Ai_Mem_Soft
EMBEDDING_PROVIDER=local python agentmem/scripts/run_benchmark.py
```

Expected output:

```
Benchmark 1 — Stale-fact contamination (5-revision NVDA chain)
✓ AgentMem — stale facts in top-5: 0 / 4
  Pure-cosine (mem0-style) — stale facts visible: 4 / 4

Benchmark 2 — Supersession classification (12-pair labeled set)
✓ Accuracy: 12/12 (100%)

Benchmark 3 — Point-in-time recall (4 quarterly queries)
✓ Correct: 4/4
  mem0 score (no as_of support): 0/4

Benchmark 4 — Compliance auditability (SEC 17a-4 hash chain)
✓ Hash chain: ok (1 rows, 0 violations)
```

### Interactive demo (requires Docker)

```bash
cd agentmem
docker compose up -d
pip install httpx
python scripts/seed_demo.py   # prints a read-only API key
# Open demo/index.html, paste the key, click through all 4 benchmarks
```

---

## What the invariants do NOT guarantee

- **Supersession accuracy on arbitrary data.** I5 guarantees that the engine applies
  its supersession decision atomically; it does not guarantee the decision is correct
  for every possible pair of facts. Accuracy is benchmarked separately (100% on the
  labeled set; see `BENCHMARK.md`).
- **Embedding quality.** Recall relevance depends on the embedding model. The
  invariants cover correctness, not ranking quality.
- **Performance.** The invariants are correctness properties. Latency and throughput
  are benchmarked separately; they are not invariants.
