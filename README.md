<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="docs/assets/logo-blue.png" width="420" alt="Lians">
  </a>
</p>

<p align="center">
  <a href="https://www.lians.ai/">Website</a> ·
  <a href="docs/install.md">Install</a> ·
  <a href="https://github.com/Lians-ai/Lians/tree/master/docs">Docs</a> ·
  <a href="https://github.com/Lians-ai/Lians/issues">Issues</a> ·
  <a href="https://github.com/Lians-ai/Lians/stargazers"><strong>Star Lians</strong></a>
</p>

<p align="center">
  <a href="https://pypi.org/project/lians-sdk"><img src="https://img.shields.io/pypi/v/lians-sdk?label=PyPI" alt="PyPI version"></a>
  <a href="https://www.npmjs.com/package/@lians-ai/lians"><img src="https://img.shields.io/npm/v/%40lians-ai%2Flians?label=npm" alt="npm version"></a>
  <a href="https://registry.modelcontextprotocol.io/?q=io.github.ebeirne%2Flians"><img src="https://img.shields.io/badge/MCP-Official%20Registry-blueviolet" alt="MCP Official Registry"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="Apache 2.0 license"></a>
</p>

# Memory for any AI agent.

Lians gives AI agents durable memory across chats, sessions, tools, and models.
Your agent can remember a useful fact now and recall it when it matters later.

- **Works with the AI you already use** through MCP, plugins, or an SDK.
- **Runs locally by default** with SQLite and no Lians account or API key.
- **Keeps memory focused** by returning a small, relevant set of current facts.
- **Stays provider-neutral** so memory is not trapped inside one model vendor.

Lians is a memory layer, not another assistant. Your agent and model stay the
same; Lians gives them a place to remember.

<p align="center">
  <a href="https://github.com/Lians-ai/Lians/releases/download/lians-memory-openai-demo-v1.0.0/Lians-Memory-OpenAI-submission-demo-v1.0.0.mp4"><strong>▶ Watch the 33-second demo: remember, recall, and confirmed deletion</strong></a>
</p>

## Install Lians without a terminal

Download **LiansMemory** for Windows, macOS, or Linux from
[GitHub Releases](https://github.com/Lians-ai/Lians/releases), open it, choose
the AI clients found on your computer, and select **Install Lians**.

The desktop setup:

- needs no Lians account, API key, Python installation, or model download;
- safely backs up existing client settings before changing them;
- gives supported clients one shared local memory profile; and
- includes a diagnostic command and silent install mode for managed devices.

Restart the selected AI client, then try:

```text
Remember that I am researching sustainable packaging for independent retailers.
```

The first standalone builds support Claude Desktop, Cursor, Windsurf, Gemini
CLI, and Codex. ChatGPT does not load local stdio MCP servers, so the installer
does not modify ChatGPT; it requires a hosted connector. See the
[guided install and IT deployment guide](docs/easy-install.md).

## Developer setup: add memory through MCP

Use this path when you prefer a package-managed MCP server or want the full
temporal and governance engine.

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/), then add
this server to your agent's MCP configuration:

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

Restart your agent and try two prompts in separate chats:

```text
Remember that this project uses Python 3.12 and pytest.
```

```text
What Python version and test runner does this project use?
```

Local MCP memory is stored in `~/.lians/mcp.db`. The starter configuration
exposes the basic loop plus the controls needed to trust it:

| Tool | What it does |
|---|---|
| `remember` | Store one durable fact, preference, constraint, or decision. |
| `recall` | Retrieve a small set of relevant, current memories. |
| `list_memories` | Inspect what Lians currently knows. |
| `correct_memory` | Replace a stale fact without hiding its history. |
| `forget_memory` | Permanently erase one memory after explicit confirmation. |

Use the exact setup guide for
[Cursor](integrations/cursor),
[Gemini CLI](integrations/gemini),
[Claude Code](integrations/lians-plugin), or
[Codex](plugins/lians-memory). Remove `LIANS_MCP_ENABLED_TOOLS` when you want
the advanced temporal and audit tools too.

## How it works

```text
you → your AI agent → remember / recall → Lians → local SQLite
```

1. You or your agent explicitly saves something worth keeping.
2. A later session asks Lians for memory related to the current task.
3. Lians returns bounded context instead of replaying every old conversation.
4. When a fact changes, Lians can supersede the stale version instead of
   sending both versions back to the model.

No model provider owns the memory. You can point another compatible agent at
the same Lians store and continue from the same context.

## Use Lians in Python

For an application, notebook, or agent loop that needs in-process memory:

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
    metadata={"project": "demo", "topic": "tooling"},
)

result = memory.recall(
    agent_id="my-agent",
    query="Which Python version and test runner should I use?",
)

for item in result["memories"]:
    print(item["content"])
```

Local mode needs no server, Docker container, or API key. The first run may
download the local embedding model.

## Install from this repository

```bash
git clone https://github.com/Lians-ai/Lians.git
cd Lians
python -m pip install -e "agentmem/sdk/python[local,mcp]"
```

That editable install includes the local Python client and the `lians-mcp`
entry point. See the [full install guide](docs/install.md) for TypeScript, Go,
Java, C, framework adapters, and self-hosting.

## Choose the interface that fits

| You want to... | Start with |
|---|---|
| Add memory without a terminal | [Lians Easy](docs/easy-install.md) |
| Give an existing AI client memory from a terminal | [MCP setup](#developer-setup-add-memory-through-mcp) |
| Add local memory inside Python | [`LocalLiansClient`](agentmem/sdk/python) |
| Connect Python or TypeScript to a Lians server | [Language SDKs](docs/install.md#language-sdks) |
| Use LangChain, LangGraph, CrewAI, OpenAI Agents, or AutoGen | [Framework integrations](docs/install.md#framework-integrations) |
| Run the full service yourself | [Self-host Lians](docs/install.md#self-host-lians) |

## Why Lians

Most memory demos store text and run vector search. Lians also handles the
problems that appear when an agent keeps memory for more than a few sessions:

- **Current over stale:** corrected facts can supersede earlier versions.
- **Small over noisy:** recall is bounded so the model gets useful context.
- **Local over locked-in:** local mode keeps data on your machine.
- **Portable over provider-specific:** MCP and SDKs work across agent stacks.
- **Inspectable over opaque:** memories can retain timestamps, sources, and
  lineage.

<details>
<summary><strong>Advanced capabilities</strong></summary>

Lians also supports point-in-time recall, conflict inspection, memory lineage,
tamper-evident audit history, governed erasure, information barriers, and
decision reconstruction. These capabilities are available when a project needs
them; they are not required to get started.

- [Memory engine](docs/memory-engine.md)
- [Decision evidence](docs/decision-evidence.md)
- [Security model](docs/security-whitepaper.md)
- [Benchmarks and reproducible evidence](docs/benchmarks/README.md)
- [Community and managed product boundary](docs/community-cloud-boundary.md)

</details>

## Repository map

```text
agentmem/src/lians/          Core engine and HTTP service
agentmem/sdk/python/        Python SDK, local client, and MCP server
agentmem/sdk/typescript/    TypeScript SDK
packages/lians-easy/        Dependency-free desktop runtime and installer
integrations/               Agent and framework integrations
plugins/                    Installable agent plugins
docs/                       Setup, architecture, security, and operations
```

## Development

```bash
git clone https://github.com/Lians-ai/Lians.git
cd Lians
python -m pip install -e ".[dev]"
python scripts/test_all.py
```

Focused test runs and development conventions are in
[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md). Published package and registry
versions are tracked in
[docs/published-release-status.json](docs/published-release-status.json).

## Community

- Ask a question or report a bug in [GitHub Issues](https://github.com/Lians-ai/Lians/issues).
- Request a new agent or framework integration with the
  [integration template](https://github.com/Lians-ai/Lians/issues/new?template=integration_request.yml).
- Read the [security policy](docs/SECURITY.md) before reporting a vulnerability.

If Lians is useful to you, [star the repository](https://github.com/Lians-ai/Lians/stargazers).
It helps other agent developers find the project.

## License

Apache 2.0 — see [LICENSE](LICENSE).

<!-- mcp-name: io.github.ebeirne/lians -->
