<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/images/logo.png" width="340" alt="Lians logo">
  </a>
</p>

# Lians

**Provider-neutral decision evidence and runtime control for AI agents.** Record verifiable decision boundaries, enforce protected actions, reconstruct what an agent knew at a past time, and retain governed bitemporal memory.

## Install

```bash
pip install lians-sdk
pip install lians-sdk[local]         # SQLite plus real local semantic embeddings
pip install lians-sdk[mcp]           # Local MCP server
pip install lians-sdk[langchain]     # LangChain
pip install lians-sdk[langgraph]     # LangGraph
pip install lians-sdk[crewai]        # CrewAI
pip install lians-sdk[openai-agents] # OpenAI Agents SDK
pip install lians-sdk[autogen]       # AutoGen v0.4
pip install lians-sdk[anthropic]     # Anthropic API middleware + webhook converter
pip install lians-sdk[google-adk]    # Google ADK BasePlugin
pip install lians-sdk[all]           # Everything
```

## Quickstart

```python
from datetime import datetime, timezone
from lians import LocalLiansClient

mem = LocalLiansClient()  # No server, Docker, or API key

mem.add(
    agent_id="analyst-1",
    content="NVDA FY2026 revenue guidance raised to $40B",
    event_time=datetime(2025, 11, 19, 16, tzinfo=timezone.utc),
    metadata={"ticker": "NVDA", "metric": "revenue_guidance"},
    importance=0.9,
)

# Superseded facts are excluded before they reach the model
current = mem.recall(agent_id="analyst-1", query="NVDA revenue guidance")

# Reconstruct what was known on a past date
past = mem.recall_at(
    agent_id="analyst-1",
    query="NVDA revenue guidance",
    as_of=datetime(2025, 3, 1, tzinfo=timezone.utc),
)
```

## Universal Recorder and runtime Gate

The HTTP clients ingest native Lians, OTLP GenAI, MCP JSON-RPC, and A2A events
through one typed envelope. Builders use hash-only capture and stable retry
identities by default.

```python
from lians import LiansClient, lians_event

event = lians_event(
    "decision.completed",
    {"model_id": "review-v1", "input": {"synthetic": True}, "output": "review"},
    run_id="synthetic-run-1",
    idempotency_key="synthetic-run-1:decision.completed",
)
with LiansClient(base_url="http://localhost:8000", api_key="...") as client:
    capture = client.ingest_recorder_event(event)
    page = client.recorder_run_events_page(
        capture["event"]["run_id"], limit=500
    )
    identity = client.whoami()
```

`recorder_run_events_page` validates the server's exact-total and completeness
headers and returns the paired `before_recorded_at`/`before_id` continuation.
The older `recorder_run_events` helper intentionally returns only one legacy
array page.

When a decision has more than 500 prior Recorder events, use
`recorder_evidence_index_job_for_decision` (or the job-ID lookup) to inspect its
fixed-snapshot progress. After repairing a stable terminal error,
`retry_recorder_evidence_index_job` performs the authenticated explicit retry.

See the [under-15-minute synthetic quickstart](../../../docs/quickstart-recorder.md)
for mixed-protocol batch capture, Gate policy evaluation, owned remediation,
attested closure, and Investigator queue/report reads.

Investigator report v1.1 exposes independent evidence, timeline, control-history,
case, task, and closure limits. Always inspect `report["coverage"]["complete"]`
and each collection window before treating an embedded packet as complete. A
capped review/approval prefix reports partial integrity rather than valid history.

For native runtime capture, use the bounded async sink with public framework
hooks. Inputs and outputs are hashed locally by default, before buffering or
HTTP transport:

```python
import os

from lians import AsyncRecorderSink, RecorderAttribution
from lians import build_langchain_recorder_handler

async with AsyncRecorderSink(
    async_client,
    commitment_key=os.environ.get("LIANS_RECORDER_COMMITMENT_KEY"),
) as recorder:
    callback = build_langchain_recorder_handler(
        recorder,
        attribution=RecorderAttribution(claimed_agent_id="reviewer"),
    )
    result = await graph.ainvoke(state, config={"callbacks": [callback]})
    await recorder.flush()
```

`max_buffered_events` is a hard total bound across scheduled cross-thread
callbacks, queued events, and in-flight delivery. Envelopes are bounded and
JSON/schema-validated individually before admission, preventing one malformed
event from poisoning a non-atomic batch. The default unkeyed SHA-256 commitment
does not hide guessable low-entropy values; set a deployment secret of at least
32 bytes as `commitment_key` to use HMAC-SHA-256.

Anthropic API middleware and verified Managed Agents webhook conversion, Google
ADK plugins, OpenAI Agents tracing processors, and CrewAI event listeners are
also supported through optional extras. See [native Recorder hooks](../../../docs/recorder-native-hooks.md)
for exact callback coverage, API-vs-tool boundaries, plugin propagation limits,
confirmed flush semantics, name privacy, bounded-buffer/failure contracts,
attribution, and explicit gaps (including streaming and hidden reasoning).

### Bounded HTTP retries

Both HTTP clients retry only operations whose method or explicit endpoint
contract makes replay safe. Reads and transactionally idempotent writes use at
most two retries by default; capability issuance, permit redemption, and other
ambiguous mutations are never retried automatically. The retryable status set is
limited to `408`, `425`, `429`, `500`, `502`, `503`, and `504`, plus transport
failures on replay-safe operations.

`Retry-After` delta-seconds and HTTP dates are honored as a minimum delay. If a
server asks for a delay above `max_retry_delay`, the client returns the response
error instead of sleeping beyond its configured bound. Configure the same
bounded policy on synchronous or asynchronous clients:

```python
client = LiansClient(
    base_url="https://lians.example",
    api_key="...",
    timeout=30,
    max_retries=2,
    backoff_factor=0.5,
    max_retry_delay=30,
)
```

After a timeout or connection reset on a non-retryable mutation, reconcile the
authoritative resource before issuing a new request. See the
[mutation retry and concurrency contract](../../../docs/mutation-retry-concurrency.md).

Mutable control resources use exact optimistic preconditions from the preceding
read. Never synthesize or truncate these timestamps, and preserve an observed
`None` relationship value:

```python
review_page = client.review_supersessions()
item = review_page["items"][0]
client.confirm_supersession(
    item["memory_id"],
    expected_superseded_by=item["superseded_by"],
)

case = client.investigation_case(case_id)
task = client.create_remediation_task(
    case_id,
    {
        "expected_case_updated_at": case["updated_at"],
        "title": "Re-evaluate affected decisions",
    },
)
client.update_remediation_task(
    task["id"],
    {"expected_updated_at": task["updated_at"], "status": "in_progress"},
)
```

Supersession review returns an exact `total` and explicit `complete`,
`has_more`, and `next_chain_position` fields. Continue with
`before_chain_position=next_chain_position`; do not treat one bounded page as
the full unresolved queue.

Conflict review uses the same explicit contract with a paired
`after_detected_at`/`after_id` cursor. Supply both values from the preceding
page whenever `has_more` is true.

Responses containing generated credentials, webhook secrets, Gate permits,
approval or closure statements are non-cacheable. Mutations that issue a
one-time secret/capability or append a statement are never retried by the SDK;
move returned secrets directly into a secret manager and do not log the
response. After an ambiguous mutation outcome, reconcile through the
corresponding read/list endpoint. Explicit statement-bearing GETs remain
read-only and may use the bounded GET retry policy, but their bodies must not be
cached or logged. Reading a decrypted closure statement is admin-only:

```python
attestation = client.closure_attestation(
    "case",
    case_id,
    include_statement=True,
)
```

An allow evaluation returns one opaque `execution_permit`. Configure policies
with the canonical `whoami()` principal of a separate broker/sidecar, then have
that mediator call `consume_gate_execution_permit()` with the actual action,
target, decision, and canonical request digest immediately before dispatch. The
SDK example consumes only when `LIANS_MEDIATOR_API_KEY` supplies a separate
credential; otherwise it uses an unredeemable placeholder. It never prints or
falsely consumes the token. Capability issuance and redemption deliberately
disable the client's automatic transport/5xx retries because a response can be
lost after the server commits; request a new evaluation after an ambiguous
outcome. See
[Gate execution permits](../../../docs/gate-execution-permits.md).

## Complete list traversal

The legacy decision, ledger-event, and evidence-artifact endpoints retain JSON
array bodies. Use `decisions_page`, `record_events_page`, and
`evidence_artifacts_page` when you need exact totals and safe traversal. Each
method returns `items`, exact `total`, `returned`, `has_more`,
`page_complete`, strict `collection_complete`, and a paired `next_cursor`.
The SDK rejects missing or inconsistent pagination headers instead of inferring
completeness from the number of items.

## Decision dependency impact

Use the fast assessment for an immediate ranked answer. Its typed response
reports whether legacy fallback was used, how much coverage was scanned, and
whether `total` is a lower bound. For a resumable, snapshot-bounded assessment,
start a durable job and advance it in bounded batches:

```python
with LiansClient(base_url="https://lians.example", api_key="...") as client:
    fast = client.assess_decision_impact(
        "policy",
        "credit-policy-17",
        change_type="retired",
    )
    if fast["total_is_lower_bound"]:
        print(fast["analysis_mode"], fast["legacy_candidates_scanned"])

    job = client.start_exhaustive_impact_assessment(
        idempotency_key="policy-17-retired-v1",
        dependency_kind="policy",
        dependency_value="credit-policy-17",
        change_type="retired",
    )
    while job["status"] in {"pending", "running"}:
        job = client.advance_exhaustive_impact_assessment(
            job["id"], page_size=250, max_pages=10
        )

    after = 0
    while True:
        page = client.list_exhaustive_impact_assessment_results(
            job["id"], after=after, limit=200
        )
        for match in page["items"]:
            handle_affected_decision(match)
        if page["next_cursor"] is None:
            break
        after = page["next_cursor"]
```

`get_exhaustive_impact_assessment()` can read progress from another worker or
after a restart. Async clients expose the same four methods with `await`.

## Expiring workload credentials

A human OIDC tenant administrator can issue, list, rotate, and revoke bounded
workload credentials without the cross-tenant break-glass secret:

```python
from lians import LiansClient

with LiansClient(base_url="https://lians.example", access_token=human_oidc_token) as client:
    created = client.create_workload_credential({
        "label": "production-recorder",
        "role": "analyst",
        "ttl_seconds": 86_400,
    })
    store_in_secret_manager(created["secret"])  # returned only once
```

See [tenant workload credentials](../../../docs/workload-credentials.md) for
least-privilege, barrier, expiry, and rotation guarantees.

## Why Lians

- Bitemporal facts with event time and ingestion time
- Deterministic supersession before memories reach the model
- Point-in-time recall and lookahead-bias checks
- Tamper-evident audit history and a crypto-erasure workflow
- Local SQLite mode with no server or API key
- Hosted and self-hosted deployment paths

See the [published benchmark results](https://github.com/Lians-ai/Lians/blob/master/docs/benchmark.md), [regulated-memory evaluation](https://github.com/Lians-ai/Lians/blob/master/docs/regulated-eval-results.md), and [public correction ledger](https://github.com/Lians-ai/Lians/blob/master/docs/gtm/public-right-of-reply-2026-07-17.md). The evaluation includes runnable adapters so results can be reproduced and challenged.

## Framework integrations

```python
from lians.langchain_integration import LiansChatHistory, build_tools
from lians.langgraph_integration import create_recall_node, create_remember_node
from lians.crewai_integration import build_crewai_tools
from lians.openai_agents_integration import build_openai_agent_tools
from lians.autogen_integration import build_autogen_tools
```

## Hosted or self-hosted API

```python
from lians import LiansClient

mem = LiansClient(base_url="https://mem.yourfirm.internal", api_key="...")
```

Full documentation: [github.com/Lians-ai/Lians](https://github.com/Lians-ai/Lians)

<!-- mcp-name: io.github.ebeirne/lians -->
