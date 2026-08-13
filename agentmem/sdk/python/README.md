<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/assets/logo-blue.png" width="340" alt="Lians">
  </a>
</p>

# Lians for Python

**Local-first memory for AI agents.** Remember useful facts, recall relevant
context in later sessions, and keep that memory independent of the model
provider.

## Local quickstart

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

Local mode stores memory in SQLite and needs no server, Docker container, or
API key. The first run may download the local embedding model.

## Add memory to an MCP client

```bash
pip install "lians-sdk[mcp]"
lians-mcp
```

For most MCP hosts, use `uvx` so you do not need to manage a virtual
environment:

```json
{
  "mcpServers": {
    "lians": {
      "command": "uvx",
      "args": ["--from", "lians-sdk[mcp]", "lians-mcp"],
      "env": {"LIANS_MCP_ENABLED_TOOLS": "remember,recall"}
    }
  }
}
```

## Connect to a Lians server

```bash
pip install lians-sdk
```

```python
from lians import LiansClient

memory = LiansClient(
    base_url="https://memory.example.com",
    api_key="...",
)
```

## Framework integrations

```bash
pip install "lians-sdk[langchain]"
pip install "lians-sdk[langgraph]"
pip install "lians-sdk[crewai]"
pip install "lians-sdk[openai-agents]"
pip install "lians-sdk[autogen]"
```

The package includes adapters for LangChain, LangGraph, CrewAI, OpenAI Agents,
and AutoGen.

## Advanced memory controls

Lians also supports point-in-time recall, supersession, memory lineage,
tamper-evident audit history, governed erasure, and shared deployments. These
features are optional; `remember` and `recall` are enough to start.

Full documentation: [github.com/Lians-ai/Lians](https://github.com/Lians-ai/Lians)

<!-- mcp-name: io.github.ebeirne/lians -->
