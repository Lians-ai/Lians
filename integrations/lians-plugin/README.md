# Lians plugin for Claude Code

This plugin gives Claude Code persistent Lians MCP memory, guided commands, and
an evidence-oriented agent. The bounded three-tool server profile requires the
updated SDK in this checkout or a later published release.

## Capabilities

- Store and recall agent memory
- Suppress superseded facts during current-state recall
- Reconstruct what was known at a requested time
- Inspect lineage and fact history
- Check historical simulations for lookahead contamination
- Request erasure with an explicit reference and confirmation

## Low-context core profile

The plugin starts Lians automatically through `.mcp.json`. Its configuration
requests the three tools used in ordinary work (`remember`, `recall`, and
`recall_at`) and, with a compatible updated SDK, considers up to 50 candidates
while returning at most 2,650 estimated tokens. The cap was chosen near the
recorded LOCOMO top-50 mean of 2,656 exact
retrieval tokens. That run achieved 90.0% judged accuracy with 85.4% less answer
context, but it supplied all 50 memories rather than using this production
renderer and cap. The exact capped profile therefore still needs a representative
quality rerun and is not an across-the-board promise about Claude usage, cost,
latency, or answer quality.

Claude Code defers MCP tool schemas by default on supported models and falls
back when a host is incompatible. Leave `ENABLE_TOOL_SEARCH` unset unless you
have verified a first-party endpoint or proxy supports `tool_reference` blocks:

```json
{
  "env": {
    "MCP_TIMEOUT": "120000"
  }
}
```

Place the timeout setting in your user or project Claude settings. Forcing
`ENABLE_TOOL_SEARCH=true` can fail on older Vertex models or incompatible
proxies; use it only after compatibility testing. Do not set `alwaysLoad` on
Lians because it loads schemas up front.

## Local setup

Local SQLite mode requires no API key:

```bash
uvx --from "lians-sdk[mcp]" lians-mcp
```

The bundled profile stores local data under Claude's persistent plugin-data
directory but derives its default agent and namespace from
`${CLAUDE_PROJECT_DIR}`, preventing unrelated projects from sharing recall.
Setting `LIANS_AGENT_ID` or `LIANS_NAMESPACE` explicitly opts into a shared
cross-project scope. Set `LIANS_URL` and `LIANS_API_KEY` in the host environment
to use a managed or self-hosted Lians service instead.

Public SDK 0.5.0 does not understand project-root derivation. With that legacy
package, set project-unique `LIANS_AGENT_ID` and `LIANS_NAMESPACE` values before
using memory; the bundled empty fallbacks fail closed instead of sharing one
default scope. The updated checkout derives them automatically.

### Readiness and cold start

The plugin constructs the local client and imports the embedding runtime before
starting Claude's MCP event loop. It then loads the model and runs a probe on its
dedicated worker while the MCP connection becomes available. Set the host-level
`MCP_TIMEOUT` above because Claude's short default can expire during those
bounded imports or a first `uvx` install. The server itself gives each tool call
two minutes.

In the final checkout probe, the handshake and three-tool discovery completed in
11.6 seconds and the first bounded recall then completed in 4.5 seconds. These
are machine-specific cold timings, not latency guarantees. Run `/mcp` to confirm
the server is connected, then make one harmless recall to warm it before a
latency-sensitive workflow. Hosted mode avoids the local model startup but adds
network latency.

The exact three-tool server filter requires an SDK that implements
`LIANS_MCP_ENABLED_TOOLS`. Until that SDK revision is published, Claude Code's
default Tool Search still defers the eight schemas, but a marketplace install
may discover all eight tool names. Validate the installed server with `/mcp` and
do not claim exact core filtering from an older published SDK.

For checkout validation before publication, set `LIANS_MCP_PACKAGE` to a PEP 508
direct reference that retains the MCP extra, not to a bare directory:

```text
lians-sdk[mcp] @ file:///absolute/path/to/Lians/agentmem/sdk/python
```

### Reproduce the bounded-context path with Claude

From the repository root, use the recorded LOCOMO retrieval artifacts:

```bash
python integrations/lians-plugin/benchmarks/claude_locomo_ab.py --dry-run
python integrations/lians-plugin/benchmarks/claude_locomo_ab.py \
  --model sonnet \
  --out integrations/lians-plugin/benchmarks/results/claude-locomo-ab.json
```

The dry run verifies the semantic-context reduction without spending model
usage. The live run sends the same question to Claude once with the complete
conversation and once with the top-20 retrieved memories, records Claude's own
usage fields and cost, and leaves correctness as an explicit per-answer result.
It deliberately sets `--tools ""` and manually injects the stored artifact, so
it isolates answer context and excludes plugin/MCP schemas, selection, tool-call
traffic, runtime retrieval, and output framing.

Recorded on 2026-08-08 with the signed-in Claude `fable` alias, both paths
answered the default temporal question correctly. Claude reported 22,382 total
input tokens and $0.296701 for full history versus 2,144 input tokens and
$0.029340 for top-20 Lians context: reductions of 90.4% and 90.1%, respectively,
for this paired run. The reported-cost ratio corresponds to 10.11x same-budget
usage for this context-only comparison, clearing the 1.85x arithmetic threshold
but not validating the installed plugin end to end. The reduced call took 5.4
seconds versus 3.3 seconds for the baseline, so this result does not support a
latency-improvement claim. Raw usage is in
`benchmarks/results/claude-locomo-ab-2026-08-08.json`.

The same command is published in the official MCP Registry under
`io.github.ebeirne/lians`.

## Plugin components

- `/lians-remember`
- `/lians-recall`
- `/lians-audit`
- `/lians-integrate`
- `lians-compliance` agent for evidence-oriented memory operations
- `lians-memory` skill for setup and safe operation

Repository: https://github.com/Lians-ai/Lians

License: Apache-2.0
