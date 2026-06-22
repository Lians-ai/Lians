# Lian (蓮)

**Financial-grade AI memory** — bitemporal facts, SEC 17a-4 audit chain, GDPR crypto-shred.

## Install

```bash
pip install lian-sdk          # HTTP client only
pip install lian-sdk[local]        # + zero-setup SQLite mode (no server needed)
pip install lian-sdk[langchain]    # + LangChain chat history & tools
pip install lian-sdk[langgraph]    # + LangGraph node factories
pip install lian-sdk[crewai]       # + CrewAI BaseTool wrappers
pip install lian-sdk[openai-agents] # + OpenAI Agents SDK tools
pip install lian-sdk[autogen]      # + AutoGen v0.4 tools
pip install lian-sdk[all]          # Everything
```

## Quickstart

```python
from lian import LocalLianClient
from datetime import datetime, timezone

mem = LocalLianClient()  # no server, no Docker, no API key

mem.add(
    agent_id="analyst-1",
    content="NVDA FY2026 revenue guidance raised to $40B",
    event_time=datetime(2025, 11, 19, 16, tzinfo=timezone.utc),
    metadata={"ticker": "NVDA", "metric": "revenue_guidance"},
    importance=0.9,
)

# Superseded facts are excluded at the DB layer — LLM never sees stale data
result = mem.recall(agent_id="analyst-1", query="NVDA revenue guidance")

# Point-in-time: what did we know on March 1?
result = mem.recall_at(
    agent_id="analyst-1",
    query="NVDA revenue guidance",
    as_of=datetime(2025, 3, 1, tzinfo=timezone.utc),
)

# Extract memories directly from a conversation (like mem0.add(messages=[...]))
mem.add_from_messages(
    agent_id="analyst-1",
    messages=[
        {"role": "user",      "content": "What guidance did NVDA give?"},
        {"role": "assistant", "content": "NVDA raised FY2026 revenue guidance to $40B."},
    ],
)
```

## What makes Lian different

| Feature | Lian | mem0 | Graphiti/Zep |
|---------|------|------|-------------|
| Bitemporal model (event + ingestion time) | ✓ | ✗ | ✓ |
| Supersession (stale facts excluded at DB layer) | ✓ | ✗ | Partial |
| SEC 17a-4 tamper-evident audit chain | ✓ | ✗ | ✗ |
| GDPR crypto-shred with audit survival | ✓ | ✗ | ✗ |
| Information barriers (PostgreSQL RLS) | ✓ | ✗ | ✗ |
| Backtest contamination detection | ✓ | ✗ | ✗ |

## Framework integrations

```python
# LangChain
from lian.langchain_integration import LianChatHistory, build_tools

# LangGraph
from lian.langgraph_integration import create_recall_node, create_remember_node

# CrewAI
from lian.crewai_integration import build_crewai_tools

# OpenAI Agents SDK
from lian.openai_agents_integration import build_openai_agent_tools

# AutoGen v0.4
from lian.autogen_integration import build_autogen_tools
```

## Switching to hosted API

```python
# Dev (local SQLite, no server)
from lian import LocalLianClient
mem = LocalLianClient()

# Production (self-hosted or managed)
from lian import LianClient
mem = LianClient(base_url="https://mem.yourfirm.internal", api_key="...")
```

Full documentation: [github.com/ebeirne/Lian](https://github.com/ebeirne/Lian)
