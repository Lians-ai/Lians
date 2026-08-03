"""
Lians -- financial-grade agent memory layer.

This is the server package. For the Python client SDK, install lians-sdk:

    pip install lians-sdk[local]   # local SQLite mode, no server needed
    pip install lians-sdk          # HTTP client for self-hosted or cloud server

Then import from the SDK:

    from lians import LiansClient, AsyncLiansClient, LocalLiansClient

The deployable server distribution is named ``lians-platform`` to distinguish
its package metadata from the separately published ``lians-sdk`` client. The
isolated server image owns the private top-level ``lians`` import; do not
co-install the server wheel and client SDK in one Python environment.

Server entry point: lians.main:app (uvicorn)
"""

from .version import __version__

__all__ = ["__version__"]
