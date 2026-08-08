"""Exercise a checked-out Lians MCP server exactly as Codex launches it.

The optional JSON report is rewritten after each protocol phase. This makes
Windows stdio/startup failures diagnosable even when the parent process must be
terminated by a timeout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from time import perf_counter

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True, help="Python executable used by Codex")
    parser.add_argument("--sdk-root", required=True, help="Directory containing the lians package")
    parser.add_argument("--database", required=True, help="Persistent local SQLite database")
    parser.add_argument("--namespace", default="codex-smoke")
    parser.add_argument("--agent-id", default="codex-smoke")
    parser.add_argument("--report", help="Optional progress/result JSON path")
    parser.add_argument("--embedding-provider", default="sentence-transformers")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> dict[str, object]:
    report: dict[str, object] = {"phase": "starting"}
    started = perf_counter()

    def checkpoint(phase: str, **values: object) -> None:
        report.update(phase=phase, elapsed_seconds=round(perf_counter() - started, 3), **values)
        if args.report:
            Path(args.report).write_text(
                json.dumps(report, indent=2, sort_keys=True),
                encoding="utf-8",
            )

    environment = {
        **os.environ,
        "LIANS_AGENT_ID": args.agent_id,
        "LIANS_NAMESPACE": args.namespace,
        "LIANS_LOCAL_DB": str(Path(args.database).resolve()),
        "EMBEDDING_PROVIDER": args.embedding_provider,
        "SENTENCE_TRANSFORMER_MODEL": "Snowflake/snowflake-arctic-embed-l-v2.0",
        "HF_HUB_OFFLINE": "1",
    }
    params = StdioServerParameters(
        command=str(Path(args.python).resolve()),
        args=["-m", "lians.mcp_server"],
        cwd=str(Path(args.sdk_root).resolve()),
        env=environment,
    )
    checkpoint("launching")
    async with stdio_client(params) as (read_stream, write_stream):
        checkpoint("transport_open")
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            checkpoint("initialized")
            tools = await session.list_tools()
            tool_names = [tool.name for tool in tools.tools]
            checkpoint("tools_listed", tools=tool_names)
            recall_started = perf_counter()
            recalled = await session.call_tool(
                "recall",
                {
                    "query": "What is the current Lians product direction for AI agents?",
                    "k": 3,
                    "filters": {"ticker": "LIANS"},
                },
            )
            recall_seconds = round(perf_counter() - recall_started, 3)
            text = recalled.content[0].text if recalled.content else ""
            checkpoint(
                "recall_complete",
                tools=tool_names,
                recall_seconds=recall_seconds,
                recalled_text=text,
                is_error=bool(recalled.isError),
            )
    checkpoint("complete")
    return report


def main() -> None:
    report = asyncio.run(_run(_arguments()))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
