"""
Lians Python SDK — financial-grade AI memory with compliance built in.

Three clients, same API surface:

    LiansClient        — synchronous HTTP client (scripts, CLIs)
    AsyncLiansClient   — async HTTP client (FastAPI, async frameworks)
    LocalLiansClient   — zero-setup local SQLite mode (prototyping, CI)

Convenience methods on all clients::

    client.add(agent_id, content, event_time, metadata=...)
    client.add_from_messages(agent_id, messages=[{"role": "assistant", "content": "..."}])
    client.recall(agent_id, query, k=5)
    client.recall_at(agent_id, query, as_of=datetime(...))   # point-in-time / compliance
    client.snapshot(agent_id, as_of=datetime(...))           # full knowledge state at T
    client.backtest_check(agent_id, simulation_as_of=...)    # lookahead-bias detection
    client.erase(subject_id, request_ref)                    # GDPR crypto-shred

Framework integrations (optional extras)::

    # LangChain (chat history + StructuredTools)
    from lians.langchain_integration import LiansChatHistory, build_tools

    # LangGraph (node factory functions)
    from lians.langgraph_integration import create_recall_node, create_remember_node

    # CrewAI (BaseTool wrappers)
    from lians.crewai_integration import build_crewai_tools

    # OpenAI Agents SDK (FunctionTool wrappers)
    from lians.openai_agents_integration import build_openai_agent_tools

    # AutoGen v0.4 (FunctionTool) / v0.2 (ConversableAgent)
    from lians.autogen_integration import build_autogen_tools, build_autogen_functions

Install with extras::

    pip install lians-sdk[langchain]       # LangChain chat history + tools
    pip install lians-sdk[langgraph]       # LangGraph node factories
    pip install lians-sdk[crewai]          # CrewAI BaseTool wrappers
    pip install lians-sdk[openai-agents]   # OpenAI Agents SDK FunctionTools
    pip install lians-sdk[autogen]         # AutoGen v0.4 FunctionTools
    pip install lians-sdk[local]           # LocalLiansClient (SQLite)
    pip install lians-sdk[all]             # Everything
"""
from .sync_client import LiansClient
from .client import AsyncLiansClient
from .local_client import LocalLiansClient
from .harness import (
    LiansMemoryHarness,
    RecalledMemory,
    TurnResult,
    MemoryClient,
)

# Backward-compatibility aliases
AgentMemClient = LiansClient
AsyncAgentMemClient = AsyncLiansClient
LocalAgentMemClient = LocalLiansClient

__all__ = [
    "LiansClient",
    "AsyncLiansClient",
    "LocalLiansClient",
    # Agent harness
    "LiansMemoryHarness",
    "RecalledMemory",
    "TurnResult",
    "MemoryClient",
    # aliases
    "AgentMemClient",
    "AsyncAgentMemClient",
    "LocalAgentMemClient",
]
