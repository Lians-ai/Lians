<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="docs/images/logo.png" width="320" alt="Lians">
  </a>
</p>

<p align="center"><strong>Persistent project memory for Claude Code, Codex, and Cursor.</strong></p>

<p align="center">
  <a href="docs/quickstart.md"><strong>Quickstart</strong></a> ·
  <a href="docs/install.md">Install</a> ·
  <a href="docs/">Docs</a> ·
  <a href="https://github.com/Lians-ai/Lians/issues">Issues</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/lians-sdk"><img src="https://img.shields.io/pypi/v/lians-sdk?label=PyPI" alt="PyPI version"></a>
  <a href="https://www.npmjs.com/package/@lians-ai/lians"><img src="https://img.shields.io/npm/v/%40lians-ai%2Flians?label=npm" alt="npm version"></a>
  <a href="https://registry.modelcontextprotocol.io/?q=io.github.ebeirne%2Flians"><img src="https://img.shields.io/badge/MCP-Official%20Registry-blueviolet" alt="MCP Official Registry"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="Apache 2.0 license"></a>
</p>

## Stop re-explaining your project to AI

Save a project decision once. Lians recalls the relevant part in a later
session—even when you switch supported AI tools—without replaying your entire
chat history.

- **Continue across sessions.** Carry completed work, open work, decisions, and
  constraints into a fresh task.
- **Keep memory current.** Correct or supersede stale facts instead of letting
  an agent revive an old decision.
- **Stay in control.** Inspect, export, or permanently delete anything Lians
  stores.
- **Run locally.** The starter setup needs no Lians account, AI password, or
  provider API key.

Lians works with your existing AI account and editor. It does not replace your
model, enlarge its context window, or change its subscription quota.

## See it in two chats

In one chat:

```text
You: Remember that this project uses PostgreSQL, not SQLite.
Lians: Saved for this project.
```

Open a new chat—or switch to another supported AI tool:

```text
You: Which database should this feature use?
Lians: PostgreSQL. The project explicitly excludes SQLite.
```

If that decision changes, correct it once and future current-state recall uses
the replacement. [Watch the 33-second remember, reuse, and delete proof](https://github.com/Lians-ai/Lians/releases/download/lians-memory-openai-demo-v1.0.0/Lians-Memory-OpenAI-submission-demo-v1.0.0.mp4).

## Try it in two minutes

Choose the AI tool you already use:

| Tool | Fastest setup |
|---|---|
| Codex app, CLI, or IDE | [One command](integrations/codex) |
| Claude Code | [Two plugin commands](integrations/lians-plugin) |
| Cursor | [One-click MCP install](integrations/cursor) |
| Other MCP clients | [Minimal MCP setup](docs/install.md#existing-ai-client-use-mcp) |

For example, after [installing `uv`](https://docs.astral.sh/uv/getting-started/installation/), connect Codex with:

```bash
codex mcp add lians --env LIANS_MCP_ENABLED_TOOLS=remember,recall,list_memories,correct_memory,forget_memory -- uvx --from "lians-sdk[mcp]" lians-mcp
```

Restart Codex, then try the two-chat example above. Local memory is stored in
`~/.lians/mcp.db` by default.

[Follow the complete quickstart](docs/quickstart.md) for setup, verification,
correction, deletion, and troubleshooting.

## What a fresh coding agent receives

Lians can generate a bounded project handoff instead of replaying a transcript:

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

Next:
- update documentation before touching unrelated UI
```

The handoff is derived from current Lians state, not a manually maintained
summary.

## Project status

Lians is under active development. Start with local memory; preview features
are clearly labeled so you can choose the appropriate risk level.

| Capability | Status |
|---|---|
| Local memory through MCP and Python | Available |
| Codex, Claude Code, and Cursor setup paths | Available |
| Inspect, correct, and confirmed permanent deletion | Available |
| Bounded context and signed selection receipts | Available |
| Automatic Claude-to-Codex project handoff | Beta |
| Guided desktop installer and local control center | Release candidate |
| Managed cross-device continuity | Technical preview |

The macOS and Windows desktop builds remain release candidates pending platform
signing and notarization. See the [desktop preview boundary](docs/easy-install.md).

## Evidence

The included Claude-to-Codex continuity fixture recovered **10/10 expected
facts**, exposed **0 stale facts as current**, and produced a **231-token
handoff**. These are bounded beta results, not a promise that every live coding
session extracts perfectly. [Run the experiment](experiments/cross-agent-continuity/README.md).

A separate live test saved a synthetic project fact through Cursor, recalled it
in a new Cursor chat and a fresh Claude Code session, and confirmed it was gone
after deletion. [Read the test method](docs/benchmarks/cross-agent-memory-2026-08-14.md).

Four paired synthetic workloads also returned the exact expected answer while
using **79.9% to 96.7% fewer provider-reported input tokens**. Results depend on
the workload and do not guarantee lower usage or cost. [Read the benchmark](docs/benchmarks/work-per-token-2026-08-16.md).

## Build with Lians

Use the local Python SDK inside an application:

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

See the [install guide](docs/install.md) for TypeScript, Go, Java, C, framework
integrations, and self-hosting.

Running a class, club, hackathon, or campus developer group? Use the
[student and community kit](docs/student-community-kit.md). Contributors and
package integrators can start with
[Supported paths and repository status](docs/supported-paths.md).

<details>
<summary><strong>Advanced capabilities</strong></summary>

Lians also includes tools for project-scoped agent handoffs, signed selection
and review receipts, local research and browser briefs, temporal reconstruction,
lineage, information barriers, confirmed erasure, and bounded formal checks.
These capabilities are useful for advanced or governed deployments but are not
required for the starter memory workflow.

- [Memory engine](docs/memory-engine.md)
- [Cross-agent continuity experiment](experiments/cross-agent-continuity/README.md)
- [Agent-work verification](docs/formal-verification.md)
- [Security model](docs/security-whitepaper.md)
- [Community and managed product boundary](docs/community-cloud-boundary.md)
- [Supported paths and repository status](docs/supported-paths.md)

</details>

## Development

```bash
git clone https://github.com/Lians-ai/Lians.git
cd Lians
python -m pip install -e ".[dev]"
python scripts/test_all.py
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Feature
ideas, integration requests, and reproducible bugs are welcome in
[GitHub Issues](https://github.com/Lians-ai/Lians/issues).

If Lians helps your workflow, [star the repository](https://github.com/Lians-ai/Lians/stargazers)
so other AI-tool users can find it.

## License

Apache 2.0. See [LICENSE](LICENSE).

<!-- mcp-name: io.github.ebeirne/lians -->
