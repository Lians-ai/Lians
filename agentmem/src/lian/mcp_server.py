"""
AgentMem MCP (Model Context Protocol) server.

Exposes remember / recall / recall_at / reconstruct as native MCP tools so
any MCP-compatible in-house LLM can call AgentMem without a custom SDK adapter.
This is the integration path for financial firms running self-hosted models via
LiteLLM, vLLM, or similar — configure once in the model server, no per-agent
SDK code required.

Install:
    pip install mcp httpx

Run (stdio transport — standard for local LLM integration):
    python -m agentmem.mcp_server

Environment variables:
    AGENTMEM_URL        AgentMem API base URL (default: http://localhost:8000)
    AGENTMEM_API_KEY    API key with read+write scopes
    AGENTMEM_AGENT_ID   Agent identifier (default: mcp-agent)

Configure in your LLM server or Claude Desktop:
    {
      "mcpServers": {
        "agentmem": {
          "command": "python",
          "args": ["-m", "agentmem.mcp_server"],
          "env": {
            "AGENTMEM_URL": "https://your-agentmem.internal",
            "AGENTMEM_API_KEY": "agentmem_...",
            "AGENTMEM_AGENT_ID": "trading-desk-1"
          }
        }
      }
    }

The recall_at tool is the key differentiator over generic memory stores:
it returns the exact fact set that was valid at a past timestamp, enabling
true compliance reconstruction ("what did the model know before the trade?").
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

AGENTMEM_URL = os.environ.get("AGENTMEM_URL", "http://localhost:8000")
AGENTMEM_API_KEY = os.environ.get("AGENTMEM_API_KEY", "")
AGENTMEM_AGENT_ID = os.environ.get("AGENTMEM_AGENT_ID", "mcp-agent")


async def _api(method: str, path: str, body: dict | None = None) -> dict:
    import httpx
    headers = {"X-API-Key": AGENTMEM_API_KEY, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "POST":
            r = await client.post(f"{AGENTMEM_URL}{path}", json=body, headers=headers)
        else:
            r = await client.get(f"{AGENTMEM_URL}{path}", params=body or {}, headers=headers)
        r.raise_for_status()
        return r.json()


def _fmt_memories(memories: list[dict]) -> str:
    if not memories:
        return "No relevant memories found."
    return "\n".join(
        f"[{(m.get('event_time') or '')[:10]}] {m.get('content') or '[erased]'}"
        for m in memories
    )


def _build_server() -> Any:
    from mcp.server import Server
    from mcp.types import Tool, TextContent

    server = Server("agentmem")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="remember",
                description=(
                    "Store a financial fact, observation, or decision in persistent memory. "
                    "Always provide event_time_iso as when the event occurred, not now. "
                    "Add ticker/metric/entity metadata for precise supersession detection — "
                    "this is what lets AgentMem automatically replace stale guidance numbers."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["content", "event_time_iso"],
                    "properties": {
                        "content": {"type": "string"},
                        "event_time_iso": {
                            "type": "string",
                            "description": "ISO 8601 timestamp of when this event occurred.",
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Tags: ticker, metric, entity, instrument, cusip, isin.",
                        },
                        "source": {
                            "type": "string",
                            "description": "Provenance: earnings_call, analyst_report, bloomberg, etc.",
                        },
                    },
                },
            ),
            Tool(
                name="recall",
                description=(
                    "Retrieve the most relevant CURRENT memories for a query. "
                    "Returns only presently-valid facts — superseded facts are excluded. "
                    "Call this before answering any question that may be in memory. "
                    "Use filters={ticker: NVDA} to narrow to a specific instrument."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "k": {"type": "integer", "default": 5},
                        "filters": {
                            "type": "object",
                            "description": "Metadata equality filters, e.g. {ticker: NVDA}",
                        },
                    },
                },
            ),
            Tool(
                name="recall_at",
                description=(
                    "Retrieve memories that were valid at a specific past point in time. "
                    "Use for compliance and audit: 'What guidance did we have on 2026-03-01?' "
                    "Later superseding updates are excluded — this is true point-in-time recall. "
                    "mem0 has no bitemporal model. Graphiti/Zep has temporal graph queries but "
                    "no compliance audit stack (hash chain, crypto-shred, information barriers)."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["query", "as_of_iso"],
                    "properties": {
                        "query": {"type": "string"},
                        "as_of_iso": {
                            "type": "string",
                            "description": "ISO 8601 timestamp for the point-in-time snapshot.",
                        },
                        "k": {"type": "integer", "default": 5},
                    },
                },
            ),
            Tool(
                name="reconstruct",
                description=(
                    "Reconstruct the complete memory state and full audit event trail "
                    "at a past point in time. Returns every memory that was valid at as_of "
                    "plus the timestamped, hashed event log behind them. "
                    "Use for regulatory audit submissions and trade reconstruction."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["as_of_iso"],
                    "properties": {
                        "as_of_iso": {"type": "string"},
                        "query": {
                            "type": "string",
                            "description": "Optional semantic filter to narrow the memory set.",
                        },
                    },
                },
            ),
            Tool(
                name="list_conflicts",
                description=(
                    "List open conflict flags — cases where two sources reported different values "
                    "for the same fact at the same event_time.  Use this to surface data quality "
                    "issues before they affect trading decisions.  Returns up to 20 open conflicts "
                    "with both memory contents so a human or LLM can decide which source to trust."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["open", "accept_a", "accept_b", "dismissed"],
                            "default": "open",
                            "description": "Filter by resolution status.",
                        },
                    },
                },
            ),
            Tool(
                name="memory_lineage",
                description=(
                    "Return the full supersession history of a memory — every prior version "
                    "of the same fact and the chain of updates that led to the current value. "
                    "Use when a trader asks 'how did this guidance number evolve over time?' "
                    "or when investigating why a memory was replaced."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["memory_id"],
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "UUID of any memory in the lineage chain.",
                        },
                    },
                },
            ),
            Tool(
                name="fact_history",
                description=(
                    "Return every recorded version of a structured fact ordered by event_time. "
                    "Query by ticker + metric instead of a memory_id — ideal for time-series views "
                    "like 'show me how AAPL EPS evolved over the last four quarters'. "
                    "Superseded versions are included so you can see the full revision history. "
                    "Entity normalization is automatic: 'Apple Inc.', ISIN 'US0378331005', and "
                    "'AAPL' all resolve to the same fact series."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["ticker", "metric"],
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": "Ticker symbol, ISIN, CUSIP, or company name.",
                        },
                        "metric": {
                            "type": "string",
                            "description": "Metric name (e.g. 'eps', 'price_target', 'guidance').",
                        },
                        "limit": {"type": "integer", "default": 50},
                    },
                },
            ),
            Tool(
                name="backtest_check",
                description=(
                    "Detect lookahead bias in a backtest simulation. "
                    "Scans the agent's memory store and flags every fact that the agent "
                    "couldn't have known at the given simulation date. "
                    "Returns two contamination types: FUTURE_EVENT (event_time is after the "
                    "simulation checkpoint — clear lookahead) and LATE_REVISION (the event is "
                    "historical but the revised figure hadn't been published yet). "
                    "A clean report (is_clean=true) is the proof a risk committee needs."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["simulation_as_of_iso"],
                    "properties": {
                        "simulation_as_of_iso": {
                            "type": "string",
                            "description": "ISO-8601 UTC timestamp of the simulation checkpoint.",
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "remember":
                body = {
                    "agent_id": AGENTMEM_AGENT_ID,
                    "content": arguments["content"],
                    "event_time": arguments["event_time_iso"],
                    "source": arguments.get("source", "mcp"),
                    "metadata": arguments.get("metadata", {}),
                }
                await _api("POST", "/v1/memories", body)
                preview = arguments["content"][:120]
                return [TextContent(type="text", text=f"Stored: {preview}")]

            elif name == "recall":
                body = {
                    "agent_id": AGENTMEM_AGENT_ID,
                    "query": arguments["query"],
                    "k": arguments.get("k", 5),
                    "filters": arguments.get("filters", {}),
                }
                result = await _api("POST", "/v1/recall", body)
                return [TextContent(type="text", text=_fmt_memories(result.get("memories", [])))]

            elif name == "recall_at":
                body = {
                    "agent_id": AGENTMEM_AGENT_ID,
                    "query": arguments["query"],
                    "k": arguments.get("k", 5),
                    "as_of": arguments["as_of_iso"],
                }
                result = await _api("POST", "/v1/recall", body)
                header = f"Memories valid as of {arguments['as_of_iso'][:10]}:"
                return [TextContent(
                    type="text",
                    text=header + "\n" + _fmt_memories(result.get("memories", [])),
                )]

            elif name == "reconstruct":
                body: dict = {
                    "agent_id": AGENTMEM_AGENT_ID,
                    "as_of": arguments["as_of_iso"],
                }
                if "query" in arguments:
                    body["query"] = arguments["query"]
                result = await _api("POST", "/v1/audit/reconstruct", body)
                memories = result.get("memories", [])
                trail = result.get("event_trail", [])
                lines = [
                    f"State as of {arguments['as_of_iso'][:10]} — {len(memories)} memories:",
                    _fmt_memories(memories),
                    f"\nAudit trail: {len(trail)} events",
                ]
                for e in trail[-5:]:
                    lines.append(
                        f"  {(e.get('created_at') or '')[:19]}  "
                        f"{e.get('op','')}  id={str(e.get('memory_id') or '')[:8]}"
                    )
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "list_conflicts":
                status = arguments.get("status", "open")
                result = await _api("GET", f"/v1/conflicts?status={status}&limit=20")
                conflicts = result.get("conflicts", [])
                if not conflicts:
                    return [TextContent(type="text", text=f"No {status} conflicts found.")]
                lines = [f"{len(conflicts)} {status} conflict(s):"]
                for c in conflicts:
                    lines.append(
                        f"  [{c['id'][:8]}] A: {(c.get('memory_a_content') or '')[:80]!r}  "
                        f"vs  B: {(c.get('memory_b_content') or '')[:80]!r}"
                        f"  (confidence={c.get('confidence', 0):.2f})"
                    )
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "memory_lineage":
                memory_id = arguments["memory_id"]
                result = await _api("GET", f"/v1/memories/{memory_id}/lineage")
                nodes = result.get("nodes", [])
                edges = result.get("edges", [])
                root = result.get("root_id", "")
                tip  = result.get("tip_id", "")
                lines = [
                    f"Lineage for {memory_id[:8]}…: {len(nodes)} versions, {len(edges)} edges",
                    f"Root: {str(root)[:8]}  →  Tip (current): {str(tip)[:8]}",
                ]
                for node in nodes:
                    status = "✓ CURRENT" if node.get("is_current") else "superseded"
                    et = (node.get("event_time") or "")[:10]
                    content = (node.get("content") or "[erased]")[:80]
                    lines.append(f"  [{str(node['id'])[:8]}] {et}  {status}  {content!r}")
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "fact_history":
                ticker = arguments["ticker"]
                metric = arguments["metric"]
                limit = arguments.get("limit", 50)
                result = await _api(
                    "GET",
                    f"/v1/facts/history?ticker={ticker}&metric={metric}"
                    f"&agent_id={AGENTMEM_AGENT_ID}&limit={limit}",
                )
                items = result.get("items", [])
                canonical = result.get("ticker", ticker)
                lines = [
                    f"{len(items)} version(s) of {canonical} {metric} "
                    f"(canonical ticker: {canonical}):",
                ]
                for item in items:
                    et = (item.get("event_time") or "")[:10]
                    status = "active" if item.get("valid_to") is None else "superseded"
                    content = (item.get("content") or "[erased]")[:100]
                    lines.append(f"  {et}  [{status}]  {content!r}")
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "backtest_check":
                result = await _api("POST", "/v1/backtest/check", {
                    "agent_id": AGENTMEM_AGENT_ID,
                    "simulation_as_of": arguments["simulation_as_of_iso"],
                })
                is_clean = result.get("is_clean", True)
                flags = result.get("flags", [])
                checked = result.get("memories_checked", 0)
                rate = result.get("contamination_rate", 0.0)
                if is_clean:
                    return [TextContent(
                        type="text",
                        text=f"✓ CLEAN — {checked} memories checked, no lookahead bias detected.",
                    )]
                lines = [
                    f"⚠ CONTAMINATED — {len(flags)} flag(s) out of {checked} memories "
                    f"({rate:.1%} contamination rate):",
                ]
                for flag in flags:
                    ctype = flag.get("contamination_type", "")
                    delta = flag.get("delta_days", 0)
                    preview = (flag.get("content_preview") or "[erased]")[:80]
                    et = (flag.get("event_time") or "")[:10]
                    lines.append(
                        f"  [{ctype}] +{delta:.1f}d  event={et}  {preview!r}"
                    )
                return [TextContent(type="text", text="\n".join(lines))]

            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        except Exception as exc:
            return [TextContent(type="text", text=f"AgentMem error ({name}): {exc}")]

    return server


async def _main() -> None:
    try:
        from mcp.server.stdio import stdio_server
    except ImportError:
        raise SystemExit(
            "MCP package not installed.  Run: pip install mcp httpx\n"
            "Or: pip install 'agentmem[mcp]'"
        )

    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(_main())
