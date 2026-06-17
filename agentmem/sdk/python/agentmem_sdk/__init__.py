"""
AgentMem Python SDK.

Three clients, same API surface:

    AgentMemClient        — synchronous HTTP client (scripts, CLIs)
    AsyncAgentMemClient   — async HTTP client (FastAPI, async frameworks)
    LocalAgentMemClient   — zero-setup local SQLite mode (prototyping, CI)
"""
from .sync_client import AgentMemClient
from .client import AsyncAgentMemClient
from .local_client import LocalAgentMemClient

__all__ = ["AgentMemClient", "AsyncAgentMemClient", "LocalAgentMemClient"]
