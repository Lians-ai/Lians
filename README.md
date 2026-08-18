<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="docs/images/favicon.png" width="88" alt="Lians Lotus">
  </a>
</p>

<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="docs/images/logo.png" width="320" alt="Lians">
  </a>
</p>

<p align="center">
  <a href="docs/install.md">Install</a> ·
  <a href="docs/easy-install.md">Desktop preview</a> ·
  <a href="docs/">Docs</a> ·
  <a href="https://github.com/Lians-ai/Lians/issues">Issues</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/lians-sdk"><img src="https://img.shields.io/pypi/v/lians-sdk?label=PyPI" alt="PyPI version"></a>
  <a href="https://www.npmjs.com/package/@lians-ai/lians"><img src="https://img.shields.io/npm/v/%40lians-ai%2Flians?label=npm" alt="npm version"></a>
  <a href="https://registry.modelcontextprotocol.io/?q=io.github.ebeirne%2Flians"><img src="https://img.shields.io/badge/MCP-Official%20Registry-blueviolet" alt="MCP Official Registry"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="Apache 2.0 license"></a>
  <a href="https://github.com/Lians-ai/Lians/stargazers"><img src="https://img.shields.io/github/stars/Lians-ai/Lians?style=social" alt="Star Lians on GitHub"></a>
</p>

# Start a new AI coding session without re-explaining your project.

Lians gives Claude Code, Codex, Cursor, and other AI tools the current project
state they need to pick up where another session stopped. It keeps completed
work, open work, decisions, constraints, and changed facts separate so the next
agent can continue without repeating or reversing prior work.

Keep your current AI account, editor, and normal workflow. Lians runs locally
between your work history and your AI tool.

- Resume a project with what is complete, what is open, and what should happen next.
- Stop agents from redoing completed work or reviving a superseded decision.
- Give each new session a small, project-scoped handoff instead of a transcript dump.
- Bind repository changes to the original task and produce a signed review receipt.
- Turn thousands of research posts or browser events into one bounded brief.
- Inspect, correct, export, or delete anything Lians saves.
- Use one local memory store with multiple compatible AI tools.

Lians does not ask for your Claude, Cursor, or Codex password or provider API
key. It does not enlarge a provider context window or change a subscription
quota.

> **Cross-agent continuity beta:** the included Claude-to-Codex fixture recovers
> **10/10 expected continuity facts**, presents **0 stale facts as current**, and
> produces a **231-token handoff**. This is a deterministic synthetic fixture,
> not yet a claim about every live coding session.
> [Run the experiment](experiments/cross-agent-continuity/README.md).

## See the handoff

A fresh agent receives a bounded continuation view like this:

```text
Completed:
- migrated the orders API to /v2/orders
- updated tests

Still open:
- update documentation

Decisions:
- keep pytest

Changed:
- /v1/orders is stale; use /v2/orders

Do NOT:
- redo the migration
- replace pytest with unittest

Next:
- update documentation before touching unrelated UI
```

The handoff is derived from current Lians state. It is not a manually maintained
summary and it does not expose the full session transcript.

## Try saved memory in two chats

Choose the AI tool you already use:

| Tool | Fastest setup |
|---|---|
| Cursor | [One-click MCP install](integrations/cursor) |
| Claude Code | [Two plugin commands](integrations/lians-plugin) |
| Codex app, CLI, or IDE | [One-command MCP setup](integrations/codex) |
| Other MCP clients | [Minimal local MCP configuration](#minimal-local-mcp-setup) |

Then tell the AI tool:

```text
Remember that this project uses Python 3.12 and pytest.
```

Open a new chat in the same project and ask:

```text
What Python version and test runner does this project use?
```

Lians can supply the saved detail without replaying the previous chat.

If that saved detail later changes, Lians can mark dependent memories and work
for review, keep stale memory out of normal recall, and give every connected AI
the same current replacement state.

[Watch the 33-second remember, reuse, and delete proof](https://github.com/Lians-ai/Lians/releases/download/lians-memory-openai-demo-v1.0.0/Lians-Memory-OpenAI-submission-demo-v1.0.0.mp4).

## Verify agent work before you ship it

For repository work, a connected agent can create a task contract, restrict the
approved file scope, map changed files back to success criteria, and request a
signed Lians verification receipt before claiming completion. Lians checks the
actual Git diff, whitespace integrity, current-state invalidations, recorded
task evidence, required check attestations, credential patterns, and common
high-risk code patterns.

The receipt binds those measured facts to the base commit and exact diff hash.
It does not ask a model to grade its own work, run arbitrary project commands,
or claim that semantic correctness has been formally proven. Test output
provided by an agent is labeled caller-attested, and every passing result still
requires a human ship decision.

For bounded critical logic, Lians can also exhaustively prove a declared finite
model, reject vacuous assumptions, return a concrete counterexample, and bind
the proof plus source hashes into the same receipt. A second backend proves an
actual restricted pure Python function across every declared finite input
without importing or executing it. These are bounded proofs, not a claim that
an arbitrary application is completely correct. [Read the formal verification
boundary and manifest format](docs/formal-verification.md).

## Compress a large workday

Compile a JSON or JSON Lines export locally before giving it to Claude or
Codex:

```bash
lians brief research posts.jsonl --output research-brief.json
lians brief browser browser-events.jsonl --output browser-brief.json
```

Lians removes repeated research text and superseded browser states, preserves
representative evidence, and writes a hash receipt. Raw records are not sent to
an AI provider. Credential-like records are refused.

The brief compiler is currently available from a source checkout while the
consumer package is being tested.

## What is available

| Capability | Status |
|---|---|
| Free local memory through MCP and Python | Available |
| Cursor, Claude Code, Codex, Gemini, Antigravity, Windsurf, Cline, and OpenCode setup | Available |
| Local research and browser brief compiler | Available from source |
| Project-scoped Claude-to-Codex continuity experiment | Beta, reproducible from source |
| Bounded context and signed selection receipts | Available |
| State-change blast radius and bounded repair briefs | Beta candidate |
| Git-scoped task verification and signed review receipts | Beta candidate |
| Exhaustive finite-model proofs with counterexamples | Beta candidate |
| Bounded proofs over actual restricted Python functions | Beta candidate |
| Guided desktop installer and local control center | Release candidate |
| Managed cross-device continuity | Technical preview |

The Windows and macOS desktop builds are tested release candidates. General
consumer promotion remains gated on Windows publisher signing and Apple
Developer ID signing and notarization. Read the
[desktop preview boundary](docs/easy-install.md).

## Measured results

The cross-agent continuity fixture captured current project truth, work state,
decisions, constraints, and one superseded route. A fresh Codex handoff selected
11 useful items in an estimated 231 tokens, recovered all 10 expected facts, and
excluded the stale route from current state. The evaluator and fixture are
[included in the repository](experiments/cross-agent-continuity/README.md).

Automatic extraction from an arbitrary live Claude session remains the next
production integration step. The current beta validates project isolation,
supersession, bounded recall, signed provenance, and the Claude-to-Codex handoff
contract without claiming that the live behavior test is complete.

In four bounded paired synthetic workloads, signed-in Claude Code and Codex
returned the exact expected answer while Lians used **79.9% to 96.7% fewer
provider-reported input tokens**. That equals **4.96x to 30.21x work per input
token** on the tested social-research and browser-history fixtures.

Separate compiled-only checks processed 10,000 posts and 2,400 browser events.
These are bounded synthetic results, not a promise that every workflow, plan,
quota, or bill will improve by the same amount.

[Read the method and machine-readable reports](docs/benchmarks/work-per-token-2026-08-16.md).

A separate live test saved one synthetic project fact through Cursor, recalled
it in a new Cursor chat and a fresh Claude Code session, then confirmed it was
gone after deletion. [Read the cross-agent test](docs/benchmarks/cross-agent-memory-2026-08-14.md).

## Minimal local MCP setup

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/), then add
this server to an MCP-compatible AI tool:

```json
{
  "mcpServers": {
    "lians": {
      "command": "uvx",
      "args": ["--from", "lians-sdk[mcp]", "lians-mcp"],
      "env": {
        "LIANS_MCP_ENABLED_TOOLS": "remember,recall,list_memories,correct_memory,forget_memory"
      }
    }
  }
}
```

Restart the AI tool. Local memory is stored in `~/.lians/mcp.db`. No Lians
account, Docker service, or provider API key is required. The first use may
download the local semantic model.

## Use Lians inside an application

```bash
pip install "lians-sdk[local]"
```

```python
from datetime import datetime, timezone
from lians import LocalLiansClient

memory = LocalLiansClient(db_path=".lians/memory.db")
memory.add(
    agent_id="my-agent",
    content="The project uses Python 3.12 and pytest.",
    event_time=datetime.now(timezone.utc),
)

result = memory.recall(
    agent_id="my-agent",
    query="Which Python version and test runner should I use?",
)
```

See the [full install guide](docs/install.md) for TypeScript, Go, Java, C,
framework integrations, and self-hosting.

Running a class, club, hackathon, or campus developer group? Use the
[student and community kit](docs/student-community-kit.md). If you cloned the
monorepo and need to identify the current packages, start with
[Supported paths and repository status](docs/supported-paths.md).

<details>
<summary><strong>Advanced memory and governance</strong></summary>

Lians can supersede stale facts, reconstruct point-in-time state, inspect
lineage and conflicts, maintain tamper-evident audit history, enforce
information barriers, and perform confirmed erasure. Encrypted backups and
opaque device sync preserve the dependency graph as well as the memories, so
stale-work protections survive a move to another machine.

- [Memory engine](docs/memory-engine.md)
- [Decision evidence](docs/decision-evidence.md)
- [Security model](docs/security-whitepaper.md)
- [Community and managed product boundary](docs/community-cloud-boundary.md)

</details>

## Development

```bash
git clone https://github.com/Lians-ai/Lians.git
cd Lians
python -m pip install -e ".[dev]"
python scripts/test_all.py
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) for focused test commands and project
conventions. Ask questions or report problems in
[GitHub Issues](https://github.com/Lians-ai/Lians/issues).

If Lians helps your workflow, [star the repository](https://github.com/Lians-ai/Lians/stargazers)
so other AI-tool users can find it.

## License

Apache 2.0. See [LICENSE](LICENSE).

<!-- mcp-name: io.github.ebeirne/lians -->
