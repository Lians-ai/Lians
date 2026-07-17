"""Perform a real MCP handshake against the Glama container image."""

import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


EXPECTED_TOOLS = {
    "remember",
    "recall",
    "recall_at",
    "reconstruct",
    "list_conflicts",
    "memory_lineage",
    "fact_history",
    "backtest_check",
}


async def main() -> None:
    parameters = StdioServerParameters(
        command="docker",
        args=[
            "run",
            "--rm",
            "-i",
            "--read-only",
            "--tmpfs",
            "/data:rw,noexec,nosuid,size=32m,mode=1777",
            "lians-mcp-glama:test",
        ],
    )

    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            response = await session.list_tools()

    names = {tool.name for tool in response.tools}
    missing = EXPECTED_TOOLS - names
    if missing:
        raise RuntimeError(f"container did not expose expected tools: {sorted(missing)}")

    print(f"MCP container handshake passed with {len(names)} tools")


if __name__ == "__main__":
    asyncio.run(main())
