"""
Lian Python SDK — financial-grade AI memory with compliance built in.

Three clients, same API surface:

    LianClient        — synchronous HTTP client (scripts, CLIs)
    AsyncLianClient   — async HTTP client (FastAPI, async frameworks)
    LocalLianClient   — zero-setup local SQLite mode (prototyping, CI)

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
    from lian.langchain_integration import LianChatHistory, build_tools

    # LangGraph (node factory functions)
    from lian.langgraph_integration import create_recall_node, create_remember_node

    # CrewAI (BaseTool wrappers)
    from lian.crewai_integration import build_crewai_tools

    # OpenAI Agents SDK (FunctionTool wrappers)
    from lian.openai_agents_integration import build_openai_agent_tools

    # AutoGen v0.4 (FunctionTool) / v0.2 (ConversableAgent)
    from lian.autogen_integration import build_autogen_tools, build_autogen_functions

Install with extras::

    pip install lian[langchain]       # LangChain chat history + tools
    pip install lian[langgraph]       # LangGraph node factories
    pip install lian[crewai]          # CrewAI BaseTool wrappers
    pip install lian[openai-agents]   # OpenAI Agents SDK FunctionTools
    pip install lian[autogen]         # AutoGen v0.4 FunctionTools
    pip install lian[local]           # LocalLianClient (SQLite)
    pip install lian[all]             # Everything
"""
from .sync_client import LianClient
from .client import AsyncLianClient
from .local_client import LocalLianClient

# Backward-compatibility aliases
AgentMemClient = LianClient
AsyncAgentMemClient = AsyncLianClient
LocalAgentMemClient = LocalLianClient

__all__ = [
    "LianClient",
    "AsyncLianClient",
    "LocalLianClient",
    # aliases
    "AgentMemClient",
    "AsyncAgentMemClient",
    "LocalAgentMemClient",
]
