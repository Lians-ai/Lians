# Governed agent improvement plane

Lians now connects immutable agent configurations to evaluation, optimization,
runtime decisions, release authorization, deployment evidence, and production
learning. The design stops before robotics and never changes a production agent
automatically.

```text
Recorder / Decision Receipt
           |
           v
AgentVersion -> EvalCase -> EvalSuite -> repeated EvalRuns -> Comparison
                                                           |
                                                           v
                                             Evaluation Attestation
                                                |           |
                              advisory studies -+           +-> constrained routing
                                                            |
                                                            v
Gate approvals -> ReleaseCandidate -> Release Attestation -> shadow -> canary -> production
                                                                       |             |
                                                                       +-- rollback  v
                                                                              outcomes / drift
                                                                                     |
                                                                                     v
                                                                      review-only proposals
```

## Non-negotiable invariants

- `AgentVersion`, evaluation evidence, runtime decisions, attestations, release
  evidence, outcomes, and proposals are append-only and tenant/barrier scoped.
- Hashes bind canonical manifests and evidence inventories. Signed Evaluation
  and Release Attestations use Ed25519 and can be independently verified.
- A release candidate requires an `eligible_for_review` comparison with every
  protected constraint and critical invariant passing. Release signing also
  requires current, exact-action, exact-target Gate approval attestations.
- Context and tool-schema budgets use the named exact tokenizer. There is no
  character-count or estimated-token fallback.
- Cache keys bind namespace, barrier, runtime policy, agent manifest,
  permissions, release, and canonical request. Semantic caching is disabled;
  consequential or mutating requests bypass cache.
- Optimizers and production learning create recommendations only. Human or
  customer approval remains required before a new version or release exists.

## API surface

| Plane | Principal endpoints |
| --- | --- |
| Versions | `POST /v1/agents`, `POST /v1/agents/{id}/versions` |
| Evaluation | `POST /v1/eval/cases/from-decision`, `/suites`, `/runs`, `/comparisons`, `/attestations`, `/attestations/verify` |
| Optimization | `POST /v1/context/compile`, `/v1/tools/registries`, `/v1/tools/select`, `/v1/optimization/studies` |
| Runtime | `POST /v1/runtime/policies`, `/v1/routing/decide`, `/v1/cache/decide`, `/v1/runtime/concurrency/plan` |
| Release | `POST /v1/releases`, `/v1/releases/attestations`, `/v1/deployments`, `/v1/rollback` |
| Learning | `POST /v1/outcomes`, `/v1/feedback`, `/v1/drift/analyze`; `GET /v1/learning/proposals` |

Mutation endpoints in this initial contract explicitly reject an
`Idempotency-Key` instead of pretending replay safety. Business-identity unique
constraints reject duplicates. A future API version may add request-bound,
transactional replay without weakening this behavior.

## Recorder v0.2

The v0.2 envelope adds an `operational` object for provider and framework,
operation, prompt/tool/request hashes, `AgentVersion`, release reference, input,
output and cached tokens, latency, finish/error state, attributed cost, and an
outcome correlation. Every number carries its provenance. The server continues
to accept v0.1, while current Python and TypeScript builders emit v0.2.

Published contracts:

- `specs/universal-recorder/v0.2/envelope.schema.json`
- `specs/universal-recorder/v0.2/event.schema.json`
- `specs/evaluation-attestation/v0.1/schema.json`
- `specs/release-attestation/v0.1/schema.json`

## Data and deployment controls

Migration `0064_agent_improvement_plane` creates the governed records. On
PostgreSQL each table has forced namespace RLS, restrictive barrier RLS,
runtime-role `SELECT`/`INSERT` only grants, and database triggers rejecting
`UPDATE`, `DELETE`, and `TRUNCATE`. API mutations append a core audit-chain
event in the same application transaction.

The runtime cache is disabled by default. Enable it only after configuring the
existing Redis encryption boundary and validating tenant/barrier isolation.
Release evidence must progress in order: zero-traffic shadow, bounded canary,
then 100-percent production. Rollback references an earlier healthy deployment
in the same environment and stage.

## Verification

Run from the repository root:

```console
uv run pytest agentmem/tests/test_agent_improvement_plane.py -q
uv run ruff check agentmem/src/lians agentmem/sdk/python/lians agentmem/tests
uv run python .github/scripts/check_release_versions.py
uv run python .github/scripts/openapi_contract.py --surface public --check
uv run python .github/scripts/openapi_contract.py --surface admin --check
```

For the PostgreSQL-specific security and migration gate, point the test at a
fresh database and run:

```console
$env:TEST_DATABASE_URL = "postgresql+asyncpg://.../lians_improvement_test"
uv run pytest agentmem/tests/test_agent_improvement_postgres.py -q
uv run pytest agentmem/tests/test_pgvector.py -q
```

The 2026-08-08 validation migrated a fresh PostgreSQL 16 database with pgvector
0.8.6 through revision `0064`, verified all 30 improvement tables, forced RLS,
append-only triggers, runtime-role grants, barrier isolation, and the existing
11-test pgvector suite. The fresh-database run exposed and fixed a missing
composite uniqueness constraint required by PostgreSQL's candidate foreign key.

Acceptance evidence covers Recorder normalization, exact token accounting and
compression lineage, permission-aware tool selection, repeated evaluations,
protected-regression blocking, signed attestations, router overhead, advisory
recommendations, Gate-bound releases, ordered rollout, rollback, outcomes,
drift, and incident-derived evaluation cases.

## Remaining boundary

Phases 5–6 of the roadmap—ROS 2/Open-RMF mappings, edge buffering, simulation,
HIL, physical deployment, and mission-level attestations—are not implemented.
They require a named design partner, an explicit hardware/software profile, and
separate timing and safety claims.
