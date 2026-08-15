# Install Lians

Lians is a memory tool for AI agents. Use MCP to add memory to an existing AI
client, use the local Python SDK inside an application, or connect an SDK to a
self-hosted Lians server.

## Existing AI client: use MCP

This is the recommended path for Claude Desktop, Cursor, Windsurf, VS Code,
Antigravity CLI, supported Gemini CLI deployments, and other MCP-compatible
agents.

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/), then add
this server to your client's MCP configuration:

```json
{
  "mcpServers": {
    "lians": {
      "command": "uvx",
      "args": ["--from", "lians-sdk[mcp]", "lians-memory-mcp"]
    }
  }
}
```

Restart the client. Local mode needs no Lians account, API key, or Docker
service and stores memory in `~/.lians/mcp.db`.

On a clean machine, the first memory tool may download and initialize the local
semantic model. Progress-aware MCP clients show a warmup message. If warmup is
still running after 90 seconds, Lians returns a retryable error and guarantees
that the attempted write was not queued; keep the MCP server running and retry
shortly. Model files use the Hugging Face cache controlled by `HF_HOME`. Set
`LIANS_MCP_LOCAL_READY_TIMEOUT` to 5-600 seconds to change the bound.

The starter command exposes `remember` and `recall`. Replace
`lians-memory-mcp` with `lians-mcp` to expose the advanced point-in-time,
reconstruction, lineage, conflict, feedback, and backtest tools shipped in the
current PyPI release.

Client-specific examples:

- [Cursor](../integrations/cursor)
- [Antigravity CLI](../integrations/antigravity)
- [Gemini CLI](../integrations/gemini)
- [Claude Code](../integrations/lians-plugin)
- [Codex](../plugins/lians-memory)

## Python: local memory

Use this path for a Python application, notebook, or custom agent loop:

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

print([item["content"] for item in result["memories"]])
```

The first run may download the local embedding model. Use a persistent
`db_path` to keep memory between application runs.

## Install from source

```bash
git clone https://github.com/Lians-ai/Lians.git
cd Lians
python -m pip install -e "agentmem/sdk/python[local,mcp]"
```

The editable install provides `LocalLiansClient` and the `lians-mcp` command.
Developers who want the complete service and test dependencies can instead run:

```bash
python -m pip install -e ".[dev]"
```

## Language SDKs

The current release line is `0.5.0`. The Python and TypeScript packages are
published to registries; Go, Java, and C clients are versioned with the repository.

| Language | Install | Client |
|---|---|---|
| Python | `pip install lians-sdk` | `from lians import LiansClient` |
| TypeScript / Node | `npm install @lians-ai/lians` | `import { LiansClient } from "@lians-ai/lians"` |
| Go | `go get github.com/Lians-ai/Lians/agentmem/sdk/go@v0.5.0` | `lians.NewClient(url, key)` |
| Java 11+ | Maven `ai.lians:lians-sdk:0.5.0` | `new LiansClient(options)` |
| C99 | Check out `v0.5.0`, then build `agentmem/sdk/c` | `lians_client_new(...)` |

The non-local clients connect to a hosted or self-hosted Lians HTTP service.
See each SDK directory for the exact API surface:

- [Python](../agentmem/sdk/python)
- [TypeScript](../agentmem/sdk/typescript)
- [Go](../agentmem/sdk/go)
- [Java](../agentmem/sdk/java)
- [C](../agentmem/sdk/c)

## Framework integrations

Start with a tested, credential-free local example:

- [Pydantic AI integration](../integrations/pydantic-ai/python)
- [LangGraph integration](../integrations/langgraph/python)

The Python package also includes adapters for these frameworks:

```bash
pip install "lians-sdk[langchain]"
pip install "lians-sdk[langgraph]"
pip install "lians-sdk[crewai]"
pip install "lians-sdk[openai-agents]"
pip install "lians-sdk[autogen]"
```

Or install every optional integration:

```bash
pip install "lians-sdk[all]"
```

## Self-host Lians

Use the full service when multiple agents or users need a shared deployment:

```bash
git clone https://github.com/Lians-ai/Lians.git
cd Lians/agentmem
cp .env.demo .env
docker compose up --build -d
python scripts/seed_demo.py
```

The API is available at `http://localhost:8000`; interactive documentation is
at `http://localhost:8000/docs`. See the [deployment guide](deploy.md) before
running a production environment.

## Verify published versions

The machine-readable release matrix lives in
[`published-release-status.json`](published-release-status.json). Verify it
against live registries with:

```bash
python scripts/check_published_artifacts.py
```

Maintainers should follow [RELEASING.md](../RELEASING.md) for the complete release
process.
