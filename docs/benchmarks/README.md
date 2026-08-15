# Lians benchmark evidence

Lians separates fast offline engineering gates from full public benchmark runs.
That distinction keeps product regressions cheap to catch without presenting a
local functional test as a production or cross-vendor result.

## Offline evidence suite

Run the deterministic, no-network gate from the repository root:

```bash
cd agentmem
python -m benchmarks.evidence_suite
```

The suite removes paid-provider keys from each child process, forces the local
test embedding provider, and writes a receipt to
`agentmem/results/evidence-suite-latest.json`. The receipt contains the commit,
working-tree state, exact commands, durations, parsed metrics, and hashes of raw
output.

The five gates cover:

1. Supersession relation classification
2. Point-in-time recall
3. Five regulated-memory invariants
4. RIAD-1 decision reconstruction and tamper evidence
5. Typed compilation, explicit serving modes, temporal recall, and
   content-addressed recall receipts

These are local functional checks. They are not production load tests and they
are not directly comparable to LLM-judged answer-accuracy leaderboards.

## Public retrieval and answer benchmarks

- [Cross-agent memory evidence, August 14, 2026](cross-agent-memory-2026-08-14.md)
  records a live Cursor-to-Claude handoff, confirmed deletion, a balanced Cursor
  native-rule comparison, and the exact platform blockers observed during the
  same test window.
- [LOCOMO reports](../../agentmem/docs/benchmarks/) preserve the exact protocol,
  embedding configuration, scoring method, and limitations for each published
  run.
- [RIAD-1](riad-1.md) is the open decision-reconstruction benchmark.
- LongMemEval-V2 remains unpublished until the official end-to-end protocol
  completes and its paid evaluator spend is independently verified.

Historical reports remain available for auditability. New headline claims
should point to a machine-readable receipt and name whether the result was
executed, capability-assessed, judge-free, or LLM-judged.

## Production load gate

Run the concurrent HTTP harness against a Postgres deployment. The key is read
only from a file and is never printed:

```bash
python -m benchmarks.http_load_eval \
  --base-url https://memory.example.com \
  --api-key-file ./secrets/load-test.key \
  --agent-id load-gate \
  --mode fast \
  --requests 10000 \
  --concurrency 100 \
  --out results/postgres-load.json
```

The report gates p95 latency, success rate, deadline misses, provenance
coverage, receipt coverage, status codes, and throughput. Default p95 limits
match the serving-mode budgets.

Convert a passed artifact into a hash-bound release record:

```bash
python -m benchmarks.attest_evidence \
  --name postgres_load_test \
  --artifact results/postgres-load.json \
  --methodology "Postgres 16 and pgvector, 10k requests, concurrency 100" \
  --out results/external-evidence.json
```

External booleans cannot unlock a claim. Every production artifact needs a
SHA-256 digest, timestamp, and methodology. Competitive artifacts also require
an independent party and an HTTPS source.

Probe database-enforced isolation with two keys from different namespaces:

```bash
python -m benchmarks.tenant_isolation_eval \
  --base-url https://memory.example.com \
  --key-a-file ./secrets/tenant-a.key \
  --key-b-file ./secrets/tenant-b.key \
  --out results/tenant-isolation.json
```

The probe writes a unique sentinel in each namespace, requires each tenant to
recall its own sentinel, and requires zero cross-tenant retrievals.

## Claim levels

`benchmarks/release_gate_policy.json` defines three levels:

1. `foundation_verified`: all deterministic local gates pass.
2. `production_validated`: foundation plus Postgres load, multi-tenant
   isolation, backup restore, and failure injection evidence.
3. `competitive_leader`: production validation plus official Agent Memory
   Benchmark and LongMemEval-V2 LAFS submissions with independent reproduction.

Check the currently permitted language:

```bash
python -m benchmarks.release_claims \
  --external-evidence results/external-evidence.json
```

The phrase "best memory engine" is not permitted unless the
`competitive_leader` gate passes.
