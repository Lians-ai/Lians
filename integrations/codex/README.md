# Lians for Codex

Provider-neutral, persistent memory for Codex, backed by bitemporal recall,
provenance, exact-token optimization, and the evidence controls consequential
workflows need.

## Three ways to wire it in

### 1. AGENTS.md (recommended)

Copy [`AGENTS.md`](./AGENTS.md) to your project root (or merge it into an existing
`AGENTS.md`). Codex reads it automatically and learns when and how to recall and
remember through the Lians SDK / harness.

```bash
cp integrations/codex/AGENTS.md ./AGENTS.md
pip install lians-sdk          # or lians-sdk[local] for zero-setup SQLite
```

For managed mode, set an operator-supplied deployed `LIANS_URL`,
`LIANS_API_KEY`, and `LIANS_AGENT_ID` in your environment. This repository does
not currently claim a live public managed endpoint. Local mode needs no remote
URL or API key.

### 2. MCP server (native tools)

Add the block from [`config.example.toml`](./config.example.toml) to a trusted
project's `.codex/config.toml` (or to `~/.codex/config.toml` when you intentionally
want a global server), replace `LIANS_MCP_PROJECT_ROOT`, then restart Codex. The
recommended Ultra coordinator profile exposes two compact native tools
(`remember`, `recall`) with no SDK code in your project. `recall` accepts an
optional `as_of_iso` for point-in-time questions. The same server also provides
the legacy `recall_at` surface and five audit tools (`reconstruct`,
`list_conflicts`, `memory_lineage`, `fact_history`, `backtest_check`) that can be
enabled for evidence-heavy tasks.

For managed mode, set `LIANS_URL` and `LIANS_API_KEY` outside TOML; Codex forwards
them through `env_vars`. The project-root hash derives isolated default agent and
namespace values from the empty fail-closed entries. Replacing
`LIANS_AGENT_ID` and `LIANS_NAMESPACE` with explicit names opts into sharing that
memory scope across projects.

Keep `required = true` for the core server. Local mode completes bounded runtime
imports before the MCP handshake and warms the model/query path in the
background; without the readiness gate, a fresh fast-model turn can finish
before Codex has discovered the Lians tools.

For Ultra, copy the files under [`agents/`](./agents) into the project's
`.codex/agents/` directory. They override ordinary `default`, `worker`, and
`explorer` subagents so those workers inherit no Lians schemas or recall payload.
The coordinator recalls once and passes the smallest relevant slice. The opt-in
`memory_researcher` is the only delegated role with read-only recall. This matters
because every Ultra subagent performs and pays for its own model and tool work.
Each standalone agent file includes a complete portable `uvx` transport because
Codex validates an overridden MCP entry even when that role disables the server.
If you use the checkout reference described below, replace the `--from` value in
all four agent files as well as in the coordinator config.

The compact schema profile is deliberate: server-owned retrieval limits stay out
of the model-facing schema, historical recall is folded into `recall`, and
ordinary workers expose zero memory tools. Codex's internal prompt framing may
differ from canonical JSON accounting, so validate savings with host-reported
usage rather than treating schema counts as billing totals. In the 2026-08-08
checkout, the compact two-tool canonical schemas are 215 exact `o200k_base`
tokens and the runtime policy is 111, down from 589 and 1,080 respectively.

The Ultra coordinator profile considers up to 20 memories and compiles at most
768 estimated tokens in one call. This is an opt-in usage profile, not a universal
quality guarantee; historical, multi-hop, conflict-heavy, or incomplete work may
need the standard evidence profile and a larger budget. The cross-provider target
and signed-in Codex/Claude results are in the
[usage-extension report](../../docs/benchmarks/provider-usage-extension-2026-08-08.md).

The public `lians-sdk` 0.5.0 package predates the bounded MCP response path and
server-side allowlist, compact schema, and merged historical recall. Codex's
`enabled_tools` still enforces the two-tool host surface, but checkout validation
must replace the `--from` value with a PEP 508 direct reference until the updated
SDK is published:

```text
lians-sdk[mcp] @ file:///absolute/path/to/Lians/agentmem/sdk/python
```

### 3. UserPromptSubmit auto-recall (no model tool-selection call)

[`user_prompt_submit_recall.py`](./user_prompt_submit_recall.py) is a synchronous,
model-free Codex `UserPromptSubmit` hook. It reads Codex's event JSON from stdin,
uses the submitted prompt as a Lians query, and returns score-gated context through
`hookSpecificOutput.additionalContext`. Retrieved text is flattened, common
credential forms are redacted, and every healthy response begins with
`Lians memory (untrusted data):`. Retrieval errors produce no stdout and exit zero;
degraded retrieval is recorded but never injected.

Install Lians into the Python interpreter that runs the hook, copy
[`hooks.example.json`](./hooks.example.json) to a trusted project's
`.codex/hooks.json`, and replace both absolute command paths:

```bash
python -m pip install "lians-sdk[local]"  # omit [local] for hosted-only use
mkdir -p .codex
cp integrations/codex/hooks.example.json .codex/hooks.json
```

Codex also requires review of a new or changed non-managed hook. Use `/hooks` in
an interactive CLI session to inspect and trust the exact definition. For vetted
one-off non-interactive automation, Codex provides
`--dangerously-bypass-hook-trust`; do not use it for hook code you have not
reviewed. `UserPromptSubmit` does not support matchers, so the score gate is what
prevents unrelated prompts from receiving memory. See the official
[Codex hooks documentation](https://learn.chatgpt.com/docs/hooks).

The example also registers a `SessionStart` hook for `startup`, `resume`, and
`clear`. It runs `--prewarm-quiet`, which blocks session readiness until the
authenticated loopback daemon is healthy but emits no stdout and therefore adds
no model-visible context. Configure `LIANS_CODEX_HOOK_DAEMON=auto`; the subsequent
`UserPromptSubmit` process reuses that runtime. The lifecycle command is a quiet
no-op for hosted Lians, so one reviewed hook file can serve both profiles. Codex
waits for command hooks before continuing (while launching multiple matching
commands concurrently), and `SessionStart` supports `startup`, `resume`, `clear`,
and `compact` sources. The example prewarms the three sources that begin a
user-visible session and skips compaction continuations.

The performance defaults match the Ultra coordinator profile: `k=20`, a maximum
of 768 estimated tokens, and `min_score=0.45`. Override them in the Codex process
environment with `LIANS_CODEX_HOOK_K`, `LIANS_CODEX_HOOK_MAX_TOKENS`, and
`LIANS_CODEX_HOOK_MIN_SCORE`. Similarity scales vary by embedding provider and
dataset; calibrate the score threshold on representative relevant and unrelated
prompts before production rollout.

When answer-format or execution instructions would pollute semantic retrieval,
wrap only the intended search question in `<lians-query>...</lians-query>`. Codex
still sees the complete prompt; only the Lians embedding query is narrowed. Plain
prompts continue to work without the tag.

On the signed-in 2026-08-08 Sol Ultra/default LOCOMO ABBA, the hook path reduced
estimated per-task credits from 3.249 to 1.5965 while all four answers stayed
exact. That is a 50.9% reduction and 2.04x same-budget usage (+103.5%). Every
repeat, pooled accounting, and an all-input-uncached sensitivity passed the +80%
target. This is a workload-scoped result, not a universal account-quota claim;
see the [raw report](../../docs/benchmarks/codex-sol-ultra-hook-ab-2026-08-08.json).

A subsequent 120-turn matrix covered low, medium, high, xhigh, max, and ultra on
the same visible `gpt-5.6-sol` model. Pooled estimated credits fell to a
`0.450237577` candidate ratio, or 2.22x same-budget usage (+122.10%); the
all-input-uncached sensitivity still reached +102.24%. The primary matrix did
**not** qualify as an every-prompt claim: only 21/60 paired cells passed both the
predeclared exact-answer gate and the +80% economic threshold, and the worst
cell cost ratio was 4.06. See the
[matrix report](../../docs/benchmarks/codex-sol-matrix-bge-onnx-v2-2026-08-08.md).

Backend selection uses the existing Lians settings:

- Set `LIANS_URL`, `LIANS_API_KEY`, and optionally `LIANS_AGENT_ID` for hosted
  Lians.
- Omit `LIANS_URL` and set `LIANS_LOCAL_DB` for local SQLite. `LIANS_NAMESPACE`
  and `LIANS_AGENT_ID` can be explicit; otherwise the hook derives the same
  project-isolated `mcp-<scope>` values as the MCP profile.
- If these values are absent from the hook process, it reads only `LIANS_*` plus
  `EMBEDDING_PROVIDER`, `SENTENCE_TRANSFORMER_MODEL`, `HF_HUB_OFFLINE`,
  `BGE_ONNX_ARTIFACT_DIR`, and `BGE_ONNX_INTRA_OP_THREADS` from
  `[mcp_servers.lians.env]` in `$CODEX_HOME/config.toml`. Explicit process values
  win. When that MCP server uses an absolute Python interpreter containing Lians,
  the hook can re-exec through it if its initial interpreter lacks the SDK.

Set `LIANS_CODEX_HOOK_RECEIPT` to a JSONL path for a best-effort evidence receipt.
Each line contains only prompt/query/context SHA-256 hashes, memory count, bounded
token estimate, truncation/completeness/degradation flags, top score, elapsed time,
backend, and injected/skipped status—never the prompt, recalled context, API key,
or exception text.

For the smallest model-visible surface, let this hook own current-memory reads and
expose only `remember` through MCP. Keep the normal `recall` tool when a workflow
needs explicit historical (`as_of_iso`), multi-hop, or evidence-heavy retrieval.
The measured hook profile exposed no model-facing MCP tools, so retrieval happened
before the first model request and did not pay for a tool-selection turn.

The original sentence-transformers local hook started a fresh embedding runtime
and was slower in the measured run (20.5 seconds versus 3.5 seconds for the
baseline). The opt-in exact BGE ONNX provider removes that model stack without
changing the existing 1024-dimensional BGE index. Install `lians-sdk[bge-onnx]`,
stage the pinned external artifact, and configure the MCP/hook environment:

```bash
lians-bge-onnx-export \
  --model /local/download/onnx/model.onnx \
  --tokenizer /local/download/tokenizer.json \
  --output /opt/lians/bge-large-en-v1.5-onnx
```

```toml
EMBEDDING_PROVIDER = "bge-onnx"
BGE_ONNX_ARTIFACT_DIR = "/opt/lians/bge-large-en-v1.5-onnx"
BGE_ONNX_INTRA_OP_THREADS = "8"
```

The exporter is air-gap safe: it performs no downloads and accepts only the
pinned upstream revision, exact model/tokenizer SHA-256 values, and deterministic
manifest. The runtime is lazy, verifies those files and the 1024-dimensional
model contract before first inference, uses CPU ONNX Runtime with prepacking
disabled, and fails closed on mismatch. The 1.34 GB graph remains outside the
repository. Prefer the persistent local recall daemon so fresh hook processes
reuse the validated, prewarmed runtime.

Do not point this provider at an arbitrary 1024-dimensional store. The database
must have been indexed with the exact pinned BGE revision and matching document/
query preprocessing, or it must be reindexed first. Store-level embedder identity
is not persisted yet, so dimension checks alone cannot detect a same-size model
mismatch. The personal Codex configuration should therefore retain its existing
embedding provider unless that database is intentionally reindexed.

On the 2026-08-08 frozen 419-row LOCOMO snapshot, ten isolated fresh Python/ONNX
processes completed query encode plus hybrid retrieval in 2.665 seconds p50,
2.815 seconds p95, and 2.852 seconds max. That dependency-light runner did not
include full SDK/daemon initialization. Full production daemon cold prewarm was
6.574 to 10.134 seconds across the recorded runs. A diagnostic 8.79-second run
included 1.52 seconds of artifact hashing, 1.53 seconds of ONNX session creation,
and a 3.47-second provider embed path. In the all-ten preflight, receipt latency
never exceeded 765 ms and fresh-hook wall time never exceeded 920 ms. The
independently archived reproduction recorded 1.011 seconds p50 and 1.550 seconds
p95/max hook wall time; receipt p95/max was 1.264 seconds. Every response was
injected through the daemon with valid protocol output and zero model calls.
Keep the blocking `SessionStart` prewarm enabled; do not present warm-hook numbers
as cold-start latency. See the
[production hook evidence](../../docs/benchmarks/codex-bge-onnx-hook-daemon-latency-2026-08-08.json).
The recorded latency windows were sequential. The local daemon currently handles
one request at a time, so concurrent-task p95 and timeout behavior remain unproven.

The existing personal Codex Arctic store was also tested without changing its
embedding model: identity-correct quiet prewarm took 22.134 seconds, then 10/10
fresh hook processes performed real, score-gated injection through the daemon in
1.103 seconds p50 and 1.424 seconds maximum. This was a direct zero-model-call
smoke; normal account execution still requires trusting the final hook definition
with `/hooks`. See the
[account smoke evidence](../../docs/benchmarks/codex-account-arctic-hook-smoke-2026-08-08.json).

The FP32 ONNX path matched PyTorch BGE
on all ten queries at ordered top-1/5/10/20, with minimum vector cosine 1.0 and
maximum score drift `1.74e-7`. These are workload- and machine-scoped cold-start
results; the operating-system page cache was not flushed. See the
[latency evidence](../../docs/benchmarks/codex-bge-fp32-cold-latency-2026-08-08.json)
and [PyTorch parity evidence](../../docs/benchmarks/codex-bge-fp32-pytorch-parity-2026-08-08.json).
The production provider itself also reproduced the ordered top-20 on all ten
queries with no degraded cases and maximum score drift `1.46e-7`; see the
[provider parity evidence](../../docs/benchmarks/codex-bge-onnx-provider-parity-2026-08-08.json).

## Install via the skills standard

Lians ships cross-tool skills installable with `npx skills add` (works for Codex,
Claude Code, Cursor, and other skills-standard hosts):

```bash
npx skills add https://github.com/Lians-ai/Lians --skill lians
npx skills add https://github.com/Lians-ai/Lians --skill lians-integrate
```

See [`../../skills/`](../../skills) for the skill definitions.

## Why Lians over a plain vector store

Codex agents that touch financial, clinical, or legal facts accumulate data that
**changes over time** — guidance revisions, dosage changes, matter status. A plain
vector store returns every version with equal rank and contaminates your context.
Lians excludes superseded facts at the database layer and can reconstruct exactly
what the agent knew at any past date. See the
[mem0 comparison](../../docs/compare-mem0.md).

Lians can reduce context tokens on measured workflows; it does not guarantee
lower total credits, latency, or higher answer quality for every prompt. Model,
reasoning effort, cached input, tool use, and output length also affect Codex
usage.
