"""
Lians MCP server — exposes Lians memory tools over Model Context Protocol (stdio transport).

Any MCP-compatible host (Claude Desktop, Cursor, VS Code with MCP, custom LLM servers)
can call Lians directly without a custom SDK adapter after one-time configuration.

Install:
    pip install lians-sdk[mcp]

Run (stdio transport — standard for local LLM integration):
    lians-mcp

Environment variables:
    LIANS_URL        Optional Lians API base URL; omit for local mode
    LIANS_API_KEY    API key for remote mode
    LIANS_AGENT_ID   Agent identifier / memory namespace (default: mcp-agent)
    LIANS_LOCAL_DB   Local SQLite path (default: ~/.lians/mcp.db)
    LIANS_NAMESPACE  Local tenant namespace (default: mcp)
    LIANS_MCP_PROJECT_ROOT Optional project root used to derive isolated defaults
    LIANS_MCP_PREWARM Runtime warmup: background (default), true/sync, or false/off
    LIANS_MCP_ENABLED_TOOLS Optional comma-separated tool allowlist
    LIANS_MCP_RECALL_K Number of candidates considered for recall (default: 50)
    LIANS_MCP_CONTEXT_MAX_TOKENS Maximum returned recall context (default: 2650)

Configure in Claude Desktop (~/Library/Application Support/Claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "lians": {
          "command": "lians-mcp",
          "env": {
            "LIANS_URL": "https://your-lians.internal",
            "LIANS_API_KEY": "lians_...",
            "LIANS_AGENT_ID": "trading-desk-1"
          }
        }
      }
    }
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

def _parse_project_scope(raw: str | None) -> str | None:
    """Derive a stable, non-secret project identifier from an absolute root."""
    if raw is None:
        return None
    if not raw.strip():
        raise ValueError("LIANS_MCP_PROJECT_ROOT must not be blank when set")
    root = Path(raw).expanduser().resolve()
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", root.name).strip("-_.").lower()
    slug = slug[:40] or "project"
    digest = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


LIANS_URL = os.environ.get("LIANS_URL", "").rstrip("/")
LIANS_API_KEY = os.environ.get("LIANS_API_KEY", "")
LIANS_MCP_PROJECT_SCOPE = _parse_project_scope(os.environ.get("LIANS_MCP_PROJECT_ROOT"))
LIANS_AGENT_ID = os.environ.get("LIANS_AGENT_ID") or (
    f"mcp-{LIANS_MCP_PROJECT_SCOPE}" if LIANS_MCP_PROJECT_SCOPE else "mcp-agent"
)
LIANS_LOCAL_DB = os.environ.get("LIANS_LOCAL_DB", str(Path.home() / ".lians" / "mcp.db"))
LIANS_NAMESPACE = os.environ.get("LIANS_NAMESPACE") or (
    f"mcp-{LIANS_MCP_PROJECT_SCOPE}" if LIANS_MCP_PROJECT_SCOPE else "mcp"
)


def _parse_prewarm_mode(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"background", "async"}:
        return "background"
    if value in {"1", "true", "yes", "on", "sync", "synchronous"}:
        return "sync"
    if value in {"0", "false", "no", "off"}:
        return "off"
    raise ValueError(
        "LIANS_MCP_PREWARM must be background, sync/true, or off/false"
    )


LIANS_MCP_PREWARM = _parse_prewarm_mode(
    os.environ.get("LIANS_MCP_PREWARM", "background")
)


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


# The defaults consider top-50 candidates and cap the rendered response near
# the recorded LOCOMO top-50 mean. The exact capped renderer still needs its
# own representative quality run and implies no universal usage or latency gain.
LIANS_MCP_RECALL_K = _bounded_int_env("LIANS_MCP_RECALL_K", 50, 1, 100)
LIANS_MCP_CONTEXT_MAX_TOKENS = _bounded_int_env(
    "LIANS_MCP_CONTEXT_MAX_TOKENS", 2650, 64, 32000
)

_TOOL_NAMES = frozenset({
    "remember",
    "recall",
    "recall_at",
    "reconstruct",
    "list_conflicts",
    "memory_lineage",
    "fact_history",
    "backtest_check",
})


def _parse_enabled_tools(raw: str | None) -> frozenset[str] | None:
    """Parse the optional provider-neutral MCP tool allowlist.

    An unset value preserves the historical behavior and exposes every tool.
    An explicitly blank value is rejected so a failed host interpolation cannot
    silently turn a restrictive profile into the full tool surface.
    """
    if raw is None:
        return None
    if not raw.strip():
        raise ValueError("LIANS_MCP_ENABLED_TOOLS must not be blank when set")
    enabled = frozenset(part.strip() for part in raw.split(",") if part.strip())
    unknown = sorted(enabled - _TOOL_NAMES)
    if unknown:
        raise ValueError(
            "LIANS_MCP_ENABLED_TOOLS contains unknown tool(s): " + ", ".join(unknown)
        )
    return enabled


LIANS_MCP_ENABLED_TOOLS = _parse_enabled_tools(os.environ.get("LIANS_MCP_ENABLED_TOOLS"))

_LOCAL_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lians-mcp-local")
_LOCAL_CLIENT: Any = None
_LOCAL_PREWARM_FUTURE: Future[Any] | None = None


def _iso(value: str) -> datetime:
    """Parse the ISO-8601 form used by MCP clients, including a trailing Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _get_local_client() -> Any:
    global _LOCAL_CLIENT
    if _LOCAL_CLIENT is None:
        from .local_client import LocalLiansClient
        _LOCAL_CLIENT = LocalLiansClient(
            db_path=LIANS_LOCAL_DB,
            namespace=LIANS_NAMESPACE,
        )
    return _LOCAL_CLIENT


def _local_api(method: str, path: str, body: dict | None = None) -> dict:
    """Map the public HTTP-shaped MCP calls onto LocalLiansClient."""
    client = _get_local_client()
    body = body or {}
    parsed = urlsplit(path)
    query = parse_qs(parsed.query)

    if method == "POST" and parsed.path == "/v1/memories":
        return client.add(
            agent_id=body["agent_id"],
            content=body["content"],
            event_time=_iso(body["event_time"]),
            source=body.get("source"),
            metadata=body.get("metadata", {}),
        )
    if method == "POST" and parsed.path == "/v1/recall":
        as_of = _iso(body["as_of"]) if body.get("as_of") else None
        return client.recall(
            agent_id=body["agent_id"],
            query=body["query"],
            k=body.get("k", 5),
            as_of=as_of,
            filters=body.get("filters", {}),
        )
    if method == "POST" and parsed.path == "/v1/context":
        as_of = _iso(body["as_of"]) if body.get("as_of") else None
        return client.context(
            agent_id=body["agent_id"],
            query=body["query"],
            k=body.get("k", LIANS_MCP_RECALL_K),
            as_of=as_of,
            filters=body.get("filters", {}),
            max_tokens=body.get("max_tokens", LIANS_MCP_CONTEXT_MAX_TOKENS),
            header=body.get("header"),
            mmr=body.get("mmr", False),
            surface_conflicts=body.get("surface_conflicts", True),
            max_conflicts=body.get("max_conflicts", 5),
        )
    if method == "POST" and parsed.path == "/v1/audit/reconstruct":
        return client.reconstruct(
            agent_id=body["agent_id"],
            as_of=_iso(body["as_of"]),
            query=body.get("query"),
        )
    if method == "GET" and parsed.path == "/v1/conflicts":
        return client.list_conflicts(
            status=query.get("status", ["open"])[0],
            limit=int(query.get("limit", [20])[0]),
        )
    if (
        method == "GET"
        and parsed.path.startswith("/v1/memories/")
        and parsed.path.endswith("/lineage")
    ):
        memory_id = parsed.path.removeprefix("/v1/memories/").removesuffix("/lineage")
        return client.memory_lineage(memory_id)
    if method == "GET" and parsed.path == "/v1/facts/history":
        ticker = query.get("ticker", [""])[0]
        return {
            "ticker": ticker,
            "items": client.fact_history(
                agent_id=query.get("agent_id", [LIANS_AGENT_ID])[0],
                ticker=ticker,
                metric=query.get("metric", [""])[0],
                limit=int(query.get("limit", [50])[0]),
            ),
        }
    if method == "POST" and parsed.path == "/v1/backtest/check":
        return client.backtest_check(
            agent_id=body["agent_id"],
            simulation_as_of=_iso(body["simulation_as_of"]),
            flag_limit=int(body.get("flag_limit", 1000)),
            after_event_time=(
                _iso(body["after_event_time"])
                if body.get("after_event_time")
                else None
            ),
            after_id=body.get("after_id"),
        )
    raise ValueError(f"Unsupported local MCP route: {method} {parsed.path}")


async def _api(method: str, path: str, body: dict | None = None) -> dict:
    if not LIANS_URL:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_LOCAL_EXECUTOR, _local_api, method, path, body)

    import httpx
    headers = {"X-API-Key": LIANS_API_KEY, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "POST":
            r = await client.post(f"{LIANS_URL}{path}", json=body, headers=headers)
        else:
            # NB: pass params=None (not {}). httpx treats an empty params dict as
            # "replace the query string", which wipes a query already baked into
            # `path` (e.g. fact_history) and 422s the request.
            r = await client.get(f"{LIANS_URL}{path}", params=body, headers=headers)
        r.raise_for_status()
        return r.json()


def _fmt_memories(memories: list[dict]) -> str:
    if not memories:
        return "No relevant memories found."
    return "\n".join(
        f"[{(m.get('event_time') or '')[:10]}] {m.get('content') or '[erased]'}"
        for m in memories
    )


def _fmt_context(result: dict) -> str:
    has_conflicts = bool(
        result.get("open_conflicts") or result.get("open_conflicts_total", 0)
    )
    if not result.get("memories") and not has_conflicts:
        return "No relevant memories found."
    context = str(result.get("context", "")).strip()
    return context or "No relevant memories found."


def _build_server() -> Any:
    from mcp.server import Server
    from mcp.types import TextContent, Tool, ToolAnnotations

    server = Server("lians")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools = [
            Tool(
                name="remember",
                description=(
                    "Store a financial fact, observation, or decision in persistent memory. "
                    "Always provide event_time_iso as when the event occurred, not now. "
                    "Add ticker/metric/entity metadata for precise supersession detection — "
                    "this lets Lians automatically replace stale guidance numbers."
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
                annotations=ToolAnnotations(
                    title="Remember in Lians",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            ),
            Tool(
                name="recall",
                description=(
                    "Retrieve token-bounded context from the most relevant CURRENT memories. "
                    "Returns only presently-valid facts — superseded facts are excluded at the DB layer. "
                    "Call this before answering any question that may be in memory. "
                    "Use filters={ticker: NVDA} to narrow to a specific instrument."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "k": {"type": "integer", "default": LIANS_MCP_RECALL_K},
                        "max_tokens": {
                            "type": "integer",
                            "minimum": 64,
                            "maximum": 32000,
                            "default": LIANS_MCP_CONTEXT_MAX_TOKENS,
                            "description": "Maximum estimated tokens returned to the model.",
                        },
                        "filters": {
                            "type": "object",
                            "description": "Metadata equality filters, e.g. {ticker: NVDA}",
                        },
                    },
                },
                annotations=ToolAnnotations(
                    title="Recall current Lians memory",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            ),
            Tool(
                name="recall_at",
                description=(
                    "Retrieve memories that were valid at a specific past point in time. "
                    "Use for compliance and audit: 'What guidance did we have on 2026-03-01?' "
                    "Later superseding updates are excluded — this is true point-in-time recall. "
                    "mem0 and Zep have no bitemporal model with compliance audit stack."
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
                        "k": {"type": "integer", "default": LIANS_MCP_RECALL_K},
                        "max_tokens": {
                            "type": "integer",
                            "minimum": 64,
                            "maximum": 32000,
                            "default": LIANS_MCP_CONTEXT_MAX_TOKENS,
                            "description": "Maximum estimated tokens returned to the model.",
                        },
                    },
                },
                annotations=ToolAnnotations(
                    title="Recall Lians memory at a point in time",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            ),
            Tool(
                name="reconstruct",
                description=(
                    "Reconstruct a bounded memory-state and audit-event page at a past "
                    "point in time, with exact totals and completeness fields. "
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
                annotations=ToolAnnotations(
                    title="Reconstruct Lians memory state",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            ),
            Tool(
                name="list_conflicts",
                description=(
                    "List open conflict flags — cases where two sources reported different values "
                    "for the same fact at the same event_time. Use this to surface data quality "
                    "issues before they affect decisions. Returns up to 20 open conflicts "
                    "with both memory contents so a human or LLM can decide which source to trust."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["open", "accept_a", "accept_b", "dismissed"],
                            "default": "open",
                        },
                    },
                },
                annotations=ToolAnnotations(
                    title="List Lians memory conflicts",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            ),
            Tool(
                name="memory_lineage",
                description=(
                    "Return a bounded supersession graph for a memory, including "
                    "cardinality, truncation, root/tip, and audit-binding fields. "
                    "Use when asked 'how did this guidance number evolve over time?' "
                    "or when investigating why a memory was replaced."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["memory_id"],
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "UUID of any memory in the lineage graph.",
                        },
                    },
                },
                annotations=ToolAnnotations(
                    title="Inspect Lians memory lineage",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            ),
            Tool(
                name="fact_history",
                description=(
                    "Return matches from a bounded, ordered structured-fact scan. "
                    "Query by ticker + metric — ideal for time-series views like "
                    "'show me how AAPL EPS evolved over the last four quarters'. "
                    "Superseded versions are included when found within the disclosed scan. "
                    "Entity normalization: 'Apple Inc.', ISIN 'US0378331005', and 'AAPL' "
                    "all resolve to the same fact series automatically."
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
                            "description": "Metric name (e.g. eps, price_target, guidance).",
                        },
                        "limit": {"type": "integer", "default": 50},
                    },
                },
                annotations=ToolAnnotations(
                    title="Inspect Lians fact history",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            ),
            Tool(
                name="backtest_check",
                description=(
                    "Detect lookahead bias in a backtest simulation. "
                    "Counts every recorded contaminant in the authenticated scope "
                    "and returns a bounded page of detailed flags. "
                    "Returns FUTURE_EVENT (event_time is after the checkpoint) and "
                    "LATE_REVISION (the revised figure hadn't been published yet) flags. "
                    "is_clean does not attest to unrecorded external inputs."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["simulation_as_of_iso"],
                    "properties": {
                        "simulation_as_of_iso": {
                            "type": "string",
                            "description": "ISO-8601 UTC timestamp of the simulation checkpoint.",
                        },
                        "flag_limit": {"type": "integer", "minimum": 1, "maximum": 10000},
                        "after_event_time_iso": {"type": "string"},
                        "after_id": {"type": "string"},
                    },
                },
                annotations=ToolAnnotations(
                    title="Check recorded memory for backtest contamination",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            ),
        ]
        if LIANS_MCP_ENABLED_TOOLS is None:
            return tools
        return [tool for tool in tools if tool.name in LIANS_MCP_ENABLED_TOOLS]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if LIANS_MCP_ENABLED_TOOLS is not None and name not in LIANS_MCP_ENABLED_TOOLS:
                return [TextContent(type="text", text=f"Lians tool disabled: {name}")]
            if name == "remember":
                body = {
                    "agent_id": LIANS_AGENT_ID,
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
                    "agent_id": LIANS_AGENT_ID,
                    "query": arguments["query"],
                    "k": arguments.get("k", LIANS_MCP_RECALL_K),
                    "max_tokens": arguments.get(
                        "max_tokens", LIANS_MCP_CONTEXT_MAX_TOKENS
                    ),
                    "filters": arguments.get("filters", {}),
                    "mmr": False,
                    "surface_conflicts": True,
                    "max_conflicts": 5,
                }
                result = await _api("POST", "/v1/context", body)
                return [TextContent(type="text", text=_fmt_context(result))]

            elif name == "recall_at":
                body = {
                    "agent_id": LIANS_AGENT_ID,
                    "query": arguments["query"],
                    "k": arguments.get("k", LIANS_MCP_RECALL_K),
                    "max_tokens": arguments.get(
                        "max_tokens", LIANS_MCP_CONTEXT_MAX_TOKENS
                    ),
                    "as_of": arguments["as_of_iso"],
                    "header": f"Memories valid as of {arguments['as_of_iso'][:10]}:",
                    "mmr": False,
                    # Open conflict flags describe current adjudication state,
                    # not state at the requested bitemporal cutoff.
                    "surface_conflicts": False,
                    "max_conflicts": 5,
                }
                result = await _api("POST", "/v1/context", body)
                return [TextContent(type="text", text=_fmt_context(result))]

            elif name == "reconstruct":
                body_r: dict = {
                    "agent_id": LIANS_AGENT_ID,
                    "as_of": arguments["as_of_iso"],
                }
                if "query" in arguments:
                    body_r["query"] = arguments["query"]
                result = await _api("POST", "/v1/audit/reconstruct", body_r)
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
                        f"{e.get('op', '')}  id={str(e.get('memory_id') or '')[:8]}"
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
                roots = result.get("root_ids") or [result.get("root_id", "")]
                tips = result.get("tip_ids") or [result.get("tip_id", "")]
                lines = [
                    f"Lineage graph for {memory_id[:8]}…: {len(nodes)} versions, "
                    f"{len(edges)} edges, shape={result.get('shape', 'unknown')}",
                    f"Roots: {', '.join(str(value)[:8] for value in roots)}; "
                    f"tips/boundaries: {', '.join(str(value)[:8] for value in tips)}",
                    f"Complete: {result.get('complete', False)}; "
                    f"audit bindings complete: {result.get('audit_binding_complete', False)}",
                ]
                for node in nodes:
                    status = "CURRENT" if node.get("is_current") else "superseded"
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
                    f"&agent_id={LIANS_AGENT_ID}&limit={limit}",
                )
                items = result.get("items", [])
                canonical = result.get("ticker", ticker)
                lines = [f"{len(items)} version(s) of {canonical} {metric}:"]
                for item in items:
                    et = (item.get("event_time") or "")[:10]
                    status = "active" if item.get("valid_to") is None else "superseded"
                    content = (item.get("content") or "[erased]")[:100]
                    lines.append(f"  {et}  [{status}]  {content!r}")
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "backtest_check":
                result = await _api("POST", "/v1/backtest/check", {
                    "agent_id": LIANS_AGENT_ID,
                    "simulation_as_of": arguments["simulation_as_of_iso"],
                    "flag_limit": arguments.get("flag_limit", 1000),
                    "after_event_time": arguments.get("after_event_time_iso"),
                    "after_id": arguments.get("after_id"),
                })
                is_clean = result.get("is_clean", True)
                flags = result.get("flags", [])
                checked = result.get("memories_checked", 0)
                flags_total = result.get("flags_total", len(flags))
                flags_complete = result.get("flags_complete", True)
                rate = result.get("contamination_rate", 0.0)
                if is_clean:
                    return [TextContent(
                        type="text",
                        text=(
                            f"CLEAN RECORDED SCOPE — {checked} visible memories checked; "
                            "this does not attest to unrecorded external inputs."
                        ),
                    )]
                lines = [
                    f"CONTAMINATED — showing {len(flags)} of {flags_total} flag(s) "
                    f"out of {checked} memories "
                    f"({rate:.1%} contamination rate):",
                ]
                for flag in flags:
                    ctype = flag.get("contamination_type", "")
                    delta = flag.get("delta_days", 0)
                    preview = (flag.get("content_preview") or "[erased]")[:80]
                    et = (flag.get("event_time") or "")[:10]
                    lines.append(f"  [{ctype}] +{delta:.1f}d  event={et}  {preview!r}")
                if not flags_complete:
                    lines.append(
                        "  More flags exist; continue with next_event_time and next_id "
                        f"({result.get('next_event_time')}, {result.get('next_id')})."
                    )
                return [TextContent(type="text", text="\n".join(lines))]

            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        except Exception as exc:
            return [TextContent(type="text", text=f"Lians error ({name}): {exc}")]

    return server


def _report_background_prewarm(future: Future[Any]) -> None:
    try:
        future.result()
    except Exception:
        logging.getLogger("lians.mcp").exception(
            "background local MCP prewarm failed; recall may start in degraded mode"
        )


def _run_local_prewarm() -> dict:
    return _local_api(
        "POST",
        "/v1/recall",
        {
            "agent_id": LIANS_AGENT_ID,
            "query": "__lians_mcp_startup_probe__",
            "k": 1,
            "filters": {},
        },
    )


def _prepare_local_runtime_imports() -> None:
    """Load import-heavy local dependencies before asyncio/AnyIO starts.

    On Windows, importing the ML stack for the first time from a worker after
    AnyIO has started can stall indefinitely. Client/schema construction and
    the sentence-transformers package import are bounded startup work; model
    loading and the probe query remain on the dedicated background worker.
    """
    if LIANS_URL or LIANS_MCP_PREWARM == "off":
        return
    try:
        _get_local_client()
        if os.environ.get("EMBEDDING_PROVIDER", "").strip().lower() == (
            "sentence-transformers"
        ):
            import sentence_transformers  # noqa: F401
    except Exception:
        logging.getLogger("lians.mcp").exception(
            "local MCP runtime import preparation failed; continuing without warmup"
        )


def _prewarm_local_runtime() -> None:
    """Start local initialization on its owning worker before AnyIO starts.

    Background mode lets the MCP handshake complete after bounded import
    preparation while the first tool call queues behind model/query warmup on
    the same single-thread executor. Sync mode preserves the older fully
    startup-blocking behavior for hosts with long startup timeouts.
    """
    global _LOCAL_PREWARM_FUTURE
    if LIANS_URL or LIANS_MCP_PREWARM == "off":
        return
    try:
        if LIANS_MCP_PREWARM == "sync":
            _LOCAL_PREWARM_FUTURE = _LOCAL_EXECUTOR.submit(_run_local_prewarm)
            _LOCAL_PREWARM_FUTURE.result()
        else:
            _LOCAL_PREWARM_FUTURE = _LOCAL_EXECUTOR.submit(_run_local_prewarm)
            _LOCAL_PREWARM_FUTURE.add_done_callback(_report_background_prewarm)
    except Exception:
        logging.getLogger("lians.mcp").exception(
            "local MCP prewarm failed; the server will start in degraded mode"
        )


async def _main(server: Any, stdio_server: Any) -> None:
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        # ThreadPoolExecutor workers are non-daemon threads. Once local mode has
        # served a tool call, failing to close the client and executor prevents
        # stdio hosts from stopping or restarting the MCP process cleanly.
        if _LOCAL_CLIENT is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(_LOCAL_EXECUTOR, _LOCAL_CLIENT.close)
        _LOCAL_EXECUTOR.shutdown(wait=True, cancel_futures=True)


def main() -> None:
    try:
        from mcp.server.stdio import stdio_server
    except ImportError:
        raise SystemExit(
            "MCP package not installed. Run: pip install 'lians-sdk[mcp]'"
        )

    # Construct MCP first, then import the local runtime synchronously. The
    # remaining model/query warmup can safely run on its owning worker before
    # AnyIO creates worker threads.
    server = _build_server()
    _prepare_local_runtime_imports()
    _prewarm_local_runtime()
    asyncio.run(_main(server, stdio_server))


if __name__ == "__main__":
    main()
