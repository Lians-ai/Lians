<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="https://raw.githubusercontent.com/Lians-ai/Lians/HEAD/docs/images/logo.png" width="340" alt="Lians logo">
  </a>
</p>

# Lians

**Bitemporal long-term memory for AI agents.** Keep current facts clean, reconstruct what an agent knew at a past time, and retain tamper-evident audit records.

## Install

```bash
pip install lians-sdk
pip install lians-sdk[local]         # Zero-setup SQLite mode
pip install lians-sdk[mcp]           # Local MCP server
pip install lians-sdk[langchain]     # LangChain
pip install lians-sdk[langgraph]     # LangGraph
pip install lians-sdk[crewai]        # CrewAI
pip install lians-sdk[openai-agents] # OpenAI Agents SDK
pip install lians-sdk[autogen]       # AutoGen v0.4
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
