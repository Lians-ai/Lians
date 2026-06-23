"""
Lian -- financial-grade agent memory layer.

This is the server package. For the Python client SDK, install lian-sdk:

    pip install lian-sdk[local]   # local SQLite mode, no server needed
    pip install lian-sdk          # HTTP client for self-hosted or cloud server

Then import from the SDK:

    from lian import LianClient, AsyncLianClient, LocalLianClient

Server entry point: src.lian.main:app (uvicorn)
"""

__version__ = "0.1.0"
