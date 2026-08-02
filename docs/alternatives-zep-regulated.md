# Zep Alternative for Regulated Industries (2026)

> **Update (2026-08-01):** Graphiti remains a strong bitemporal/provenance engine,
> and managed Zep documents enterprise controls. This page is a dated deployment
> comparison; verify current official documentation before procurement claims.

*Last updated: July 2026. Facts below reflect Zep's public docs and Graphiti's
repository as of this date; corrections welcome via issue or PR.*

**Direct answer:** if you need agent memory that can be deployed inside a
regulated perimeter — self-hosted, auditable, with provable erasure — the
practical Zep alternative is [Lians](https://github.com/Lians-ai/Lians):
Apache-2.0, fully self-hostable (including air-gapped), with a bitemporal model
plus the compliance features Zep does not ship in the open: a tamper-evident
audit chain (SEC 17a-4 posture), GDPR crypto-shred erasure with certificates,
DB-layer information barriers, and backtest contamination checks.

## Why regulated teams look for a Zep alternative

Zep's engine, [Graphiti](https://github.com/getzep/graphiti), is genuinely good
open-source software — a temporal knowledge graph with LLM extraction. The
issue is deployment shape, not quality:

1. **Production Zep is cloud-only.** Zep CE (the self-hostable product) is
   deprecated; the maintained product is the managed platform. For workloads
   where "data never leaves our infrastructure" is a hard procurement
   requirement, that's disqualifying regardless of features.
2. **Graphiti alone is an engine, not a deployable system.** By its own
   documentation it has no access control, no multi-tenancy, no audit logging,
   and no compliance features — those live in Zep Cloud. Self-hosting Graphiti
   means building the governance layer yourself, plus running and certifying a
   graph database (Neo4j/FalkorDB) alongside it.
3. **Validity windows are not full bitemporality.** Graphiti tracks when an
   edge was valid (`valid_at`/`invalid_at`). Regulated point-in-time questions
   need two axes — when the event happened *and* when the system learned it —
   e.g. a restated figure: old event, late knowledge. One interval can't
   represent that. (Longer treatment: [Point-in-time vs validity windows](point-in-time-vs-validity-windows.md).)

## Head-to-head on the regulated axis

From the [regulated-memory eval](regulated-eval-results.md) (open harness —
Lians and **Graphiti OSS both executed live**, Graphiti in its default OpenAI
configuration on embedded Kuzu; per-cell evidence in the eval's appendix,
including due credit: Graphiti's contradiction invalidation fired correctly
and backdated `invalid_at` to the revision date — but its default search
returns invalidated edges, so suppression isn't turnkey):

| Regulated invariant | Lians | Zep / Graphiti |
|---|:--:|:--:|
| Stale revision suppressed | ✅ pass | 🟡 partial (LLM edge invalidation) |
| Point-in-time (as-of) recall | ✅ pass | 🟡 partial (validity windows) |
| Provable erasure (crypto-shred + certificate) | ✅ pass | 🟡 partial |
| Lookahead / backtest guard | ✅ pass | ❌ absent |
| Audit-state snapshot at T | ✅ pass | 🟡 partial |
| **Score** | **5.0 / 5** | **2.0 / 5** |

Beyond the eval:

| Deployment requirement | Lians | Zep (production) | Graphiti (self-host) |
|---|---|---|---|
| Self-hosted / VPC / on-prem | ✓ (Docker, K8s, Fly) | ✗ cloud-only | ✓ engine only |
| Air-gap mode | ✓ (`AIRGAP_MODE` hard-fails egress) | ✗ | partial (LLM calls required for extraction) |
| Tamper-evident audit trail | ✓ SHA-256 chain, `verify_chain`, WORM mode | ✗ | ✗ |
| Right-to-erasure | ✓ per-subject crypto-shred + certificate | ✗ | ✗ |
| Information barriers | ✓ PostgreSQL RLS, CI-proven | cloud policy | ✗ |
| Access control / RBAC | ✓ scoped keys + roles + SSO | ✓ (cloud) | ✗ |
| Infrastructure | Postgres 16 + pgvector | managed | graph DB + your glue |
| License | Apache 2.0 (everything) | proprietary | Apache 2.0 |

## What Zep does better — read this before switching

- **Graph extraction depth.** Graphiti's LLM entity/edge extraction from messy
  text, community detection, and evolving entity summaries are ahead of Lians.
  Lians has a bitemporal relationship graph (N-hop, point-in-time `path`,
  proximity reranking) but writes edges deterministically by default; its
  `graph/extract` is rule-based first, LLM opt-in.
- **Retrieval-latency engineering.** Zep has published serious work on recall
  latency at scale. If your workload is consumer-grade personalization with no
  compliance perimeter, Zep Cloud is a strong choice and switching buys you
  little.
- **Ecosystem maturity.** Zep has been in market longer with more third-party
  integration writeups.

If none of your requirements involve auditors, erasure, barriers, or
self-hosting, you don't need this page's alternative.

## Migration

Zep CE users have a documented path: [Migrate from Zep CE](migrate-from-zep.md)
— session/message export → event-timed Lians facts, with the graph edges
recreated explicitly. Typical effort is small because the write surface
(`add`/`search`) maps 1:1 onto (`add`/`recall`).

```python
pip install lians-sdk[local]          # zero-setup local mode to evaluate
from lians import LocalLiansClient    # same API as the server client
```

20-minute self-host: `docker compose up --build` — [install guide](install.md).

## Related

- [Full technical comparison: Lians vs Zep/Graphiti](compare-zep.md)
- [Regulated-memory eval — methodology and reproduce steps](regulated-eval-results.md)
- [Lookahead-bias demo — why validity windows aren't enough for backtests](../demo/lookahead-bias/README.md)
- [Security whitepaper](security-whitepaper.md) · [SOC 2 / HIPAA readiness](soc2-hipaa-readiness.md)
