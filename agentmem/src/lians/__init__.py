"""
Lians: cross-platform decision evidence, reconstruction, and governed memory.

This is the server package. For the Python client SDK, install lians-sdk:

    pip install lians-sdk[local]   # local SQLite mode, no server needed
    pip install lians-sdk          # HTTP client for self-hosted or cloud server

Then import from the SDK:

    from lians import LiansClient, AsyncLiansClient, LocalLiansClient

Server entry point: src.lians.main:app (uvicorn)
"""

__version__ = "0.5.0"
