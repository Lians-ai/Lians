"""Launch the published Lians MCP server inside Claude Desktop's UV runtime."""

import os


# Keep ordinary Claude Desktop conversations on the same bounded three-tool
# surface as Claude Code. Operators can override these values in a custom
# extension build when they intentionally need the evidence profile.
os.environ.setdefault("LIANS_MCP_ENABLED_TOOLS", "remember,recall,recall_at")
os.environ.setdefault("LIANS_MCP_RECALL_K", "50")
os.environ.setdefault("LIANS_MCP_CONTEXT_MAX_TOKENS", "2650")
os.environ.setdefault("LIANS_MCP_PREWARM", "background")

from lians.mcp_server import main  # noqa: E402 - profile must precede module import


if __name__ == "__main__":
    main()
