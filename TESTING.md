# CLAUDE.md

Project context for Claude Code. Read this fully before writing or running anything.

## What this is

A **bitemporal memory layer for financial AI agents.** It stores what an agent
learns, knows what was true *as of any past date*, reconstructs the trail behind
any decision for an audit, and honors data-erasure without breaking that trail.
The customer is regulated finance, so **correctness and auditability outrank
speed and cleverness in every tradeoff.**

Full spec: `BUILD.md`. Testing strategy: `TESTING.md`. Read both before large changes.

## The mental model (understand before editing)

Two stores, never collapsed into one:
- **Content store** (`memories`) — MUTABLE, erasable. Holds the facts; personal
  data is encrypted per-subject. Vectors live here (pgvector, 1024-dim, Voyage).
- **Audit layer** (`event_log`) — APPEND-ONLY, never edited or deleted. Holds
  references + hashes + decision metadata, never personal content. This is the
  compliance backbone.
- **Subject keys** (`subject_keys`) — per-subject encryption keys; destroying a
  key crypto-shreds that subject's content while the audit layer stays intact.

Every fact is **bitemporal**: `event_time` (when true in the world) and
`ingestion_time` (when we learned it). Facts are **superseded, never overwritten** —
the old version's validity window is closed; it is never deleted.

Four core operations: `add`, `recall`, `recall(as_of=date)`, `reconstruct`. Plus `erase`.

## Commands

```bash
# Local infra (Postgres+pgvector + Redis)
docker-compose up -d

# Install deps
pip install -e ".[dev]"          # or: pip install -r requirements.txt

# Migrations
alembic upgrade head

# Run the service
uvicorn agentmem.main:app --reload

# Tests — run these constantly
pytest                            # full suite
pytest tests/test_temporal.py -v  # bitemporal correctness (the critical one)
pytest tests/test_supersession.py # conflict-detection quality
python benchmarks/supersession_eval.py   # precision/recall, not pass/fail
```

The current prototype is `memory.py` (SQLite, toy embedding) + `prop_test_temporal.py`.
It proves the logic; production ports it onto Postgres+pgvector per `BUILD.md`.

## NON-NEGOTIABLE correctness rules

These define "correct" for this codebase. A change that violates any of them is
wrong even if all tests pass.

1. **Hunt for silent failures.** Our worst bugs don't throw — a fact silently
   dropped, a recall returning the wrong date's truth, an audit gap. Never write
   a test that only checks "did it return something." Check it returned EXACTLY
   the right thing and NOTHING it shouldn't. When you add a test, ask: "could
   this be wrong in a way that looks right?"

2. **The six invariants must always hold** (see `TESTING.md` for full statements):
   - I1 Temporal soundness — `recall(as_of=t)` returns only facts valid at `t`.
   - I2 No silent loss — every fact ever written stays retrievable from the log.
   - I3 Audit immutability — `event_log` is never edited/deleted; erasure adds a row.
   - I4 Erasure + audit survival — after erase, content unreadable AND audit proves it existed.
   - I5 Present-time validity preference — current facts outrank superseded ones.
   - I6 Tenant isolation — no query crosses `namespace` boundaries.
   Prefer property-based tests (Hypothesis) for these, not hand-picked cases.

3. **In finance, when unsure, FLAG — never silently drop.** If supersession
   confidence is low, store both facts and flag for review. Silently dropping a
   true fact is the exact failure we sell against. Bias: a false keep is bad; a
   false drop is unacceptable.

4. **Never put personal data in the audit layer.** `event_log` and `metadata`
   hold non-personal references/tags only. Personal data goes in encrypted
   `content_encrypted`, tied to a `subject_id`, so erasure can reach it.

5. **Don't claim supersession works without measuring it.** It's graded by
   precision/recall on a labeled set, not asserted. Precision floor matters most.

## Conventions

- Python, async FastAPI. Type-hint everything. Pydantic for schemas.
- Embeddings go through the `embeddings.py` interface — never call a provider
  directly elsewhere, so we can swap Voyage/OpenAI/local freely.
- The vector dimension (1024) is load-bearing in the DB. Do not change it without
  a migration plan that re-embeds existing rows.
- Use `iterparse`/streaming for large files; assume real data is large.

## Gotchas / what NOT to do

- Don't build the dashboard, hosted backend, or exotic vector DBs early. Order is
  core engine → supersession depth → SDK → hosted/erasure → dashboard. pgvector
  until it genuinely hurts.
- Don't make `add()` eventually-consistent (async, read-after-write delay) to
  chase throughput — a window where a written fact isn't recallable is a finance
  correctness hole. Keep writes synchronous unless reads are provably unaffected.
- Don't let an LLM silently decide what's "worth remembering." Store faithfully.
- The schema is NOT the moat — don't over-engineer it. The moat is correctness,
  audit trust, and finance-tuned supersession. Spend effort there.
- Compliance behavior is architectural capability only; never hardcode a legal
  policy claim — those are set by counsel.

## When you finish a task

Run the full test suite. If you touched the core, run `test_temporal.py` and the
property tests specifically. Report which invariants your change touches and how
you verified they still hold.
