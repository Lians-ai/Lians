"""
AgentMem Python SDK.

Three clients, same API surface:

    AgentMemClient        — synchronous HTTP client (scripts, CLIs)
    AsyncAgentMemClient   — async HTTP client (FastAPI, async frameworks)
    LocalAgentMemClient   — zero-setup local SQLite mode (prototyping, CI)

Framework integrations (optional extras):

    from agentmem_sdk.langchain_integration import AgentMemChatHistory, build_tools
    from agentmem_sdk.langgraph_integration import create_recall_node, create_remember_node
    from agentmem_sdk.crewai_integration import build_crewai_tools

Install with extras::

    pip install agentmem-sdk[langchain]    # LangChain chat history + tools
    pip install agentmem-sdk[langgraph]    # LangGraph node factories
    pip install agentmem-sdk[crewai]       # CrewAI BaseTool wrappers
    pip install agentmem-sdk[local]        # LocalAgentMemClient dependencies
    pip install agentmem-sdk[all]          # Everything
"""
from .sync_client import AgentMemClient
from .client import AsyncAgentMemClient
from .local_client import LocalAgentMemClient

__all__ = [
    "AgentMemClient",
    "AsyncAgentMemClient",
    "LocalAgentMemClient",
]
