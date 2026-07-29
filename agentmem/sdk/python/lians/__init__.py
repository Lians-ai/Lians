"""
Lians Python SDK: durable, temporal AI memory with governance built in.

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
from pkgutil import extend_path

# The monorepo also contains the Lians server package under the same public
# namespace. Extending the path lets editable installs and integration tests
# expose SDK clients and server modules together. Standalone SDK installs still
# resolve only this package.
__path__ = extend_path(__path__, __name__)

from .client import AsyncLiansClient
from .harness import (
    CompactionGuard,
    LiansMemoryHarness,
    MemoryClient,
    MemoryIntelligenceMetrics,
    PreparedMemoryContext,
    RecalledMemory,
    SmartTurnResult,
    TurnResult,
)
from .sync_client import LiansClient

# Backward-compatibility aliases
AgentMemClient = LiansClient
AsyncAgentMemClient = AsyncLiansClient


def __getattr__(name: str):
    # LocalLiansClient needs the optional [local] extra (sqlalchemy/aiosqlite).
    # Import it lazily so a plain `pip install lians-sdk` — whose only core
    # dependency is httpx — can `import lians` without crashing.
    if name in ("LocalLiansClient", "LocalAgentMemClient"):
        from .local_client import LocalLiansClient
        return LocalLiansClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "LiansClient",
    "AsyncLiansClient",
    "LocalLiansClient",
    # Agent harness
    "LiansMemoryHarness",
    "CompactionGuard",
    "RecalledMemory",
    "TurnResult",
    "PreparedMemoryContext",
    "SmartTurnResult",
    "MemoryIntelligenceMetrics",
    "MemoryClient",
    # aliases
    "AgentMemClient",
    "AsyncAgentMemClient",
    "LocalAgentMemClient",
]
