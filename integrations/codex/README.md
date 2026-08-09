# Lians for Codex

Persistent, financial-grade memory for the [Codex](https://github.com/openai/codex)
agent — with inspectable controls regulated teams can validate (bitemporal recall,
a tamper-evident audit chain, crypto-shred erasure, and information barriers).

## Three ways to wire it in

### 1. AGENTS.md (recommended)

Copy [`AGENTS.md`](./AGENTS.md) to your project root (or merge it into an existing
`AGENTS.md`). Codex reads it automatically and learns when and how to recall and
remember through the Lians SDK / harness.

```bash
cp integrations/codex/AGENTS.md ./AGENTS.md
pip install lians-sdk          # or lians-sdk[local] for zero-setup SQLite
```

Set `LIANS_URL`, `LIANS_API_KEY`, and `LIANS_AGENT_ID` in your environment (free
key in the [Lians Console](https://www.lians.ai/login)). Local mode needs no env vars.

### 2. MCP server (native tools)

Add the block from [`config.example.toml`](./config.example.toml) to
`~/.codex/config.toml`. Codex gains eight native memory tools (`remember`,
`recall`, `recall_at`, `reconstruct`, `list_conflicts`, `memory_lineage`,
`fact_history`, `backtest_check`) with no SDK code in your project.

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
The hook is designed to retrieve before the first model request, without exposing
a model-facing recall tool. Validate latency and task economics on the target
machine and workload before making performance or savings claims.

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
